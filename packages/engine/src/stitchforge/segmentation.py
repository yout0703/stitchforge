from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .imaging import GridSample
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
        raise ValueError("网格边缘没有可用于估计底色的非空针格")

    background = _median_color(border_colors)
    unique_colors = sorted({color for color in sample.display_srgb if color is not None})
    color_labs = srgb_hex_to_lab(unique_colors)
    background_lab = srgb_hex_to_lab([background])[0]
    distances = delta_e_2000_lab(color_labs, background_lab)
    background_like = {
        color
        for color, distance in zip(unique_colors, distances, strict=True)
        if float(distance) <= delta_e00_threshold
    }

    removable = [color in background_like for color in sample.display_srgb]
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
        None if removed[offset] else color
        for offset, color in enumerate(sample.display_srgb)
    )
    coverage = tuple(
        0 if removed[offset] else value
        for offset, value in enumerate(sample.coverage_ppm)
    )
    original_nonempty = sum(color is not None for color in sample.display_srgb)
    removed_count = sum(removed)
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
        algorithm="connected-border-ciede2000-v1",
        background_display_srgb=background,
        delta_e00_threshold=delta_e00_threshold,
        border_samples=len(border_colors),
        original_nonempty_cells=original_nonempty,
        removed_cells=removed_count,
        retained_cells=original_nonempty - removed_count,
    )
    return result, report
