"""Persistent accounts, revocable sessions and auditable credit accounting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from typing import Any


class AccountError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    ).hex()
    return f"scrypt${salt}${digest}"


def debit_credit(
    connection: sqlite3.Connection, user_id: str, revision_id: str, project_id: str, timestamp: int
) -> None:
    updated = connection.execute(
        "UPDATE users SET credits = credits - 1 WHERE user_id = ? AND credits > 0", (user_id,)
    )
    if updated.rowcount != 1:
        raise AccountError(
            "INSUFFICIENT_CREDITS", "生成次数不足，请先充值；OXS 导入仍然免费。", 402
        )
    connection.execute(
        "INSERT INTO credit_ledger VALUES (?, ?, -1, 'generation', ?, ?, ?)",
        (uuid.uuid4().hex, user_id, revision_id, project_id, timestamp),
    )


class AccountStore:
    def __init__(self, store):
        self.store = store
        with store._transaction() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY REFERENCES workspaces(workspace_id),
                    email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                    credits INTEGER NOT NULL DEFAULT 0 CHECK(credits >= 0),
                    created_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
                    expires_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS asset_access (
                    user_id TEXT NOT NULL REFERENCES users(user_id), artifact_hash TEXT NOT NULL,
                    PRIMARY KEY(user_id, artifact_hash)
                );
                CREATE TABLE IF NOT EXISTS credit_orders (
                    order_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
                    request_key TEXT NOT NULL, pack_id TEXT NOT NULL, credits INTEGER NOT NULL,
                    amount_minor INTEGER NOT NULL, currency TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'paid')),
                    payment_reference TEXT UNIQUE, created_at_ms INTEGER NOT NULL,
                    paid_at_ms INTEGER,
                    UNIQUE(user_id, request_key)
                );
                CREATE TABLE IF NOT EXISTS credit_ledger (
                    entry_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
                    delta INTEGER NOT NULL, kind TEXT NOT NULL,
                    reference TEXT NOT NULL UNIQUE, project_id TEXT,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ledger_user ON credit_ledger(user_id, created_at_ms);
            """)

    def throttle(self, bucket: str, limit: int = 20) -> None:
        timestamp = now_ms()
        with self.store._transaction() as connection:
            connection.execute("DELETE FROM auth_attempts WHERE expires_at_ms < ?", (timestamp,))
            connection.execute(
                "INSERT INTO auth_attempts VALUES (?, 1, ?) ON CONFLICT(bucket) "
                "DO UPDATE SET attempts = attempts + 1",
                (token_hash(bucket), timestamp + 900_000),
            )
            attempts = connection.execute(
                "SELECT attempts FROM auth_attempts WHERE bucket = ?", (token_hash(bucket),)
            ).fetchone()[0]
        if attempts > limit:
            raise AccountError("RATE_LIMITED", "尝试次数过多，请 15 分钟后重试。", 429)

    def register(self, email: str, password: str) -> dict:
        digest = password_hash(password)
        user_id = "user_" + uuid.uuid4().hex
        timestamp = now_ms()
        try:
            with self.store._transaction() as connection:
                connection.execute("INSERT INTO workspaces VALUES (?, ?)", (user_id, timestamp))
                connection.execute(
                    "INSERT INTO users VALUES (?, ?, ?, 0, ?)", (user_id, email, digest, timestamp)
                )
        except sqlite3.IntegrityError as error:
            raise AccountError("ACCOUNT_EXISTS", "该邮箱已注册，请登录。", 409) from error
        return self.user(user_id)

    def login(self, email: str, password: str) -> dict:
        with self.store._reader() as connection:
            row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        stored = row["password_hash"] if row else password_hash("dummy-password", "00" * 16)
        candidate = password_hash(password, stored.split("$")[1])
        if not hmac.compare_digest(candidate, stored) or row is None:
            raise AccountError("INVALID_CREDENTIALS", "邮箱或密码不正确。", 401)
        return self.user(row["user_id"])

    def user(self, user_id: str) -> dict:
        with self.store._reader() as connection:
            row = connection.execute(
                "SELECT user_id, email, credits, created_at_ms FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            raise AccountError("AUTH_REQUIRED", "请先登录。", 401)
        return dict(row)

    def new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.store._transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at_ms < ?", (now_ms(),))
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (token_hash(token), user_id, now_ms() + 30 * 86400_000),
            )
        return token

    def authenticate(self, token: str) -> dict:
        with self.store._reader() as connection:
            row = connection.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ? AND expires_at_ms > ?",
                (token_hash(token), now_ms()),
            ).fetchone()
        if row is None:
            raise AccountError("AUTH_REQUIRED", "登录已过期，请重新登录。", 401)
        return self.user(row["user_id"])

    def logout(self, token: str) -> None:
        with self.store._transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def grant_assets(self, user_id: str, hashes: list[str]) -> None:
        with self.store._transaction() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO asset_access VALUES (?, ?)",
                [(user_id, value) for value in hashes],
            )

    def owns_asset(self, user_id: str, artifact_hash: str) -> bool:
        with self.store._reader() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM asset_access WHERE user_id = ? AND artifact_hash = ?",
                    (user_id, artifact_hash),
                ).fetchone()
                is not None
            )

    def billing(self, user_id: str) -> dict:
        with self.store._reader() as connection:
            orders = connection.execute(
                "SELECT * FROM credit_orders WHERE user_id = ? "
                "ORDER BY created_at_ms DESC LIMIT 50",
                (user_id,),
            ).fetchall()
            ledger = connection.execute(
                "SELECT * FROM credit_ledger WHERE user_id = ? "
                "ORDER BY created_at_ms DESC LIMIT 50",
                (user_id,),
            ).fetchall()
        return {
            "credits": self.user(user_id)["credits"],
            "orders": [dict(r) for r in orders],
            "ledger": [dict(r) for r in ledger],
        }

    def create_order(self, user_id: str, pack: dict[str, Any], request_key: str) -> dict:
        with self.store._transaction() as connection:
            old = connection.execute(
                "SELECT * FROM credit_orders WHERE user_id = ? AND request_key = ?",
                (user_id, request_key),
            ).fetchone()
            if old:
                if old["pack_id"] != pack["id"]:
                    raise AccountError("ORDER_CONFLICT", "请求编号已用于其他套餐。", 409)
                return dict(old)
            order_id = "order_" + uuid.uuid4().hex
            connection.execute(
                "INSERT INTO credit_orders VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)",
                (
                    order_id,
                    user_id,
                    request_key,
                    pack["id"],
                    pack["credits"],
                    pack["amount_minor"],
                    pack["currency"],
                    now_ms(),
                ),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM credit_orders WHERE order_id = ?", (order_id,)
                ).fetchone()
            )

    def settle_order(
        self, order_id: str, payment_reference: str, amount_minor: int, currency: str
    ) -> dict:
        """Trusted operator/provider adapter only; never exposed as a customer API."""
        with self.store._transaction() as connection:
            order = connection.execute(
                "SELECT * FROM credit_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            if order is None:
                raise AccountError("ORDER_NOT_FOUND", "订单不存在。", 404)
            if (order["amount_minor"], order["currency"]) != (amount_minor, currency):
                raise AccountError("PAYMENT_MISMATCH", "实付金额或币种与订单不符。", 409)
            if order["status"] == "paid":
                if order["payment_reference"] != payment_reference:
                    raise AccountError("PAYMENT_CONFLICT", "订单已使用其他交易号入账。", 409)
                return dict(order)
            if not payment_reference.strip():
                raise AccountError("INVALID_PAYMENT", "交易号不能为空。")
            try:
                connection.execute(
                    "UPDATE credit_orders SET status = 'paid', payment_reference = ?, "
                    "paid_at_ms = ? WHERE order_id = ?",
                    (payment_reference, now_ms(), order_id),
                )
            except sqlite3.IntegrityError as error:
                raise AccountError("PAYMENT_CONFLICT", "交易号已入账。", 409) from error
            connection.execute(
                "UPDATE users SET credits = credits + ? WHERE user_id = ?",
                (order["credits"], order["user_id"]),
            )
            connection.execute(
                "INSERT INTO credit_ledger VALUES (?, ?, ?, 'recharge', ?, NULL, ?)",
                (uuid.uuid4().hex, order["user_id"], order["credits"], order_id, now_ms()),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM credit_orders WHERE order_id = ?", (order_id,)
                ).fetchone()
            )
