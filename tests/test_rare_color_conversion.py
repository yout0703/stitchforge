import io
import json
from pathlib import Path

import pytest
from conftest import AccountClient
from PIL import Image
from stitchforge_api import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("enabled", [True, False])
def test_conversion_merges_rare_color_and_persists_report_unless_disabled(tmp_path, enabled):
    catalog = json.loads((ROOT / "catalogs/dmc-117-2021-quiltysolid-2025-07.json").read_text())
    catalog["materials"] = [m for m in catalog["materials"] if m["display_srgb"]][:2]
    for material, color in zip(catalog["materials"], ("#808080", "#828282"), strict=True):
        material["display_srgb"] = color
    catalog_path = tmp_path / "test-catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    image = Image.new("RGB", (20, 20), "#808080")
    for x, y in ((8, 8), (9, 8), (8, 9), (9, 9)):
        image.putpixel((x, y), (130, 130, 130))
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    with AccountClient(create_app(tmp_path / "data", catalog_path=catalog_path)) as client:
        created = client.post("/api/v1/projects", json={"name": "少量线色测试"}).json()
        project_id = created["project"]["project_id"]
        response = client.post(
            f"/api/v1/projects/{project_id}/convert-image",
            params={
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
                "grid_width": 20,
                "grid_height": 20,
                "color_budget": 2,
                "palette_strategy": "greedy",
                "remove_border_background": "false",
                "optimize": str(enabled).lower(),
            },
            content=payload.getvalue(),
        )
        assert response.status_code == 201, response.text
        result = response.json()
        report = result["rare_color_report"]
        document = result["revision"]["document"]
        if enabled:
            assert report["colors_before"] == 2
            assert report["colors_after"] == 1
            assert report["changed_stitches"] == 4
            assert len(document["recipes"]) == 1
            summary = document["extensions"]["stitchforge.rare_colors"]
            assert summary["changed_stitches"] == 4
            revision = client.get(
                f"/api/v1/projects/{project_id}/revisions/{result['revision']['revision_id']}"
            ).json()
            assert revision["document"]["extensions"]["stitchforge.rare_colors"] == summary
            manifest_hash = document["extensions"]["stitchforge.conversion"]["artifact_sha256"]
            manifest = client.get(f"/api/v1/assets/{manifest_hash}").json()
            assert manifest["reports"]["rare_color_report"] == report
        else:
            assert report is None
            assert len(document["recipes"]) == 2
            assert "stitchforge.rare_colors" not in document["extensions"]
