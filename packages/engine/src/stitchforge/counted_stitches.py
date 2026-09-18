"""Conservative recovery of a visible stitch lattice in small, coloured motifs.

This is a separate path from photographic pixel conversion. It only accepts a
light neutral background and a strong, matching period on both image axes.
Ambiguous images return None and retain the normal conversion workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .imaging import GridSample


@dataclass(frozen=True, slots=True)
class CountedStitches:
    sample: GridSample
    crop_box: tuple[int, int, int, int]
    report: dict


def _floss_distance(colour: np.ndarray, representative: np.ndarray) -> float:
    if (
        max(colour.max(), representative.max()) / max(0.01, min(colour.max(), representative.max()))
    ) >= 1.8:
        return float("inf")
    return float(
        np.linalg.norm(
            colour / max(0.01, colour.max()) - representative / max(0.01, representative.max())
        )
    )


def _agreed_period(
    horizontal: tuple[int, float] | None,
    vertical: tuple[int, float] | None,
) -> int | None:
    if horizontal is None or vertical is None:
        return None
    px, py = horizontal[0], vertical[0]
    if abs(px - py) / max(px, py) > 0.12:
        return None
    return int(round((px + py) / 2))


def _period(signal: np.ndarray) -> tuple[int, float] | None:
    window = max(9, (len(signal) // 6) | 1)
    smooth = np.convolve(
        np.pad(signal, window // 2, mode="edge"), np.ones(window) / window, mode="valid"
    )
    residual = signal - smooth
    if residual.std() < 0.008:
        return None
    correlation = np.correlate(residual, residual, mode="full")[len(signal) - 1 :]
    correlation /= correlation[0]
    peaks = [
        lag
        for lag in range(8, min(128, len(signal) // 4))
        if correlation[lag] > correlation[lag - 1]
        and correlation[lag] >= correlation[lag + 1]
        and correlation[lag] >= 0.50
    ]
    if not peaks:
        return None
    best = max(correlation[lag] for lag in peaks)
    # Prefer the fundamental over its harmonics.
    lag = next(lag for lag in peaks if correlation[lag] >= best * 0.9)
    return lag, float(correlation[lag])


def _period_measurement(source: str, signal: np.ndarray) -> dict:
    horizontal = _period(signal.mean(axis=0))
    vertical = _period(signal.mean(axis=1))
    return {
        "source": source,
        "period_x": horizontal[0] if horizontal is not None else None,
        "period_y": vertical[0] if vertical is not None else None,
        "confidence_x_ppm": round(horizontal[1] * 1_000_000) if horizontal is not None else None,
        "confidence_y_ppm": round(vertical[1] * 1_000_000) if vertical is not None else None,
        "agreed_period": _agreed_period(horizontal, vertical),
    }


def _edge_lattice_measurements(luma: np.ndarray) -> list[dict]:
    """Measure clean cloth bands without averaging through a large stitched subject."""
    height, width = luma.shape
    band_y = min(256, max(80, height // 10))
    band_x = min(256, max(80, width // 10))
    texture = 1 - luma
    regions = {
        "bare_fabric_top": texture[:band_y, :],
        "bare_fabric_bottom": texture[-band_y:, :],
        "bare_fabric_left": texture[:, :band_x],
        "bare_fabric_right": texture[:, -band_x:],
    }
    return [_period_measurement(source, signal) for source, signal in regions.items()]


def _consensus_edge_period(measurements: list[dict]) -> int | None:
    """Require the same two-axis pitch in at least two independent cloth bands."""
    periods = [
        int(item["agreed_period"])
        for item in measurements
        if item["source"].startswith("bare_fabric_") and item["agreed_period"] is not None
    ]
    best: list[int] = []
    for period in periods:
        group = [value for value in periods if abs(value - period) / max(value, period) <= 0.12]
        if len(group) > len(best):
            best = group
    if len(best) < 2:
        return None
    return int(round(float(np.median(best))))


def _grid_candidates(width: int, height: int, period: int | None) -> list[dict]:
    if period is None or period < 8:
        return []
    grid_width = max(1, round(width / period))
    grid_height = max(1, round(height / period))
    candidates = [
        {
            "grid_width": grid_width,
            "grid_height": grid_height,
            "lattice_period": period,
            "sampling": "one_stitch_per_cell",
        }
    ]
    if max(grid_width, grid_height) < 160 and period >= 16:
        candidates.append(
            {
                "grid_width": max(1, round(width / (period / 2))),
                "grid_height": max(1, round(height / (period / 2))),
                "lattice_period": period,
                "sampling": "half_pitch_detail_preserving",
            }
        )
    return candidates


def recover_counted_stitches(
    source: Path,
    *,
    period_hint: tuple[int, int] | None = None,
    colour_tolerance: float = 0.12,
) -> CountedStitches | None:
    with Image.open(source) as image:
        original_size = image.size
        preview = image.convert("RGBA")
        preview.thumbnail((1536, 1536))
        pixels = np.asarray(preview, dtype=np.float64) / 255
    rgb = pixels[:, :, :3]
    if np.mean(pixels[:, :, 3] < 0.99) > 0.01 or min(rgb.shape[:2]) < 80:
        return None
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    background = np.median(border, axis=0)
    if background.min() < 0.65 or np.ptp(background) > 0.16:
        return None
    if np.quantile(np.linalg.norm(border - background, axis=1), 0.95) > 0.18:
        return None
    chroma = np.ptp(rgb, axis=2)
    threshold = max(0.22, float(np.quantile(np.ptp(border, axis=1), 0.99)) + 0.12)
    mask = chroma > threshold
    if not 0.015 < mask.mean() < 0.45:
        return None
    # Dark eyes are real stitches too. Limit this path to mainly coloured motifs;
    # large neutral subjects still need a different segmentation strategy.
    dark_neutral = (chroma <= threshold) & (rgb.max(axis=2) < background.max() - 0.30)
    if dark_neutral.sum() > mask.sum() * 0.25:
        return None
    ys, xs = np.nonzero(mask)
    left, right = np.quantile(xs, [0.001, 0.999])
    top, bottom = np.quantile(ys, [0.001, 0.999])
    right, bottom = right + 1, bottom + 1
    motif = mask[int(top) : int(bottom), int(left) : int(right)]
    horizontal, vertical = _period(motif.mean(axis=0)), _period(motif.mean(axis=1))
    period_source = "colour_coverage"
    if horizontal is None or vertical is None:
        # Saturated yellow thread can fill the colour mask between crosses. Its
        # thread valleys still repeat in luminance. Require evidence on both
        # axes inside the coloured motif, not just the weave of the bare cloth.
        valleys = ((1 - rgb.mean(axis=2)) * mask)[int(top) : int(bottom), int(left) : int(right)]
        horizontal, vertical = _period(valleys.mean(axis=0)), _period(valleys.mean(axis=1))
        period_source = "thread_luminance"
        if period_hint is not None:
            horizontal = (period_hint[0], horizontal[1] if horizontal else 0.0)
            vertical = (period_hint[1], vertical[1] if vertical else 0.0)
            period_source = "candidate_from_partial_evidence"
        if horizontal is None or vertical is None:
            return None
    px, py = horizontal[0], vertical[0]
    if abs(px - py) / max(px, py) > 0.12:
        return None
    columns, rows = round((right - left) / px), round((bottom - top) / py)
    if not (4 <= columns <= 64 and 4 <= rows <= 64):
        return None
    pitch_x, pitch_y = (right - left) / columns, (bottom - top) / rows
    if abs(pitch_x / px - 1) > 0.08 or abs(pitch_y / py - 1) > 0.08:
        return None
    # Require room for the working margin; don't invent pixels outside the source.
    box = (left - 2 * pitch_x, top - 2 * pitch_y, right + 2 * pitch_x, bottom + 2 * pitch_y)
    height, width = mask.shape
    if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
        return None
    outside = mask.copy()
    outside[int(box[1]) : int(box[3]) + 1, int(box[0]) : int(box[2]) + 1] = False
    if outside.any():
        return None

    # Include neutral dark stitches only inside the same subject extent.
    dark_outside = dark_neutral.copy()
    dark_outside[int(top) : int(bottom) + 1, int(left) : int(right) + 1] = False
    # A few dark cloth holes/JPEG pixels are not a detached eye or outline.
    if dark_outside.sum() > px * py * 0.10:
        return None
    foreground = mask | dark_neutral

    colours: list[np.ndarray | None] = []
    occupancies = []
    for row in range(rows):
        for col in range(columns):
            # Sample the inside of each actual stitch, excluding mixed cloth edges.
            x0, x1 = (round(left + (col + inset) * pitch_x) for inset in (0.16, 0.84))
            y0, y1 = (round(top + (row + inset) * pitch_y) for inset in (0.16, 0.84))
            cell_mask = foreground[y0:y1, x0:x1]
            occupancy = float(cell_mask.mean())
            occupancies.append(occupancy)
            colours.append(
                np.median(rgb[y0:y1, x0:x1][cell_mask], axis=0) if occupancy > 0.35 else None
            )
    # Unclear half-cells indicate a rotated, misaligned or non-stitch image.
    if np.mean([(0.12 < value < 0.70) for value in occupancies]) > 0.12:
        return None
    occupied = np.array([colour is not None for colour in colours]).reshape(rows, columns)
    if not 0.25 < occupied.mean() < 0.9:
        return None
    # A light enclosed cell could be a white stitch. Defer instead of losing it.
    for row in range(1, rows - 1):
        for col in range(1, columns - 1):
            if not occupied[row, col] and all(
                (
                    occupied[row - 1, col],
                    occupied[row + 1, col],
                    occupied[row, col - 1],
                    occupied[row, col + 1],
                )
            ):
                return None

    groups: list[list[np.ndarray]] = []
    indices = []
    for colour in colours:
        if colour is None:
            indices.append(None)
            continue
        # Compare channel proportions to merge shadows of the same coloured floss.
        representatives = [np.median(g, axis=0) for g in groups]
        distances = [_floss_distance(colour, representative) for representative in representatives]
        index = int(np.argmin(distances)) if distances else 0
        if not distances or distances[index] > colour_tolerance:
            index = len(groups)
            groups.append([])
        groups[index].append(colour)
        indices.append(index)
    if not 1 <= len(groups) <= 16:
        return None
    # Online medians move as new stitches arrive. Reconcile groups afterwards so
    # early highlights do not leave duplicate thread colours behind.
    while len(groups) > 1:
        representatives = [np.median(g, axis=0) for g in groups]
        distance, first, second = min(
            (_floss_distance(a, b), i, j)
            for i, a in enumerate(representatives)
            for j, b in enumerate(representatives)
            if i < j
        )
        if distance > colour_tolerance:
            break
        groups[first].extend(groups.pop(second))
        indices = [
            None if i is None else first if i == second else i - (i > second) for i in indices
        ]
    if len(groups) > 8:
        return None
    palette = [
        "#"
        + "".join(
            f"{channel:02X}" for channel in np.round(np.median(group, axis=0) * 255).astype(int)
        )
        for group in groups
    ]
    grid_width, grid_height = columns + 4, rows + 4
    cells: list[str | None] = [None] * (grid_width * grid_height)
    for offset, index in enumerate(indices):
        if index is not None:
            row, col = divmod(offset, columns)
            cells[(row + 2) * grid_width + col + 2] = palette[index]
    sample = GridSample(
        width=grid_width,
        height=grid_height,
        coverage_threshold_ppm=500_000,
        display_srgb=tuple(cells),
        coverage_ppm=tuple(1_000_000 if c else 0 for c in cells),
    )
    crop_box = tuple(
        round(value * size / preview_size)
        for value, size, preview_size in zip(
            box,
            original_size * 2,
            (width, height) * 2,
            strict=True,
        )
    )
    return CountedStitches(
        sample,
        crop_box,
        {
            "algorithm": "counted-stitch-lattice-v2",
            "subject_columns": columns,
            "subject_rows": rows,
            "stitch_count": int(occupied.sum()),
            "thread_colours": len(palette),
            "period_x_pixels": px,
            "period_y_pixels": py,
            "analysis_size": [width, height],
            "period_source": period_source,
            "periodicity_x_ppm": round(horizontal[1] * 1_000_000),
            "periodicity_y_ppm": round(vertical[1] * 1_000_000),
            "crop_box": list(crop_box),
            "padding": 2,
            "colour_tolerance_ppm": round(colour_tolerance * 1_000_000),
        },
    )


def inspect_stitch_evidence(source: Path) -> dict:
    """Keep partial evidence instead of silently falling back to a guessed size."""
    with Image.open(source) as image:
        original_size = image.size
        image = image.convert("RGB")
        image.thumbnail((1536, 1536))
        rgb = np.asarray(image, dtype=np.float64) / 255
    empty = {
        "periods": [],
        "subject_box": None,
        "lattice_period": None,
        "lattice_measurements": [],
        "grid_candidates": [],
    }
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    background = np.median(border, axis=0)
    if background.min() < 0.65 or np.ptp(background) > 0.16:
        return empty
    luma = rgb.mean(axis=2)
    chroma = np.ptp(rgb, axis=2)
    mask = chroma > max(0.22, float(np.quantile(np.ptp(border, axis=1), 0.99)) + 0.12)
    if not 0.015 < mask.mean() < 0.45:
        return empty
    ys, xs = np.nonzero(mask)
    left, right = np.quantile(xs, [0.001, 0.999]).astype(int)
    top, bottom = np.quantile(ys, [0.001, 0.999]).astype(int)
    periods: set[int] = set()
    measurements: list[dict] = []
    for source_name, signal in (
        ("subject_colour_coverage", mask),
        ("subject_thread_luminance", (1 - luma) * mask),
    ):
        subject = signal[top : bottom + 1, left : right + 1]
        measurements.append(_period_measurement(source_name, subject))
    fabric = np.linalg.norm(rgb - background, axis=2) < 0.12
    if 0.12 < float(fabric.mean()) < 0.88:
        measurements.extend(
            (
                _period_measurement("whole_fabric_luminance", (1 - luma) * fabric),
                _period_measurement("whole_fabric_chroma", chroma * fabric),
            )
        )
    measurements.extend(_edge_lattice_measurements(luma))
    for measurement in measurements:
        if measurement["period_x"] is not None:
            periods.add(int(measurement["period_x"]))
        if measurement["period_y"] is not None:
            periods.add(int(measurement["period_y"]))
    lattice = _consensus_edge_period(measurements)
    if lattice is None:
        agreed = [item for item in measurements if item["agreed_period"] is not None]
        if agreed:
            strongest = max(
                agreed,
                key=lambda item: min(item["confidence_x_ppm"], item["confidence_y_ppm"]),
            )
            lattice = int(strongest["agreed_period"])
    return {
        "periods": sorted(periods),
        "lattice_period": lattice,
        "analysis_size": [image.width, image.height],
        "lattice_measurements": measurements,
        "grid_candidates": _grid_candidates(image.width, image.height, lattice),
        "subject_box": [
            round(value * dimension / analysed)
            for value, dimension, analysed in zip(
                (left, top, right + 1, bottom + 1),
                original_size * 2,
                (image.width, image.height) * 2,
                strict=True,
            )
        ],
    }


def check_stitch_structure(source: Path, candidate: CountedStitches) -> dict:
    """Compare the reconstructed footprint to the source, independent of AI."""
    with Image.open(source) as image:
        image = image.convert("RGB").crop(candidate.crop_box)
        image.thumbnail((768, 768))
        rgb = np.asarray(image, dtype=np.float64) / 255
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]))
    background = np.median(border, axis=0)
    threshold = max(0.22, float(np.quantile(np.ptp(border, axis=1), 0.99)) + 0.12)
    foreground = (np.ptp(rgb, axis=2) > threshold) | (rgb.max(axis=2) < background.max() - 0.30)
    sample = candidate.sample
    grid = np.array([c is not None for c in sample.display_srgb]).reshape(
        sample.height, sample.width
    )
    ys = np.minimum(sample.height - 1, np.arange(image.height) * sample.height // image.height)
    xs = np.minimum(sample.width - 1, np.arange(image.width) * sample.width // image.width)
    footprint = grid[ys[:, None], xs[None, :]]
    intersection = np.count_nonzero(footprint & foreground)
    recall = intersection / max(1, np.count_nonzero(foreground))
    precision = intersection / max(1, np.count_nonzero(footprint))
    return {
        "passed": bool(recall >= 0.90 and precision >= 0.60),
        "foreground_recall_ppm": round(recall * 1_000_000),
        "foreground_precision_ppm": round(precision * 1_000_000),
    }


def counted_stitch_candidates(source: Path) -> tuple[list[CountedStitches], dict]:
    evidence = inspect_stitch_evidence(source)
    native = recover_counted_stitches(source)
    proposals = [native] if native else []
    if native:
        proposals.append(recover_counted_stitches(source, colour_tolerance=0.20))
    else:
        # A square stitch gives a bounded hypothesis for the weak axis. It is a
        # proposal requiring visual review, not a claimed exact measurement.
        for period in evidence["periods"][:3]:
            for tolerance in (0.12, 0.20):
                proposals.append(
                    recover_counted_stitches(
                        source,
                        period_hint=(period, period),
                        colour_tolerance=tolerance,
                    )
                )
    candidates, seen = [], set()
    for proposal in proposals:
        if proposal is None:
            continue
        key = (proposal.sample.width, proposal.sample.height, proposal.sample.display_srgb)
        if key in seen:
            continue
        seen.add(key)
        check = check_stitch_structure(source, proposal)
        if not check["passed"]:
            continue
        proposal.report["structure_check"] = check
        proposal.report["requires_visual_review"] = native is None
        candidates.append(proposal)
    # Preserve the strict local result first; AI may prefer an alternative colour
    # grouping only after comparing each candidate to the same source crop.
    return candidates[:3], evidence
