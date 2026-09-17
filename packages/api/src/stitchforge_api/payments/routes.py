from __future__ import annotations

import io
import json
from typing import Annotated, Literal

import qrcode
from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from stitchforge_storage.accounts import AccountError, now_ms

from ..accounts import current_user
from .base import PaymentError

# Only these exact server-to-server endpoints bypass browser CSRF checks.
WEBHOOK_PATHS = frozenset(
    f"/api/v1/payments/webhooks/{name}" for name in ("wechat", "alipay", "stripe")
)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    device: Literal["desktop", "mobile"] = "desktop"


def provider_descriptions(providers: dict) -> list[dict]:
    return [
        {
            "id": provider.id,
            "label": provider.label,
            "currencies": sorted(provider.currencies),
            "modes": list(provider.modes),
            "test_mode": getattr(provider, "test_mode", False),
        }
        for provider in providers.values()
    ]


def payment_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def provider_for(request: Request, provider_id: str):
        provider = request.app.state.payment_providers.get(provider_id)
        if provider is None:
            raise PaymentError("该支付渠道尚未开放。")
        return provider

    def expires_at(attempt: dict, result: dict, provider) -> int:
        return result.get("expires_at_ms") or (
            attempt["created_at_ms"] + getattr(provider, "checkout_ttl_ms", 1800_000)
        )

    def public_checkout(order_id: str, attempt: dict, result: dict, provider) -> dict:
        # Session identifiers and raw payment codes remain server-side.
        return {
            "order_id": order_id,
            "provider": attempt["provider"],
            "kind": result["kind"],
            "url": result.get("url"),
            "qr_url": f"/api/v1/billing/orders/{order_id}/qr" if result["kind"] == "qr" else None,
            "expires_at_ms": expires_at(attempt, result, provider),
        }

    @router.post("/billing/orders/{order_id}/checkout")
    def checkout(
        order_id: str,
        body: CheckoutRequest,
        request: Request,
        user: Annotated[dict, Depends(current_user)],
    ):
        payments = request.app.state.payments
        order = payments.order(order_id, user["user_id"])
        provider = provider_for(request, body.provider)
        if order["currency"] not in provider.currencies:
            raise AccountError("CURRENCY_UNSUPPORTED", "此支付方式不支持当前套餐币种。", 422)
        mode = {
            "wechat": "h5" if body.device == "mobile" else "native",
            "alipay": "wap" if body.device == "mobile" else "page",
        }.get(provider.id, "hosted")
        if mode not in provider.modes:
            mode = provider.modes[0]
        if (
            provider.id == "wechat"
            and mode == "h5"
            and "micromessenger" in request.headers.get("user-agent", "").lower()
        ):
            raise AccountError(
                "PAYMENT_BROWSER_REQUIRED", "请在手机系统浏览器中打开网站后发起微信支付。", 422
            )
        request.app.state.accounts.throttle(f"checkout:{user['user_id']}", limit=60)
        attempt = payments.bind(order_id, user["user_id"], provider.id, mode)
        cached = json.loads(attempt["checkout_json"] or "{}")
        if now_ms() >= expires_at(attempt, cached, provider):
            raise AccountError(
                "CHECKOUT_EXPIRED", "此支付入口已过期，请先查单确认，再新建充值订单。", 409
            )
        if attempt["checkout_json"]:
            return public_checkout(order_id, attempt, cached, provider)
        result = provider.checkout(
            order, attempt, mode, request.client.host if request.client else "127.0.0.1"
        )
        payments.save_checkout(order_id, result)
        return public_checkout(order_id, attempt, result, provider)

    @router.get("/billing/orders/{order_id}")
    def order_status(order_id: str, request: Request, user: Annotated[dict, Depends(current_user)]):
        return request.app.state.payments.order(order_id, user["user_id"])

    @router.post("/billing/orders/{order_id}/sync")
    def sync_order(order_id: str, request: Request, user: Annotated[dict, Depends(current_user)]):
        payments = request.app.state.payments
        order = payments.order(order_id, user["user_id"])
        if order["status"] == "paid":
            return order
        attempt = payments.attempt(order_id)
        if attempt is None:
            return order
        request.app.state.accounts.throttle(f"query:{user['user_id']}", limit=120)
        provider = provider_for(request, attempt["provider"])
        receipt = provider.query(order, attempt)
        if receipt:
            return payments.settle(provider.id, receipt)
        return order

    @router.get("/billing/orders/{order_id}/qr")
    def qr(order_id: str, request: Request, user: Annotated[dict, Depends(current_user)]):
        payments = request.app.state.payments
        order = payments.order(order_id, user["user_id"])
        attempt = payments.attempt(order_id)
        if not attempt or order["status"] != "pending" or not attempt["checkout_json"]:
            raise AccountError("CHECKOUT_EXPIRED", "支付二维码不可用。", 404)
        result = json.loads(attempt["checkout_json"])
        if now_ms() >= expires_at(attempt, result, provider_for(request, attempt["provider"])):
            raise AccountError("CHECKOUT_EXPIRED", "支付二维码不可用。", 404)
        if result.get("kind") != "qr":
            raise AccountError("NOT_FOUND", "此订单不使用二维码支付。", 404)
        output = io.BytesIO()
        qrcode.make(result["code_url"]).save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png")

    @router.post("/payments/webhooks/{provider_id}")
    async def webhook(provider_id: str, request: Request):
        provider = provider_for(request, provider_id)
        # Bounded streaming preserves the original bytes required by signature verification.
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 256 * 1024:
                raise AccountError("INVALID_PAYMENT", "通知内容过大。", 413)
        receipt = await run_in_threadpool(
            provider.notification, bytes(payload), dict(request.headers)
        )
        if receipt:
            await run_in_threadpool(request.app.state.payments.settle, provider.id, receipt)
        if provider.id == "alipay":
            return Response("success", media_type="text/plain")
        return Response(status_code=204)

    return router
