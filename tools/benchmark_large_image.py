"""Generate an automatic large chart and an old-size comparison from one image.

The automatic result is saved in the running local service. The comparison uses
an isolated temporary SQLite database. Outputs include OXS, previews and reports.
Usage: uv run python tools/benchmark_large_image.py INPUT OUTPUT_DIR
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from stitchforge.oxs import SUPPORTED_COORDINATE_PROFILE, export_oxs, import_oxs
from stitchforge.render import render_flat_preview
from stitchforge_api import create_app

source, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
payload = source.read_bytes()
shutil.copyfile(source, output / "source.png")
headers = {"Content-Type": "image/png"}


def write_result(label, result):
    document = result["revision"]["document"]
    (output / f"{label}.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    render_flat_preview(document, output / f"{label}.png", cell_size=2)
    report = export_oxs(
        document,
        output / f"{label}.oxs",
        coordinate_profile=SUPPORTED_COORDINATE_PROFILE,
        confirm_loss=True,
    )
    assert not report.blocked
    restored = import_oxs(output / f"{label}.oxs", coordinate_profile=SUPPORTED_COORDINATE_PROFILE)
    assert restored.document["full_stitches"] == document["full_stitches"]
    (output / f"{label}-report.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )


with httpx.Client(base_url="http://127.0.0.1:8765", timeout=300) as client:
    created = client.post("/api/v1/projects", json={"name": "八仙过海 · 大图自动打版"})
    created.raise_for_status()
    project = created.json()
    project_id = project["project"]["project_id"]
    print(f"Analyzing and generating large chart: {project_id}", flush=True)
    start = time.perf_counter()
    response = client.post(
        f"/api/v1/projects/{project_id}/convert-image",
        params={
            "expected_head_revision_id": project["initial_revision"]["revision_id"],
        },
        content=payload,
        headers=headers,
    )
    response.raise_for_status()
    high = response.json()
    high_seconds = time.perf_counter() - start
    write_result("large-auto", high)
    print(f"Large result: {high['revision']['document']['grid']}, {high_seconds:.2f}s", flush=True)
    plan = high["planning_report"]["plan"]

with tempfile.TemporaryDirectory(prefix="large-image-baseline-") as temp:
    with TestClient(create_app(temp)) as client:
        created = client.post("/api/v1/projects", json={"name": "八仙过海 · 旧尺寸对照"}).json()
        project = created["project"]["project_id"]
        start = time.perf_counter()
        response = client.post(
            f"/api/v1/projects/{project}/convert-image",
            params={
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
                "grid_width": 346,
                "grid_height": 259,
                "color_budget": plan["color_budget"],
                "fabric_count": plan["fabric_count"],
                "strands": plan["strands"],
                "source_profile": plan["source_profile"],
                "remove_border_background": str(plan["remove_border_background"]).lower(),
            },
            content=payload,
            headers=headers,
        )
        assert response.status_code == 201, response.text[:300]
        baseline = response.json()
        baseline_seconds = time.perf_counter() - start
        write_result("old-size-comparison", baseline)
        print(f"Old-size comparison: {baseline_seconds:.2f}s", flush=True)

summary = {
    "source": str(source),
    "project_id": project_id,
    "large_seconds_including_ai": high_seconds,
    "baseline_seconds_without_ai": baseline_seconds,
    "planning": high["planning_report"],
    "large_statistics": high["statistics"],
    "baseline_statistics": baseline["statistics"],
}
(output / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)
(output / "comparison.html").write_text(
    """<!doctype html><meta charset="utf-8"><title>八仙过海 · 分辨率对照</title>
<style>
body{font:16px system-ui;margin:24px;background:#f4f4f4;color:#243040}
main{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
img{width:100%;image-rendering:pixelated}
article{background:white;padding:12px}
p{line-height:1.6}
</style>
<h1>八仙过海 · 分辨率对照</h1>
<p>保留原图比例。两份图纸使用相同配色预算和处理选项，仅针格尺寸不同；并非恢复原生绣格。</p>
<main><article><h2>原图</h2><img src="source.png"></article>
<article><h2>旧尺寸对照 · 346 × 259</h2><img src="old-size-comparison.png"></article>
<article><h2>大图自动方案</h2><img src="large-auto.png"></article></main>""",
    encoding="utf-8",
)
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
