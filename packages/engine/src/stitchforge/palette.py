from __future__ import annotations

import hashlib
import json
import math
import re
import warnings
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .fabric import MAX_GRID_CELLS, MAX_GRID_SIDE

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message='"SciPy" related API features are not available.*')
    warnings.filterwarnings(
        "ignore", message='"Matplotlib" related API features are not available.*'
    )
    import colour

from .catalog import MaterialCatalog
from .fabric import validate_aida_count
from .imaging import GridSample
from .validation import validate_document

_HEX_COLOR = re.compile(r"^#[0-9A-F]{6}$")
MAX_REPRESENTATIVE_COLORS = 512
MAX_CANDIDATE_MATERIALS = 128


class PaletteConstraintError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaletteQuality:
    weighted_mean_delta_e00: float
    p95_delta_e00: float
    max_delta_e00: float
    nonempty_cells: int
    representative_colors: int
    candidate_materials: int
    selected_materials: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PaletteResult:
    selected_material_ids: tuple[str, ...]
    cells: tuple[int, ...]
    quality: PaletteQuality
    assignment_by_color: dict[str, str]
    algorithm: str = "material-greedy-ciede2000-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "selected_material_ids": list(self.selected_material_ids),
            "cells": list(self.cells),
            "quality": self.quality.as_dict(),
            "assignment_by_color": dict(sorted(self.assignment_by_color.items())),
        }


def _colors_to_rgb(colors: list[str]) -> np.ndarray:
    return np.asarray(
        [[int(color[offset : offset + 2], 16) / 255 for offset in (1, 3, 5)] for color in colors],
        dtype=np.float64,
    )


def srgb_hex_to_lab(colors: list[str]) -> np.ndarray:
    if any(_HEX_COLOR.fullmatch(color) is None for color in colors):
        raise ValueError("颜色必须是大写 #RRGGBB")
    return colour.XYZ_to_Lab(colour.sRGB_to_XYZ(_colors_to_rgb(colors)))


def lab_to_srgb_hex(colors: np.ndarray) -> tuple[str, ...]:
    values = np.asarray(colors, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 3 or not np.all(np.isfinite(values)):
        raise ValueError("Lab 颜色必须是有限的 N×3 数组")
    srgb = colour.XYZ_to_sRGB(colour.Lab_to_XYZ(values))
    encoded = np.clip(np.floor(srgb * 255.0 + 0.5), 0, 255).astype(np.uint8)
    return tuple(f"#{red:02X}{green:02X}{blue:02X}" for red, green, blue in encoded)


def delta_e_2000_lab(first: Any, second: Any) -> np.ndarray:
    return np.asarray(colour.delta_E(first, second, method="CIE 2000"), dtype=np.float64)


def _distance_matrix(target_lab: np.ndarray, material_lab: np.ndarray) -> np.ndarray:
    return delta_e_2000_lab(target_lab[:, np.newaxis, :], material_lab[np.newaxis, :, :])


def grid_sample_from_dict(value: Any) -> GridSample:
    if not isinstance(value, dict) or value.get("schema_version") != (
        "stitchforge-grid-sample-0.1.0"
    ):
        raise ValueError("网格采样文件版本无效")
    width, height = value.get("width"), value.get("height")
    coverage = value.get("coverage_ppm")
    colors = value.get("display_srgb")
    threshold = value.get("coverage_threshold_ppm")
    if not isinstance(width, int) or not isinstance(height, int):
        raise ValueError("网格采样宽高必须是整数")
    if (
        not 1 <= width <= MAX_GRID_SIDE
        or not 1 <= height <= MAX_GRID_SIDE
        or width * height > MAX_GRID_CELLS
    ):
        raise ValueError("网格采样尺寸超出限制")
    if not isinstance(coverage, list) or not isinstance(colors, list):
        raise ValueError("网格采样数组缺失")
    if len(coverage) != width * height or len(colors) != width * height:
        raise ValueError("网格采样数组长度与宽高不一致")
    if not isinstance(threshold, int) or not 0 <= threshold <= 1_000_000:
        raise ValueError("coverage_threshold_ppm 超出范围")
    if any(not isinstance(item, int) or not 0 <= item <= 1_000_000 for item in coverage):
        raise ValueError("coverage_ppm 包含无效值")
    if any(
        item is not None and (not isinstance(item, str) or not _HEX_COLOR.fullmatch(item))
        for item in colors
    ):
        raise ValueError("display_srgb 包含无效颜色")
    for position, (coverage_value, color) in enumerate(zip(coverage, colors, strict=True)):
        if (coverage_value < threshold) != (color is None):
            raise ValueError(f"格 {position} 的覆盖率和颜色留空状态不一致")
    source_hash = value.get("source_sha256")
    if source_hash is not None and (
        not isinstance(source_hash, str) or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
    ):
        raise ValueError("source_sha256 无效")
    source_locator = value.get("source_locator")
    if source_locator is not None and not isinstance(source_locator, str):
        raise ValueError("source_locator 无效")
    return GridSample(
        width=width,
        height=height,
        coverage_threshold_ppm=threshold,
        coverage_ppm=tuple(coverage),
        display_srgb=tuple(colors),
        source_sha256=source_hash,
        source_locator=source_locator,
    )


def load_grid_sample(path: str | Path) -> GridSample:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"网格采样不是有效 UTF-8 JSON: {error}") from error
    return grid_sample_from_dict(value)


