from __future__ import annotations

from copy import deepcopy

import pytest
from stitchforge.imaging import GridSample
from stitchforge.optimize import optimize_small_components
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
