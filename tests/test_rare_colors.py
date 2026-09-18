from copy import deepcopy

import pytest
from stitchforge.imaging import GridSample
from stitchforge.rare_colors import merge_rare_colors, normalize_rendered_stitch_palette
from stitchforge.validation import validate_document


def fixture(document, offsets=(168, 169, 188, 189), *, rare_color="#828282"):
    document = deepcopy(document)
    document["grid"].update(width=20, height=20)
    document["regions"] = []
    document["materials"][0]["color"]["display_srgb"] = "#808080"
    document["materials"][1]["color"]["display_srgb"] = rare_color
    cells = [1] * 400
    for offset in offsets:
        cells[offset] = 2
    document["full_stitches"]["cells"] = cells
    colors = tuple(rare_color if cell == 2 else "#808080" for cell in cells)
    sample = GridSample(20, 20, 500_000, (1_000_000,) * 400, colors)
    return document, sample


def rendered_fixture(
    document,
    offsets=(210,),
    *,
    base_color="#808080",
    detail_color="#828282",
    sample_detail="#818181",
):
    document, _ = fixture(
        document,
        offsets,
        rare_color=detail_color,
    )
    document["materials"][0]["color"]["display_srgb"] = base_color
    colors = tuple(
        sample_detail if cell == 2 else base_color for cell in document["full_stitches"]["cells"]
    )
    sample = GridSample(20, 20, 500_000, (1_000_000,) * 400, colors)
    return document, sample


def test_rendered_palette_absorbs_only_safe_internal_lighting_fragment(minimal_document):
    document, sample = rendered_fixture(minimal_document)
    original_occupancy = [bool(cell) for cell in document["full_stitches"]["cells"]]

    result = normalize_rendered_stitch_palette(document, sample)

    assert result.document["full_stitches"]["cells"] == [1] * 400
    assert [recipe["index"] for recipe in result.document["recipes"]] == [1]
    assert [bool(cell) for cell in result.document["full_stitches"]["cells"]] == original_occupancy
    assert result.report["colors_before"] == 2
    assert result.report["colors_after"] == 1
    assert result.report["changed_stitches"] == 1
    assert result.report["merged_components"] == 1
    assert result.report["removed_recipe_indices"] == [2]
    assert validate_document(result.document).valid

    repeated = normalize_rendered_stitch_palette(result.document, sample)
    assert repeated.document == result.document
    assert repeated.report["changed_stitches"] == 0
    assert repeated.report["colors_before"] == repeated.report["colors_after"] == 1


def test_rendered_palette_absorbs_multiple_disconnected_safe_fragments(minimal_document):
    offsets = (126, 210, 273)
    document, sample = rendered_fixture(minimal_document, offsets)

    result = normalize_rendered_stitch_palette(document, sample)

    assert result.report["changed_stitches"] == len(offsets)
    assert result.report["merged_components"] == len(offsets)
    assert result.report["colors_after"] == 1
    assert result.document["full_stitches"]["cells"] == [1] * 400


@pytest.mark.parametrize(
    ("offsets", "detail_color", "sample_detail", "reason"),
    [
        ((0,), "#828282", "#818181", "silhouette_boundary"),
        ((168, 169, 170, 171), "#828282", "#818181", "large_component"),
        ((210,), "#D02050", "#D02050", "no_safe_neighbor"),
        ((210,), "#909090", "#909090", "no_safe_neighbor"),
    ],
)
def test_rendered_palette_preserves_boundaries_large_regions_and_accents(
    minimal_document,
    offsets,
    detail_color,
    sample_detail,
    reason,
):
    document, sample = rendered_fixture(
        minimal_document,
        offsets,
        detail_color=detail_color,
        sample_detail=sample_detail,
    )

    result = normalize_rendered_stitch_palette(document, sample)

    assert result.document == document
    assert result.report["changed_stitches"] == 0
    assert result.report["retained_components"][reason] >= 1


def test_rendered_palette_preserves_locked_and_preserve_regions(minimal_document):
    for policy in ("locked", "preserve"):
        document, sample = rendered_fixture(minimal_document)
        document["regions"] = [
            {
                "id": f"{policy}_detail",
                "name": "受保护细节",
                "policy": policy,
                "spans": [{"row": 10, "start_col": 10, "end_col": 11}],
            }
        ]
        result = normalize_rendered_stitch_palette(document, sample)
        assert result.document == document
        assert result.report["retained_components"]["protected_region"] >= 1


def test_rendered_palette_rejects_invalid_grid_and_parameters(minimal_document):
    document, sample = rendered_fixture(minimal_document)
    with pytest.raises(ValueError, match="尺寸"):
        normalize_rendered_stitch_palette(document, GridSample(1, 1, 0, (0,), (None,)))
    for kwargs in (
        {"max_component_size": 0},
        {"max_delta_e00": -1},
        {"max_error_increase": -1},
        {"max_mean_error_increase": -1},
        {"min_neighbor_fraction": 0.4},
        {"max_rounds": 0},
    ):
        with pytest.raises(ValueError, match="参数"):
            normalize_rendered_stitch_palette(document, sample, **kwargs)


