from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from .imaging import GridSample
from .palette import delta_e_2000_lab, srgb_hex_to_lab
from .validation import validate_document

_MAX_COST_TABLE_ENTRIES = 16_777_216  # 128 MiB of float64 values, including CLI use.


@dataclass(frozen=True, slots=True)
class CellChange:
    offset: int
    from_recipe_index: int
    to_recipe_index: int


@dataclass(frozen=True, slots=True)
class OptimizationReport:
    algorithm: str
    rounds: int
    accepted_components: int
    changes: tuple[CellChange, ...]
    energy_before: float
    energy_after: float
    data_error_before: float
    data_error_after: float
    edge_penalty_before: float
    edge_penalty_after: float
    small_penalty_before: float
    small_penalty_after: float
    locked_changed_cells: int

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["changes"] = [asdict(change) for change in self.changes]
        return result


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    document: dict[str, Any]
    report: OptimizationReport


@dataclass(frozen=True, slots=True)
class _Energy:
    data: float
    edge: float
    small: float

    @property
    def total(self) -> float:
        return self.data + self.edge + self.small


def _component_map(cells: list[int], width: int, height: int) -> tuple[list[list[int]], list[int]]:
    components: list[list[int]] = []
    component_by_offset = [-1] * len(cells)
    for start, recipe_index in enumerate(cells):
        if recipe_index == 0 or component_by_offset[start] != -1:
            continue
        component_id = len(components)
        stack = [start]
        component_by_offset[start] = component_id
        offsets: list[int] = []
        while stack:
            offset = stack.pop()
            offsets.append(offset)
            x, y = offset % width, offset // width
            for neighbor_x, neighbor_y in (
                (x - 1, y),
                (x + 1, y),
                (x, y - 1),
                (x, y + 1),
            ):
                if not (0 <= neighbor_x < width and 0 <= neighbor_y < height):
                    continue
                neighbor = neighbor_y * width + neighbor_x
                if component_by_offset[neighbor] == -1 and cells[neighbor] == recipe_index:
                    component_by_offset[neighbor] = component_id
                    stack.append(neighbor)
        components.append(sorted(offsets))
    return components, component_by_offset


def _neighbors(offset: int, width: int, height: int) -> tuple[int, ...]:
    x, y = offset % width, offset // width
    result = []
    for neighbor_x, neighbor_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= neighbor_x < width and 0 <= neighbor_y < height:
            result.append(neighbor_y * width + neighbor_x)
    return tuple(result)


def _region_policies(document: dict[str, Any]) -> tuple[list[bool], list[float]]:
    width = document["grid"]["width"]
    size = width * document["grid"]["height"]
    locked = [False] * size
    weights = [1.0] * size
    for region in document["regions"]:
        for span in region["spans"]:
            for col in range(span["start_col"], span["end_col"]):
                offset = span["row"] * width + col
                if region["policy"] == "locked":
                    locked[offset] = True
                elif region["policy"] == "preserve":
                    weights[offset] = max(weights[offset], 2.0)
    return locked, weights


def _recipe_labs(document: dict[str, Any]) -> dict[int, Any]:
    material_by_id = {item["id"]: item for item in document["materials"]}
    colors: list[str] = []
    indices: list[int] = []
    for recipe in sorted(document["recipes"], key=lambda item: item["index"]):
        material = material_by_id[recipe["components"][0]["material_id"]]
        color = material["color"]["display_srgb"]
        if color is None:
            raise ValueError(f"配方 {recipe['id']} 的材料没有可优化显示色")
        indices.append(recipe["index"])
        colors.append(color)
    labs = srgb_hex_to_lab(colors)
    return {index: lab for index, lab in zip(indices, labs, strict=True)}


@dataclass(frozen=True, slots=True)
class _OptimizationCosts:
    target_indices: np.ndarray
    recipe_columns: dict[int, int]
    distances: np.ndarray | None
    edge_weights: np.ndarray
    distance_row: Callable[[int], np.ndarray]

    def distance(self, offset: int, recipe_index: int) -> float:
        target = self.target_indices[offset]
        if recipe_index == 0 or target < 0:
            return 0.0
        column = self.recipe_columns[recipe_index]
        if self.distances is not None:
            return float(self.distances[target, column])
        return float(self.distance_row(int(target))[column])


