from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PatternStatistics:
    grid_width: int
    grid_height: int
    total_cells: int
    full_stitch_count: int
    blank_count: int
    blank_coverage: float
    used_recipe_count: int
    material_count: int
    recipe_stitch_counts: dict[str, int]
    occupied_bounds: tuple[int, int, int, int] | None
    isolated_stitches_4: int
    small_component_ratio_4: float | None
    low_usage_recipes: tuple[str, ...]
    local_recipe_p95: int | None
    canvas_width_mm: float
    canvas_height_mm: float
    cloth_width_mm: float
    cloth_height_mm: float

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.occupied_bounds is not None:
            result["occupied_bounds"] = list(self.occupied_bounds)
        result["low_usage_recipes"] = list(self.low_usage_recipes)
        return result


def _component_sizes(cells: list[int], width: int, height: int) -> list[int]:
    seen: set[int] = set()
    sizes: list[int] = []
    for offset, recipe_index in enumerate(cells):
        if recipe_index == 0 or offset in seen:
            continue
        queue = deque([offset])
        seen.add(offset)
        size = 0
        while queue:
            current = queue.popleft()
            size += 1
            col, row = current % width, current // width
            for neighbor_col, neighbor_row in (
                (col - 1, row),
                (col + 1, row),
                (col, row - 1),
                (col, row + 1),
            ):
                if not (0 <= neighbor_col < width and 0 <= neighbor_row < height):
                    continue
                neighbor = neighbor_row * width + neighbor_col
                if neighbor not in seen and cells[neighbor] == recipe_index:
                    seen.add(neighbor)
                    queue.append(neighbor)
        sizes.append(size)
    return sizes


def _nearest_rank_p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def calculate_statistics(document: dict[str, Any]) -> PatternStatistics:
    width = document["grid"]["width"]
    height = document["grid"]["height"]
    cells = document["full_stitches"]["cells"]
    count_by_index = Counter(cell for cell in cells if cell != 0)
    recipe_by_index = {recipe["index"]: recipe for recipe in document["recipes"]}
    recipe_counts = {
        recipe_by_index[index]["id"]: count
        for index, count in sorted(count_by_index.items())
        if index in recipe_by_index
    }
    used_materials = {
        component["material_id"]
        for index in count_by_index
        if index in recipe_by_index
        for component in recipe_by_index[index]["components"]
    }

    occupied = [offset for offset, cell in enumerate(cells) if cell != 0]
    if occupied:
        cols = [offset % width for offset in occupied]
        rows = [offset // width for offset in occupied]
        occupied_bounds = (min(cols), min(rows), max(cols) + 1, max(rows) + 1)
    else:
        occupied_bounds = None

    component_sizes = _component_sizes(cells, width, height)
    full_stitch_count = len(occupied)
    small_stitch_count = sum(size for size in component_sizes if size <= 3)

    local_counts: list[int] = []
    for row_start in range(0, height, 10):
        for col_start in range(0, width, 10):
            recipes = {
                cells[row * width + col]
                for row in range(row_start, min(row_start + 10, height))
                for col in range(col_start, min(col_start + 10, width))
                if cells[row * width + col] != 0
            }
            if recipes:
                local_counts.append(len(recipes))

    fabric_count = document["fabric"]["count"]
    canvas_width_mm = width / fabric_count * 25.4
    canvas_height_mm = height / fabric_count * 25.4
    margins = document["fabric"]["margins_mm"]

    return PatternStatistics(
        grid_width=width,
        grid_height=height,
        total_cells=width * height,
        full_stitch_count=full_stitch_count,
        blank_count=width * height - full_stitch_count,
        blank_coverage=(width * height - full_stitch_count) / (width * height),
        used_recipe_count=len(count_by_index),
        material_count=len(used_materials),
        recipe_stitch_counts=recipe_counts,
        occupied_bounds=occupied_bounds,
        isolated_stitches_4=sum(size == 1 for size in component_sizes),
        small_component_ratio_4=(small_stitch_count / full_stitch_count)
        if full_stitch_count
        else None,
        low_usage_recipes=tuple(
            sorted(recipe_id for recipe_id, count in recipe_counts.items() if count < 5)
        ),
        local_recipe_p95=_nearest_rank_p95(local_counts),
        canvas_width_mm=canvas_width_mm,
        canvas_height_mm=canvas_height_mm,
        cloth_width_mm=canvas_width_mm + margins["left"] + margins["right"],
        cloth_height_mm=canvas_height_mm + margins["top"] + margins["bottom"],
    )