def test_merges_four_cell_similar_rare_color_and_removes_only_its_legend(minimal_document):
    document, sample = fixture(minimal_document)
    original = deepcopy(document)
    result = merge_rare_colors(document, sample)
    assert result.document["full_stitches"]["cells"] == [1] * 400
    assert [recipe["index"] for recipe in result.document["recipes"]] == [1]
    assert len(result.document["legend"]) == 1
    assert result.report["colors_before"] == 2
    assert result.report["colors_after"] == 1
    assert result.report["changed_stitches"] == 4
    assert result.report["merges"][0]["from_recipe_index"] == 2
    assert result.report["merges"][0]["to_recipe_index"] == 1
    assert document == original
    assert validate_document(result.document).valid
    assert merge_rare_colors(result.document, sample).report["changed_stitches"] == 0


@pytest.mark.parametrize("offsets", [(168, 169, 170, 171), (126, 147, 168, 189)])
def test_preserves_thin_horizontal_and_diagonal_details(minimal_document, offsets):
    document, sample = fixture(minimal_document, offsets)
    result = merge_rare_colors(document, sample)
    assert result.document == document
    assert result.report["retained"][0]["reason"] == "thin_structure"


def test_preserves_dark_outline_even_when_it_has_only_four_stitches(minimal_document):
    document, sample = fixture(minimal_document, rare_color="#303030")
    result = merge_rare_colors(document, sample)
    assert result.document == document
    assert result.report["retained"][0]["reason"] == "no_safe_neighbor"


@pytest.mark.parametrize("policy", ["locked", "preserve"])
def test_does_not_merge_a_color_used_in_a_protected_region(minimal_document, policy):
    document, sample = fixture(minimal_document)
    document["regions"] = [
        {
            "id": "protected",
            "name": "细节",
            "policy": policy,
            "spans": [{"row": 8, "start_col": 8, "end_col": 9}],
        }
    ]
    assert merge_rare_colors(document, sample).document == document


def test_fraction_uses_worked_stitches_not_blank_canvas(minimal_document):
    document, sample = fixture(minimal_document)
    document["full_stitches"]["cells"] = [
        cell if i < 200 else 0 for i, cell in enumerate(document["full_stitches"]["cells"])
    ]
    result = merge_rare_colors(document, sample)
    assert result.document == document  # Four of 200 is 2%, not a rare color.
    assert result.report["retained"] == []


def test_does_not_merge_different_strand_counts(minimal_document):
    document, sample = fixture(minimal_document)
    document["recipes"][1]["components"][0]["strands"] = 3
    assert merge_rare_colors(document, sample).document == document


def test_preserves_transparent_silhouette_boundary(minimal_document):
    document, sample = fixture(minimal_document)
    document["full_stitches"]["cells"][148] = 0
    # Keep the rare fraction <= 1% after removing one worked stitch.
    result = merge_rare_colors(document, sample, max_fraction=0.02)
    assert result.document == document
    assert result.report["retained"][0]["reason"] == "silhouette_boundary"


def test_does_not_merge_to_similar_color_elsewhere_in_the_image(minimal_document):
    document, sample = fixture(minimal_document)
    third_material = deepcopy(document["materials"][0])
    third_material.update(id="red", code="RED")
    third_material["color"]["display_srgb"] = "#FF0000"
    third_recipe = deepcopy(document["recipes"][0])
    third_recipe.update(id="red_recipe", index=3)
    third_recipe["components"][0]["material_id"] = "red"
    document["materials"].append(third_material)
    document["recipes"].append(third_recipe)
    document["legend"].append(
        {**document["legend"][0], "recipe_id": "red_recipe", "symbol_id": "C"}
    )
    document["constraints"]["color_budget"] = 3
    for row in range(6, 12):
        for col in range(6, 12):
            if document["full_stitches"]["cells"][row * 20 + col] != 2:
                document["full_stitches"]["cells"][row * 20 + col] = 3
    assert merge_rare_colors(document, sample).document == document


def test_limits_original_image_error_even_when_palette_colors_are_close(minimal_document):
    document, sample = fixture(minimal_document, rare_color="#898989")
    assert merge_rare_colors(document, sample).document == document


def test_rejects_mismatched_grid_and_invalid_limits(minimal_document):
    document, sample = fixture(minimal_document)
    with pytest.raises(ValueError, match="尺寸"):
        merge_rare_colors(document, GridSample(1, 1, 0, (0,), (None,)))
    for kwargs in ({"max_fraction": -1}, {"max_stitches": 0}, {"max_delta_e00": -1}):
        with pytest.raises(ValueError):
            merge_rare_colors(document, sample, **kwargs)
