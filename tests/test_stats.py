from __future__ import annotations

from copy import deepcopy

import pytest
from stitchforge.stats import calculate_statistics


def test_minimal_statistics_match_manual_fixture(minimal_document: dict) -> None:
    stats = calculate_statistics(minimal_document)
    assert stats.total_cells == 12
    assert stats.full_stitch_count == 6
    assert stats.blank_count == 6
    assert stats.blank_coverage == 0.5
    assert stats.recipe_stitch_counts == {"r_black_2": 3, "r_white_2": 3}
    assert stats.used_recipe_count == 2
    assert stats.material_count == 2
    assert stats.occupied_bounds == (0, 0, 4, 3)
    assert stats.isolated_stitches_4 == 4
    assert stats.small_component_ratio_4 == 1.0
    assert stats.local_recipe_p95 == 2
    assert stats.canvas_width_mm == pytest.approx(7.257142857142857)
    assert stats.canvas_height_mm == pytest.approx(5.442857142857142)
    assert stats.cloth_width_mm == pytest.approx(107.25714285714285)
    assert stats.cloth_height_mm == pytest.approx(105.44285714285714)


def test_empty_pattern_has_explicit_null_metrics(minimal_document: dict) -> None:
    minimal_document["full_stitches"]["cells"] = [0] * 12
    stats = calculate_statistics(minimal_document)
    assert stats.full_stitch_count == 0
    assert stats.occupied_bounds is None
    assert stats.small_component_ratio_4 is None
    assert stats.local_recipe_p95 is None


def test_four_connected_rectangle_is_one_large_component(minimal_document: dict) -> None:
    document = deepcopy(minimal_document)
    document["full_stitches"]["cells"] = [1, 1, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0]
    stats = calculate_statistics(document)
    assert stats.isolated_stitches_4 == 0
    assert stats.small_component_ratio_4 == 0
    assert stats.occupied_bounds == (0, 0, 2, 2)


def test_diagonal_checker_cells_remain_separate_in_four_connectivity(
    minimal_document: dict,
) -> None:
    document = deepcopy(minimal_document)
    document["grid"].update(width=3, height=3)
    document["full_stitches"]["cells"] = [1, 2, 1, 2, 1, 2, 1, 2, 1]
    stats = calculate_statistics(document)
    assert stats.isolated_stitches_4 == 9
    assert stats.small_component_ratio_4 == 1


def test_partial_ten_by_ten_windows_contribute_to_p95(minimal_document: dict) -> None:
    document = deepcopy(minimal_document)
    width = height = 11
    cells = [0] * (width * height)
    for x, y, recipe in ((0, 0, 1), (1, 0, 2), (10, 0, 1), (0, 10, 1), (10, 10, 1)):
        cells[y * width + x] = recipe
    document["grid"].update(width=width, height=height)
    document["full_stitches"]["cells"] = cells
    stats = calculate_statistics(document)
    assert stats.local_recipe_p95 == 2
