from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .fabric import MAX_GRID_SIDE
from .imaging import GridSample, sample_grid, sample_normalized_image
from .palette import delta_e_2000_lab, srgb_hex_to_lab


@dataclass(frozen=True, slots=True)
class BackgroundRemovalReport:
    algorithm: str
    background_display_srgb: str
    delta_e00_threshold: float
    border_samples: int
    original_nonempty_cells: int
    removed_cells: int
    retained_cells: int

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        total = self.original_nonempty_cells
        result["removed_fraction"] = self.removed_cells / total if total else 0.0
        return result


def _border_offsets(width: int, height: int) -> tuple[int, ...]:
    offsets = {col for col in range(width)}
    offsets.update((height - 1) * width + col for col in range(width))
    offsets.update(row * width for row in range(height))
    offsets.update(row * width + width - 1 for row in range(height))
    return tuple(sorted(offsets))


def _median_color(colors: list[str]) -> str:
    channels = np.asarray(
        [[int(color[offset : offset + 2], 16) for offset in (1, 3, 5)] for color in colors],
        dtype=np.uint8,
    )
    red, green, blue = np.median(channels, axis=0).round().astype(np.uint8)
    return f"#{red:02X}{green:02X}{blue:02X}"


def remove_connected_border_background(
    sample: GridSample,
    *,
    delta_e00_threshold: float = 2.0,
    textured_fabric: bool = False,
) -> tuple[GridSample, BackgroundRemovalReport]:
    """Remove background-like cells that are connected to the grid border.

    The background color is estimated from the median border color. Only cells
    connected to an outer edge are removed, so similarly colored highlights or
    pale subject areas enclosed by the design remain stitchable.
    """
    if not np.isfinite(delta_e00_threshold) or not 0 <= delta_e00_threshold <= 50:
        raise ValueError("delta_e00_threshold 必须是 0–50 的有限数")

    border_offsets = _border_offsets(sample.width, sample.height)
    border_colors = [
        sample.display_srgb[offset]
        for offset in border_offsets
        if sample.display_srgb[offset] is not None
    ]
    if not border_colors:
        nonempty = sum(color is not None for color in sample.display_srgb)
        return sample, BackgroundRemovalReport(
            "connected-border-ciede2000-v1",
            "#FFFFFF",
            delta_e00_threshold,
            0,
            nonempty,
            0,
            nonempty,
        )

    background = _median_color(border_colors)
    unique_colors = sorted({color for color in sample.display_srgb if color is not None})
    color_labs = srgb_hex_to_lab(unique_colors)
    background_lab = srgb_hex_to_lab([background])[0]
    if textured_fabric:
        # Learn the variation of the actual cloth, rather than treating every
        # bright weave intersection as a new stitch. Cap tolerance to protect
        # pale subjects; enclosed highlights still cannot be reached by flood fill.
        border_distances = delta_e_2000_lab(srgb_hex_to_lab(border_colors), background_lab)
        delta_e00_threshold = max(
            delta_e00_threshold, min(8.0, float(np.percentile(border_distances, 99)) + 1.5)
        )
    distances = delta_e_2000_lab(color_labs, background_lab)
    background_like = {
        color
        for color, distance in zip(unique_colors, distances, strict=True)
        if float(distance) <= delta_e00_threshold
    }

    removable = [color is None or color in background_like for color in sample.display_srgb]
    removed = [False] * (sample.width * sample.height)
    queue = deque(offset for offset in border_offsets if removable[offset])
    for offset in queue:
        removed[offset] = True

    while queue:
        offset = queue.popleft()
        col, row = offset % sample.width, offset // sample.width
        for neighbor in (
            offset - 1 if col > 0 else -1,
            offset + 1 if col + 1 < sample.width else -1,
            offset - sample.width if row > 0 else -1,
            offset + sample.width if row + 1 < sample.height else -1,
        ):
            if neighbor >= 0 and removable[neighbor] and not removed[neighbor]:
                removed[neighbor] = True
                queue.append(neighbor)

    colors = tuple(
        None if removed[offset] else color for offset, color in enumerate(sample.display_srgb)
    )
    coverage = tuple(
        0 if removed[offset] else value for offset, value in enumerate(sample.coverage_ppm)
    )
    original_nonempty = sum(color is not None for color in sample.display_srgb)
    removed_count = sum(
        flag and color is not None for flag, color in zip(removed, sample.display_srgb, strict=True)
    )
    result = GridSample(
        width=sample.width,
        height=sample.height,
        coverage_threshold_ppm=sample.coverage_threshold_ppm,
        coverage_ppm=coverage,
        display_srgb=colors,
        source_sha256=sample.source_sha256,
        source_locator=sample.source_locator,
    )
    report = BackgroundRemovalReport(
        algorithm=(
            "connected-fabric-ciede2000-v2" if textured_fabric else "connected-border-ciede2000-v1"
        ),
        background_display_srgb=background,
        delta_e00_threshold=delta_e00_threshold,
        border_samples=len(border_colors),
        original_nonempty_cells=original_nonempty,
        removed_cells=removed_count,
        retained_cells=original_nonempty - removed_count,
    )
    return result, report


