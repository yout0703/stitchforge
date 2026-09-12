from __future__ import annotations

import pytest
from stitchforge.fabric import plan_physical_fit, validate_aida_count


def test_physical_fit_exposes_detail_gain_from_higher_count() -> None:
    plans = plan_physical_fit(
        4,
        3,
        max_width_mm=254,
        max_height_mm=254,
        fabric_counts=(20, 10, 20),
    )

    assert [(item.fabric_count, item.width_cells, item.height_cells) for item in plans] == [
        (10, 100, 75),
        (20, 200, 150),
    ]
    assert plans[0].stitched_width_mm == pytest.approx(254)
    assert plans[1].stitched_height_mm == pytest.approx(190.5)


def test_physical_fit_respects_engine_grid_ceiling() -> None:
    (plan,) = plan_physical_fit(
        1,
        1,
        max_width_mm=1000,
        max_height_mm=1000,
        fabric_counts=(40,),
    )
    assert (plan.width_cells, plan.height_cells) == (600, 600)


@pytest.mark.parametrize("value", [5, 41, True, 14.0])
def test_aida_count_outside_supported_range_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="Aida count"):
        validate_aida_count(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"image_width": 0, "image_height": 1, "max_width_mm": 10, "max_height_mm": 10},
        {"image_width": 1, "image_height": 1, "max_width_mm": 0, "max_height_mm": 10},
    ],
)
def test_physical_fit_rejects_invalid_dimensions(kwargs: dict[str, float | int]) -> None:
    with pytest.raises(ValueError):
        plan_physical_fit(**kwargs, fabric_counts=(14,))  # type: ignore[arg-type]
