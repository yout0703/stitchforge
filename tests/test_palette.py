from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from stitchforge.catalog import MaterialCatalog, load_catalog
from stitchforge.imaging import GridSample
from stitchforge.palette import (
    PaletteConstraintError,
    build_pattern_document,
    delta_e_2000_lab,
    grid_sample_from_dict,
    lab_to_srgb_hex,
    select_clustered_palette,
    select_palette,
    srgb_hex_to_lab,
)
from stitchforge.validation import validate_document

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def catalog() -> MaterialCatalog:
    return load_catalog(ROOT / "examples/synthetic-material-catalog.json")


@pytest.fixture
def sample() -> GridSample:
    return GridSample(
        width=3,
        height=1,
        coverage_threshold_ppm=500_000,
        coverage_ppm=(1_000_000, 1_000_000, 0),
        display_srgb=("#D62828", "#2457C5", None),
        source_sha256="a" * 64,
        source_locator="normalized.png",
    )


def test_ciede2000_matches_published_sharma_pair() -> None:
    first = np.array([50.0, 2.6772, -79.7751])
    second = np.array([50.0, 0.0, -82.7485])
    assert float(delta_e_2000_lab(first, second)) == pytest.approx(2.0425, abs=0.0001)


def test_srgb_lab_uses_reference_scale() -> None:
    black, white = srgb_hex_to_lab(["#000000", "#FFFFFF"])
    assert black[0] == pytest.approx(0, abs=0.001)
    assert white[0] == pytest.approx(100, abs=0.01)
    assert float(delta_e_2000_lab(black, white)) == pytest.approx(100, abs=0.01)


