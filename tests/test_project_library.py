from __future__ import annotations

import io
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from stitchforge_api import create_app

ROOT = Path(__file__).resolve().parents[1]


def create(client, name="图纸"):
    response = client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_library_pagination_search_sort_and_workspace_isolation(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        store = client.app.state.store
        store.ensure_workspace("other", now_ms=1)
        store.create_project("private", workspace_id="other", name="隐藏图纸", now_ms=1)
        for name in ["小浣熊", "灯塔_100%", "Alpha", "beta", "a needle"]:
            create(client, name)
        first = client.get("/api/v1/projects?limit=2&sort=name").json()
        assert first["total"] == 5
        assert [item["name"] for item in first["items"]] == ["a needle", "Alpha"]
        second = client.get("/api/v1/projects?limit=2&offset=2&sort=name").json()
        assert not (
            {p["project_id"] for p in first["items"]} & {p["project_id"] for p in second["items"]}
        )
        for search in ["浣熊", "_100%", "ALPHA", "' OR 1=1 --"]:
            items = client.get("/api/v1/projects", params={"search": search}).json()["items"]
            assert len(items) == (0 if search.startswith("'") else 1)
        assert "document" not in first["items"][0]
        assert first["items"][0]["width"] == 1
        assert first["items"][0]["color_count"] == 0
        assert first["items"][0]["source_kind"] == "blank"
        created_order = client.get("/api/v1/projects?sort=created").json()["items"]
        assert created_order[0]["name"] == "a needle"


def test_rename_is_versioned_atomic_and_survives_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        created = create(client, "旧名称")
        project_id = created["project"]["project_id"]
        initial = created["initial_revision"]
        url = f"/api/v1/projects/{project_id}"
        response = client.patch(
            url,
            json={
                "name": "  新名称  ",
                "expected_head_revision_id": initial["revision_id"],
            },
        )
        assert response.status_code == 200
        head = response.json()["revision_id"]
        renamed = client.get(f"{url}/revisions/{head}").json()["document"]
        assert renamed["metadata"]["title"] == "新名称"
        assert renamed["full_stitches"] == initial["document"]["full_stitches"]
        assert (
            client.get(f"{url}/revisions/{initial['revision_id']}").json()["document"]["metadata"][
                "title"
            ]
            == "旧名称"
        )
        assert (
            client.patch(
                url,
                json={
                    "name": "过期修改",
                    "expected_head_revision_id": initial["revision_id"],
                },
            ).status_code
            == 409
        )
        assert client.get(url).json()["name"] == "新名称"
        assert (
            client.patch(
                url,
                json={
                    "name": "   ",
                    "expected_head_revision_id": head,
                },
            ).status_code
            == 422
        )
    with TestClient(create_app(tmp_path)) as client:
        item = client.get("/api/v1/projects").json()["items"][0]
        assert item["name"] == "新名称"
        assert item["head_revision_id"] == head


def test_revision_save_keeps_library_name_in_sync(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        created = create(client)
        project_id = created["project"]["project_id"]
        document = deepcopy(created["initial_revision"]["document"])
        document["metadata"]["title"] = "保存的标题"
        response = client.post(
            f"/api/v1/projects/{project_id}/revisions",
            json={
                "document": document,
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
            },
        )
        assert response.status_code == 201
        assert client.get("/api/v1/projects").json()["items"][0]["name"] == "保存的标题"


def test_trash_restore_preserves_oxs_and_history(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        imported = client.post(
            "/api/v1/projects/import-oxs",
            content=(ROOT / "examples/minimal-pattern.oxs").read_bytes(),
            headers={"content-type": "application/octet-stream"},
        ).json()
        project_id = imported["project"]["project_id"]
        head = imported["initial_revision"]["revision_id"]
        document = imported["initial_revision"]["document"]
        url = f"/api/v1/projects/{project_id}"
        assert client.get("/api/v1/projects").json()["items"][0]["source_kind"] == "oxs"
        assert client.delete(url).status_code == 202
        assert client.delete(url).status_code == 202
        assert client.get("/api/v1/projects").json()["total"] == 0
        assert client.get("/api/v1/projects?deleted=true").json()["total"] == 1
        assert (
            client.patch(
                url, json={"name": "已删除", "expected_head_revision_id": head}
            ).status_code
            == 409
        )
        assert client.post(url + "/restore").status_code == 200
        assert client.post(url + "/restore").status_code == 200
        assert client.get("/api/v1/projects?deleted=true").json()["total"] == 0
        assert client.get(f"{url}/revisions/{head}").json()["document"] == document
        assert client.get(document["provenance"]["sources"][0]["locator"]).status_code == 200


def test_thumbnail_exact_colors_aspect_and_revision_ownership(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        imported = client.post(
            "/api/v1/projects/import-oxs",
            content=(ROOT / "examples/minimal-pattern.oxs").read_bytes(),
            headers={"content-type": "application/octet-stream"},
        ).json()
        project_id = imported["project"]["project_id"]
        head = imported["initial_revision"]["revision_id"]
        response = client.get(
            f"/api/v1/projects/{project_id}/thumbnail", params={"revision_id": head}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert "immutable" in response.headers["cache-control"]
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.size == (320, 240)
            assert image.getpixel((0, 0)) == (0, 0, 0)
        other = create(client)["project"]["project_id"]
        assert (
            client.get(
                f"/api/v1/projects/{other}/thumbnail", params={"revision_id": head}
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/v1/projects/{other}",
                json={
                    "name": "跨图纸修改",
                    "expected_head_revision_id": head,
                },
            ).status_code
            == 404
        )
        assert client.post("/api/v1/projects/missing/restore").status_code == 404


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "sort=arbitrary"])
def test_invalid_library_query(tmp_path, query):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f"/api/v1/projects?{query}").status_code == 422


def test_large_oxs_import_save_reload_and_library_thumbnail(tmp_path):
    payload = (ROOT / "examples/minimal-pattern.oxs").read_bytes()
    payload = payload.replace(b'chartwidth="4"', b'chartwidth="480"').replace(
        b'chartheight="3"', b'chartheight="304"'
    )
    payload = payload.replace(
        b"</fullstitches>", b'<stitch x="479" y="303" palindex="1"/></fullstitches>'
    )
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/projects/import-oxs",
            content=payload,
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 201
        imported = response.json()
        project_id = imported["project"]["project_id"]
        revision = imported["initial_revision"]
        document = revision["document"]
        assert len(document["full_stitches"]["cells"]) == 145_920
        assert document["full_stitches"]["cells"][-1] == 1
        document["full_stitches"]["cells"][-1] = 2
        saved = client.post(
            f"/api/v1/projects/{project_id}/revisions",
            json={
                "document": document,
                "expected_head_revision_id": revision["revision_id"],
            },
        )
        assert saved.status_code == 201
        head = saved.json()["revision_id"]
    with TestClient(create_app(tmp_path)) as client:
        restored = client.get(f"/api/v1/projects/{project_id}/revisions/{head}").json()["document"]
        assert restored == document
        listed = client.get("/api/v1/projects").json()["items"][0]
        assert (listed["width"], listed["height"]) == (480, 304)
        assert (
            client.get(f"/api/v1/projects/{project_id}/thumbnail?revision_id={head}").status_code
            == 200
        )


def test_oxs_error_summary_omits_nonblocking_warnings(tmp_path):
    from xml.etree import ElementTree

    root = ElementTree.fromstring((ROOT / "examples/minimal-pattern.oxs").read_bytes())
    root.find("properties").set("software", "长说明" * 4000)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/projects/import-oxs",
            content=ElementTree.tostring(root),
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 422
        message = response.json()["message"]
        assert "not valid" in message
        assert "slash" not in message and "50 mm" not in message
        assert len(message) <= 320
        assert client.get("/api/v1/projects").json()["total"] == 0
