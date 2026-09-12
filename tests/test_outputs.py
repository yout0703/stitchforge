from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from PIL import Image
from pypdf import PdfReader
from stitchforge.export import export_bundle
from stitchforge.pdf import PAGE_COLS, PAGE_ROWS, export_chart_pdf, page_ranges
from stitchforge.project import read_project
from stitchforge.render import render_flat_preview, render_preview, render_symbol_chart


def test_page_ranges_cover_once_plus_declared_overlap() -> None:
    assert page_ranges(160, PAGE_COLS) == [(0, 62), (60, 122), (120, 160)]
    assert page_ranges(240, PAGE_ROWS) == [(0, 81), (79, 160), (158, 239), (237, 240)]
    assert len(page_ranges(300, PAGE_COLS)) * len(page_ranges(300, PAGE_ROWS)) == 20


def test_png_renderers_are_created(tmp_path, minimal_document: dict) -> None:
    preview = render_preview(minimal_document, tmp_path / "preview.png", cell_size=32)
    flat = render_flat_preview(minimal_document, tmp_path / "flat.png", cell_size=32)
    chart = render_symbol_chart(minimal_document, tmp_path / "chart.png", cell_size=32)
    with Image.open(preview) as image:
        assert image.size == (129, 97)
        assert image.mode == "RGB"
    with Image.open(chart) as image:
        assert image.size == (129, 97)
    with Image.open(flat) as image:
        assert image.size == (128, 96)
        assert image.getpixel((40, 10)) == (221, 213, 197)

    with pytest.raises(ValueError, match="cell_size"):
        render_flat_preview(minimal_document, tmp_path / "invalid.png", cell_size=0)


def test_pdf_has_summary_and_chart_pages(tmp_path, minimal_document: dict) -> None:
    output = export_chart_pdf(minimal_document, tmp_path / "chart.pdf", color=False)
    reader = PdfReader(output)
    assert len(reader.pages) == 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Grid: 4 x 3 cells" in text
    assert "Full stitches: 6; blank cells: 6" in text
    assert "columns 1-4" in text
    assert "rows 1-3" in text


def test_pdf_embeds_unicode_font(tmp_path, minimal_document: dict) -> None:
    output = export_chart_pdf(minimal_document, tmp_path / "chart.pdf", color=True)
    reader = PdfReader(output)
    embedded_fonts = []
    for page in reader.pages:
        for font_reference in page["/Resources"]["/Font"].values():
            font = font_reference.get_object()
            descriptor = font.get("/FontDescriptor")
            if descriptor is not None:
                descriptor = descriptor.get_object()
                embedded_fonts.append(
                    any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))
                )
    assert any(embedded_fonts)


def test_160_by_240_pdf_has_twelve_chart_pages(tmp_path, minimal_document: dict) -> None:
    document = deepcopy(minimal_document)
    width, height = 160, 240
    cells = [0] * (width * height)
    for col, row, recipe in (
        (0, 0, 1),
        (61, 80, 2),
        (60, 79, 1),
        (121, 159, 2),
        (120, 158, 1),
        (159, 239, 2),
    ):
        cells[row * width + col] = recipe
    document["grid"].update(width=width, height=height)
    document["full_stitches"]["cells"] = cells
    document["regions"] = []
    document["metadata"]["title"] = "160×240 分页边界测试"

    output = export_chart_pdf(document, tmp_path / "multipage.pdf", color=False)
    reader = PdfReader(output)
    assert len(reader.pages) == 13
    first_chart_text = reader.pages[1].extract_text() or ""
    last_chart_text = reader.pages[-1].extract_text() or ""
    assert "columns 1-62" in first_chart_text
    assert "rows 1-81" in first_chart_text
    assert "columns 121-160" in last_chart_text
    assert "rows 238-240" in last_chart_text


def test_bundle_manifest_covers_every_artifact(tmp_path, minimal_document: dict) -> None:
    manifest = export_bundle(minimal_document, tmp_path, allow_test_fixture=True)
    assert manifest["test_fixture"] is True
    expected = {
        "validation-report.json",
        "statistics.json",
        "pattern.stitchforge",
        "preview.png",
        "chart-color.png",
        "chart-bw.png",
        "chart-color.pdf",
        "chart-bw.pdf",
        "materials.csv",
    }
    assert {item["path"] for item in manifest["files"]} == expected
    for item in manifest["files"]:
        payload = (tmp_path / item["path"]).read_bytes()
        assert len(payload) == item["byte_length"]
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    restored = read_project(tmp_path / "pattern.stitchforge")
    assert restored == minimal_document
