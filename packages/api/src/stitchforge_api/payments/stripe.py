from __future__ import annotations

import json

import stripe

from .base import CURRENCIES, PaymentError, PaymentReceipt


class StripeProvider:
    id = "stripe"
    label = "银行卡 / 海外支付"
    currencies = CURRENCIES
    modes = ("hosted",)
    checkout_ttl_ms = 3600_000

    def __init__(self, *, secret_key, webhook_secret, public_origin):
        self.client = stripe.StripeClient(
            secret_key, max_network_retries=1, http_client=stripe.RequestsClient(timeout=15)
        )
        self.webhook_secret, self.public_origin = webhook_secret, public_origin
        self.test_mode = not secret_key.startswith("sk_live_")

    def checkout(self, order: dict, attempt: dict, mode: str, client_ip: str) -> dict:
        try:
            session = self.client.v1.checkout.sessions.create(
                {
                    "mode": "payment",
                    "ui_mode": "hosted",
                    "client_reference_id": attempt["merchant_trade_no"],
                    "metadata": {"merchant_trade_no": attempt["merchant_trade_no"]},
                    "line_items": [
                        {
                            "quantity": 1,
                            "price_data": {
                                "currency": order["currency"].lower(),
                                "unit_amount": order["amount_minor"],
                                "product_data": {
                                    "name": f"StitchForge · {order['credits']} pattern generations"
                                },
                            },
                        }
                    ],
                    "expires_at": (attempt["created_at_ms"] + self.checkout_ttl_ms) // 1000,
                    "success_url": self.public_origin + "/#billing",
                    "cancel_url": self.public_origin + "/#billing",
                },
                options={"idempotency_key": "checkout-" + attempt["merchant_trade_no"]},
            )
        except stripe.StripeError as error:
            raise PaymentError("暂时无法创建海外支付，请从原订单重试。") from error
        if not session.url:
            raise PaymentError("海外支付未返回支付页面。")
        return {
            "kind": "redirect",
            "url": session.url,
            "session_id": session.id,
            "expires_at_ms": session.expires_at * 1000,
        }

    def receipt(self, session: dict) -> PaymentReceipt | None:
        if session.get("livemode") != (not self.test_mode) or session.get("mode") != "payment":
            raise PaymentError("海外支付环境或模式不匹配。", "PAYMENT_MISMATCH", 400)
        if session.get("payment_status") != "paid":
            return None
        if session.get("metadata", {}).get("merchant_trade_no") != session.get(
            "client_reference_id"
        ):
            raise PaymentError("海外支付订单标识不匹配。", "PAYMENT_MISMATCH", 400)
        try:
            return PaymentReceipt(
                session["client_reference_id"],
                session["payment_intent"],
                session["amount_total"],
                session["currency"].upper(),
            )
        except (KeyError, AttributeError) as error:
            raise PaymentError("海外支付结果字段不完整。", "INVALID_PAYMENT", 400) from error

    def query(self, order: dict, attempt: dict) -> PaymentReceipt | None:
        checkout = json.loads(attempt["checkout_json"] or "{}")
        if not checkout.get("session_id"):
            return None
        try:
            result = self.client.v1.checkout.sessions.retrieve(checkout["session_id"])
        except stripe.StripeError as error:
            raise PaymentError("海外支付查单未成功，请稍后重试。") from error
        receipt = self.receipt(result.to_dict())
        if receipt and receipt.merchant_trade_no != attempt["merchant_trade_no"]:
            raise PaymentError("海外支付查单结果与订单不匹配。", "PAYMENT_MISMATCH", 400)
        return receipt

    def notification(self, payload: bytes, headers: dict[str, str]) -> PaymentReceipt | None:
        try:
            event = stripe.Webhook.construct_event(
                payload, headers.get("stripe-signature", ""), self.webhook_secret, tolerance=300
            )
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise PaymentError(
                "海外支付通知签名校验失败。", "INVALID_PAYMENT_SIGNATURE", 400
            ) from error
        if event["type"] not in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
        }:
            return None
        return self.receipt(event["data"]["object"].to_dict())
