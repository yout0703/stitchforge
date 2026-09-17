"""Payment UI regression with a mocked provider boundary; never contacts a payment gateway.

Requires web dev server at 127.0.0.1:5173.
uv run --with playwright python tools/check_web_payments.py
"""

from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
output = root / "output" / "account-review"
output.mkdir(parents=True, exist_ok=True)
user = {"user_id": "payment-user", "email": "test@example.com", "credits": 0}
orders, ledger, checkouts = [], [], []
providers = [
    {
        "id": "wechat",
        "label": "微信支付",
        "currencies": ["CNY"],
        "modes": ["native", "h5"],
        "test_mode": False,
    },
    {
        "id": "alipay",
        "label": "支付宝",
        "currencies": ["CNY"],
        "modes": ["page", "wap"],
        "test_mode": True,
    },
    {
        "id": "stripe",
        "label": "银行卡 / 海外支付",
        "currencies": ["USD"],
        "modes": ["hosted"],
        "test_mode": True,
    },
]
with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def api(route):
        path = route.request.url.split("/api/v1", 1)[1]
        if path == "/auth/me":
            route.fulfill(json=user)
        elif path == "/billing":
            route.fulfill(
                json={
                    "credits": user["credits"],
                    "checkout_enabled": True,
                    "providers": providers,
                    "orders": orders,
                    "ledger": ledger,
                    "packs": [
                        {"id": "cn", "credits": 5, "amount_minor": 500, "currency": "CNY"},
                        {"id": "global", "credits": 5, "amount_minor": 100, "currency": "USD"},
                    ],
                }
            )
        elif path == "/billing/orders" and route.request.method == "POST":
            data = route.request.post_data_json
            orders.insert(
                0,
                {
                    "order_id": f"order-{len(orders)}",
                    "status": "pending",
                    "credits": 5,
                    "amount_minor": 500 if data["pack_id"] == "cn" else 100,
                    "currency": "CNY" if data["pack_id"] == "cn" else "USD",
                    "created_at_ms": int(time.time() * 1000),
                },
            )
            route.fulfill(status=201, json=orders[0])
        elif path.endswith("/checkout"):
            data = route.request.post_data_json
            order = next(order for order in orders if order["order_id"] == path.split("/")[-2])
            order.update(payment_provider=data["provider"], payment_device=data["device"])
            checkouts.append(data)
            route.fulfill(
                json={
                    "order_id": order["order_id"],
                    "provider": data["provider"],
                    "kind": "qr" if data["provider"] == "wechat" else "redirect",
                    "qr_url": f"/api/v1/billing/orders/{order['order_id']}/qr",
                    "url": "https://checkout.stripe.com/test-only",
                    "expires_at_ms": int(time.time() * 1000) + 1800_000,
                }
            )
        elif path.endswith("/qr"):
            image = io.BytesIO()
            Image.new("RGB", (240, 240), "black").save(image, format="PNG")
            route.fulfill(content_type="image/png", body=image.getvalue())
        elif path.endswith("/sync"):
            order = next(order for order in orders if order["order_id"] == path.split("/")[-2])
            if order["status"] != "paid":
                order["status"] = "paid"
                user["credits"] += 5
                ledger.append(
                    {
                        "entry_id": "ledger-1",
                        "delta": 5,
                        "kind": "recharge",
                        "created_at_ms": int(time.time() * 1000),
                    }
                )
            route.fulfill(json=order)
        elif path.startswith("/billing/orders/"):
            route.fulfill(
                json=next(order for order in orders if order["order_id"] == path.split("/")[-1])
            )
        else:
            route.fulfill(json={"items": [], "total": 0})

    page.route("**/api/v1/**", api)
    page.goto("http://127.0.0.1:5173/#billing")
    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.get_by_label("微信支付", exact=True)).to_be_checked()
    expect(page.get_by_text("支付宝（测试环境）", exact=True)).to_be_visible()
    expect(page.get_by_text("银行卡 / 海外支付（测试环境）", exact=True)).not_to_be_visible()
    page.get_by_role("button", name="充值", exact=True).click()
    expect(page.get_by_alt_text("微信支付二维码")).to_be_visible()
    assert user["credits"] == 0
    assert checkouts[-1] == {"provider": "wechat", "device": "desktop"}
    page.get_by_role("button", name="稍后支付", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="继续支付", exact=True).click()
    expect(page.get_by_alt_text("微信支付二维码")).to_be_visible()
    assert checkouts[-1] == {"provider": "wechat", "device": "mobile"}
    page.set_viewport_size({"width": 1440, "height": 900})
    page.get_by_role("button", name="我已支付，查询到账", exact=True).click()
    expect(page.get_by_role("dialog").get_by_role("status")).to_contain_text("已到账")
    expect(page.locator(".credit-balance strong")).to_have_text("5")
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(output / "mobile-payment-methods.png"), full_page=True)
    page.get_by_label("支付币种", exact=True).select_option("USD")
    expect(page.get_by_text("银行卡 / 海外支付（测试环境）", exact=True)).to_be_visible()
    expect(page.get_by_label("微信支付", exact=True)).not_to_be_visible()
    page.get_by_role("button", name="充值", exact=True).click()
    link = page.get_by_role("link", name="前往支付页面")
    expect(link).to_have_attribute("href", "https://checkout.stripe.com/test-only")
    assert checkouts[-1] == {"provider": "stripe", "device": "mobile"}
    assert not errors, errors
    browser.close()
print(
    "PASS: currency filters methods, checkout does not add credits, "
    "verified sync updates balance, overseas checkout redirects"
)
