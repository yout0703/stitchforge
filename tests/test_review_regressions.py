import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import AccountClient
from starlette.requests import Request
from stitchforge_api import create_app
from stitchforge_api.app import read_oxs_upload
from stitchforge_api.payments.stripe import StripeProvider
from stitchforge_storage.accounts import AccountError
from test_generation_jobs import enqueue, png
from test_payments import PACK, create_order


def test_blank_project_dimensions_palette_and_first_paint(tmp_path):
    with AccountClient(create_app(tmp_path)) as client:
        response = client.post("/api/v1/projects", json={"name": "绘制", "width": 32, "height": 48})
        assert response.status_code == 201
        created = response.json()
        revision = created["initial_revision"]
        document = revision["document"]
        assert document["grid"]["width"] == 32
        assert document["grid"]["height"] == 48
        assert document["full_stitches"]["cells"] == [0] * (32 * 48)
        assert len(document["recipes"]) == len(document["materials"]) == 8
        painted = client.post(
            f"/api/v1/projects/{created['project']['project_id']}/commands",
            json={
                "command_id": "first-paint",
                "base_revision_id": revision["revision_id"],
                "type": "paint_cells",
                "updates": [{"index": 0, "recipe_index": 1}],
            },
        )
        assert painted.status_code == 201
        assert painted.json()["revision"]["document"]["full_stitches"]["cells"][0] == 1
        assert client.post("/api/v1/projects", json={"name": "bad", "width": 0}).status_code == 422


def test_oxs_limit_stops_reading_before_tail(monkeypatch):
    monkeypatch.setattr("stitchforge_api.app.MAX_OXS_BYTES", 4)
    chunks = iter([b"123", b"45", b"never-read"])
    received = []

    async def receive():
        chunk = next(chunks)
        received.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    with pytest.raises(AccountError) as failure:
        asyncio.run(read_oxs_upload(Request({"type": "http"}, receive)))
    assert failure.value.status == 413
    assert received == [b"123", b"45"]


def test_conflicting_upload_and_idempotent_retry_never_publish(tmp_path, monkeypatch):
    with AccountClient(create_app(tmp_path)) as client:
        created, first = enqueue(client)
        artifact_store = client.app.state.artifact_store
        before = set((tmp_path / "artifacts").rglob("*"))

        def unexpected_write(*args, **kwargs):
            pytest.fail("rejected or idempotent uploads must not publish files")

        monkeypatch.setattr(artifact_store, "put_bytes", unexpected_write)
        _, repeated = enqueue(client, created=created)
        assert repeated["job_id"] == first["job_id"]
        response = client.post(
            f"/api/v1/projects/{created['project']['project_id']}/image-jobs",
            params={
                "request_key": "test-generation-request-01",
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
            },
            content=b"different bytes",
        )
        assert response.status_code == 409
        assert set((tmp_path / "artifacts").rglob("*")) == before
        assert artifact_store.read_bytes(first["parameters"]["source_sha256"]) == png()


def test_capacity_change_after_preflight_does_not_publish(tmp_path, monkeypatch):
    with AccountClient(create_app(tmp_path)) as client:
        store = client.app.state.store
        original = store.preflight_generation

        def preflight_then_lose_credits(*args):
            original(*args)
            with store._transaction() as connection:
                connection.execute("UPDATE users SET credits = 0")

        monkeypatch.setattr(store, "preflight_generation", preflight_then_lose_credits)
        created = client.post("/api/v1/projects", json={"name": "并发变更"}).json()
        before = set((tmp_path / "artifacts").rglob("*"))
        response = client.post(
            f"/api/v1/projects/{created['project']['project_id']}/image-jobs",
            params={
                "request_key": "capacity-race-request",
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
            },
            content=png(),
        )
        assert response.status_code == 402
        assert set((tmp_path / "artifacts").rglob("*")) == before


