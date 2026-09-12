from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from .imaging import GridSample
from .palette import lab_to_srgb_hex, srgb_hex_to_lab


@dataclass(frozen=True, slots=True)
class RenderedStitchRecoveryReport:
    algorithm: str
    lightness_lift: float
    adjusted_colors: int
    adjusted_cells: int
    maximum_applied_lift: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def recover_rendered_stitch_colors(
    sample: GridSample,
    *,
    lightness_lift: float = 5.0,
) -> tuple[GridSample, RenderedStitchRecoveryReport]:
    """Compensate pale floss colors recovered from a rendered stitch preview.

    Thread valleys and simulated illumination make cream and gray floss appear
    darker than its intended material color. The correction is limited to
    light, low-chroma cells; saturated accents and dark outlines stay intact.
    """
    if not math.isfinite(lightness_lift) or not 0 <= lightness_lift <= 12:
        raise ValueError("lightness_lift 必须是 0–12 的有限数")

    unique_colors = sorted({color for color in sample.display_srgb if color is not None})
    if not unique_colors:
        report = RenderedStitchRecoveryReport(
            algorithm="rendered-stitch-neutral-lightness-v1",
            lightness_lift=lightness_lift,
            adjusted_colors=0,
            adjusted_cells=0,
            maximum_applied_lift=0.0,
        )
        return sample, report

    labs = srgb_hex_to_lab(unique_colors)
    chroma = np.hypot(labs[:, 1], labs[:, 2])
    lightness_weight = np.clip((labs[:, 0] - 62.0) / 15.0, 0.0, 1.0)
    neutral_weight = np.clip((26.0 - chroma) / 12.0, 0.0, 1.0)
    applied_lift = lightness_lift * lightness_weight * neutral_weight
    adjusted_labs = labs.copy()
    adjusted_labs[:, 0] = np.minimum(100.0, adjusted_labs[:, 0] + applied_lift)
    adjusted_hex = lab_to_srgb_hex(adjusted_labs)
    color_map = dict(zip(unique_colors, adjusted_hex, strict=True))
    changed = {
        color
        for color, replacement in zip(unique_colors, adjusted_hex, strict=True)
        if color != replacement
    }

    result = GridSample(
        width=sample.width,
        height=sample.height,
        coverage_threshold_ppm=sample.coverage_threshold_ppm,
        coverage_ppm=sample.coverage_ppm,
        display_srgb=tuple(
            None if color is None else color_map[color] for color in sample.display_srgb
        ),
        source_sha256=sample.source_sha256,
        source_locator=sample.source_locator,
    )
    report = RenderedStitchRecoveryReport(
        algorithm="rendered-stitch-neutral-lightness-v1",
        lightness_lift=lightness_lift,
        adjusted_colors=len(changed),
        adjusted_cells=sum(color in changed for color in sample.display_srgb),
        maximum_applied_lift=float(applied_lift.max(initial=0.0)),
    )
    return result, report
