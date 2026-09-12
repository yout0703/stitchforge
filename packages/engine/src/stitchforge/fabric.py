from __future__ import annotations

import math
from dataclasses import asdict, dataclass

MIN_AIDA_COUNT = 6
MAX_AIDA_COUNT = 40
MAX_GRID_SIDE = 600
# Shared ceiling for generation, import and stored documents.
MAX_GRID_CELLS = MAX_GRID_SIDE * MAX_GRID_SIDE


def validate_aida_count(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Aida count 必须是整数")
    if not MIN_AIDA_COUNT <= value <= MAX_AIDA_COUNT:
        raise ValueError(f"Aida count 必须在 {MIN_AIDA_COUNT}–{MAX_AIDA_COUNT} CT 之间")


@dataclass(frozen=True, slots=True)
class GridPlan:
    fabric_count: int
    width_cells: int
    height_cells: int
    stitched_width_mm: float
    stitched_height_mm: float

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def plan_physical_fit(
    image_width: int,
    image_height: int,
    *,
    max_width_mm: float,
    max_height_mm: float,
    fabric_counts: tuple[int, ...],
) -> tuple[GridPlan, ...]:
    """Return CT/grid candidates fitted inside one physical bounding box."""

    if (
        isinstance(image_width, bool)
        or isinstance(image_height, bool)
        or not isinstance(image_width, int)
        or not isinstance(image_height, int)
        or image_width < 1
        or image_height < 1
    ):
        raise ValueError("图片宽高必须是正整数")
    if (
        not math.isfinite(max_width_mm)
        or not math.isfinite(max_height_mm)
        or max_width_mm <= 0
        or max_height_mm <= 0
    ):
        raise ValueError("目标物理宽高必须是正有限数")
    if not fabric_counts:
        raise ValueError("至少需要一个 Aida count 候选")

    aspect = image_width / image_height
    plans = []
    for count in sorted(set(fabric_counts)):
        validate_aida_count(count)
        width_limit = min(math.floor(max_width_mm / 25.4 * count), MAX_GRID_SIDE)
        height_limit = min(math.floor(max_height_mm / 25.4 * count), MAX_GRID_SIDE)
        if width_limit < 1 or height_limit < 1:
            continue

        if width_limit / height_limit <= aspect:
            width = width_limit
            height = max(1, math.floor(width / aspect))
        else:
            height = height_limit
            width = max(1, math.floor(height * aspect))
        while width * height > MAX_GRID_CELLS:
            scale = math.sqrt(MAX_GRID_CELLS / (width * height))
            width = max(1, math.floor(width * scale))
            height = max(1, math.floor(height * scale))
        plans.append(
            GridPlan(
                fabric_count=count,
                width_cells=width,
                height_cells=height,
                stitched_width_mm=width / count * 25.4,
                stitched_height_mm=height / count * 25.4,
            )
        )
    if not plans:
        raise ValueError("目标物理尺寸不足以容纳一个针格")
    return tuple(plans)
