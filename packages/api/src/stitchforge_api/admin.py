"""Local operator commands; no administrative HTTP endpoints are exposed."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
from pathlib import Path

from stitchforge_storage import SQLiteStore
from stitchforge_storage.accounts import AccountStore, password_hash

from .accounts import load_packs
from .payments.base import CURRENCIES


def migrate_local(accounts: AccountStore, email: str) -> int:
    """Explicitly assign the former single-user workspace to an existing account."""
    with accounts.store._transaction() as connection:
        user = connection.execute("SELECT user_id FROM users WHERE email = ?", (email,)).fetchone()
        if user is None:
            raise ValueError("请先注册目标邮箱")
        user_id = user["user_id"]
        rows = connection.execute(
            "SELECT r.document_json FROM revisions r "
            "JOIN projects p ON p.project_id = r.project_id "
            "WHERE p.workspace_id = 'local'"
        ).fetchall()
        hashes = set()
        for row in rows:
            provenance = json.loads(row[0]).get("provenance", {})
            hashes.update(provenance.get("source_asset_hashes", []))
            for source in provenance.get("sources", []):
                locator = source.get("locator", "")
                if locator.startswith("/api/v1/assets/"):
                    hashes.add(locator.rsplit("/", 1)[-1])
        connection.executemany(
            "INSERT OR IGNORE INTO asset_access VALUES (?, ?)",
            [(user_id, value) for value in hashes if re.fullmatch(r"[0-9a-f]{64}", value)],
        )
        count = connection.execute(
            "UPDATE projects SET workspace_id = ? WHERE workspace_id = 'local'", (user_id,)
        ).rowcount
        connection.execute(
            "UPDATE jobs SET workspace_id = ? WHERE workspace_id = 'local'", (user_id,)
        )
        return count


def run() -> None:
    parser = argparse.ArgumentParser(description="StitchForge 本地账号与充值管理")
    parser.add_argument(
        "--data-dir", default=os.environ.get("STITCHFORGE_DATA_DIR", ".stitchforge-data")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate-local", help="将旧本地图纸分配给指定账号")
    migrate.add_argument("--email", required=True)
    order = commands.add_parser("create-order", help="按服务端套餐创建待付款订单")
    order.add_argument("--email", required=True)
    order.add_argument("--pack-id", required=True)
    order.add_argument("--request-key", required=True)
    settle = commands.add_parser("settle-order", help="核实真实收款后将订单入账")
    settle.add_argument("--order-id", required=True)
    settle.add_argument("--payment-reference", required=True)
    settle.add_argument("--amount-minor", type=int, required=True)
    settle.add_argument("--currency", choices=sorted(CURRENCIES), required=True)
    reset = commands.add_parser("reset-password", help="核实用户身份后重置密码并撤销全部会话")
    reset.add_argument("--email", required=True)
    args = parser.parse_args()
    accounts = AccountStore(SQLiteStore(Path(args.data_dir) / "metadata.sqlite3"))
    try:
        if args.command == "migrate-local":
            print(f"已迁移 {migrate_local(accounts, args.email.strip().lower())} 份图纸")
        elif args.command == "settle-order":
            result = accounts.settle_order(
                args.order_id, args.payment_reference, args.amount_minor, args.currency
            )
            print(f"订单 {result['order_id']} 已入账；重复运行不会重复增加次数。")
        elif args.command == "create-order":
            pack = next((p for p in load_packs() if p["id"] == args.pack_id), None)
            if pack is None:
                raise ValueError("未配置该套餐")
            with accounts.store._transaction() as connection:
                user = connection.execute(
                    "SELECT user_id FROM users WHERE email = ?", (args.email.strip().lower(),)
                ).fetchone()
            if user is None:
                raise ValueError("账号不存在")
            result = accounts.create_order(user["user_id"], pack, args.request_key)
            print(json.dumps(result, ensure_ascii=False))
        else:
            password = getpass.getpass("新密码（10–128 字符）: ")
            if not 10 <= len(password) <= 128 or password != getpass.getpass("再次输入: "):
                raise ValueError("密码长度不符或两次输入不一致")
            digest = password_hash(password)
            with accounts.store._transaction() as connection:
                user = connection.execute(
                    "SELECT user_id FROM users WHERE email = ?", (args.email.strip().lower(),)
                ).fetchone()
                if user is None:
                    raise ValueError("账号不存在")
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE user_id = ?",
                    (digest, user["user_id"]),
                )
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user["user_id"],))
            print("密码已更新，全部旧会话已撤销。")
    except ValueError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    run()
