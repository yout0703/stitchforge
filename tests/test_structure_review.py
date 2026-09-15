import base64
import io
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import AccountClient
from PIL import Image
from stitchforge.counted_stitches import (
    check_stitch_structure,
    counted_stitch_candidates,
)
from stitchforge_api import create_app, planning, structure_review
from test_counted_stitches import stitched_photo

CATALOG = Path(__file__).resolve().parents[1] / "catalogs/dmc-117-2021-quiltysolid-2025-07.json"
HTTP_CLIENT = httpx.Client


@pytest.fixture
def example(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-private-key")
    source = stitched_photo(tmp_path / "private-source.png")
    candidates, _ = counted_stitch_candidates(source)
    assert candidates
    return source, candidates


def assessment(count=1):
    return {
        "source_features": ["菱形轮廓", "单针浅色装饰"],
        "candidates": [
            {
                "candidate_id": f"candidate-{i + 1}",
                "verdict": "pass",
                "missing_features": [],
                "explanation": "轮廓及装饰针保留。",
            }
            for i in range(count)
        ],
        "selected_candidate_id": "candidate-1",
    }


def mock_review(monkeypatch, payload, *, status=200):
    original = HTTP_CLIENT
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(payload)},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        structure_review.httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )
    return calls


def test_review_sends_source_and_generated_chart_without_private_metadata(example, monkeypatch):
    source, candidates = example
    calls = mock_review(monkeypatch, assessment())
    result = structure_review.review_counted_candidates(source, candidates, CATALOG)
    assert result["status"] == "passed" and result["selected_index"] == 0
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert str(source) not in calls[0].content.decode()
    assert "test-private-key" not in calls[0].content.decode()
    assert calls[0].headers["Authorization"] == "Bearer test-private-key"
    images = [block for block in body["messages"][1]["content"] if block["type"] == "image_url"]
    assert len(images) == len(candidates)
    with Image.open(
        io.BytesIO(base64.b64decode(images[0]["image_url"]["url"].split(",")[1]))
    ) as image:
        assert image.size == (1000, 680) and image.info == {}


@pytest.mark.parametrize("bad", ["unknown", "missing", "contradiction", "extra", "long"])
def test_invalid_review_is_not_an_approval(example, monkeypatch, bad):
    source, candidates = example
    payload = assessment()
    if bad == "unknown":
        payload["selected_candidate_id"] = "candidate-999"
    elif bad == "missing":
        payload["candidates"] = []
    elif bad == "contradiction":
        payload["candidates"][0]["missing_features"] = ["眼睛丢失"]
    elif bad == "extra":
        payload["new_grid"] = [2, 3]
    else:
        payload["source_features"] = ["test-private-key" * 1000]
    mock_review(monkeypatch, payload)
    result = structure_review.review_counted_candidates(source, candidates, CATALOG)
    assert result["status"] == "invalid" and result["selected_index"] is None
    assert "test-private-key" not in json.dumps(result)


def test_fewer_colours_chosen_only_between_approved_identical_stitch_positions(
    example, monkeypatch
):
    source, candidates = example
    original = candidates[0]
    cells = list(original.sample.display_srgb)
    cells[next(i for i, c in enumerate(cells) if c)] = "#D02028"
    shaded = replace(
        original,
        sample=replace(original.sample, display_srgb=tuple(cells)),
        report={**original.report, "thread_colours": 3},
    )
    mock_review(monkeypatch, assessment(2))
    result = structure_review.review_counted_candidates(source, [shaded, original], CATALOG)
    assert result["selected_index"] == 1
    payload = assessment(2)
    payload["candidates"][1].update(verdict="fail", missing_features=["装饰颜色丢失"])
    mock_review(monkeypatch, payload)
    assert (
        structure_review.review_counted_candidates(source, [shaded, original], CATALOG)[
            "selected_index"
        ]
        == 0
    )
    incomplete_cells = list(original.sample.display_srgb)
    incomplete_cells[next(i for i, c in enumerate(incomplete_cells) if c)] = None
    incomplete = replace(
        original, sample=replace(original.sample, display_srgb=tuple(incomplete_cells))
    )
    mock_review(monkeypatch, assessment(2))
    result = structure_review.review_counted_candidates(source, [shaded, incomplete], CATALOG)
    assert result["selected_index"] == 0