def _optimization_costs(
    sample: GridSample,
    recipe_labs: dict[int, Any],
    *,
    edge_lambda: float,
) -> _OptimizationCosts:
    """Evaluate exact CIEDE2000 costs in bounded batches, once per conversion.

    Scalar colour-science calls dominated large charts, including two full
    energy scans. Reuse float64 distances without changing the search or its
    acceptance thresholds. Batch size bounds temporary broadcast arrays.
    """
    width, height = sample.width, sample.height
    unique_targets = sorted({color for color in sample.display_srgb if color is not None})
    target_lookup = {color: index for index, color in enumerate(unique_targets)}
    target_indices = np.asarray(
        [target_lookup.get(color, -1) for color in sample.display_srgb], dtype=np.int32
    )
    # Keep all-empty charts usable; colour-science expects an N x 3 input.
    target_labs = srgb_hex_to_lab(unique_targets) if unique_targets else np.empty((0, 3))
    recipe_indices = sorted(recipe_labs)
    material_labs = np.asarray([recipe_labs[index] for index in recipe_indices]).reshape(-1, 3)
    distances = None
    if len(unique_targets) * len(recipe_indices) <= _MAX_COST_TABLE_ENTRIES:
        distances = np.empty((len(unique_targets), len(recipe_indices)), dtype=np.float64)
        for start in range(0, len(unique_targets), 1024):
            distances[start : start + 1024] = delta_e_2000_lab(
                target_labs[start : start + 1024, np.newaxis, :],
                material_labs[np.newaxis, :, :],
            )

    @lru_cache(maxsize=128)
    def distance_row(target: int) -> np.ndarray:
        # Imported documents may contain far more than generation's 64 colors.
        # Compute only the requested rows when the full table would be too big.
        return delta_e_2000_lab(target_labs[target], material_labs)

    edge_weights = np.zeros((2, len(sample.display_srgb)), dtype=np.float64)
    grid_offsets = np.arange(width * height).reshape(height, width)
    for axis, first, second in (
        (0, grid_offsets[:, :-1].ravel(), grid_offsets[:, 1:].ravel()),
        (1, grid_offsets[:-1, :].ravel(), grid_offsets[1:, :].ravel()),
    ):
        valid = (target_indices[first] >= 0) & (target_indices[second] >= 0)
        first, second = first[valid], second[valid]
        for start in range(0, len(first), 8192):
            left, right = first[start : start + 8192], second[start : start + 8192]
            # Match the old cache's sorted-color order for deterministic ties.
            left_index = np.minimum(target_indices[left], target_indices[right])
            right_index = np.maximum(target_indices[left], target_indices[right])
            delta = delta_e_2000_lab(target_labs[left_index], target_labs[right_index])
            edge_weights[axis, left] = edge_lambda * np.maximum(
                0.1, 1.0 - np.minimum(delta / 30.0, 0.9)
            )
    return _OptimizationCosts(
        target_indices,
        {index: column for column, index in enumerate(recipe_indices)},
        distances,
        edge_weights,
        distance_row,
    )


def _energy(
    cells: list[int],
    sample: GridSample,
    costs: _OptimizationCosts,
    policy_weights: list[float],
    *,
    small_mu: float,
) -> _Energy:
    width, height = sample.width, sample.height
    data = 0.0
    edge = 0.0
    for offset, recipe_index in enumerate(cells):
        target = sample.display_srgb[offset]
        if recipe_index and target is not None:
            data += (
                costs.distance(offset, recipe_index)
                * sample.coverage_ppm[offset]
                / 1_000_000
                * policy_weights[offset]
            )
        x, y = offset % width, offset // width
        for neighbor in (
            offset + 1 if x + 1 < width else -1,
            offset + width if y + 1 < height else -1,
        ):
            if neighbor < 0 or not cells[offset] or not cells[neighbor]:
                continue
            if cells[offset] == cells[neighbor]:
                continue
            left_target = sample.display_srgb[offset]
            right_target = sample.display_srgb[neighbor]
            if left_target is None or right_target is None:
                continue
            edge += float(costs.edge_weights[0 if neighbor == offset + 1 else 1, offset])
    components, _ = _component_map(cells, width, height)
    small = small_mu * sum(len(component) for component in components if len(component) <= 3)
    return _Energy(data, edge, small)


