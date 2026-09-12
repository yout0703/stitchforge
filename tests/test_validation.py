from __future__ import annotations

from copy import deepcopy

from stitchforge.canonical import document_hash, normalize_document
from stitchforge.validation import validate_document


def test_minimal_document_is_valid(minimal_document: dict) -> None:
    report = validate_document(minimal_document)
    assert report.valid
    assert report.diagnostics == ()


def test_reference_portrait_grid_is_valid(minimal_document: dict) -> None:
    document = deepcopy(minimal_document)
    document["grid"]["width"] = 220
    document["grid"]["height"] = 330
    document["full_stitches"]["cells"] = [0] * (220 * 330)
    document["regions"] = []

    assert validate_document(document).valid


def test_schema_and_semantic_errors_are_locatable(minimal_document: dict) -> None:
    invalid = deepcopy(minimal_document)
    invalid["full_stitches"]["cells"] = invalid["full_stitches"]["cells"][:-1]
    invalid["recipes"][0]["components"][0]["material_id"] = "missing"
    report = validate_document(invalid)
    assert not report.valid
    assert {item.rule_id for item in report.diagnostics} == {"GRID_SHAPE", "RECIPE_EXISTS"}
    assert all(item.path.startswith("$") for item in report.diagnostics)


def test_release_blocks_test_materials_by_default(minimal_document: dict) -> None:
    blocked = validate_document(minimal_document, release=True)
    allowed = validate_document(minimal_document, release=True, allow_test_fixture=True)
    assert "PRODUCTION_MATERIALS" in {item.rule_id for item in blocked.diagnostics}
    assert allowed.valid


def test_unknown_recipe_and_duplicate_legend_are_rejected(minimal_document: dict) -> None:
    invalid = deepcopy(minimal_document)
    invalid["full_stitches"]["cells"][1] = 99
    invalid["legend"][1]["symbol_id"] = invalid["legend"][0]["symbol_id"]
    report = validate_document(invalid)
    rules = {item.rule_id for item in report.diagnostics}
    assert {"RECIPE_EXISTS", "LEGEND_COMPLETE"} <= rules


def test_normalization_produces_stable_hash(minimal_document: dict) -> None:
    reordered = deepcopy(minimal_document)
    reordered["materials"].reverse()
    reordered["legend"].reverse()
    reordered["provenance"]["sources"].reverse()
    assert document_hash(reordered) == document_hash(minimal_document)
    assert normalize_document(reordered)["materials"][0]["id"] == "m_black"
    assert normalize_document(normalize_document(reordered)) == normalize_document(reordered)


def test_duplicate_ids_and_recipe_composition_are_rejected(minimal_document: dict) -> None:
    invalid = deepcopy(minimal_document)
    invalid["materials"][1]["id"] = invalid["materials"][0]["id"]
    invalid["recipes"][1]["id"] = invalid["recipes"][0]["id"]
    invalid["recipes"][1]["index"] = invalid["recipes"][0]["index"]
    invalid["recipes"][1]["components"] = deepcopy(invalid["recipes"][0]["components"])
    invalid["legend"][1]["recipe_id"] = invalid["legend"][0]["recipe_id"]
    report = validate_document(invalid)
    assert "UNIQUE_IDS" in {item.rule_id for item in report.diagnostics}
    assert sum(item.rule_id == "UNIQUE_IDS" for item in report.diagnostics) >= 4


def test_material_color_state_and_source_are_consistent(minimal_document: dict) -> None:
    invalid = deepcopy(minimal_document)
    invalid["materials"][0]["color"] = {
        "display_srgb": "#000000",
        "status": "unknown",
        "source_ref": "src_fixture",
    }
    invalid["materials"][1]["color"]["source_ref"] = "missing_source"
    report = validate_document(invalid)
    assert {"MATERIAL_COLOR_STATE", "MATERIAL_SOURCE_EXISTS"} <= {
        item.rule_id for item in report.diagnostics
    }


def test_palette_budget_and_missing_legend_are_rejected(minimal_document: dict) -> None:
    invalid = deepcopy(minimal_document)
    invalid["constraints"]["color_budget"] = 1
    invalid["legend"] = invalid["legend"][:1]
    report = validate_document(invalid)
    assert {"PALETTE_BUDGET", "LEGEND_COMPLETE"} <= {item.rule_id for item in report.diagnostics}


def test_region_bounds_overlap_and_adjacency_are_rejected(minimal_document: dict) -> None:
    invalid = deepcopy(minimal_document)
    invalid["regions"][0]["spans"] = [
        {"row": 0, "start_col": 0, "end_col": 1},
        {"row": 0, "start_col": 1, "end_col": 2},
        {"row": 3, "start_col": 0, "end_col": 1},
    ]
    report = validate_document(invalid)
    assert sum(item.rule_id == "REGION_BOUNDS" for item in report.diagnostics) == 2


def test_empty_release_is_rejected_even_when_fixture_is_allowed(minimal_document: dict) -> None:
    invalid = deepcopy(minimal_document)
    invalid["full_stitches"]["cells"] = [0] * 12
    invalid["legend"] = []
    report = validate_document(invalid, release=True, allow_test_fixture=True)
    assert {item.rule_id for item in report.diagnostics} == {"NONEMPTY_RELEASE"}


def test_full_600_square_document_is_supported(minimal_document: dict) -> None:
    document = deepcopy(minimal_document)
    document["grid"].update(width=600, height=600)
    document["full_stitches"]["cells"] = [0] * 360_000
    document["regions"] = []
    assert validate_document(document).valid


def test_oversized_arrays_have_concise_diagnostics(minimal_document: dict) -> None:
    document = deepcopy(minimal_document)
    document["full_stitches"]["cells"] = [0] * 360_001
    report = validate_document(document)
    assert not report.valid
    issue = next(item for item in report.diagnostics if item.path == "$.full_stitches.cells")
    assert "360,001" in issue.message and "360,000" in issue.message
    assert len(issue.message) < 150
    assert "[0, 0" not in issue.message


def test_schema_messages_bound_long_string_and_type_errors(minimal_document: dict) -> None:
    document = deepcopy(minimal_document)
    document["metadata"]["title"] = "长标题" * 1000
    document["full_stitches"]["top_leg"] = ["invalid"] * 1000
    report = validate_document(document)
    assert not report.valid
    assert all(len(item.message) <= 320 for item in report.diagnostics)