def test_structure_check_detects_lost_motif(example):
    source, candidates = example
    original = candidates[0]
    empty = replace(original.sample, display_srgb=(None,) * len(original.sample.display_srgb))
    assert not check_stitch_structure(source, replace(original, sample=empty))["passed"]


@pytest.mark.parametrize("grid", [(12, 18), (360, 540)])
def test_whole_image_plan_cannot_conflict_with_subject_pitch(grid):
    plan = {"grid_width": grid[0], "grid_height": grid[1], "source_profile": "rendered_stitch"}
    evidence = {"periods": [35], "analysis_size": [1024, 1536]}
    with pytest.raises(ValueError, match="针距不一致"):
        planning.validate_stitch_plan(plan, evidence)
    plan.update(grid_width=29, grid_height=44)
    planning.validate_stitch_plan(plan, evidence)


@pytest.mark.parametrize("review_status", ["rejected", "invalid", "unavailable"])
def test_failed_review_of_partial_evidence_does_not_save_or_charge(
    example, tmp_path, monkeypatch, review_status
):
    from stitchforge_api import conversion as app_module

    source, candidates = example
    candidates[0].report["requires_visual_review"] = True
    monkeypatch.setattr(app_module, "counted_stitch_candidates", lambda *_: (candidates, {}))
    monkeypatch.setattr(
        app_module,
        "review_counted_candidates",
        lambda *_: {
            "status": review_status,
            "selected_index": None,
        },
    )
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "复核失败"}).json()
        project_id = created["project"]["project_id"]
        head = created["initial_revision"]["revision_id"]
        response = client.post(
            f"/api/v1/projects/{project_id}/convert-image",
            params={"expected_head_revision_id": head},
            content=source.read_bytes(),
        )
        assert response.status_code == 422, response.text
        assert "不扣次数" in response.json()["message"]
        assert client.get("/api/v1/auth/me").json()["credits"] == 100
        assert client.get(f"/api/v1/projects/{project_id}").json()["head_revision_id"] == head


def test_upstream_failure_is_reported_as_unavailable_not_passed(example, monkeypatch):
    source, candidates = example
    mock_review(monkeypatch, {"private": "test-private-key"}, status=503)
    result = structure_review.review_counted_candidates(source, candidates, CATALOG)
    assert result["status"] == "unavailable"
    assert "test-private-key" not in json.dumps(result)


def test_timeout_does_not_retry_external_review(example, monkeypatch):
    source, candidates = example
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("test-private-key")

    monkeypatch.setattr(
        structure_review.httpx,
        "Client",
        lambda **kwargs: HTTP_CLIENT(
            **kwargs,
            transport=httpx.MockTransport(timeout),
        ),
    )
    result = structure_review.review_counted_candidates(source, candidates, CATALOG)
    assert result["status"] == "unavailable" and len(calls) == 1
    assert "test-private-key" not in json.dumps(result)


def test_rejected_size_does_not_charge_or_fall_back_to_a_large_chart(tmp_path, monkeypatch):
    from stitchforge_api import conversion as app_module

    source = stitched_photo(tmp_path / "source.png")
    evidence = {"periods": [35], "analysis_size": [1024, 1536]}
    monkeypatch.setattr(app_module, "counted_stitch_candidates", lambda *_: ([], evidence))
    monkeypatch.setattr(
        app_module,
        "plan_import",
        lambda *_: {
            "provider": "deepseek",
            "plan": {
                "source_profile": "rendered_stitch",
                "grid_width": 12,
                "grid_height": 18,
            },
        },
    )
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "尺寸矛盾"}).json()
        response = client.post(
            f"/api/v1/projects/{created['project']['project_id']}/convert-image",
            params={"expected_head_revision_id": created["initial_revision"]["revision_id"]},
            content=source.read_bytes(),
        )
        assert response.status_code == 422 and "针距不一致" in response.json()["message"]
        assert client.get("/api/v1/auth/me").json()["credits"] == 100
