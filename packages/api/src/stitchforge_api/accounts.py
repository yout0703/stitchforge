from __future__ import annotations

import json
import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from stitchforge_storage import StorageNotFoundError
from stitchforge_storage.accounts import AccountError

from .payments.base import CURRENCIES

COOKIE = "stitchforge_session"


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("请输入有效邮箱")
        return value


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pack_id: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=16, max_length=128)


def current_user(request: Request) -> dict:
    user = request.app.state.accounts.authenticate(request.cookies.get(COOKIE, ""))
    request.state.user = user
    return user


def guard_resources(request: Request, user: Annotated[dict, Depends(current_user)]) -> None:
    """Every resource route uses the same ownership check, including nested routes."""
    store = request.app.state.store
    project_id = request.path_params.get("project_id")
    job_id = request.path_params.get("job_id")
    if job_id and store.get_job(job_id).workspace_id != user["user_id"]:
        raise StorageNotFoundError("任务不存在")
    if project_id and store.get_project(project_id).workspace_id != user["user_id"]:
        raise StorageNotFoundError("图纸不存在")
    artifact_hash = request.path_params.get("artifact_hash")
    if artifact_hash and not request.app.state.accounts.owns_asset(user["user_id"], artifact_hash):
        raise StorageNotFoundError("素材不存在")


def load_packs() -> list[dict]:
    packs = json.loads(os.environ.get("STITCHFORGE_CREDIT_PACKS", "[]"))
    if not isinstance(packs, list):
        raise ValueError("STITCHFORGE_CREDIT_PACKS 必须是 JSON 数组")
    identifiers = set()
    for pack in packs:
        if (
            not isinstance(pack, dict)
            or set(pack) != {"id", "credits", "amount_minor", "currency"}
            or not isinstance(pack["id"], str)
            or not 1 <= len(pack["id"]) <= 64
            or pack["id"] in identifiers
            or type(pack["credits"]) is not int
            or not 0 < pack["credits"] <= 100_000
            or type(pack["amount_minor"]) is not int
            or not 0 < pack["amount_minor"] <= 100_000_000
            or pack["currency"] not in CURRENCIES
        ):
            raise ValueError("充值套餐配置无效")
        identifiers.add(pack["id"])
    return packs


def account_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/health")
    def health():
        return {"status": "ok"}

    def sign_in(request: Request, response: Response, body: Credentials, register: bool):
        accounts = request.app.state.accounts
        ip = request.client.host if request.client else "unknown"
        accounts.throttle(f"ip:{ip}", limit=30)
        accounts.throttle(f"email:{body.email}", limit=15)
        user = (
            accounts.register(body.email, body.password)
            if register
            else accounts.login(body.email, body.password)
        )
        response.set_cookie(
            COOKIE,
            accounts.new_session(user["user_id"]),
            max_age=30 * 86400,
            secure=request.app.state.secure_cookies,
            httponly=True,
            samesite="strict",
        )
        return user

    @router.post("/auth/register", status_code=201)
    def register(body: Credentials, request: Request, response: Response):
        return sign_in(request, response, body, True)

    @router.post("/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        return sign_in(request, response, body, False)

    @router.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        request.app.state.accounts.logout(request.cookies.get(COOKIE, ""))
        response.delete_cookie(
            COOKIE, secure=request.app.state.secure_cookies, httponly=True, samesite="strict"
        )

    @router.get("/auth/me")
    def me(user: Annotated[dict, Depends(current_user)]):
        return user

    @router.get("/billing")
    def billing(request: Request, user: Annotated[dict, Depends(current_user)]):
        from .payments.routes import provider_descriptions

        data = request.app.state.accounts.billing(user["user_id"])
        for order in data["orders"]:
            attempt = request.app.state.payments.attempt(order["order_id"])
            order["payment_provider"] = attempt["provider"] if attempt else None
            order["payment_device"] = (
                "mobile" if attempt and attempt["mode"] in {"wap", "h5"} else "desktop"
            )
        return {
            **data,
            "packs": request.app.state.credit_packs,
            "checkout_enabled": bool(request.app.state.payment_providers),
            "providers": provider_descriptions(request.app.state.payment_providers),
        }

    @router.post("/billing/orders", status_code=201)
    def order(body: OrderRequest, request: Request, user: Annotated[dict, Depends(current_user)]):
        pack = next((p for p in request.app.state.credit_packs if p["id"] == body.pack_id), None)
        if pack is None:
            raise AccountError("PACK_UNAVAILABLE", "该充值套餐尚未开放。", 422)
        return request.app.state.accounts.create_order(user["user_id"], pack, body.request_key)

    return router
