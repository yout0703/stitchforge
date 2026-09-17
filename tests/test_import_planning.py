from __future__ import annotations

import io
import json
import subprocess
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import AccountClient
from PIL import Image
from stitchforge_api import create_app, planning

ROOT = Path(__file__).resolve().parents[1]


def image(tmp_path: Path, size=(400, 600)) -> Path:
    path = tmp_path / "input.png"
    Image.new("RGB", size, "#AABBCC").save(path)
    return path


def test_stitch_pitch_plan_refines_small_lattice_to_keep_one_stitch_marks(tmp_path):
    path = image(tmp_path, (1024, 1536))
    plan = planning.plan_from_stitch_evidence(
        {"lattice_period": 20, "analysis_size": [1024, 1536]},
        path,
    )
    assert plan is not None
    assert (plan.grid_width, plan.grid_height) == (102, 154)
    assert plan.source_profile == "rendered_stitch"
    assert plan.remove_border_background
    assert "加密" in plan.explanation
    assert plan.grid_width * plan.grid_height < 30_000


def test_stitch_pitch_plan_keeps_an_already_detailed_lattice():
    plan = planning.plan_from_stitch_evidence(
        {"lattice_period": 8, "analysis_size": [1536, 1536]}
    )
    assert plan is not None
    assert (plan.grid_width, plan.grid_height) == (192, 192)
    assert "加密" not in plan.explanation


def test_stitch_pitch_plan_ignores_one_axis_periods():
    assert (
        planning.plan_from_stitch_evidence(
            {"periods": [16], "analysis_size": [960, 1440], "lattice_period": None}
        )
        is None
    )


def test_local_plan_keeps_aspect_and_limits(tmp_path):
    for width, height in [(400, 600), (4000, 1000), (16, 8), (1, 10000)]:
        plan = planning.local_plan(image(tmp_path, (width, height)))
        assert 1 <= plan.grid_width <= min(600, width)
        assert 1 <= plan.grid_height <= min(600, height)
        assert plan.grid_width * plan.grid_height <= 360000
        assert plan.fabric_count == 16 and plan.strands == 2
        assert plan.remove_border_background


def test_codex_output_is_validated_and_command_is_not_shell(tmp_path, monkeypatch):
    path = image(tmp_path)
    proposed = planning.local_plan(path).model_dump()
    proposed.update(grid_width=200, grid_height=300, source_profile="rendered_stitch")
    monkeypatch.setattr(planning.shutil, "which", lambda _: "/bin/fake-codex")

    def run(command, **kwargs):
        assert isinstance(command, list) and "shell" not in kwargs
        assert "--output-schema" in command and "--image" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert kwargs["timeout"] == 60
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(proposed))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(planning.subprocess, "run", run)
    report = planning.plan_import(path, tmp_path)
    assert report["provider"] == "codex"
    assert report["plan"]["grid_width"] == 200
    assert report["plan"]["source_profile"] == "rendered_stitch"


@pytest.mark.parametrize(
    "failure", ["missing", "timeout", "invalid_json", "bad_shape", "oversize", "exit", "no_output"]
)
def test_codex_failure_falls_back(tmp_path, monkeypatch, failure):
    path = image(tmp_path)
    monkeypatch.setattr(
        planning.shutil, "which", lambda _: None if failure == "missing" else "/fake/codex"
    )

    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        proposal = planning.local_plan(path).model_dump()
        proposal.update(grid_width=600, grid_height=600)
        text = "garbage" if failure == "invalid_json" else json.dumps(proposal)
        if failure == "oversize":
            text = "x" * 40000
        if failure != "no_output":
            Path(command[command.index("--output-last-message") + 1]).write_text(text)
        return SimpleNamespace(returncode=1 if failure == "exit" else 0)

    monkeypatch.setattr(planning.subprocess, "run", run)
    report = planning.plan_import(path, tmp_path)
    assert report["provider"] == "local"
    assert report["fallback_reason"]
    assert report["plan"] == planning.local_plan(path).model_dump()


def test_automatic_conversion_needs_no_user_parameters(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_CODEX_ENABLED", "0")
    payload = image(tmp_path, (20, 30)).read_bytes()
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "自动导入"}).json()
        project_id = created["project"]["project_id"]
        head = created["initial_revision"]["revision_id"]
        url = f"/api/v1/projects/{project_id}/convert-image?expected_head_revision_id={head}"
        response = client.post(url, content=payload, headers={"Content-Type": "image/png"})
        assert response.status_code == 201, response.text
        result = response.json()
        plan = result["planning_report"]["plan"]
        doc = result["revision"]["document"]
        assert (doc["grid"]["width"], doc["grid"]["height"]) == (20, 30)
        assert doc["fabric"]["count"] == plan["fabric_count"]
        assert doc["recipes"][0]["components"][0]["strands"] == plan["strands"]
        artifact = client.get("/api/v1/assets/" + result["planning_report"]["artifact_sha256"])
        assert artifact.status_code == 200
        assert json.loads(artifact.content)["plan"] == plan
        stale = client.post(url, content=payload, headers={"Content-Type": "image/png"})
        assert stale.status_code == 409


