from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stitchforge_api import create_app
from stitchforge_storage.accounts import AccountError, AccountStore

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-StitchForge-Request": "1"}


def register(client, email="alice@example.com"):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-password-123",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def project(client):
    response = client.post("/api/v1/projects", json={"name": "private pattern"})
    assert response.status_code == 201, response.text
    return response.json()


def topup(accounts, user_id, credits=1):
    order = accounts.create_order(
        user_id,
        {
            "id": "test",
            "credits": credits,
            "amount_minor": 100,
            "currency": "CNY",
        },
        "test-order-key",
    )
    accounts.settle_order(order["order_id"], "pay-" + user_id, 100, "CNY")
    return order


def test_sessions_csrf_expiration_and_private_cache(tmp_path):
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        assert client.get("/api/v1/projects").status_code == 401
        credentials = {"email": " Alice@Example.com ", "password": "correct-password-123"}
        response = client.post("/api/v1/auth/register", json=credentials)
        assert response.status_code == 201
        assert response.json()["email"] == "alice@example.com"
        assert response.json()["credits"] == 0
        assert "password" not in response.text
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        token = client.cookies.get("stitchforge_session")
        assert client.get("/api/v1/auth/me").headers["cache-control"] == "no-store"
        assert (
            client.post(
                "/api/v1/projects", json={"name": "CSRF"}, headers={"X-StitchForge-Request": ""}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/projects", json={"name": "CSRF"}, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert client.post("/api/v1/auth/logout").status_code == 204
        client.cookies.set("stitchforge_session", token)
        assert client.get("/api/v1/projects").status_code == 401
        assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
        with client.app.state.store._transaction() as connection:
            connection.execute("UPDATE sessions SET expires_at_ms = 0")
            row = connection.execute("SELECT password_hash FROM users").fetchone()
            assert row[0].startswith("scrypt$") and credentials["password"] not in row[0]
        assert client.get("/api/v1/auth/me").status_code == 401
        bad = client.post("/api/v1/auth/login", json={**credentials, "password": "s3cret!"})
        assert "s3cret!" not in bad.text  # Validation errors must not echo passwords.


def test_all_resource_paths_are_scoped_and_oxs_is_free(tmp_path):
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        alice = register(client)
        imported = client.post(
            "/api/v1/projects/import-oxs",
            content=(ROOT / "examples/minimal-pattern.oxs").read_bytes(),
            headers={"content-type": "application/octet-stream"},
        ).json()
        pid, rid = imported["project"]["project_id"], imported["initial_revision"]["revision_id"]
        asset = imported["source_sha256"]
        assert client.get(f"/api/v1/assets/{asset}").status_code == 200
        job = client.post(f"/api/v1/projects/{pid}/jobs", json={"job_type": "test"}).json()
        assert client.get("/api/v1/auth/me").json()["credits"] == 0
        client.post("/api/v1/auth/logout")
        bob = register(client, "bob@example.com")
        assert bob["user_id"] != alice["user_id"]
        assert client.get("/api/v1/projects").json()["total"] == 0
        for method, path, data in [
            ("GET", f"/projects/{pid}", None),
            ("GET", f"/projects/{pid}/revisions/{rid}", None),
            ("GET", f"/projects/{pid}/thumbnail?revision_id={rid}", None),
            ("DELETE", f"/projects/{pid}", None),
            ("POST", f"/projects/{pid}/restore", None),
            ("PATCH", f"/projects/{pid}", {"name": "stolen", "expected_head_revision_id": rid}),
            (
                "POST",
                f"/projects/{pid}/revisions",
                {
                    "document": imported["initial_revision"]["document"],
                    "expected_head_revision_id": rid,
                },
            ),
            ("POST", f"/projects/{pid}/jobs", {"job_type": "test"}),
            (
                "POST",
                f"/projects/{pid}/commands",
                {
                    "command_id": "evil",
                    "base_revision_id": rid,
                    "type": "paint_cells",
                    "updates": [{"index": 0, "recipe_index": 0}],
                },
            ),
            (
                "POST",
                f"/projects/{pid}/commands/any/undo",
                {"command_id": "evil", "base_revision_id": rid},
            ),
            (
                "POST",
                f"/projects/{pid}/commands/any/redo",
                {"command_id": "evil", "base_revision_id": rid},
            ),
            ("GET", f"/jobs/{job['job_id']}", None),
            ("POST", f"/jobs/{job['job_id']}/cancel", None),
            ("GET", f"/assets/{asset}", None),
        ]:
            response = client.request(method, "/api/v1" + path, json=data)
            assert response.status_code == 404, (path, response.text)
        response = client.post(
            f"/api/v1/projects/{pid}/convert-image?expected_head_revision_id={rid}",
            content=b"bad",
            headers={"content-type": "image/png"},
        )
        assert response.status_code == 404
        # Forging a provenance URL does not grant access to another user's content hash.
        own = project(client)
        forged = deepcopy(own["initial_revision"]["document"])
        forged["provenance"]["source_asset_hashes"] = [asset]
        client.post(
            f"/api/v1/projects/{own['project']['project_id']}/revisions",
            json={
                "document": forged,
                "expected_head_revision_id": own["initial_revision"]["revision_id"],
            },
        )
        assert client.get(f"/api/v1/assets/{asset}").status_code == 404
        assert client.get("/api/v1/billing").json()["ledger"] == []


def test_orders_validate_server_prices_and_settle_once(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "STITCHFORGE_CREDIT_PACKS",
        '[{"id":"small","credits":5,"amount_minor":990,"currency":"CNY"}]',
    )
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        user = register(client)
        data = {"pack_id": "small", "request_key": "request-key-00001"}
        assert (
            client.post("/api/v1/billing/orders", json={**data, "credits": 999}).status_code == 422
        )
        order = client.post("/api/v1/billing/orders", json=data).json()
        assert client.post("/api/v1/billing/orders", json=data).json() == order
        assert order["status"] == "pending"
        assert client.get("/api/v1/auth/me").json()["credits"] == 0
        accounts = client.app.state.accounts
        with pytest.raises(AccountError, match="金额"):
            accounts.settle_order(order["order_id"], "pay-1", 1, "CNY")
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _: accounts.settle_order(order["order_id"], "pay-1", 990, "CNY"),
                    range(4),
                )
            )
        assert all(result["status"] == "paid" for result in results)
        assert accounts.user(user["user_id"])["credits"] == 5
        assert len(accounts.billing(user["user_id"])["ledger"]) == 1
        other = accounts.create_order(
            user["user_id"],
            {"id": "other", "credits": 5, "amount_minor": 990, "currency": "CNY"},
            "request-key-00002",
        )
        with pytest.raises(AccountError, match="交易号"):
            accounts.settle_order(other["order_id"], "pay-1", 990, "CNY")
        assert accounts.user(user["user_id"])["credits"] == 5
        assert (
            client.post(f"/api/v1/billing/orders/{other['order_id']}/settle", json={}).status_code
            == 404
        )