def test_publication_failure_rolls_back_job_and_generation_request(tmp_path, monkeypatch):
    with AccountClient(create_app(tmp_path)) as client:

        def fail_to_publish(*args, **kwargs):
            raise OSError("test disk write failed")

        monkeypatch.setattr(client.app.state.artifact_store, "put_bytes", fail_to_publish)
        with pytest.raises(OSError, match="disk write failed"):
            enqueue(client)
        recovered = client.get("/api/v1/generation-requests/test-generation-request-01")
        assert recovered.status_code == 404
        with client.app.state.store._reader() as connection:
            assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_guarded_abandon_keeps_completed_revision_and_cancels_pending(tmp_path):
    with AccountClient(create_app(tmp_path)) as client:
        created, job = enqueue(client)
        pid = created["project"]["project_id"]
        original = created["initial_revision"]
        document = original["document"]
        document["metadata"]["title"] = "已完成的结果"
        saved = client.post(
            f"/api/v1/projects/{pid}/revisions",
            json={
                "expected_head_revision_id": original["revision_id"],
                "document": document,
            },
        ).json()
        conflict = client.delete(
            f"/api/v1/projects/{pid}",
            params={
                "expected_head_revision_id": original["revision_id"],
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "REVISION_CONFLICT"
        assert client.get(f"/api/v1/projects/{pid}").json()["deleted_at_ms"] is None
        deleted = client.delete(
            f"/api/v1/projects/{pid}",
            params={
                "expected_head_revision_id": saved["revision_id"],
            },
        )
        assert deleted.status_code == 202
        assert client.app.state.store.get_job(job["job_id"]).state == "CANCELLED"


@pytest.mark.parametrize("legacy", [False, True])
def test_stripe_checkout_uses_provider_expiry_not_thirty_minutes(tmp_path, monkeypatch, legacy):
    monkeypatch.setenv("STITCHFORGE_CREDIT_PACKS", json.dumps([PACK]))
    provider = StripeProvider(
        secret_key="sk_test_example",
        webhook_secret="whsec_test",
        public_origin="https://example.com",
    )

    def create(params, options):
        return SimpleNamespace(
            id="cs_review", url="https://checkout.stripe.com/test", expires_at=params["expires_at"]
        )

    monkeypatch.setattr(provider.client.v1.checkout.sessions, "create", create)
    with AccountClient(create_app(tmp_path)) as client:
        client.app.state.payment_providers = {"stripe": provider}
        order = create_order(client)
        url = f"/api/v1/billing/orders/{order['order_id']}/checkout"
        checkout = client.post(url, json={"provider": "stripe"})
        assert checkout.status_code == 200
        expires = checkout.json()["expires_at_ms"]
        if legacy:
            attempt = client.app.state.payments.attempt(order["order_id"])
            cached = json.loads(attempt["checkout_json"])
            cached.pop("expires_at_ms")
            client.app.state.payments.save_checkout(order["order_id"], cached)
            expires = attempt["created_at_ms"] + provider.checkout_ttl_ms
        monkeypatch.setattr("stitchforge_api.payments.routes.now_ms", lambda: expires - 60_000)
        resumed = client.post(url, json={"provider": "stripe"})
        assert resumed.status_code == 200
        assert resumed.json()["expires_at_ms"] == expires
        monkeypatch.setattr("stitchforge_api.payments.routes.now_ms", lambda: expires)
        assert client.post(url, json={"provider": "stripe"}).status_code == 409


@pytest.mark.parametrize(
    "provider_id,modes", [("wechat", ("native", "h5")), ("alipay", ("page", "wap"))]
)
def test_cross_device_payment_preserves_original_attempt(tmp_path, monkeypatch, provider_id, modes):
    monkeypatch.setenv("STITCHFORGE_CREDIT_PACKS", json.dumps([PACK]))
    calls = []

    class Provider:
        id, label, currencies = provider_id, "测试", frozenset({"CNY"})

        def checkout(self, order, attempt, mode, client_ip):
            calls.append(attempt["merchant_trade_no"])
            return {"kind": "redirect", "url": "https://example.com/payment"}

    provider = Provider()
    provider.modes = modes
    with AccountClient(create_app(tmp_path)) as client:
        client.app.state.payment_providers = {provider_id: provider}
        order = create_order(client)
        url = f"/api/v1/billing/orders/{order['order_id']}/checkout"
        first = client.post(url, json={"provider": provider_id, "device": "desktop"})
        assert first.status_code == 200
        original = client.app.state.payments.attempt(order["order_id"])
        mobile = client.post(url, json={"provider": provider_id, "device": "mobile"})
        assert mobile.status_code == 409
        assert mobile.json()["code"] == "PAYMENT_DEVICE_LOCKED"
        assert "电脑" in mobile.json()["message"]
        assert "勿重复下单" in mobile.json()["message"]
        assert client.app.state.payments.attempt(order["order_id"]) == original
        assert len(calls) == 1
        assert (
            client.post(url, json={"provider": provider_id, "device": "desktop"}).json()
            == first.json()
        )
