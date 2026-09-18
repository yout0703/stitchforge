"""Conservative whole-recipe reduction after local speckle cleanup."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np

from .imaging import GridSample
from .optimize import _component_map, _neighbors, _recipe_labs, _region_policies
from .palette import delta_e_2000_lab, srgb_hex_to_lab
from .validation import validate_document


@dataclass(frozen=True, slots=True)
class RareColorResult:
    document: dict[str, Any]
    report: dict[str, Any]


def normalize_rendered_stitch_palette(
    document: dict[str, Any],
    sample: GridSample,
    *,
    max_component_size: int = 3,
    max_delta_e00: float = 10.0,
    max_error_increase: float = 4.0,
    max_mean_error_increase: float = 3.0,
    min_neighbor_fraction: float = 0.5,
    max_rounds: int = 3,
) -> RareColorResult:
    """Absorb tiny lighting fragments without changing the stitched silhouette.

    A component is changed only when it is internal, unprotected, surrounded by
    one more-common neighboring material, close in thread color, and still fits
    the sampled source nearly as well. Large regions and distinct accent colors
    therefore remain available even when they use only a few stitches.
    """
    validate_document(document).raise_for_errors()
    width, height = document["grid"]["width"], document["grid"]["height"]
    if (width, height) != (sample.width, sample.height):
        raise ValueError("目标网格与 PatternDocument 尺寸不一致")
    if (
        not isinstance(max_component_size, int)
        or not 1 <= max_component_size <= 8
        or not isfinite(max_delta_e00)
        or not 0 <= max_delta_e00 <= 20
        or not isfinite(max_error_increase)
        or not 0 <= max_error_increase <= 10
        or not isfinite(max_mean_error_increase)
        or not 0 <= max_mean_error_increase <= 10
        or not isfinite(min_neighbor_fraction)
        or not 0.5 <= min_neighbor_fraction <= 1
        or not isinstance(max_rounds, int)
        or not 1 <= max_rounds <= 5
    ):
        raise ValueError("成品绣片线色归一化参数超出有效范围")

    optimized = deepcopy(document)
    cells = optimized["full_stitches"]["cells"]
    occupancy = tuple(bool(cell) for cell in cells)
    colors_before = len(set(cells) - {0})
    locked, policy_weights = _region_policies(optimized)
    protected = {
        offset
        for offset, (is_locked, weight) in enumerate(zip(locked, policy_weights, strict=True))
        if is_locked or weight > 1
    }
    recipes = {recipe["index"]: recipe for recipe in optimized["recipes"]}
    strands = {
        index: tuple(component["strands"] for component in recipe["components"])
        for index, recipe in recipes.items()
    }
    recipe_labs = _recipe_labs(optimized)
    raw_colors = sorted({color for color in sample.display_srgb if color is not None})
    raw_labs = {
        color: lab for color, lab in zip(raw_colors, srgb_hex_to_lab(raw_colors), strict=True)
    }
    data_cost_cache: dict[tuple[str, int], float] = {}

    def data_cost(color: str, recipe_index: int) -> float:
        key = (color, recipe_index)
        if key not in data_cost_cache:
            data_cost_cache[key] = float(
                delta_e_2000_lab(raw_labs[color], recipe_labs[recipe_index])
            )
        return data_cost_cache[key]

    report: dict[str, Any] = {
        "algorithm": "rendered-stitch-local-lighting-merge-v1",
        "colors_before": colors_before,
        "colors_after": colors_before,
        "changed_stitches": 0,
        "merged_components": 0,
        "rounds": 0,
        "removed_recipe_indices": [],
        "merges": [],
        "retained_components": {},
        "limits": {
            "max_component_size": max_component_size,
            "max_delta_e00": max_delta_e00,
            "max_error_increase": max_error_increase,
            "max_mean_error_increase": max_mean_error_increase,
            "min_neighbor_fraction": min_neighbor_fraction,
            "max_rounds": max_rounds,
        },
    }
    retained: Counter[str] = Counter()
    for round_index in range(max_rounds):
        changed_this_round = 0
        counts = Counter(cell for cell in cells if cell)
        components, _ = _component_map(cells, width, height)
        for component in sorted(components, key=lambda value: (len(value), value[0])):
            source = cells[component[0]]
            if any(cells[offset] != source for offset in component):
                continue
            if len(component) > max_component_size:
                retained["large_component"] += 1
                continue
            if any(offset in protected for offset in component):
                retained["protected_region"] += 1
                continue
            component_set = set(component)
            touches_silhouette = False
            contact: Counter[int] = Counter()
            for offset in component:
                x, y = offset % width, offset // width
                if x in {0, width - 1} or y in {0, height - 1}:
                    touches_silhouette = True
                for neighbor in _neighbors(offset, width, height):
                    if neighbor in component_set:
                        continue
                    target = cells[neighbor]
                    if target == 0:
                        touches_silhouette = True
                    elif target != source:
                        contact[target] += 1
            if touches_silhouette:
                retained["silhouette_boundary"] += 1
                continue
            total_contact = sum(contact.values())
            if not total_contact:
                retained["no_neighbor"] += 1
                continue
            candidates = []
            for target, touching in contact.items():
                if touching / total_contact < min_neighbor_fraction:
                    continue
                if counts[target] < counts[source] or strands[target] != strands[source]:
                    continue
                material_delta = float(delta_e_2000_lab(recipe_labs[source], recipe_labs[target]))
                if material_delta > max_delta_e00:
                    continue
                increases = []
                for offset in component:
                    color = sample.display_srgb[offset]
                    if color is None:
                        break
                    increases.append(data_cost(color, target) - data_cost(color, source))
                if len(increases) != len(component):
                    continue
                maximum = max(increases)
                mean = float(np.mean(increases))
                if maximum > max_error_increase or mean > max_mean_error_increase:
                    continue
                candidates.append(
                    (
                        maximum,
                        mean,
                        material_delta,
                        -touching,
                        -counts[target],
                        target,
                    )
                )
            if not candidates:
                retained["no_safe_neighbor"] += 1
                continue
            maximum, mean, material_delta, negative_touching, _, target = min(candidates)
            for offset in component:
                cells[offset] = target
            changed_this_round += len(component)
            report["changed_stitches"] += len(component)
            report["merged_components"] += 1
            report["merges"].append(
                {
                    "round": round_index + 1,
                    "from_recipe_index": source,
                    "to_recipe_index": target,
                    "stitches": len(component),
                    "neighbor_contacts": -negative_touching,
                    "delta_e00": round(material_delta, 4),
                    "max_error_increase": round(maximum, 4),
                    "mean_error_increase": round(mean, 4),
                }
            )
        report["rounds"] = round_index + 1
        if not changed_this_round:
            break

    used = set(cells) - {0}
    removed = sorted(set(recipes) - used)
    if removed:
        removed_ids = {recipes[index]["id"] for index in removed}
        optimized["recipes"] = [
            recipe for recipe in optimized["recipes"] if recipe["index"] in used
        ]
        optimized["legend"] = [
            item for item in optimized["legend"] if item["recipe_id"] not in removed_ids
        ]
    report["removed_recipe_indices"] = removed
    report["colors_after"] = len(used)
    report["retained_components"] = dict(sorted(retained.items()))
    if tuple(bool(cell) for cell in cells) != occupancy:
        raise ValueError("成品绣片线色归一化改变了针格轮廓")
    validate_document(optimized).raise_for_errors()
    return RareColorResult(optimized, report)


def _has_thin_structure(offsets: list[int], width: int, height: int) -> bool:
    # Eight-neighbor connectivity also catches diagonal one-stitch outlines.
    remaining = set(offsets)
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        stack, component = [start], [start]
        while stack:
            offset = stack.pop()
            x, y = offset % width, offset // width
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    neighbor = ny * width + nx
                    if 0 <= nx < width and 0 <= ny < height and neighbor in remaining:
                        remaining.remove(neighbor)
                        component.append(neighbor)
                        stack.append(neighbor)
        if len(component) < 4:
            continue
        xs, ys = [i % width for i in component], [i // width for i in component]
        span_x, span_y = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if min(span_x, span_y) == 1 or len(component) / (span_x * span_y) <= 0.5:
            return True
    return False


def merge_rare_colors(
    document: dict[str, Any],
    sample: GridSample,
    *,
    max_fraction: float = 0.01,
    max_stitches: int = 128,
    max_delta_e00: float = 6.0,
    max_error_increase: float = 2.0,
) -> RareColorResult:
    """Remove a rare recipe only if every occurrence can safely use one common neighbor.

    Limits are engineering defaults, not calibrated physical-thread color tolerances.
    Recipe indices and symbols of surviving colors remain unchanged.
    """
    validate_document(document).raise_for_errors()
    width, height = document["grid"]["width"], document["grid"]["height"]
    if (width, height) != (sample.width, sample.height):
        raise ValueError("目标网格与 PatternDocument 尺寸不一致")
    if (
        not isfinite(max_fraction)
        or not 0 < max_fraction <= 0.1
        or not isinstance(max_stitches, int)
        or max_stitches < 1
        or not isfinite(max_delta_e00)
        or max_delta_e00 < 0
        or not isfinite(max_error_increase)
        or max_error_increase < 0
    ):
        raise ValueError("少量线色合并参数超出有效范围")
    optimized = deepcopy(document)
    cells = optimized["full_stitches"]["cells"]
    counts = Counter(cell for cell in cells if cell)
    limit = min(max_stitches, int(sum(counts.values()) * max_fraction))
    report = {
        "algorithm": "rare_neighbor_merge_v1",
        "colors_before": len(counts),
        "colors_after": len(counts),
        "changed_stitches": 0,
        "merges": [],
        "retained": [],
        "limits": {
            "max_fraction": max_fraction,
            "max_stitches": max_stitches,
            "effective_max_stitches": limit,
            "max_delta_e00": max_delta_e00,
            "max_error_increase": max_error_increase,
            "min_neighbor_fraction": 0.6,
        },
    }
    candidates = {index for index, count in counts.items() if count <= limit}
    if not candidates:
        return RareColorResult(optimized, report)
    offsets_by_recipe: dict[int, list[int]] = defaultdict(list)
    for offset, index in enumerate(cells):
        if index in candidates:
            offsets_by_recipe[index].append(offset)
    protected = set()
    for region in document["regions"]:
        if region["policy"] in {"locked", "preserve"}:
            for span in region["spans"]:
                start = span["row"] * width + span["start_col"]
                protected.update(range(start, start + span["end_col"] - span["start_col"]))
    labs = _recipe_labs(document)
    recipes = {recipe["index"]: recipe for recipe in document["recipes"]}
    target_lab_cache = {}
    removed = set()
    for source in sorted(candidates, key=lambda index: (counts[index], index)):
        offsets = offsets_by_recipe[source]
        reason = None
        if any(offset in protected for offset in offsets):
            reason = "protected_region"
        elif _has_thin_structure(offsets, width, height):
            reason = "thin_structure"
        elif any(
            offset % width in {0, width - 1}
            or offset // width in {0, height - 1}
            or any(cells[n] == 0 for n in _neighbors(offset, width, height))
            for offset in offsets
        ):
            reason = "silhouette_boundary"
        best = None
        if reason is None:
            contact = Counter()
            for offset in offsets:
                contact.update(
                    {
                        cells[n]
                        for n in _neighbors(offset, width, height)
                        if cells[n] and cells[n] != source
                    }
                )
            for target, touching in sorted(contact.items()):
                # Do not chain rare colors through intermediate colors.
                if target in candidates or touching / len(offsets) < 0.6:
                    continue
                if [c["strands"] for c in recipes[source]["components"]] != [
                    c["strands"] for c in recipes[target]["components"]
                ]:
                    continue
                distance = float(delta_e_2000_lab(labs[source], labs[target]))
                if distance > max_delta_e00:
                    continue
                increases = []
                for offset in offsets:
                    color = sample.display_srgb[offset]
                    if color is None:
                        break
                    if color not in target_lab_cache:
                        target_lab_cache[color] = srgb_hex_to_lab([color])[0]
                    original = target_lab_cache[color]
                    increases.append(
                        float(delta_e_2000_lab(original, labs[target]))
                        - float(delta_e_2000_lab(original, labs[source]))
                    )
                if len(increases) != len(offsets) or max(increases) > max_error_increase:
                    continue
                score = (distance, -touching, target)
                if best is None or score < best[0]:
                    best = (score, target, max(increases))
            if best is None:
                reason = "no_safe_neighbor"
        if reason:
            report["retained"].append(
                {"recipe_index": source, "stitches": len(offsets), "reason": reason}
            )
            continue
        _, target, error_increase = best
        for offset in offsets:
            cells[offset] = target
        removed.add(source)
        report["merges"].append(
            {
                "from_recipe_index": source,
                "to_recipe_index": target,
                "stitches": len(offsets),
                "delta_e00": round(best[0][0], 4),
                "max_error_increase": round(error_increase, 4),
            }
        )
        report["changed_stitches"] += len(offsets)
    if removed:
        removed_ids = {recipes[index]["id"] for index in removed}
        optimized["recipes"] = [r for r in optimized["recipes"] if r["index"] not in removed]
        optimized["legend"] = [r for r in optimized["legend"] if r["recipe_id"] not in removed_ids]
        report["colors_after"] -= len(removed)
    validate_document(optimized).raise_for_errors()
    return RareColorResult(optimized, report)