def select_palette(
    sample: GridSample,
    catalog: MaterialCatalog,
    *,
    color_budget: int,
    required_material_ids: tuple[str, ...] = (),
    minimum_gain: float = 0.05,
) -> PaletteResult:
    if not 1 <= color_budget <= 64:
        raise PaletteConstraintError("颜色预算必须为 1–64")
    if minimum_gain < 0 or not math.isfinite(minimum_gain):
        raise PaletteConstraintError("minimum_gain 必须是非负有限数")
    nonempty = [
        (color, coverage)
        for color, coverage in zip(sample.display_srgb, sample.coverage_ppm, strict=True)
        if color is not None
    ]
    if not nonempty:
        raise PaletteConstraintError("全空网格没有可配色针格")

    catalog_by_id = catalog.material_by_id()
    matchable = sorted(catalog.matchable_materials(), key=lambda item: item["id"])
    matchable_by_id = {item["id"]: item for item in matchable}
    required = tuple(sorted(set(required_material_ids)))
    missing_required = [item for item in required if item not in catalog_by_id]
    unusable_required = [
        item for item in required if item in catalog_by_id and item not in matchable_by_id
    ]
    if missing_required:
        raise PaletteConstraintError(f"必选材料不存在: {missing_required}")
    if unusable_required:
        raise PaletteConstraintError(f"必选材料不可用于自动匹配: {unusable_required}")
    if len(required) > color_budget:
        raise PaletteConstraintError("PALETTE_CONSTRAINT_CONFLICT: 必选材料数超过颜色预算")
    if not matchable:
        raise PaletteConstraintError("材料目录没有可用且带显示色的材料")

    weights_by_color: defaultdict[str, int] = defaultdict(int)
    for color, coverage in nonempty:
        weights_by_color[color] += coverage
    representative_colors = sorted(
        weights_by_color,
        key=lambda color: (-weights_by_color[color], color),
    )[:MAX_REPRESENTATIVE_COLORS]
    representative_weights = np.asarray(
        [weights_by_color[color] for color in representative_colors], dtype=np.float64
    )
    representative_weights /= representative_weights.sum()
    representative_lab = srgb_hex_to_lab(representative_colors)

    all_material_colors = [item["display_srgb"] for item in matchable]
    all_material_lab = srgb_hex_to_lab(all_material_colors)
    all_distances = _distance_matrix(representative_lab, all_material_lab)
    if len(matchable) > MAX_CANDIDATE_MATERIALS:
        proximity = all_distances.min(axis=0)
        ranked = sorted(
            range(len(matchable)),
            key=lambda index: (float(proximity[index]), matchable[index]["id"]),
        )
        required_indices = {index for index, item in enumerate(matchable) if item["id"] in required}
        chosen_indices = list(sorted(required_indices))
        chosen_indices.extend(
            index
            for index in ranked
            if index not in required_indices and len(chosen_indices) < MAX_CANDIDATE_MATERIALS
        )
        chosen_indices.sort(key=lambda index: matchable[index]["id"])
        candidates = [matchable[index] for index in chosen_indices]
        distances = all_distances[:, chosen_indices]
    else:
        candidates = matchable
        distances = all_distances

    candidate_ids = [item["id"] for item in candidates]
    candidate_index = {item: index for index, item in enumerate(candidate_ids)}
    selected = [candidate_index[item] for item in required]
    if not selected:
        total_error = (distances * representative_weights[:, np.newaxis]).sum(axis=0)
        selected = [
            min(
                range(len(candidates)),
                key=lambda index: (total_error[index], candidate_ids[index]),
            )
        ]

    while len(selected) < color_budget:
        current = distances[:, selected].min(axis=1)
        best_index: int | None = None
        best_gain = -1.0
        for index, material_id in enumerate(candidate_ids):
            if index in selected:
                continue
            gain = float(
                (
                    representative_weights * (current - np.minimum(current, distances[:, index]))
                ).sum()
            )
            if gain > best_gain + 1e-12 or (
                abs(gain - best_gain) <= 1e-12
                and best_index is not None
                and material_id < candidate_ids[best_index]
            ):
                best_gain = gain
                best_index = index
        if best_index is None or best_gain < minimum_gain:
            break
        selected.append(best_index)

    selected_material_ids = tuple(sorted(candidate_ids[index] for index in selected))
    selected_materials = [matchable_by_id[item] for item in selected_material_ids]
    selected_lab = srgb_hex_to_lab([item["display_srgb"] for item in selected_materials])
    unique_colors = sorted(weights_by_color)
    unique_lab = srgb_hex_to_lab(unique_colors)
    final_distances = _distance_matrix(unique_lab, selected_lab)
    assignment_index = np.argmin(final_distances, axis=1)
    color_to_material = {
        color: selected_material_ids[int(material_index)]
        for color, material_index in zip(unique_colors, assignment_index, strict=True)
    }
    recipe_index_by_material = {
        material_id: index for index, material_id in enumerate(selected_material_ids, start=1)
    }
    cells = tuple(
        0 if color is None else recipe_index_by_material[color_to_material[color]]
        for color in sample.display_srgb
    )
    distance_by_color = {
        color: float(final_distances[index, assignment_index[index]])
        for index, color in enumerate(unique_colors)
    }
    cell_distances = np.asarray([distance_by_color[color] for color, _ in nonempty])
    cell_weights = np.asarray([coverage for _, coverage in nonempty], dtype=np.float64)
    quality = PaletteQuality(
        weighted_mean_delta_e00=float(np.average(cell_distances, weights=cell_weights)),
        p95_delta_e00=float(np.quantile(cell_distances, 0.95, method="inverted_cdf")),
        max_delta_e00=float(cell_distances.max()),
        nonempty_cells=len(nonempty),
        representative_colors=len(representative_colors),
        candidate_materials=len(candidates),
        selected_materials=len(selected_material_ids),
    )
    return PaletteResult(selected_material_ids, cells, quality, color_to_material)


