from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from conftest import AccountClient
from PIL import Image
from stitchforge_api import create_app, planning
from stitchforge_api import main as api_main


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-never-export")
    monkeypatch.delenv("STITCHFORGE_DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("STITCHFORGE_DEEPSEEK_TIMEOUT", raising=False)
    path = tmp_path / "private-file.png"
    Image.new("RGB", (2000, 1000), "#8899AA").save(path)
    return path


def mock_service(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(
        planning.httpx,
        "Client",
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)),
    )


def completion(plan, **kwargs):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan)}, **kwargs}]
    }


def test_deepseek_sends_image_and_validates_json(source, tmp_path, monkeypatch):
    plan = planning.local_plan(source).model_dump()
    plan.update(color_budget=32, source_profile="rendered_stitch")
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret-never-export"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["response_format"] == {"type": "json_object"}
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens"] == 2048
        assert body["stream"] is False
        content = body["messages"][1]["content"]
        assert "2000x1000" in content[0]["text"]
        url = content[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as preview:
            assert preview.size == (1536, 768)
            assert preview.info == {}
        assert str(source) not in request.content.decode()
        assert "test-secret" not in request.content.decode()
        return httpx.Response(200, json=completion(plan))

    mock_service(monkeypatch, handler)
    report = planning.plan_import(source, tmp_path)
    assert report == {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "plan": plan,
        "fallback_reason": None,
    }
    assert len(calls) == 1
    assert not list(tmp_path.glob("plan-*.json"))


def test_deepseek_selects_measured_grid_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-never-export")
    source = tmp_path / "stitched-photo.png"
    Image.new("RGB", (1024, 1536), "#EEE8DC").save(source)
    evidence = {
        "analysis_size": [1024, 1536],
        "periods": [13, 14, 15],
        "lattice_period": 15,
        "lattice_measurements": [
            {
                "source": "bare_fabric_top",
                "period_x": 15,
                "period_y": 15,
                "confidence_x_ppm": 925_000,
                "confidence_y_ppm": 894_000,
                "agreed_period": 15,
            }
        ],
        "grid_candidates": [
            {
                "grid_width": 68,
                "grid_height": 102,
                "lattice_period": 15,
                "sampling": "one_stitch_per_cell",
            }
        ],
    }
    proposed = planning.plan_from_stitch_evidence(evidence, source)
    assert proposed is not None
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"][0]["text"]
        assert '"grid_width":68' in prompt
        assert '"grid_height":102' in prompt
        assert "必须原样选择grid_candidates" in prompt
        return httpx.Response(200, json=completion(proposed.model_dump()))

    mock_service(monkeypatch, handler)
    report = planning.plan_import(source, tmp_path, evidence)

    assert report["provider"] == "deepseek"
    assert (report["plan"]["grid_width"], report["plan"]["grid_height"]) == (68, 102)
    assert len(calls) == 1


def test_conflicting_deepseek_grid_uses_measured_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("STITCHFORGE_AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-never-export")
    source = tmp_path / "stitched-photo.png"
    Image.new("RGB", (1024, 1536), "#EEE8DC").save(source)
    evidence = {
        "analysis_size": [1024, 1536],
        "periods": [15],
        "lattice_period": 15,
        "grid_candidates": [
            {
                "grid_width": 68,
                "grid_height": 102,
                "lattice_period": 15,
                "sampling": "one_stitch_per_cell",
            }
        ],
    }
    conflicting = planning.plan_from_stitch_evidence(evidence, source).model_dump()
    conflicting.update(grid_width=40, grid_height=60)
    mock_service(
        monkeypatch,
        lambda request: httpx.Response(200, json=completion(conflicting)),
    )

    report = planning.plan_import(source, tmp_path, evidence)

    assert report["provider"] == "local"
    assert (report["plan"]["grid_width"], report["plan"]["grid_height"]) == (68, 102)
    assert "未通过校验" in report["fallback_reason"]


@pytest.mark.parametrize(
    "failure",
    [
        "unauthorized",
        "balance",
        "limited",
        "timeout",
        "network",
        "redirect",
        "bad_json",
        "empty",
        "shape",
        "oversize",
        "truncated",
        "extra_field",
        "wrong_type",
    ],
)
def test_deepseek_failure_is_safe_and_does_not_retry(source, tmp_path, monkeypatch, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("test-secret-never-export", request=request)
        if failure == "network":
            raise httpx.ConnectError("test-secret-never-export", request=request)
        statuses = {"unauthorized": 401, "balance": 402, "limited": 429, "redirect": 307}
        if failure in statuses:
            return httpx.Response(
                statuses[failure],
                text="test-secret-never-export",
                headers={"Location": "https://untrusted.invalid"},
            )
        if failure == "bad_json":
            return httpx.Response(200, text="not JSON test-secret-never-export")
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * 70_000)
        plan = planning.local_plan(source).model_dump()
        if failure == "shape":
            plan.update(grid_width=600, grid_height=600)
        if failure == "extra_field":
            plan["command"] = "execute something"
        if failure == "wrong_type":
            plan["strands"] = "2"
        value = completion(plan, finish_reason="length" if failure == "truncated" else "stop")
        if failure == "empty":
            value["choices"] = []
        return httpx.Response(200, json=value)

    mock_service(monkeypatch, handler)
    report = planning.plan_import(source, tmp_path)
    assert report["provider"] == "local"
    assert report["plan"] == planning.local_plan(source).model_dump()
    assert report["fallback_reason"]
    assert "test-secret" not in json.dumps(report)
    assert len(calls) == 1


@pytest.mark.parametrize("provider,key", [("deepseek", ""), ("local", "some-key"), ("typo", "key")])
def test_disabled_or_unconfigured_does_not_call_network(
    source, tmp_path, monkeypatch, provider, key
):
    monkeypatch.setenv("STITCHFORGE_AI_PROVIDER", provider)
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    mock_service(monkeypatch, lambda request: pytest.fail("Unexpected external call"))
    report = planning.plan_import(source, tmp_path)
    assert report["provider"] == "local" and report["fallback_reason"]


def test_api_persists_deepseek_plan_and_charges_only_once(source, tmp_path, monkeypatch):
    # Capture the real client before patching httpx for the remote provider.
    Image.new("RGB", (20, 30), "#8899AA").save(source)
    plan = planning.local_plan(source).model_dump()
    plan.update(fabric_count=18, strands=1)
    with AccountClient(create_app(tmp_path / "data")) as client:
        mock_service(monkeypatch, lambda request: httpx.Response(200, json=completion(plan)))
        created = client.post("/api/v1/projects", json={"name": "DeepSeek test"}).json()
        project, head = created["project"]["project_id"], created["initial_revision"]["revision_id"]
        url = f"/api/v1/projects/{project}/convert-image?expected_head_revision_id={head}"
        before = client.get("/api/v1/billing").json()["credits"]
        response = client.post(
            url, content=source.read_bytes(), headers={"Content-Type": "image/png"}
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["planning_report"]["provider"] == "deepseek"
        assert result["planning_report"]["model"] == "deepseek-flash"
        assert result["revision"]["document"]["fabric"]["count"] == 18
        report = client.get("/api/v1/assets/" + result["planning_report"]["artifact_sha256"])
        assert report.status_code == 200 and "test-secret" not in report.text
        assert client.get("/api/v1/billing").json()["credits"] == before - 1
        assert client.post(url, content=source.read_bytes()).status_code == 409
        assert client.get("/api/v1/billing").json()["credits"] == before - 1


def test_server_loads_dotenv_preserving_process_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Track a value before deletion so dotenv-created variables are also restored.
    monkeypatch.setenv("STITCHFORGE_AI_PROVIDER", "local")
    monkeypatch.delenv("STITCHFORGE_AI_PROVIDER")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-key")
    (tmp_path / ".env").write_text("STITCHFORGE_AI_PROVIDER=deepseek\nDEEPSEEK_API_KEY=file-key\n")
    captured = []
    monkeypatch.setattr(api_main, "create_app", lambda *args, **kwargs: object())
    monkeypatch.setattr(api_main.uvicorn, "run", lambda *args, **kwargs: captured.append(kwargs))
    api_main.run()
    assert api_main.os.environ["STITCHFORGE_AI_PROVIDER"] == "deepseek"
    assert api_main.os.environ["DEEPSEEK_API_KEY"] == "process-key"
    assert captured[0]["port"] == 8765