def optimize_small_components(
    document: dict[str, Any],
    sample: GridSample,
    *,
    edge_lambda: float = 0.75,
    small_mu: float = 0.5,
    max_rounds: int = 5,
    preserve_max_delta_e00: float = 12.0,
) -> OptimizationResult:
    validate_document(document).raise_for_errors()
    width, height = document["grid"]["width"], document["grid"]["height"]
    if (sample.width, sample.height) != (width, height):
        raise ValueError("目标网格与 PatternDocument 尺寸不一致")
    if edge_lambda < 0 or small_mu < 0:
        raise ValueError("优化权重不能为负数")
    if not 1 <= max_rounds <= 5:
        raise ValueError("max_rounds 必须为 1–5")
    if preserve_max_delta_e00 < 0:
        raise ValueError("preserve_max_delta_e00 不能为负数")

    optimized = deepcopy(document)
    cells = optimized["full_stitches"]["cells"]
    original_cells = list(cells)
    recipe_labs = _recipe_labs(optimized)
    locked, policy_weights = _region_policies(optimized)
    costs = _optimization_costs(sample, recipe_labs, edge_lambda=edge_lambda)

    def data_cost(offset: int, recipe_index: int) -> float:
        target = sample.display_srgb[offset]
        if recipe_index == 0 or target is None:
            return 0.0
        return (
            costs.distance(offset, recipe_index)
            * sample.coverage_ppm[offset]
            / 1_000_000
            * policy_weights[offset]
        )

    def edge_cost(first: int, second: int, first_recipe: int, second_recipe: int) -> float:
        if not first_recipe or not second_recipe or first_recipe == second_recipe:
            return 0.0
        first_target = sample.display_srgb[first]
        second_target = sample.display_srgb[second]
        if first_target is None or second_target is None:
            return 0.0
        return float(costs.edge_weights[0 if second == first + 1 else 1, first])

    def small_cost(size: int) -> float:
        return small_mu * size if size <= 3 else 0.0

    before = _energy(
        cells,
        sample,
        costs,
        policy_weights,
        small_mu=small_mu,
    )
    accepted_components = 0
    rounds_run = 0

    for round_index in range(max_rounds):
        components, component_by_offset = _component_map(cells, width, height)
        affected_components: set[int] = set()
        accepted_this_round = 0
        for component_id, component in enumerate(components):
            if component_id in affected_components or len(component) > 3:
                continue
            if any(locked[offset] for offset in component):
                continue
            source_recipe = cells[component[0]]
            neighboring_component_ids = {
                component_by_offset[neighbor]
                for offset in component
                for neighbor in _neighbors(offset, width, height)
                if cells[neighbor] not in {0, source_recipe}
            }
            neighboring_component_ids.discard(-1)
            candidates = sorted({cells[components[item][0]] for item in neighboring_component_ids})
            if not candidates:
                continue
            best_recipe: int | None = None
            best_delta = 0.0
            component_set = set(component)
            incident_pairs = {
                tuple(sorted((offset, neighbor)))
                for offset in component
                for neighbor in _neighbors(offset, width, height)
            }
            for candidate in candidates:
                if any(
                    policy_weights[offset] > 1
                    and sample.display_srgb[offset] is not None
                    and costs.distance(offset, candidate) > preserve_max_delta_e00
                    for offset in component
                ):
                    continue
                data_delta = sum(
                    data_cost(offset, candidate) - data_cost(offset, source_recipe)
                    for offset in component
                )
                edge_delta = 0.0
                for first, second in incident_pairs:
                    before_first, before_second = cells[first], cells[second]
                    after_first = candidate if first in component_set else before_first
                    after_second = candidate if second in component_set else before_second
                    edge_delta += edge_cost(first, second, after_first, after_second) - edge_cost(
                        first, second, before_first, before_second
                    )
                joining_components = {
                    item
                    for item in neighboring_component_ids
                    if cells[components[item][0]] == candidate
                }
                before_small = small_cost(len(component)) + sum(
                    small_cost(len(components[item])) for item in joining_components
                )
                merged_size = len(component) + sum(
                    len(components[item]) for item in joining_components
                )
                small_delta = small_cost(merged_size) - before_small
                total_delta = data_delta + edge_delta + small_delta
                if total_delta < best_delta - 1e-9 or (
                    abs(total_delta - best_delta) <= 1e-9
                    and best_recipe is not None
                    and candidate < best_recipe
                ):
                    best_recipe = candidate
                    best_delta = total_delta
            if best_recipe is None:
                continue
            for offset in component:
                cells[offset] = best_recipe
            accepted_components += 1
            accepted_this_round += 1
            affected_components.add(component_id)
            affected_components.update(neighboring_component_ids)
        rounds_run = round_index + 1
        if accepted_this_round == 0:
            break

    validate_document(optimized).raise_for_errors()
    after = _energy(
        cells,
        sample,
        costs,
        policy_weights,
        small_mu=small_mu,
    )
    changes = tuple(
        CellChange(offset, before_recipe, after_recipe)
        for offset, (before_recipe, after_recipe) in enumerate(
            zip(original_cells, cells, strict=True)
        )
        if before_recipe != after_recipe
    )
    report = OptimizationReport(
        algorithm="small-component-local-search-v1",
        rounds=rounds_run,
        accepted_components=accepted_components,
        changes=changes,
        energy_before=before.total,
        energy_after=after.total,
        data_error_before=before.data,
        data_error_after=after.data,
        edge_penalty_before=before.edge,
        edge_penalty_after=after.edge,
        small_penalty_before=before.small,
        small_penalty_after=after.small,
        locked_changed_cells=sum(
            original_cells[offset] != cells[offset]
            for offset, is_locked in enumerate(locked)
            if is_locked
        ),
    )
    return OptimizationResult(optimized, report)