def test_automatic_fabric_conversion_trims_blank_canvas_and_aligns_private_source(
    tmp_path, monkeypatch
):
    from stitchforge_api import conversion as app_module

    source = Image.new("RGB", (200, 300), "#E4E0D6")
    for y in range(300):
        for x in range(200):
            if (x + y) % 4 == 0:
                source.putpixel((x, y), (239, 237, 232))
    for y in range(120, 180):
        for x in range(80, 120):
            source.putpixel((x, y), (190, 20, 30))
    path = tmp_path / "source.png"
    source.save(path)
    plan = planning.local_plan(path).model_dump()
    plan.update(grid_width=20, grid_height=30, color_budget=3, source_profile="rendered_stitch")
    monkeypatch.setattr(
        app_module,
        "plan_import",
        lambda *_: {
            "provider": "deepseek",
            "plan": plan,
            "fallback_reason": None,
        },
    )
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "小图"}).json()
        project_id = created["project"]["project_id"]
        response = client.post(
            f"/api/v1/projects/{project_id}/convert-image",
            params={"expected_head_revision_id": created["initial_revision"]["revision_id"]},
            content=path.read_bytes(),
        )
        assert response.status_code == 201, response.text
        result = response.json()
        document = result["revision"]["document"]
        assert (document["grid"]["width"], document["grid"]["height"]) == (8, 10)
        assert document["fabric"]["display_srgb"] == "#FFFFFF"
        assert result["statistics"]["full_stitch_count"] == 24
        assert document["provenance"]["ai_proposal_hash"]
        assert result["crop_report"]["grid_box"] == [6, 10, 14, 20]
        locator = next(
            item["locator"]
            for item in document["provenance"]["sources"]
            if item["id"] == "src_image"
        )
        assert locator == result["normalized_asset"]["url"]
        with Image.open(io.BytesIO(client.get(locator).content)) as cropped:
            assert cropped.size == (80, 100)
        assert client.get(result["source_asset"]["url"]).content == path.read_bytes()
        client.post("/api/v1/auth/logout")
        assert client.get(locator).status_code == 401


def test_web_oxs_import_preserves_cells_and_parameters(tmp_path):
    payload = (ROOT / "examples/minimal-pattern.oxs").read_bytes()
    with AccountClient(create_app(tmp_path / "data")) as client:
        response = client.post(
            "/api/v1/projects/import-oxs",
            content=payload,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 201, response.text
        result = response.json()
        doc = result["initial_revision"]["document"]
        assert doc["full_stitches"]["cells"] == [1, 0, 2, 0, 0, 1, 2, 0, 2, 0, 0, 1]
        assert doc["fabric"]["count"] == 14
        assert all(r["components"][0]["strands"] == 2 for r in doc["recipes"])
        assert not result["report"]["blocked"]
        assert client.get("/api/v1/assets/" + result["source_sha256"]).content == payload
        assert doc["provenance"]["sources"][0]["locator"].startswith("/api/v1/assets/")
        project = client.get("/api/v1/projects/" + result["project"]["project_id"]).json()
        assert project["head_revision_id"] == result["initial_revision"]["revision_id"]


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"bad xml",
        b'<!DOCTYPE chart [<!ENTITY a "x">]><chart>&a;</chart>',
        (ROOT / "examples/minimal-pattern.oxs")
        .read_bytes()
        .replace(
            b"<backstitches />",
            b'<backstitches><backstitch x1="0" y1="0" x2="1" y2="1" palindex="1"/></backstitches>',
        ),
    ],
)
def test_web_oxs_invalid_input_creates_no_partial_project(tmp_path, payload):
    with AccountClient(create_app(tmp_path / "data")) as client:
        response = client.post(
            "/api/v1/projects/import-oxs",
            content=payload,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 422
        assert response.json()["code"] in ("INVALID_OXS", "INVALID_REQUEST")
    import sqlite3

    with closing(sqlite3.connect(tmp_path / "data/metadata.sqlite3")) as db:
        assert db.execute("select count(*) from projects").fetchone()[0] == 0


def test_large_image_plan_keeps_detail_without_old_90000_cap(tmp_path):
    plan = planning.local_plan(image(tmp_path, (1448, 1086)))
    assert (plan.grid_width, plan.grid_height) == (600, 450)
    assert plan.color_budget == 64
    assert planning._valid_geometry(plan, 1448, 1086)


def test_codex_can_propose_high_detail_large_grid(tmp_path, monkeypatch):
    path = image(tmp_path, (1448, 1086))
    proposed = planning.local_plan(path).model_dump()
    monkeypatch.setattr(planning.shutil, "which", lambda _: "/fake/codex")

    def run(command, **kwargs):
        assert "360000" in kwargs["input"] and "最多90000" not in kwargs["input"]
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(proposed))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(planning.subprocess, "run", run)
    assert planning.plan_import(path, tmp_path)["provider"] == "codex"


def test_automatic_generation_supports_full_600_square_grid(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_CODEX_ENABLED", "0")
    payload = image(tmp_path, (1200, 1200)).read_bytes()
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "Full grid"}).json()
        project_id = created["project"]["project_id"]
        head = created["initial_revision"]["revision_id"]
        response = client.post(
            f"/api/v1/projects/{project_id}/convert-image",
            params={
                "expected_head_revision_id": head,
            },
            content=payload,
            headers={"Content-Type": "image/png"},
        )
        assert response.status_code == 201, response.text[:200]
        result = response.json()
        document = result["revision"]["document"]
        assert (document["grid"]["width"], document["grid"]["height"]) == (600, 600)
        assert len(document["full_stitches"]["cells"]) == 360000
        assert result["statistics"]["full_stitch_count"] == 360000
        assert (
            client.get(
                f"/api/v1/projects/{project_id}/revisions/{result['revision']['revision_id']}"
            ).json()["document"]
            == document
        )
        assert (
            client.post(
                f"/api/v1/projects/{project_id}/convert-image",
                params={
                    "expected_head_revision_id": result["revision"]["revision_id"],
                    "grid_width": 601,
                    "grid_height": 600,
                },
                content=payload,
                headers={"Content-Type": "image/png"},
            ).status_code
            == 422
        )