def test_lab_to_srgb_round_trip_and_validation() -> None:
    labs = srgb_hex_to_lab(["#000000", "#FFFFFF", "#D62828"])
    assert lab_to_srgb_hex(labs) == ("#000000", "#FFFFFF", "#D62828")
    with pytest.raises(ValueError, match="N×3"):
        lab_to_srgb_hex(np.asarray([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError, match="有限"):
        lab_to_srgb_hex(np.asarray([[float("nan"), 0.0, 0.0]]))


def test_palette_selects_exact_catalog_colors(sample: GridSample, catalog: MaterialCatalog) -> None:
    result = select_palette(sample, catalog, color_budget=2, minimum_gain=0)
    assert result.selected_material_ids == ("test_blue", "test_red")
    assert result.cells == (2, 1, 0)
    assert result.quality.weighted_mean_delta_e00 == pytest.approx(0, abs=0.001)
    assert result.quality.nonempty_cells == 2


def test_required_material_is_kept_and_budget_conflicts_are_explicit(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    result = select_palette(
        sample,
        catalog,
        color_budget=1,
        required_material_ids=("test_black",),
    )
    assert result.selected_material_ids == ("test_black",)
    assert result.cells == (1, 1, 0)

    with pytest.raises(PaletteConstraintError, match="PALETTE_CONSTRAINT_CONFLICT"):
        select_palette(
            sample,
            catalog,
            color_budget=1,
            required_material_ids=("test_black", "test_white"),
        )


@pytest.mark.parametrize("material_id", ["missing", "test_unknown"])
def test_required_material_must_exist_and_be_matchable(
    sample: GridSample, catalog: MaterialCatalog, material_id: str
) -> None:
    with pytest.raises(PaletteConstraintError, match="必选材料"):
        select_palette(sample, catalog, color_budget=2, required_material_ids=(material_id,))


def test_palette_selection_is_stable_when_catalog_order_changes(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    reversed_catalog = MaterialCatalog(
        catalog.snapshot_id,
        catalog.source,
        tuple(reversed(catalog.materials)),
    )
    first = select_palette(sample, catalog, color_budget=2, minimum_gain=0)
    second = select_palette(sample, reversed_catalog, color_budget=2, minimum_gain=0)
    assert first == second


def test_clustered_palette_is_deterministic_and_preserves_distinct_colors(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    reversed_catalog = MaterialCatalog(
        catalog.snapshot_id,
        catalog.source,
        tuple(reversed(catalog.materials)),
    )

    first = select_clustered_palette(sample, catalog, color_budget=2)
    second = select_clustered_palette(sample, reversed_catalog, color_budget=2)

    assert first == second
    assert first.algorithm == "lab-cluster-then-material-ciede2000-v1"
    assert first.selected_material_ids == ("test_blue", "test_red")
    assert first.cells == (2, 1, 0)
    assert first.quality.weighted_mean_delta_e00 == pytest.approx(0, abs=0.001)


def test_clustered_palette_honors_a_fully_required_budget(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    result = select_clustered_palette(
        sample,
        catalog,
        color_budget=2,
        required_material_ids=("test_black", "test_white"),
    )

    assert result.selected_material_ids == ("test_black", "test_white")
    assert set(result.cells) <= {0, 1, 2}


def test_clustered_palette_rejects_invalid_constraints(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    empty = GridSample(1, 1, 500_000, (0,), (None,))
    with pytest.raises(PaletteConstraintError, match="1–64"):
        select_clustered_palette(sample, catalog, color_budget=0)
    with pytest.raises(PaletteConstraintError, match="迭代"):
        select_clustered_palette(sample, catalog, color_budget=2, iterations=0)
    with pytest.raises(PaletteConstraintError, match="全空"):
        select_clustered_palette(empty, catalog, color_budget=2)
    with pytest.raises(PaletteConstraintError, match="不存在"):
        select_clustered_palette(
            sample,
            catalog,
            color_budget=2,
            required_material_ids=("missing",),
        )
    with pytest.raises(PaletteConstraintError, match="不可用于"):
        select_clustered_palette(
            sample,
            catalog,
            color_budget=2,
            required_material_ids=("test_unknown",),
        )
    with pytest.raises(PaletteConstraintError, match="PALETTE_CONSTRAINT_CONFLICT"):
        select_clustered_palette(
            sample,
            catalog,
            color_budget=1,
            required_material_ids=("test_black", "test_white"),
        )

    no_matchable = MaterialCatalog(
        catalog.snapshot_id,
        catalog.source,
        (catalog.material_by_id()["test_unknown"],),
    )
    with pytest.raises(PaletteConstraintError, match="没有可用"):
        select_clustered_palette(sample, no_matchable, color_budget=2)


def test_candidate_prefilter_keeps_required_material(sample: GridSample) -> None:
    materials = tuple(
        {
            "id": f"material_{index:03d}",
            "brand": "TEST",
            "series": "Synthetic",
            "code": str(index),
            "name": str(index),
            "display_srgb": f"#{index:06X}",
            "color_status": "synthetic",
            "available": True,
            "default_strands": 2,
        }
        for index in range(130)
    )
    large_catalog = MaterialCatalog(
        "large",
        {
            "id": "src_large",
            "locator": "generated",
            "description": "generated test catalog",
            "permission_status": "redistributable",
        },
        materials,
    )

    result = select_palette(
        sample,
        large_catalog,
        color_budget=1,
        required_material_ids=("material_129",),
    )

    assert result.selected_material_ids == ("material_129",)
    assert result.quality.candidate_materials == 128


def test_palette_stops_when_extra_material_has_no_gain(catalog: MaterialCatalog) -> None:
    one_color = GridSample(1, 1, 500_000, (1_000_000,), ("#000000",))
    result = select_palette(one_color, catalog, color_budget=6)
    assert result.selected_material_ids == ("test_black",)


def test_palette_rejects_empty_grid_and_invalid_budget(catalog: MaterialCatalog) -> None:
    empty = GridSample(1, 1, 500_000, (0,), (None,))
    with pytest.raises(PaletteConstraintError, match="全空"):
        select_palette(empty, catalog, color_budget=1)
    with pytest.raises(PaletteConstraintError, match="1–64"):
        select_palette(empty, catalog, color_budget=0)


def test_build_pattern_document_is_valid_and_keeps_image_provenance(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    palette = select_palette(sample, catalog, color_budget=2, minimum_gain=0)
    document = build_pattern_document(
        sample,
        catalog,
        palette,
        pattern_id="generated_test",
        title="配色测试",
        author="StitchForge",
        color_budget=2,
        purpose="test_fixture",
        created_at=datetime(2026, 9, 11, tzinfo=UTC),
    )
    assert validate_document(document).valid
    assert document["full_stitches"]["cells"] == [2, 1, 0]
    assert document["provenance"]["source_asset_hashes"] == ["a" * 64]
    assert {item["id"] for item in document["provenance"]["sources"]} == {
        "src_image",
        "src_synthetic_catalog",
    }


def test_build_pattern_accepts_flexible_aida_count_and_strand_override(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    palette = select_palette(sample, catalog, color_budget=2, minimum_gain=0)
    document = build_pattern_document(
        sample,
        catalog,
        palette,
        pattern_id="flexible_materials",
        title="可调材料",
        author="",
        fabric_count=20,
        strands=1,
        color_budget=2,
        purpose="test_fixture",
    )
    assert document["fabric"]["count"] == 20
    assert {recipe["components"][0]["strands"] for recipe in document["recipes"]} == {1}


@pytest.mark.parametrize("strands", [0, 7, True, 2.5])
def test_build_pattern_rejects_invalid_strand_override(
    sample: GridSample, catalog: MaterialCatalog, strands: object
) -> None:
    palette = select_palette(sample, catalog, color_budget=2)
    with pytest.raises(ValueError, match="strands"):
        build_pattern_document(
            sample,
            catalog,
            palette,
            pattern_id="invalid_strands",
            title="invalid",
            author="",
            strands=strands,  # type: ignore[arg-type]
            color_budget=2,
            purpose="test_fixture",
        )


def test_synthetic_palette_cannot_claim_user_design(
    sample: GridSample, catalog: MaterialCatalog
) -> None:
    palette = select_palette(sample, catalog, color_budget=2)
    with pytest.raises(PaletteConstraintError, match="synthetic"):
        build_pattern_document(
            sample,
            catalog,
            palette,
            pattern_id="invalid",
            title="invalid",
            author="",
            color_budget=2,
            purpose="user_design",
        )


def test_grid_sample_contract_rejects_inconsistent_blank_state(sample: GridSample) -> None:
    value = sample.as_dict()
    value["display_srgb"][0] = None
    with pytest.raises(ValueError, match="留空状态"):
        grid_sample_from_dict(value)


def test_grid_sample_contract_round_trip(sample: GridSample) -> None:
    assert grid_sample_from_dict(json.loads(json.dumps(sample.as_dict()))) == sample


def test_chunked_cluster_assignment_matches_full_array():
    import numpy as np
    from stitchforge.palette import _nearest_cluster_labels

    rng = np.random.default_rng(42)
    colors = rng.normal(size=(8201, 3))
    centers = rng.normal(size=(64, 3))
    centers[1] = centers[0]
    colors[8192] = centers[0]
    expected = np.argmin(np.sum((colors[:, None, :] - centers[None, :, :]) ** 2, axis=2), axis=1)
    assert np.array_equal(_nearest_cluster_labels(colors, centers), expected)
