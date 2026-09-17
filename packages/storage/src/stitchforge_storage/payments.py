from __future__ import annotations

import json
import uuid

from .accounts import AccountError, now_ms


class PaymentStore:
    def __init__(self, accounts):
        self.accounts = accounts
        self.store = accounts.store
        with self.store._transaction() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS payment_attempts (
                    order_id TEXT PRIMARY KEY REFERENCES credit_orders(order_id),
                    provider TEXT NOT NULL, merchant_trade_no TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL, checkout_json TEXT, created_at_ms INTEGER NOT NULL
                )
            """)

    def order(self, order_id: str, user_id: str | None = None) -> dict:
        with self.store._reader() as connection:
            row = connection.execute(
                "SELECT * FROM credit_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        if row is None or (user_id is not None and row["user_id"] != user_id):
            raise AccountError("ORDER_NOT_FOUND", "订单不存在。", 404)
        return dict(row)

    def bind(self, order_id: str, user_id: str, provider: str, mode: str) -> dict:
        with self.store._transaction() as connection:
            order = connection.execute(
                "SELECT * FROM credit_orders WHERE order_id = ? AND user_id = ?",
                (order_id, user_id),
            ).fetchone()
            if order is None:
                raise AccountError("ORDER_NOT_FOUND", "订单不存在。", 404)
            if order["status"] != "pending":
                raise AccountError("ORDER_PAID", "订单已经到账，无需再次支付。", 409)
            old = connection.execute(
                "SELECT * FROM payment_attempts WHERE order_id = ?", (order_id,)
            ).fetchone()
            if old:
                if old["provider"] != provider:
                    raise AccountError(
                        "PAYMENT_METHOD_LOCKED",
                        "此订单已选择其他支付方式，请继续原方式支付；如已付款，请查单确认，勿重复下单。",
                        409,
                    )
                if old["mode"] != mode:
                    device = "电脑" if old["mode"] in {"native", "page"} else "手机系统浏览器"
                    raise AccountError(
                        "PAYMENT_DEVICE_LOCKED",
                        f"此订单已在{device}发起，请返回原设备继续支付。"
                        "如已付款，请查单确认，勿重复下单。",
                        409,
                    )
                return dict(old)
            connection.execute(
                "INSERT INTO payment_attempts VALUES (?, ?, ?, ?, NULL, ?)",
                (order_id, provider, uuid.uuid4().hex, mode, now_ms()),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM payment_attempts WHERE order_id = ?", (order_id,)
                ).fetchone()
            )

    def attempt(self, order_id: str) -> dict | None:
        with self.store._reader() as connection:
            row = connection.execute(
                "SELECT * FROM payment_attempts WHERE order_id = ?", (order_id,)
            ).fetchone()
        return dict(row) if row else None

    def save_checkout(self, order_id: str, checkout: dict) -> None:
        with self.store._transaction() as connection:
            connection.execute(
                "UPDATE payment_attempts SET checkout_json = ? WHERE order_id = ?",
                (json.dumps(checkout, ensure_ascii=False), order_id),
            )

    def settle(self, provider: str, receipt) -> dict:
        with self.store._reader() as connection:
            attempt = connection.execute(
                "SELECT order_id FROM payment_attempts "
                "WHERE provider = ? AND merchant_trade_no = ?",
                (provider, receipt.merchant_trade_no),
            ).fetchone()
        if attempt is None:
            raise AccountError("ORDER_NOT_FOUND", "支付订单不存在。", 404)
        if (
            not receipt.transaction_id
            or type(receipt.amount_minor) is not int
            or receipt.amount_minor <= 0
        ):
            raise AccountError("INVALID_PAYMENT", "支付结果无效。", 400)
        # Bind payment to the stored provider and immutable merchant reference before crediting.
        return self.accounts.settle_order(
            attempt["order_id"],
            f"{provider}:{receipt.transaction_id}",
            receipt.amount_minor,
            receipt.currency,
        )
