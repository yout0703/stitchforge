from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
from stitchforge import optimize as optimizer
from stitchforge.imaging import GridSample
from stitchforge.optimize import optimize_small_components
from stitchforge.palette import delta_e_2000_lab, srgb_hex_to_lab
from stitchforge.validation import validate_document


def _center_noise_document(minimal_document: dict) -> dict:
    document = deepcopy(minimal_document)
    document["grid"].update(width=3, height=3)
    document["full_stitches"]["cells"] = [1, 1, 1, 1, 2, 1, 1, 1, 1]
    document["regions"] = []
    return document


def _sample(center: str = "#000000") -> GridSample:
    colors = ["#000000"] * 9
    colors[4] = center
    return GridSample(3, 3, 500_000, (1_000_000,) * 9, tuple(colors))


def test_optimizer_replaces_isolated_noise_when_energy_improves(minimal_document: dict) -> None:
    document = _center_noise_document(minimal_document)

    result = optimize_small_components(document, _sample())

    assert result.document["full_stitches"]["cells"] == [1] * 9
    assert result.report.accepted_components == 1
    assert result.report.changes[0].offset == 4
    assert result.report.energy_after < result.report.energy_before
    assert result.report.locked_changed_cells == 0
    assert validate_document(result.document).valid


def test_optimizer_never_changes_locked_component(minimal_document: dict) -> None:
    document = _center_noise_document(minimal_document)
    document["regions"] = [
        {
            "id": "locked_center",
            "name": "锁定中心",
            "policy": "locked",
            "spans": [{"row": 1, "start_col": 1, "end_col": 2}],
        }
    ]

    result = optimize_small_components(document, _sample())

    assert result.document == document
    assert result.report.changes == ()
    assert result.report.locked_changed_cells == 0


def test_preserve_region_enforces_local_visual_limit(minimal_document: dict) -> None:
    document = _center_noise_document(minimal_document)
    document["regions"] = [
        {
            "id": "preserve_center",
            "name": "保留中心",
            "policy": "preserve",
            "spans": [{"row": 1, "start_col": 1, "end_col": 2}],
        }
    ]

    result = optimize_small_components(
        document,
        _sample(center="#FFFFFF"),
        edge_lambda=100,
        small_mu=100,
        preserve_max_delta_e00=12,
    )

    assert result.document == document
    assert result.report.changes == ()


def test_optimizer_rejects_grid_mismatch_and_invalid_parameters(minimal_document: dict) -> None:
    document = _center_noise_document(minimal_document)
    with pytest.raises(ValueError, match="尺寸不一致"):
        optimize_small_components(document, GridSample(1, 1, 0, (0,), (None,)))
    with pytest.raises(ValueError, match="不能为负"):
        optimize_small_components(document, _sample(), edge_lambda=-1)
    with pytest.raises(ValueError, match="1–5"):
        optimize_small_components(document, _sample(), max_rounds=6)


@pytest.mark.parametrize("width,height", [(4, 3), (1, 5), (5, 1)])
def test_batched_costs_match_scalar_colour_and_edge_costs(width: int, height: int) -> None:
    colours = (None, "#F1EDC0", "#223344", "#FF9999", "#223344")
    targets = tuple(colours[index % len(colours)] for index in range(width * height))
    sample = GridSample(
        width,
        height,
        500_000,
        tuple(0 if colour is None else 800_000 for colour in targets),
        targets,
    )
    # Recipe indices need not be consecutive after a palette reduction.
    labs = dict(zip((1, 4, 9), srgb_hex_to_lab(["#FFFFFF", "#111111", "#FFAAAA"]), strict=True))
    costs = optimizer._optimization_costs(sample, labs, edge_lambda=0.75)
    for offset, colour in enumerate(targets):
        for recipe, lab in labs.items():
            expected = (
                0 if colour is None else float(delta_e_2000_lab(srgb_hex_to_lab([colour])[0], lab))
            )
            assert costs.distance(offset, recipe) == pytest.approx(expected, abs=1e-12)
        assert costs.distance(offset, 0) == 0
        for axis, neighbor in ((0, offset + 1), (1, offset + width)):
            if (axis == 0 and offset % width == width - 1) or neighbor >= width * height:
                assert costs.edge_weights[axis, offset] == 0
                continue
            other = targets[neighbor]
            expected_edge = 0.0
            if colour is not None and other is not None:
                pair = srgb_hex_to_lab(sorted([colour, other]))
                delta = float(delta_e_2000_lab(pair[0], pair[1]))
                expected_edge = 0.75 * max(0.1, 1 - min(delta / 30, 0.9))
            assert costs.edge_weights[axis, offset] == pytest.approx(expected_edge, abs=1e-12)


def test_large_cost_tables_use_bounded_vector_calls(monkeypatch) -> None:
    shapes = []
    original = optimizer.delta_e_2000_lab

    def recording_distance(first, second):
        shapes.append((first.shape, second.shape))
        return original(first, second)

    monkeypatch.setattr(optimizer, "delta_e_2000_lab", recording_distance)
    sample = GridSample(
        1030,
        2,
        500_000,
        (1_000_000,) * 2060,
        tuple(f"#{index:06X}" for index in range(2060)),
    )
    labs = {1: np.array([20, 5, 10]), 7: np.array([80, -20, 5])}
    costs = optimizer._optimization_costs(sample, labs, edge_lambda=0.75)

    assert costs.distances.shape == (2060, 2)
    table_calls = [shape for shape in shapes if len(shape[0]) == 3]
    assert [shape[0][0] for shape in table_calls] == [1024, 1024, 12]
    assert all(len(first) >= 2 and first[0] <= 8192 for first, _ in shapes)


def test_all_blank_sample_has_zero_costs() -> None:
    sample = GridSample(1, 2, 500_000, (0, 0), (None, None))
    costs = optimizer._optimization_costs(sample, {3: np.array([50, 0, 0])}, edge_lambda=0.75)
    assert costs.distances.shape == (0, 1)
    assert costs.distance(0, 3) == 0
    assert not costs.edge_weights.any()


def test_large_palettes_use_bounded_rows_without_changing_distances(monkeypatch) -> None:
    sample = _sample(center="#FFAAAA")
    labs = dict(zip((2, 8), srgb_hex_to_lab(["#333333", "#FFEEDD"]), strict=True))
    full = optimizer._optimization_costs(sample, labs, edge_lambda=0.75)
    monkeypatch.setattr(optimizer, "_MAX_COST_TABLE_ENTRIES", 1)
    bounded = optimizer._optimization_costs(sample, labs, edge_lambda=0.75)
    assert bounded.distances is None
    for offset in range(9):
        for recipe in labs:
            assert bounded.distance(offset, recipe) == pytest.approx(full.distance(offset, recipe))
