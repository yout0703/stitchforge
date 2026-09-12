from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from stitchforge.catalog import load_catalog
from stitchforge.imaging import GridSample
from stitchforge.palette import build_pattern_document, select_palette
from stitchforge.validation import validate_document

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "catalogs/dmc-117-2021-quiltysolid-2025-07.json"


def test_dmc_catalog_keeps_official_identifiers_and_marks_color_gaps() -> None:
    catalog = load_catalog(CATALOG_PATH)
    codes = {item["code"] for item in catalog.materials}
    unknown = {item["code"] for item in catalog.materials if item["color_status"] == "unknown"}

    assert len(catalog.materials) == 489
    assert len(catalog.matchable_materials()) == 454
    assert unknown == {f"{number:02d}" for number in range(1, 36)}
    assert {"310", "BLANC", "ECRU", "B5200"} <= codes
    assert "WHITE" not in codes


def test_real_dmc_catalog_can_build_user_design_and_use_black() -> None:
    catalog = load_catalog(CATALOG_PATH)
    sample = GridSample(1, 1, 500_000, (1_000_000,), ("#000000",))
    palette = select_palette(sample, catalog, color_budget=1)
    document = build_pattern_document(
        sample,
        catalog,
        palette,
        pattern_id="dmc_black",
        title="DMC 目录验证",
        author="StitchForge",
        fabric_count=20,
        strands=1,
        color_budget=1,
        purpose="user_design",
        created_at=datetime(2026, 9, 12, tzinfo=UTC),
    )

    assert palette.selected_material_ids == ("dmc_117_310",)
    assert document["fabric"]["count"] == 20
    assert document["recipes"][0]["components"][0]["strands"] == 1
    assert validate_document(document, release=True).valid