def trim_blank_margins(
    sample: GridSample,
    *,
    padding: int = 2,
) -> tuple[GridSample, tuple[int, int, int, int] | None]:
    """Trim only empty cells, retaining every stitch and a small working margin.

    Return half-open bounds in the original grid so callers can align the source
    preview and record provenance. Leave small margin reductions alone.
    """
    if padding < 0:
        raise ValueError("padding 不能为负数")
    occupied = [i for i, color in enumerate(sample.display_srgb) if color is not None]
    if not occupied:
        return sample, None
    left = max(0, min(i % sample.width for i in occupied) - padding)
    top = max(0, min(i // sample.width for i in occupied) - padding)
    right = min(sample.width, max(i % sample.width for i in occupied) + padding + 1)
    bottom = min(sample.height, max(i // sample.width for i in occupied) + padding + 1)
    if (right - left) * (bottom - top) >= sample.width * sample.height * 0.8:
        return sample, None
    offsets = [row * sample.width + col for row in range(top, bottom) for col in range(left, right)]
    return replace(
        sample,
        width=right - left,
        height=bottom - top,
        display_srgb=tuple(sample.display_srgb[i] for i in offsets),
        coverage_ppm=tuple(sample.coverage_ppm[i] for i in offsets),
    ), (left, top, right, bottom)


def sample_fabric_foreground(
    source: Path,
    *,
    width: int,
    height: int,
) -> tuple[GridSample, BackgroundRemovalReport]:
    """Mask cloth before reducing to stitches so edge cells don't mix with cloth.

    Work at a bounded higher resolution. Alpha-aware sampling then decides cell
    occupancy from foreground coverage, retaining the original source reference.
    """
    original = sample_normalized_image(source, width=width, height=height)
    before = sum(color is not None for color in original.display_srgb)
    with Image.open(source) as image:
        scale = min(1, MAX_GRID_SIDE / max(image.size))
        work_width = max(width, round(image.width * scale))
        work_height = max(height, round(image.height * scale))
    detailed = sample_normalized_image(source, width=work_width, height=work_height)
    cleaned, report = remove_connected_border_background(detailed, textured_fabric=True)
    if not report.retained_cells:
        return original, replace(
            report,
            original_nonempty_cells=before,
            removed_cells=0,
            retained_cells=before,
        )
    masked = Image.new("RGBA", (cleaned.width, cleaned.height))
    masked.putdata(
        [
            (*bytes.fromhex(color[1:]), round(coverage * 255 / 1_000_000))
            if color
            else (0, 0, 0, 0)
            for color, coverage in zip(cleaned.display_srgb, cleaned.coverage_ppm, strict=True)
        ]
    )
    result = sample_grid(
        masked,
        width=width,
        height=height,
        source_sha256=original.source_sha256,
        source_locator=original.source_locator,
    )
    masked.close()
    after = sum(color is not None for color in result.display_srgb)
    return result, replace(
        report,
        algorithm="masked-fabric-before-sampling-v2",
        original_nonempty_cells=before,
        removed_cells=before - after,
        retained_cells=after,
    )
