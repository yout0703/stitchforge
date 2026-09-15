from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
from stitchforge_api import create_app
from stitchforge_api.payments.alipay import AlipayProvider
from stitchforge_api.payments.base import PaymentError, PaymentReceipt
from stitchforge_api.payments.stripe import StripeProvider
from stitchforge_api.payments.wechat import WechatProvider

HEADERS = {"X-StitchForge-Request": "1"}
PACK = {"id": "test", "credits": 5, "amount_minor": 100, "currency": "CNY"}


@pytest.fixture
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, private_pem, public_pem


def wechat(keys):
    return WechatProvider(
        app_id="wx-test",
        mch_id="mch-test",
        serial="merchant-serial",
        private_key=keys[0],
        public_keys={"PUB_KEY_ID_TEST": keys[0].public_key()},
        public_key_id="PUB_KEY_ID_TEST",
        api_v3_key=b"0" * 32,
        public_origin="https://stitch.example",
        h5_enabled=True,
    )


def wx_headers(private, payload, timestamp=None):
    timestamp = str(timestamp or int(time.time()))
    signature = private.sign(
        f"{timestamp}\nnonce\n".encode() + payload + b"\n", padding.PKCS1v15(), hashes.SHA256()
    )
    return {
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": "nonce",
        "Wechatpay-Serial": "PUB_KEY_ID_TEST",
        "Wechatpay-Signature": base64.b64encode(signature).decode(),
    }


def wx_notify(private, transaction):
    ciphertext = AESGCM(b"0" * 32).encrypt(
        b"123456789012", json.dumps(transaction).encode(), b"transaction"
    )
    payload = json.dumps(
        {
            "event_type": "TRANSACTION.SUCCESS",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "nonce": "123456789012",
                "associated_data": "transaction",
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
        }
    ).encode()
    return payload, wx_headers(private, payload)


def register(client, email="alice@example.com"):
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "test-password-123"}
    )
    assert response.status_code == 201
    return response.json()


def create_order(client, key="payment-order-001"):
    response = client.post("/api/v1/billing/orders", json={"pack_id": "test", "request_key": key})
    assert response.status_code == 201
    return response.json()


