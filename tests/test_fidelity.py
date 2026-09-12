from __future__ import annotations

from stitchforge.fidelity import measure_motif_resolution


def _document(width: int, height: int, stitched: set[int]) -> dict:
    return {
        "grid": {"width": width, "height": height},
        "full_stitches": {
            "cells": [1 if index in stitched else 0 for index in range(width * height)]
        },
    }


def test_motif_resolution_uses_largest_connected_subject() -> None:
    subject = {2 + row * 10 + col for row in range(3) for col in range(5)}
    decoration = {89, 99}

    report = measure_motif_resolution(
        _document(10, 10, subject | decoration), minimum_effective_width=5
    )

    assert report.effective_width == 5
    assert report.effective_height == 3
    assert report.component_cells == 15
    assert report.stitched_cells == 17
    assert report.bounding_box == (2, 0, 7, 3)
    assert report.passes_minimum is True


def test_empty_pattern_has_zero_effective_resolution() -> None:
    report = measure_motif_resolution(_document(2, 2, set()))

    assert report.bounding_box is None
    assert report.effective_width == 0
    assert report.component_share == 0
    assert report.passes_minimum is False