def test_credit_debit_and_revision_are_atomic_under_concurrency(tmp_path):
    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        user = register(client)
        accounts, store = client.app.state.accounts, client.app.state.store
        topup(accounts, user["user_id"])
        projects = [project(client), project(client)]

        def generate(item):
            try:
                store.add_revision(
                    "new-" + item["project"]["project_id"],
                    project_id=item["project"]["project_id"],
                    expected_head_revision_id=item["initial_revision"]["revision_id"],
                    document=item["initial_revision"]["document"],
                    now_ms=100,
                    charge_user_id=user["user_id"],
                )
                return True
            except AccountError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(generate, projects))
        assert sorted(outcomes) == [False, True]
        assert accounts.user(user["user_id"])["credits"] == 0
        loser = projects[outcomes.index(False)]
        assert (
            store.get_project(loser["project"]["project_id"]).head_revision_id
            == loser["initial_revision"]["revision_id"]
        )
        # The committed accounting survives recreating the store facade.
        assert AccountStore(store).billing(user["user_id"])["credits"] == 0


def test_conversion_charges_success_only_and_retries_do_not_charge(tmp_path):
    from io import BytesIO

    from PIL import Image

    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        user = register(client)
        created = project(client)
        pid, head = created["project"]["project_id"], created["initial_revision"]["revision_id"]
        url = (
            f"/api/v1/projects/{pid}/convert-image?expected_head_revision_id={head}"
            "&grid_width=2&grid_height=2&optimize=false&remove_border_background=false"
        )
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
        assert (
            client.post(
                url, content=buffer.getvalue(), headers={"content-type": "image/png"}
            ).status_code
            == 402
        )
        accounts = client.app.state.accounts
        topup(accounts, user["user_id"], 2)
        assert (
            client.post(
                url, content=b"bad image", headers={"content-type": "image/png"}
            ).status_code
            == 422
        )
        assert accounts.user(user["user_id"])["credits"] == 2
        result = client.post(url, content=buffer.getvalue(), headers={"content-type": "image/png"})
        assert result.status_code == 201, result.text
        assert accounts.user(user["user_id"])["credits"] == 1
        assert client.get(result.json()["source_asset"]["url"]).status_code == 200
        assert (
            client.post(
                url, content=buffer.getvalue(), headers={"content-type": "image/png"}
            ).status_code
            == 409
        )
        assert accounts.user(user["user_id"])["credits"] == 1


def test_login_rate_limit_and_production_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_PUBLIC_ORIGIN", "https://stitch.example")
    with TestClient(
        create_app(tmp_path),
        base_url="https://stitch.example",
        headers={**HEADERS, "Origin": "https://stitch.example"},
    ) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "a@example.com", "password": "correct-password-123"},
        )
        assert response.status_code == 201
        assert "Secure" in response.headers["set-cookie"]
        assert (
            client.post(
                "/api/v1/projects", json={"name": "x"}, headers={"Origin": "http://localhost:5173"}
            ).status_code
            == 403
        )
        for _ in range(15):
            response = client.post(
                "/api/v1/auth/login",
                json={"email": "a@example.com", "password": "wrong-password-123"},
            )
        assert response.status_code == 429


def test_legacy_workspace_is_unclaimed_until_explicit_migration(tmp_path):
    import json

    from stitchforge_api.admin import migrate_local

    with TestClient(create_app(tmp_path), headers=HEADERS) as client:
        store = client.app.state.store
        document = json.loads((ROOT / "examples/minimal-pattern.json").read_text())
        store.create_project_with_revision(
            "legacy",
            workspace_id="local",
            name="旧作品",
            revision_id="legacy-r1",
            document=document,
            now_ms=1,
        )
        register(client)
        assert client.get("/api/v1/projects").json()["total"] == 0
        assert client.get("/api/v1/projects/legacy").status_code == 404
        assert migrate_local(client.app.state.accounts, "alice@example.com") == 1
        assert client.get("/api/v1/projects").json()["total"] == 1
        assert (
            client.get("/api/v1/projects/legacy/revisions/legacy-r1").json()["document"] == document
        )
        assert migrate_local(client.app.state.accounts, "alice@example.com") == 0
        client.post("/api/v1/auth/logout")
        register(client, "bob@example.com")
        assert client.get("/api/v1/projects/legacy").status_code == 404
