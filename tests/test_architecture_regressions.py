from copy import deepcopy

import pytest
from conftest import AccountClient
from stitchforge_api import create_app


def project_with_document(client, document):
    created = client.post("/api/v1/projects", json={"name": "保护测试"}).json()
    pid = created["project"]["project_id"]
    revision = client.post(
        f"/api/v1/projects/{pid}/revisions",
        json={
            "expected_head_revision_id": created["initial_revision"]["revision_id"],
            "document": document,
        },
    )
    assert revision.status_code == 201
    return pid, revision.json()["revision_id"]


@pytest.mark.parametrize(
    "change", ["cell", "erase", "material", "strands", "direction", "grid", "fabric", "unlock"]
)
def test_full_save_preserves_locked_semantics(tmp_path, minimal_document, change):
    document = deepcopy(minimal_document)
    if change == "cell":
        document["full_stitches"]["cells"][0] = 2
    elif change == "erase":
        document["full_stitches"]["cells"][0] = 0
    elif change == "material":
        document["materials"][0]["code"] = "OTHER"
    elif change == "strands":
        document["recipes"][0]["components"][0]["strands"] = 3
    elif change == "direction":
        document["full_stitches"]["top_leg"] = "backslash"
    elif change == "grid":
        document["grid"].update(width=3, height=4)
    elif change == "fabric":
        document["fabric"]["count"] = 18
    elif change == "unlock":
        document["regions"] = []
    with AccountClient(create_app(tmp_path)) as client:
        pid, head = project_with_document(client, minimal_document)
        response = client.post(
            f"/api/v1/projects/{pid}/revisions",
            json={
                "expected_head_revision_id": head,
                "document": document,
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "LOCK_CONFLICT"
        assert client.get(f"/api/v1/projects/{pid}").json()["head_revision_id"] == head


def test_locked_document_allows_title_and_unlocked_cells(tmp_path, minimal_document):
    with AccountClient(create_app(tmp_path)) as client:
        pid, head = project_with_document(client, minimal_document)
        edited = deepcopy(minimal_document)
        edited["metadata"]["title"] = "允许修改"
        edited["full_stitches"]["cells"][1] = 2
        assert (
            client.post(
                f"/api/v1/projects/{pid}/revisions",
                json={
                    "expected_head_revision_id": head,
                    "document": edited,
                },
            ).status_code
            == 201
        )


def test_thumbnail_revalidation_reuses_render_but_still_authenticates(tmp_path, monkeypatch):
    with AccountClient(create_app(tmp_path)) as client:
        created = client.post("/api/v1/projects", json={"name": "缓存"}).json()
        pid, head = created["project"]["project_id"], created["initial_revision"]["revision_id"]
        url = f"/api/v1/projects/{pid}/thumbnail?revision_id={head}"
        first = client.get(url)
        assert first.headers["cache-control"] == "private, no-cache"
        etag = first.headers["etag"]

        def unexpected_read(*args):
            pytest.fail("cached thumbnail must not decode the full revision again")

        monkeypatch.setattr(client.app.state.store, "get_revision", unexpected_read)
        again = client.get(url)
        assert again.content == first.content
        matched = client.get(url, headers={"If-None-Match": etag})
        assert matched.status_code == 304
        assert matched.content == b""
        assert client.get("/api/v1/auth/me").headers["cache-control"] == "no-store"
        client.post("/api/v1/auth/logout")
        assert client.get(url, headers={"If-None-Match": etag}).status_code == 401


@pytest.mark.parametrize(
    "operation", ["authenticate", "user", "owns_asset", "billing", "order", "attempt"]
)
def test_account_reads_do_not_wait_for_an_unrelated_writer(tmp_path, monkeypatch, operation):
    with AccountClient(create_app(tmp_path)) as client:
        store, accounts = client.app.state.store, client.app.state.accounts
        user = client.get("/api/v1/auth/me").json()
        token = client.cookies.get("stitchforge_session")
        order = accounts.create_order(
            user["user_id"],
            {
                "id": "read-test",
                "credits": 1,
                "amount_minor": 100,
                "currency": "CNY",
            },
            "read-test-request",
        )
        original_connect = store._connect
        writer = original_connect()
        writer.execute("BEGIN IMMEDIATE")

        def short_timeout():
            connection = original_connect()
            connection.execute("PRAGMA busy_timeout = 20")
            return connection

        monkeypatch.setattr(store, "_connect", short_timeout)
        try:
            operations = {
                "authenticate": lambda: accounts.authenticate(token),
                "user": lambda: accounts.user(user["user_id"]),
                "owns_asset": lambda: accounts.owns_asset(user["user_id"], "a" * 64),
                "billing": lambda: accounts.billing(user["user_id"]),
                "order": lambda: client.app.state.payments.order(order["order_id"]),
                "attempt": lambda: client.app.state.payments.attempt(order["order_id"]),
            }
            operations[operation]()
        finally:
            writer.rollback()
            writer.close()