def test_provider_configuration_is_opt_in_and_checkout_cannot_fake_payment(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_CREDIT_PACKS", json.dumps([PACK]))
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        register(client)
        assert client.get("/api/v1/billing").json()["providers"] == []
        order = create_order(client)
        url = f"/api/v1/billing/orders/{order['order_id']}"
        assert client.post(url + "/checkout", json={"provider": "wechat"}).status_code == 503
        assert client.post(url + "/sync", json={"status": "paid"}).json()["status"] == "pending"
        assert client.get("/api/v1/auth/me").json()["credits"] == 0
        client.post("/api/v1/auth/logout")
        register(client, "bob@example.com")
        for method, path in [("GET", url), ("GET", url + "/qr"), ("POST", url + "/sync")]:
            assert client.request(method, path).status_code == 404
        assert client.post(url + "/checkout", json={"provider": "wechat"}).status_code == 404


def test_wechat_native_signed_request_cache_callback_and_replay(tmp_path, monkeypatch, keys):
    monkeypatch.setenv("STITCHFORGE_CREDIT_PACKS", json.dumps([PACK]))
    calls = []

    def send(method, url, *, content, headers, **kwargs):
        auth = dict(
            item.split("=", 1) for item in headers["Authorization"].split(" ", 1)[1].split(",")
        )
        auth = {key: value.strip('"') for key, value in auth.items()}
        parsed = urlsplit(url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        message = (
            f"{method}\n{path}\n{auth['timestamp']}\n{auth['nonce_str']}\n".encode()
            + content
            + b"\n"
        )
        keys[0].public_key().verify(
            base64.b64decode(auth["signature"]), message, padding.PKCS1v15(), hashes.SHA256()
        )
        body = json.loads(content)
        assert len(body["out_trade_no"]) == 32
        assert body["amount"] == {"total": 100, "currency": "CNY"}
        assert body["notify_url"].endswith("/payments/webhooks/wechat")
        calls.append(body)
        raw = b'{"code_url":"weixin://wxpay/test-payment"}'
        return httpx.Response(200, content=raw, headers=wx_headers(keys[0], raw))

    monkeypatch.setattr(httpx, "request", send)
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        user = register(client)
        client.app.state.payment_providers = {"wechat": wechat(keys)}
        order = create_order(client)
        url = f"/api/v1/billing/orders/{order['order_id']}/checkout"
        response = client.post(url, json={"provider": "wechat"})
        assert response.status_code == 200, response.text
        assert response.json()["kind"] == "qr" and "code_url" not in response.json()
        assert client.get(response.json()["qr_url"]).headers["content-type"] == "image/png"
        assert client.post(url, json={"provider": "wechat"}).json() == response.json()
        assert len(calls) == 1
        transaction = {
            "appid": "wx-test",
            "mchid": "mch-test",
            "trade_state": "SUCCESS",
            "out_trade_no": calls[0]["out_trade_no"],
            "transaction_id": "wx-transaction-1",
            "amount": {"total": 100, "currency": "CNY"},
        }
        path = "/api/v1/payments/webhooks/wechat"
        payload, headers = wx_notify(keys[0], transaction)
        # Real callbacks have no browser session or CSRF header.
        client.cookies.clear()
        assert (
            client.post(
                path, content=payload, headers={**headers, "X-StitchForge-Request": ""}
            ).status_code
            == 204
        )
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(
                executor.map(
                    lambda _: client.post(path, content=payload, headers=headers).status_code,
                    range(3),
                )
            )
        assert results == [204, 204, 204]
        assert client.app.state.accounts.user(user["user_id"])["credits"] == 5
        assert len(client.app.state.accounts.billing(user["user_id"])["ledger"]) == 1
        assert client.post(path, content=payload + b" ", headers=headers).status_code == 400
        stale_headers = wx_headers(keys[0], payload, int(time.time()) - 600)
        assert client.post(path, content=payload, headers=stale_headers).status_code == 400
        for change in [
            {"mchid": "other"},
            {"appid": "other"},
            {"amount": {"total": 1, "currency": "CNY"}},
        ]:
            invalid, signed = wx_notify(keys[0], {**transaction, **change})
            assert client.post(path, content=invalid, headers=signed).status_code in {400, 409}
        assert client.app.state.accounts.user(user["user_id"])["credits"] == 5


def test_wechat_rejects_tampered_gateway_response_and_uses_h5_ip(keys, monkeypatch):
    provider = wechat(keys)
    order = {**PACK, "order_id": "order"}
    attempt = {"merchant_trade_no": "a" * 32, "created_at_ms": int(time.time() * 1000)}

    def send(method, url, *, content, **kwargs):
        assert json.loads(content)["scene_info"]["payer_client_ip"] == "203.0.113.10"
        raw = b'{"h5_url":"https://wx.tenpay.com/pay"}'
        return httpx.Response(200, content=raw + b" ", headers=wx_headers(keys[0], raw))

    monkeypatch.setattr(httpx, "request", send)
    with pytest.raises(PaymentError, match="签名"):
        provider.checkout(order, attempt, "h5", "203.0.113.10")


def alipay(keys):
    return AlipayProvider(
        app_id="ali-app",
        seller_id="ali-seller",
        private_key=keys[1],
        public_key=keys[2],
        public_origin="https://stitch.example",
    )


def ali_payload(keys, values):
    content = "&".join(
        f"{key}={values[key]}"
        for key in sorted(values)
        if key not in {"sign", "sign_type"} and values[key] != ""
    )
    signature = base64.b64encode(
        keys[0].sign(content.encode(), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    return urlencode({**values, "sign_type": "RSA2", "sign": signature}).encode()


def test_alipay_official_checkout_signature_and_notification(keys):
    provider = alipay(keys)
    order = {**PACK, "order_id": "order"}
    attempt = {"merchant_trade_no": "test-payment"}
    for mode, method in [("page", "alipay.trade.page.pay"), ("wap", "alipay.trade.wap.pay")]:
        result = provider.checkout(order, attempt, mode, "127.0.0.1")
        params = {key: value[0] for key, value in parse_qs(urlsplit(result["url"]).query).items()}
        assert params["method"] == method
        assert json.loads(params["biz_content"])["total_amount"] == "1.00"
        content = "&".join(f"{key}={params[key]}" for key in sorted(params) if key != "sign")
        keys[0].public_key().verify(
            base64.b64decode(params["sign"]), content.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
    values = {
        "app_id": "ali-app",
        "seller_id": "ali-seller",
        "out_trade_no": "test-payment",
        "trade_no": "ali-tx",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "1.00",
    }
    assert provider.notification(ali_payload(keys, values), {}).amount_minor == 100
    with pytest.raises(PaymentError):
        provider.notification(ali_payload(keys, values) + b"&total_amount=9.00", {})
    with pytest.raises(PaymentError, match="账号"):
        provider.notification(ali_payload(keys, {**values, "seller_id": "other"}), {})
    assert (
        provider.notification(ali_payload(keys, {**values, "trade_status": "WAIT_BUYER_PAY"}), {})
        is None
    )


def stripe_payload(event, secret="whsec_test"):
    payload = json.dumps(event).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    return payload, {"stripe-signature": f"t={timestamp},v1={signature}"}


def test_stripe_checkout_and_async_webhook_never_credit_unpaid_session(monkeypatch):
    provider = StripeProvider(
        secret_key="sk_test_example",
        webhook_secret="whsec_test",
        public_origin="https://stitch.example",
    )
    attempt = {"merchant_trade_no": "international-order", "created_at_ms": int(time.time() * 1000)}
    calls = []

    def create(params, options):
        calls.append((params, options))
        return SimpleNamespace(id="cs_test", url="https://checkout.stripe.com/test")

    monkeypatch.setattr(provider.client.v1.checkout.sessions, "create", create)
    checkout = provider.checkout({**PACK, "currency": "USD"}, attempt, "hosted", "127.0.0.1")
    assert checkout["session_id"] == "cs_test"
    assert calls[0][0]["line_items"][0]["price_data"]["unit_amount"] == 100
    assert calls[0][1]["idempotency_key"] == "checkout-international-order"
    session = {
        "id": "cs_test",
        "object": "checkout.session",
        "mode": "payment",
        "livemode": False,
        "client_reference_id": "international-order",
        "metadata": {"merchant_trade_no": "international-order"},
        "payment_intent": "pi_test",
        "amount_total": 100,
        "currency": "usd",
        "payment_status": "unpaid",
    }
    event = {"id": "evt_test", "type": "checkout.session.completed", "data": {"object": session}}
    assert provider.notification(*stripe_payload(event)) is None
    session["payment_status"] = "paid"
    event["type"] = "checkout.session.async_payment_succeeded"
    assert provider.notification(*stripe_payload(event)) == PaymentReceipt(
        "international-order", "pi_test", 100, "USD"
    )
    payload, headers = stripe_payload(event)
    with pytest.raises(PaymentError, match="签名"):
        provider.notification(payload + b" ", headers)
    session["livemode"] = True
    with pytest.raises(PaymentError, match="环境"):
        provider.notification(*stripe_payload(event))


def test_shared_settlement_requires_bound_provider_and_query_verified_amount(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_CREDIT_PACKS", json.dumps([PACK]))

    class TestProvider:
        id, label, currencies, modes = "test", "Test", frozenset({"CNY"}), ("hosted",)

        def checkout(self, order, attempt, mode, client_ip):
            return {"kind": "redirect", "url": "https://example.com/payment"}

        def query(self, order, attempt):
            return PaymentReceipt(attempt["merchant_trade_no"], "verified-tx", 100, "CNY")

    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        register(client)
        client.app.state.payment_providers = {"test": TestProvider()}
        order = create_order(client)
        url = f"/api/v1/billing/orders/{order['order_id']}"
        assert client.post(url + "/checkout", json={"provider": "test"}).status_code == 200
        attempt = client.app.state.payments.attempt(order["order_id"])
        with pytest.raises(Exception, match="支付订单不存在"):
            client.app.state.payments.settle(
                "other", PaymentReceipt(attempt["merchant_trade_no"], "tx", 100, "CNY")
            )
        assert client.post(url + "/sync", json={}).json()["status"] == "paid"
        assert client.post(url + "/sync", json={}).json()["status"] == "paid"
        assert client.get("/api/v1/auth/me").json()["credits"] == 5
        assert client.post(url + "/checkout", json={"provider": "test"}).status_code == 409


def test_alipay_query_uses_sdk_response_signature_and_rejects_modified_amount(keys, monkeypatch):
    import importlib

    provider = alipay(keys)
    result = {
        "code": "10000",
        "out_trade_no": "query-order",
        "trade_no": "alipay-query-tx",
        "trade_status": "TRADE_SUCCESS",
        "total_amount": "1.00",
    }
    raw = json.dumps(result, separators=(",", ":")).encode()
    signature = base64.b64encode(keys[0].sign(raw, padding.PKCS1v15(), hashes.SHA256()))
    payload = b'{"alipay_trade_query_response":' + raw + b',"sign":"' + signature + b'"}'
    module = importlib.import_module("alipay.aop.api.DefaultAlipayClient")
    monkeypatch.setattr(module, "do_post", lambda *args: payload)
    assert provider.query(PACK, {"merchant_trade_no": "query-order"}).amount_minor == 100
    payload = payload.replace(b'"1.00"', b'"9.00"')
    with pytest.raises(PaymentError):
        provider.query(PACK, {"merchant_trade_no": "query-order"})


def test_api_webhook_acks_alipay_and_stripe_without_cookie_only_after_verification(
    tmp_path, keys, monkeypatch
):
    monkeypatch.setenv("STITCHFORGE_CREDIT_PACKS", json.dumps([PACK]))
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        user = register(client)
        accounts, payments = client.app.state.accounts, client.app.state.payments
        client.app.state.payment_providers = {
            "alipay": alipay(keys),
            "stripe": StripeProvider(
                secret_key="sk_test_example",
                webhook_secret="whsec_test",
                public_origin="https://stitch.example",
            ),
        }
        alipay_order = create_order(client, "alipay-order-00001")
        ali_attempt = payments.bind(alipay_order["order_id"], user["user_id"], "alipay", "page")
        values = {
            "app_id": "ali-app",
            "seller_id": "ali-seller",
            "out_trade_no": ali_attempt["merchant_trade_no"],
            "trade_no": "ali-tx",
            "trade_status": "TRADE_SUCCESS",
            "total_amount": "1.00",
        }
        stripe_order = create_order(client, "stripe-order-00001")
        stripe_attempt = payments.bind(
            stripe_order["order_id"], user["user_id"], "stripe", "hosted"
        )
        client.cookies.clear()
        response = client.post(
            "/api/v1/payments/webhooks/alipay",
            content=ali_payload(keys, values),
            headers={"X-StitchForge-Request": ""},
        )
        assert response.status_code == 200 and response.text == "success"
        session = {
            "id": "cs_test",
            "object": "checkout.session",
            "mode": "payment",
            "livemode": False,
            "client_reference_id": stripe_attempt["merchant_trade_no"],
            "metadata": {"merchant_trade_no": stripe_attempt["merchant_trade_no"]},
            "payment_intent": "pi_test",
            "amount_total": 100,
            "currency": "cny",
            "payment_status": "paid",
        }
        event = {
            "id": "evt_test",
            "type": "checkout.session.completed",
            "data": {"object": session},
        }
        raw, headers = stripe_payload(event)
        assert (
            client.post(
                "/api/v1/payments/webhooks/stripe",
                content=raw,
                headers={**headers, "X-StitchForge-Request": ""},
            ).status_code
            == 204
        )
        assert accounts.user(user["user_id"])["credits"] == 10
        assert (
            client.post(
                "/api/v1/payments/webhooks/stripe", content=raw + b"x", headers=headers
            ).status_code
            == 400
        )
        # CSRF exemption cannot be used on other routes or arbitrary provider suffixes.
        assert (
            client.post(
                "/api/v1/payments/webhooks/stripe/extra",
                content=raw,
                headers={"X-StitchForge-Request": ""},
            ).status_code
            == 403
        )


def test_enabled_provider_config_loads_keys_without_public_exposure(tmp_path, monkeypatch, keys):
    from stitchforge_api.payments import load_providers

    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    private.write_text(keys[1])
    public.write_text(keys[2])
    monkeypatch.setenv("STITCHFORGE_ALIPAY_ENABLED", "1")
    for name, value in {
        "APP_ID": "ali-app",
        "SELLER_ID": "ali-seller",
        "MODES": "page,wap",
        "PRIVATE_KEY_FILE": str(private),
        "PUBLIC_KEY_FILE": str(public),
    }.items():
        monkeypatch.setenv("STITCHFORGE_ALIPAY_" + name, value)
    providers = load_providers("https://stitch.example")
    assert providers["alipay"].modes == ("page", "wap")
    with pytest.raises(ValueError, match="HTTPS"):
        load_providers("")
