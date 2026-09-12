from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MotifResolutionReport:
    algorithm: str
    minimum_effective_width: int
    effective_width: int
    effective_height: int
    component_cells: int
    stitched_cells: int
    component_share: float
    bounding_box: tuple[int, int, int, int] | None
    passes_minimum: bool

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["bounding_box"] = (
            list(self.bounding_box) if self.bounding_box is not None else None
        )
        return result


def measure_motif_resolution(
    document: dict[str, Any], *, minimum_effective_width: int = 160
) -> MotifResolutionReport:
    """Measure the largest 4-connected stitched subject in grid cells."""

    if minimum_effective_width < 1:
        raise ValueError("最低主体有效宽度必须是正整数")
    width = document["grid"]["width"]
    height = document["grid"]["height"]
    cells = document["full_stitches"]["cells"]
    stitched = {index for index, recipe in enumerate(cells) if recipe != 0}
    remaining = set(stitched)
    largest: set[int] = set()

    while remaining:
        seed = remaining.pop()
        component = {seed}
        pending = [seed]
        while pending:
            index = pending.pop()
            row, col = divmod(index, width)
            neighbors = []
            if col:
                neighbors.append(index - 1)
            if col + 1 < width:
                neighbors.append(index + 1)
            if row:
                neighbors.append(index - width)
            if row + 1 < height:
                neighbors.append(index + width)
            for neighbor in neighbors:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    pending.append(neighbor)
        if len(component) > len(largest):
            largest = component

    if not largest:
        bounding_box = None
        effective_width = effective_height = 0
    else:
        rows_cols = [divmod(index, width) for index in largest]
        rows = [item[0] for item in rows_cols]
        cols = [item[1] for item in rows_cols]
        bounding_box = (min(cols), min(rows), max(cols) + 1, max(rows) + 1)
        effective_width = bounding_box[2] - bounding_box[0]
        effective_height = bounding_box[3] - bounding_box[1]

    return MotifResolutionReport(
        algorithm="largest-4-connected-component-v1",
        minimum_effective_width=minimum_effective_width,
        effective_width=effective_width,
        effective_height=effective_height,
        component_cells=len(largest),
        stitched_cells=len(stitched),
        component_share=(len(largest) / len(stitched) if stitched else 0.0),
        bounding_box=bounding_box,
        passes_minimum=effective_width >= minimum_effective_width,
    )
