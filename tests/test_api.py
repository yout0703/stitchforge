from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from PIL import Image
from stitchforge_api import create_app

ROOT = Path(__file__).resolve().parents[1]


def _create_project(client: TestClient, *, name: str = "测试图纸") -> dict:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "fabric_count": 18, "fabric_color": "#FFFFFF"},
    )
    assert response.status_code == 201
    return response.json()


def test_project_creation_starts_with_valid_empty_revision(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        created = _create_project(client)
        project = created["project"]
        revision = created["initial_revision"]

        assert project["head_revision_id"] == revision["revision_id"]
        assert revision["document"]["grid"] == {
            "width": 1,
            "height": 1,
            "origin": "top_left",
            "subgrid_scale": 2,
        }
        assert revision["document"]["fabric"]["count"] == 18
        assert revision["document"]["fabric"]["display_srgb"] == "#FFFFFF"
        response = client.get(f"/api/v1/projects/{project['project_id']}")
        assert response.status_code == 200
        assert response.headers["x-request-id"].startswith("req_")


def test_revision_update_and_stale_head_return_structured_error(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        created = _create_project(client)
        project_id = created["project"]["project_id"]
        first = created["initial_revision"]
        updated_document = deepcopy(first["document"])
        updated_document["metadata"]["title"] = "修改后"

        response = client.post(
            f"/api/v1/projects/{project_id}/revisions",
            json={
                "expected_head_revision_id": first["revision_id"],
                "document": updated_document,
            },
        )
        assert response.status_code == 201
        second = response.json()
        assert second["parent_revision_id"] == first["revision_id"]

        stale = client.post(
            f"/api/v1/projects/{project_id}/revisions",
            json={
                "expected_head_revision_id": first["revision_id"],
                "document": updated_document,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "REVISION_CONFLICT"
        assert stale.json()["request_id"] == stale.headers["x-request-id"]


def test_invalid_document_and_request_have_stable_error_shape(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        created = _create_project(client)
        project_id = created["project"]["project_id"]
        revision_id = created["initial_revision"]["revision_id"]

        invalid = client.post(
            f"/api/v1/projects/{project_id}/revisions",
            json={"expected_head_revision_id": revision_id, "document": {}},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "INVALID_DOCUMENT"
        assert invalid.json()["details"]

        malformed = client.post(
            "/api/v1/projects",
            json={"name": "x", "fabric_count": 2, "unknown": True},
        )
        assert malformed.status_code == 422
        assert malformed.json()["code"] == "INVALID_REQUEST"
        assert set(malformed.json()) == {
            "code",
            "message",
            "details",
            "retryable",
            "request_id",
        }


def test_job_polling_cancellation_and_project_deletion(tmp_path: Path) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        created = _create_project(client)
        project_id = created["project"]["project_id"]
        revision_id = created["initial_revision"]["revision_id"]
        queued = client.post(
            f"/api/v1/projects/{project_id}/jobs",
            json={
                "job_type": "conversion",
                "parameters": {"color_budget": 24},
                "input_revision_id": revision_id,
            },
        )
        assert queued.status_code == 202
        job_id = queued.json()["job_id"]
        assert client.get(f"/api/v1/jobs/{job_id}").json()["state"] == "QUEUED"
        cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["state"] == "CANCELLED"
        assert client.delete(f"/api/v1/projects/{project_id}").status_code == 202

        late = client.post(
            f"/api/v1/projects/{project_id}/jobs",
            json={"job_type": "conversion"},
        )
        assert late.status_code == 409
        assert late.json()["code"] == "CONFLICT"


def test_command_is_idempotent_and_locked_cells_are_rejected(
    tmp_path: Path, minimal_document: dict
) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        created = _create_project(client)
        project_id = created["project"]["project_id"]
        initial_id = created["initial_revision"]["revision_id"]
        imported = client.post(
            f"/api/v1/projects/{project_id}/revisions",
            json={
                "expected_head_revision_id": initial_id,
                "document": minimal_document,
            },
        ).json()
        request = {
            "command_id": "paint-1",
            "base_revision_id": imported["revision_id"],
            "type": "paint_cells",
            "updates": [{"index": 1, "recipe_index": 1}],
        }
        first = client.post(f"/api/v1/projects/{project_id}/commands", json=request)
        repeated = client.post(f"/api/v1/projects/{project_id}/commands", json=request)
        assert first.status_code == repeated.status_code == 201
        assert repeated.json()["revision"]["revision_id"] == first.json()["revision"]["revision_id"]

        locked = client.post(
            f"/api/v1/projects/{project_id}/commands",
            json={
                "command_id": "paint-locked",
                "base_revision_id": first.json()["revision"]["revision_id"],
                "type": "paint_cells",
                "updates": [{"index": 0, "recipe_index": 2}],
            },
        )
        assert locked.status_code == 409
        assert locked.json()["code"] == "LOCK_CONFLICT"
        assert locked.json()["details"] == {"indices": [0]}

        undo = client.post(
            f"/api/v1/projects/{project_id}/commands/paint-1/undo",
            json={
                "command_id": "undo-1",
                "base_revision_id": first.json()["revision"]["revision_id"],
            },
        )
        assert undo.status_code == 201
        assert undo.json()["revision"]["document"] == minimal_document
        redo = client.post(
            f"/api/v1/projects/{project_id}/commands/paint-1/redo",
            json={
                "command_id": "redo-1",
                "base_revision_id": undo.json()["revision"]["revision_id"],
            },
        )
        assert redo.status_code == 201
        assert redo.json()["revision"]["document"] == first.json()["revision"]["document"]


def test_cross_site_writes_are_rejected_and_missing_resources_are_404(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(tmp_path / "data")) as client:
        forbidden = client.post(
            "/api/v1/projects",
            json={"name": "blocked"},
            headers={"Origin": "https://attacker.example"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "ORIGIN_FORBIDDEN"
        assert client.get("/api/v1/projects/missing").status_code == 404


def test_workspace_and_projects_survive_application_restart(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    with TestClient(create_app(data_dir)) as first_client:
        project_id = _create_project(first_client)["project"]["project_id"]
    with TestClient(create_app(data_dir)) as second_client:
        response = second_client.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 200
        assert response.json()["project_id"] == project_id


def test_image_conversion_creates_revision_and_serves_source_asset(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    image = Image.new("RGB", (20, 20), "#EEEEEE")
    for y in range(6, 14):
        for x in range(6, 14):
            image.putpixel((x, y), (190, 25, 35))
    image.save(source)
    payload = source.read_bytes()
    catalog = ROOT / "catalogs/dmc-117-2021-quiltysolid-2025-07.json"

    with TestClient(create_app(tmp_path / "data", catalog_path=catalog)) as client:
        created = _create_project(client, name="图片转换")
        project_id = created["project"]["project_id"]
        revision_id = created["initial_revision"]["revision_id"]
        query = urlencode(
            {
                "expected_head_revision_id": revision_id,
                "grid_width": 10,
                "grid_height": 10,
                "color_budget": 4,
                "fabric_count": 16,
                "strands": 1,
            }
        )
        response = client.post(
            f"/api/v1/projects/{project_id}/convert-image?{query}",
            content=payload,
            headers={"Content-Type": "image/png"},
        )

        assert response.status_code == 201
        result = response.json()
        document = result["revision"]["document"]
        assert document["grid"]["width"] == document["grid"]["height"] == 10
        assert document["fabric"]["count"] == 16
        assert all(recipe["components"][0]["strands"] == 1 for recipe in document["recipes"])
        assert 0 < result["statistics"]["full_stitch_count"] < 100
        assert result["background_report"]["removed_cells"] > 0
        assert 1 <= result["palette_report"]["quality"]["selected_materials"] <= 4
        assert result["palette_report"]["algorithm"] == (
            "lab-cluster-then-material-ciede2000-v1"
        )
        assert result["recovery_report"] is None
        assert result["motif_resolution"] == {
            "algorithm": "largest-4-connected-component-v1",
            "minimum_effective_width": 160,
            "effective_width": 4,
            "effective_height": 4,
            "component_cells": 16,
            "stitched_cells": 16,
            "component_share": 1.0,
            "bounding_box": [3, 3, 7, 7],
            "passes_minimum": False,
        }
        asset = client.get(result["source_asset"]["url"])
        assert asset.status_code == 200
        assert asset.content == payload
        assert asset.headers["content-type"] == "image/png"
        project = client.get(f"/api/v1/projects/{project_id}").json()
        assert project["head_revision_id"] == result["revision"]["revision_id"]


def test_image_conversion_supports_rendered_stitch_recovery_and_greedy_palette(
    tmp_path: Path,
) -> None:
    source = tmp_path / "rendered.png"
    Image.new("RGB", (4, 4), "#E0D8CC").save(source)
    catalog = ROOT / "catalogs/dmc-117-2021-quiltysolid-2025-07.json"

    with TestClient(create_app(tmp_path / "data", catalog_path=catalog)) as client:
        created = _create_project(client, name="效果图恢复")
        project_id = created["project"]["project_id"]
        query = urlencode(
            {
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
                "grid_width": 2,
                "grid_height": 2,
                "color_budget": 2,
                "remove_border_background": "false",
                "optimize": "false",
                "source_profile": "rendered_stitch",
                "palette_strategy": "greedy",
            }
        )

        response = client.post(
            f"/api/v1/projects/{project_id}/convert-image?{query}",
            content=source.read_bytes(),
            headers={"Content-Type": "image/png"},
        )

        assert response.status_code == 201
        result = response.json()
        assert result["recovery_report"]["adjusted_cells"] == 4
        assert result["palette_report"]["algorithm"] == "material-greedy-ciede2000-v1"


def test_image_conversion_rejects_invalid_image_and_stale_revision(tmp_path: Path) -> None:
    catalog = ROOT / "catalogs/dmc-117-2021-quiltysolid-2025-07.json"
    with TestClient(create_app(tmp_path / "data", catalog_path=catalog)) as client:
        created = _create_project(client)
        project_id = created["project"]["project_id"]
        revision_id = created["initial_revision"]["revision_id"]
        query = urlencode(
            {
                "expected_head_revision_id": revision_id,
                "grid_width": 2,
                "grid_height": 2,
                "remove_border_background": "false",
            }
        )
        invalid = client.post(
            f"/api/v1/projects/{project_id}/convert-image?{query}",
            content=b"not an image",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "INVALID_IMAGE_CONVERSION"

        source = tmp_path / "valid.png"
        Image.new("RGB", (2, 2), "red").save(source)
        first = client.post(
            f"/api/v1/projects/{project_id}/convert-image?{query}",
            content=source.read_bytes(),
            headers={"Content-Type": "image/png"},
        )
        assert first.status_code == 201
        stale = client.post(
            f"/api/v1/projects/{project_id}/convert-image?{query}",
            content=source.read_bytes(),
            headers={"Content-Type": "image/png"},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "REVISION_CONFLICT"
