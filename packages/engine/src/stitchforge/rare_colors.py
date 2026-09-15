"""Conservative whole-recipe reduction after local speckle cleanup."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .imaging import GridSample
from .optimize import _neighbors, _recipe_labs
from .palette import delta_e_2000_lab, srgb_hex_to_lab
from .validation import validate_document


@dataclass(frozen=True, slots=True)
class RareColorResult:
    document: dict[str, Any]
    report: dict[str, Any]


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
