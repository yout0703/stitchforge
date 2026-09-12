from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from .imaging import GridSample
from .palette import delta_e_2000_lab, srgb_hex_to_lab
from .validation import validate_document


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
                if (
                    component_by_offset[neighbor] == -1
                    and cells[neighbor] == recipe_index
                ):
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


def _energy(
    cells: list[int],
    sample: GridSample,
    recipe_labs: dict[int, Any],
    policy_weights: list[float],
    *,
    edge_lambda: float,
    small_mu: float,
) -> _Energy:
    width, height = sample.width, sample.height
    unique_targets = sorted({color for color in sample.display_srgb if color is not None})
    target_lab = {
        color: lab
        for color, lab in zip(unique_targets, srgb_hex_to_lab(unique_targets), strict=True)
    }
    data = 0.0
    edge = 0.0
    for offset, recipe_index in enumerate(cells):
        target = sample.display_srgb[offset]
        if recipe_index and target is not None:
            data += (
                float(delta_e_2000_lab(target_lab[target], recipe_labs[recipe_index]))
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
            target_delta = float(
                delta_e_2000_lab(target_lab[left_target], target_lab[right_target])
            )
            edge_weight = max(0.1, 1.0 - min(target_delta / 30.0, 0.9))
            edge += edge_lambda * edge_weight
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
    target_colors = sorted({color for color in sample.display_srgb if color is not None})
    target_labs = {
        color: lab
        for color, lab in zip(target_colors, srgb_hex_to_lab(target_colors), strict=True)
    }
    data_cost_cache: dict[tuple[str, int], float] = {}
    edge_weight_cache: dict[tuple[str, str], float] = {}

    def data_cost(offset: int, recipe_index: int) -> float:
        target = sample.display_srgb[offset]
        if recipe_index == 0 or target is None:
            return 0.0
        key = (target, recipe_index)
        if key not in data_cost_cache:
            data_cost_cache[key] = float(
                delta_e_2000_lab(target_labs[target], recipe_labs[recipe_index])
            )
        return (
            data_cost_cache[key]
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
        key = tuple(sorted((first_target, second_target)))
        if key not in edge_weight_cache:
            target_delta = float(
                delta_e_2000_lab(target_labs[key[0]], target_labs[key[1]])
            )
            edge_weight_cache[key] = max(0.1, 1.0 - min(target_delta / 30.0, 0.9))
        return edge_lambda * edge_weight_cache[key]

    def small_cost(size: int) -> float:
        return small_mu * size if size <= 3 else 0.0

    before = _energy(
        cells,
        sample,
        recipe_labs,
        policy_weights,
        edge_lambda=edge_lambda,
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
            candidates = sorted(
                {cells[components[item][0]] for item in neighboring_component_ids}
            )
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
                    and float(
                        delta_e_2000_lab(
                            target_labs[sample.display_srgb[offset]], recipe_labs[candidate]
                        )
                    )
                    > preserve_max_delta_e00
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
        recipe_labs,
        policy_weights,
        edge_lambda=edge_lambda,
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