def _nearest_cluster_labels(colors: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Bound assignment memory instead of allocating colors x centers x RGB."""
    labels = np.empty(len(colors), dtype=np.int32)
    for start in range(0, len(colors), 8192):
        chunk = colors[start : start + 8192]
        distances = np.sum((chunk[:, np.newaxis, :] - centers[np.newaxis, :, :]) ** 2, axis=2)
        labels[start : start + len(chunk)] = np.argmin(distances, axis=1)
    return labels


def select_clustered_palette(
    sample: GridSample,
    catalog: MaterialCatalog,
    *,
    color_budget: int,
    required_material_ids: tuple[str, ...] = (),
    iterations: int = 50,
) -> PaletteResult:
    """Group source colors before mapping the groups to available materials.

    The greedy selector minimizes global cell error and can spend much of a
    limited palette on densely sampled, near-identical shades. This selector
    first builds deterministic Lab-space color groups. Rare but visually
    distinct colors can therefore retain a palette slot, while small rendering
    variations share one material.
    """
    if not 1 <= color_budget <= 64:
        raise PaletteConstraintError("颜色预算必须为 1–64")
    if not 1 <= iterations <= 100:
        raise PaletteConstraintError("聚类迭代次数必须为 1–100")

    catalog_by_id = catalog.material_by_id()
    matchable = sorted(catalog.matchable_materials(), key=lambda item: item["id"])
    matchable_by_id = {item["id"]: item for item in matchable}
    required = tuple(sorted(set(required_material_ids)))
    missing_required = [item for item in required if item not in catalog_by_id]
    unusable_required = [
        item for item in required if item in catalog_by_id and item not in matchable_by_id
    ]
    if missing_required:
        raise PaletteConstraintError(f"必选材料不存在: {missing_required}")
    if unusable_required:
        raise PaletteConstraintError(f"必选材料不可用于自动匹配: {unusable_required}")
    if len(required) > color_budget:
        raise PaletteConstraintError("PALETTE_CONSTRAINT_CONFLICT: 必选材料数超过颜色预算")
    if not matchable:
        raise PaletteConstraintError("材料目录没有可用且带显示色的材料")

    weights_by_color: defaultdict[str, int] = defaultdict(int)
    for color, coverage in zip(sample.display_srgb, sample.coverage_ppm, strict=True):
        if color is not None:
            weights_by_color[color] += coverage
    if not weights_by_color:
        raise PaletteConstraintError("全空网格没有可配色针格")

    # Clustering needs the full hue distribution. Keeping only the most common
    # colors would discard small eyes, outlines, flowers, and props in favor of
    # hundreds of near-identical fur or sky shades.
    representative_colors = sorted(weights_by_color)
    weights = np.asarray(
        [weights_by_color[color] for color in representative_colors], dtype=np.float64
    )
    representative_lab = srgb_hex_to_lab(representative_colors)
    cluster_budget = (
        color_budget if len(required) == color_budget else max(1, color_budget - len(required))
    )
    cluster_count = min(cluster_budget, len(representative_colors))

    weighted_mean = np.average(representative_lab, axis=0, weights=weights)
    first = min(
        range(len(representative_colors)),
        key=lambda index: (
            -float(np.sum((representative_lab[index] - weighted_mean) ** 2)),
            representative_colors[index],
        ),
    )
    centers = [representative_lab[first]]
    distances = np.sum((representative_lab - centers[0]) ** 2, axis=1)
    for _ in range(1, cluster_count):
        next_index = int(np.argmax(distances))  # Sorted colors preserve tie-breaking.
        centers.append(representative_lab[next_index])
        distances = np.minimum(distances, np.sum((representative_lab - centers[-1]) ** 2, axis=1))
    center_array = np.asarray(centers, dtype=np.float64)

    labels = np.zeros(len(representative_colors), dtype=np.int32)
    for _ in range(iterations):
        labels = _nearest_cluster_labels(representative_lab, center_array)
        updated = center_array.copy()
        for cluster_index in range(cluster_count):
            members = labels == cluster_index
            if np.any(members):
                updated[cluster_index] = np.average(
                    representative_lab[members],
                    axis=0,
                    weights=weights[members],
                )
        if float(np.max(np.abs(updated - center_array))) < 0.01:
            center_array = updated
            break
        center_array = updated

    if len(required) == color_budget:
        required_lab = srgb_hex_to_lab([matchable_by_id[item]["display_srgb"] for item in required])
        nearest_required = np.argmin(_distance_matrix(center_array, required_lab), axis=1)
        material_by_cluster = [required[int(index)] for index in nearest_required]
        selected_material_ids = required
    else:
        material_lab = srgb_hex_to_lab([item["display_srgb"] for item in matchable])
        center_distances = _distance_matrix(center_array, material_lab)
        nearest_material = np.argmin(center_distances, axis=1)
        material_by_cluster = [matchable[int(index)]["id"] for index in nearest_material]
        selected_material_ids = tuple(sorted({*required, *material_by_cluster}))
    recipe_index_by_material = {
        material_id: index for index, material_id in enumerate(selected_material_ids, start=1)
    }
    color_to_material = {
        color: material_by_cluster[int(label)]
        for color, label in zip(representative_colors, labels, strict=True)
    }

    cells = tuple(
        0 if color is None else recipe_index_by_material[color_to_material[color]]
        for color in sample.display_srgb
    )
    unique_colors = sorted(weights_by_color)
    unique_lab = srgb_hex_to_lab(unique_colors)
    assigned_lab = srgb_hex_to_lab(
        [matchable_by_id[color_to_material[color]]["display_srgb"] for color in unique_colors]
    )
    distance_by_color = {
        color: float(distance)
        for color, distance in zip(
            unique_colors,
            delta_e_2000_lab(unique_lab, assigned_lab),
            strict=True,
        )
    }
    cell_distances = np.asarray(
        [distance_by_color[color] for color in sample.display_srgb if color is not None]
    )
    cell_weights = np.asarray(
        [
            coverage
            for color, coverage in zip(sample.display_srgb, sample.coverage_ppm, strict=True)
            if color is not None
        ],
        dtype=np.float64,
    )
    quality = PaletteQuality(
        weighted_mean_delta_e00=float(np.average(cell_distances, weights=cell_weights)),
        p95_delta_e00=float(np.quantile(cell_distances, 0.95, method="inverted_cdf")),
        max_delta_e00=float(cell_distances.max()),
        nonempty_cells=len(cell_distances),
        representative_colors=len(representative_colors),
        candidate_materials=len(matchable),
        selected_materials=len(selected_material_ids),
    )
    return PaletteResult(
        selected_material_ids,
        cells,
        quality,
        color_to_material,
        algorithm="lab-cluster-then-material-ciede2000-v1",
    )


def build_pattern_document(
    sample: GridSample,
    catalog: MaterialCatalog,
    palette: PaletteResult,
    *,
    pattern_id: str,
    title: str,
    author: str,
    fabric_count: int = 14,
    fabric_color: str = "#FFFFFF",
    strands: int | None = None,
    color_budget: int,
    purpose: str,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    if purpose not in {"user_design", "test_fixture"}:
        raise ValueError("purpose 必须是 user_design 或 test_fixture")
    catalog_by_id = catalog.material_by_id()
    selected = [catalog_by_id[item] for item in palette.selected_material_ids]
    if purpose == "user_design" and any(item["color_status"] == "synthetic" for item in selected):
        raise PaletteConstraintError("synthetic 材料只能生成 test_fixture 工程")
    validate_aida_count(fabric_count)
    if strands is not None and (
        isinstance(strands, bool) or not isinstance(strands, int) or not 1 <= strands <= 6
    ):
        raise ValueError("strands 必须是 1–6 的整数")
    timestamp = (created_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    recipes = []
    materials = []
    for index, material in enumerate(selected, start=1):
        materials.append(
            {
                "id": material["id"],
                "brand": material["brand"],
                "series": material["series"],
                "code": material["code"],
                "name": material["name"],
                "color": {
                    "display_srgb": material["display_srgb"],
                    "status": material["color_status"],
                    "source_ref": catalog.source["id"],
                },
            }
        )
        recipes.append(
            {
                "id": f"r{index:03d}",
                "index": index,
                "components": [
                    {
                        "material_id": material["id"],
                        "strands": strands if strands is not None else material["default_strands"],
                    }
                ],
            }
        )
    recipe_id_by_index = {item["index"]: item["id"] for item in recipes}
    config = {
        "algorithm": palette.algorithm,
        "color_budget": color_budget,
        "coverage_threshold_ppm": sample.coverage_threshold_ppm,
        "fabric_count": fabric_count,
        "strands": strands,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sources = [dict(catalog.source)]
    if sample.source_sha256:
        sources.append(
            {
                "id": "src_image",
                "locator": (sample.source_locator or "normalized-image")[:2048],
                "description": "StitchForge sRGB 规范化工作图；哈希绑定到本次网格采样。",
                "permission_status": "private_use",
            }
        )
    document: dict[str, Any] = {
        "schema_version": "0.1.0",
        "required_features": ["full_cross"],
        "metadata": {
            "pattern_id": pattern_id,
            "title": title,
            "author": author,
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "purpose": purpose,
        },
        "grid": {
            "width": sample.width,
            "height": sample.height,
            "origin": "top_left",
            "subgrid_scale": 2,
        },
        "fabric": {
            "type": "aida",
            "count": fabric_count,
            "count_unit": "cells_per_inch",
            "worked_over": 1,
            "display_srgb": fabric_color,
            "margins_mm": {"top": 50, "right": 50, "bottom": 50, "left": 50},
        },
        "materials": materials,
        "recipes": recipes,
        "full_stitches": {
            "encoding": "row-major-u16",
            "top_leg": "slash",
            "cells": list(palette.cells),
        },
        "regions": [],
        "legend": [
            {"recipe_id": recipe_id_by_index[index], "symbol_id": f"s{index:03d}"}
            for index in sorted(set(palette.cells) - {0})
        ],
        "constraints": {"color_budget": color_budget, "metric_profile": "quality_4n_v1"},
        "provenance": {
            "source_asset_hashes": [sample.source_sha256] if sample.source_sha256 else [],
            "engine_version": "stitchforge-0.1.0",
            "catalog_snapshot_id": catalog.snapshot_id,
            "rule_profile": "image_palette_greedy_v1",
            "generation_config_hash": config_hash,
            "random_seed": None,
            "ai_proposal_hash": None,
            "sources": sources,
        },
        "extensions": {
            "stitchforge.palette_quality": {
                "weighted_mean_delta_e00_milli": round(
                    palette.quality.weighted_mean_delta_e00 * 1000
                ),
                "p95_delta_e00_milli": round(palette.quality.p95_delta_e00 * 1000),
                "max_delta_e00_milli": round(palette.quality.max_delta_e00 * 1000),
                "algorithm": "material-greedy-ciede2000-v1",
            }
        },
    }
    validate_document(document).raise_for_errors()
    return document
