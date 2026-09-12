from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from reportlab.lib.colors import Color, HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .stats import calculate_statistics
from .symbols import symbol_for_id

PAGE_COLS = 62
PAGE_ROWS = 81
OVERLAP_CELLS = 2
CELL_MM = 3


def page_ranges(length: int, capacity: int, overlap: int = OVERLAP_CELLS) -> list[tuple[int, int]]:
    if length <= 0 or capacity <= 0 or not 0 <= overlap < capacity:
        raise ValueError("分页长度、容量或重叠参数无效")
    count = max(1, math.ceil((length - overlap) / (capacity - overlap)))
    return [
        (page * (capacity - overlap), min(page * (capacity - overlap) + capacity, length))
        for page in range(count)
    ]


def _register_text_font(font_path: str | Path | None = None) -> tuple[str, bool]:
    candidates = []
    if font_path is not None:
        candidates.append(Path(font_path))
    candidates.extend(
        [
            Path.home() / "Library/Fonts/LXGWWenKaiGB-Regular.ttf",
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            Path("/Library/Fonts/Arial Unicode.ttf"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            name = "StitchForgeText"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(candidate)))
            return name, True
    return "Helvetica", False


def _safe_text(value: str, unicode_supported: bool) -> str:
    if unicode_supported:
        return value
    return value.encode("ascii", errors="replace").decode("ascii")


def _page_footer(canvas: Canvas, font: str, page_number: int) -> None:
    canvas.setFont(font, 7)
    canvas.setFillColor(Color(0.35, 0.35, 0.35))
    canvas.drawRightString(A4[0] - 12 * mm, 8 * mm, f"StitchForge · {page_number}")


def _draw_summary_page(
    canvas: Canvas,
    document: dict[str, Any],
    font: str,
    unicode_supported: bool,
    page_number: int,
    color: bool,
) -> None:
    stats = calculate_statistics(document)
    title = _safe_text(document["metadata"]["title"], unicode_supported)
    is_fixture = document["metadata"]["purpose"] == "test_fixture"
    canvas.setFont(font, 20)
    canvas.setFillColor(black)
    canvas.drawString(18 * mm, A4[1] - 24 * mm, title)
    canvas.setFont(font, 9)
    subtitle = "测试夹具 - 不代表真实绣线校准" if is_fixture else "十字绣图纸"
    canvas.drawString(18 * mm, A4[1] - 34 * mm, _safe_text(subtitle, unicode_supported))

    summary = [
        f"Grid: {stats.grid_width} x {stats.grid_height} cells",
        f"Fabric: {document['fabric']['count']} CT Aida, over one",
        f"Stitched size: {stats.canvas_width_mm:.2f} x {stats.canvas_height_mm:.2f} mm",
        f"Cloth size with margins: {stats.cloth_width_mm:.2f} x {stats.cloth_height_mm:.2f} mm",
        f"Full stitches: {stats.full_stitch_count}; blank cells: {stats.blank_count}",
        f"Used recipes: {stats.used_recipe_count}; materials: {stats.material_count}",
        "Screen colours are approximations; follow material codes when stitching.",
    ]
    canvas.setFont(font, 10)
    y = A4[1] - 52 * mm
    for line in summary:
        canvas.drawString(18 * mm, y, line)
        y -= 7 * mm

    material_by_id = {material["id"]: material for material in document["materials"]}
    recipe_by_id = {recipe["id"]: recipe for recipe in document["recipes"]}
    count_by_recipe = stats.recipe_stitch_counts
    canvas.setFont(font, 12)
    canvas.drawString(18 * mm, y - 4 * mm, _safe_text("图例 / Legend", unicode_supported))
    y -= 14 * mm
    canvas.setFont(font, 9)
    for item in document["legend"]:
        recipe = recipe_by_id[item["recipe_id"]]
        if recipe["id"] not in count_by_recipe:
            continue
        component = recipe["components"][0]
        material = material_by_id[component["material_id"]]
        symbol = symbol_for_id(item["symbol_id"])
        display_color = material["color"]["display_srgb"] or "#FFFFFF"
        canvas.setFillColor(HexColor(display_color) if color else white)
        canvas.rect(18 * mm, y - 1 * mm, 5 * mm, 5 * mm, stroke=1, fill=1)
        rgb = tuple(int(display_color[offset : offset + 2], 16) for offset in (1, 3, 5))
        luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        canvas.setFillColor(white if color and luminance < 100 else black)
        canvas.drawCentredString(20.5 * mm, y + 0.2 * mm, symbol)
        canvas.setFillColor(black)
        label = (
            f"{material['brand']} {material['series']} {material['code']} · "
            f"{component['strands']} strands · {count_by_recipe[recipe['id']]} stitches"
        )
        canvas.drawString(27 * mm, y, _safe_text(label, unicode_supported))
        y -= 7 * mm
    _page_footer(canvas, font, page_number)
    canvas.showPage()


def _draw_chart_page(
    canvas: Canvas,
    document: dict[str, Any],
    col_range: tuple[int, int],
    row_range: tuple[int, int],
    page_position: tuple[int, int],
    page_grid: tuple[int, int],
    font: str,
    unicode_supported: bool,
    page_number: int,
    color: bool,
) -> None:
    col_start, col_end = col_range
    row_start, row_end = row_range
    columns, rows = col_end - col_start, row_end - row_start
    cell = CELL_MM * mm
    chart_left = 12 * mm
    chart_top = A4[1] - 30 * mm
    cells = document["full_stitches"]["cells"]
    width = document["grid"]["width"]
    material_by_id = {item["id"]: item for item in document["materials"]}
    recipe_by_index = {item["index"]: item for item in document["recipes"]}
    legend_by_recipe = {item["recipe_id"]: item for item in document["legend"]}

    canvas.setFillColor(black)
    canvas.setFont(font, 10)
    title = _safe_text(document["metadata"]["title"], unicode_supported)
    canvas.drawString(12 * mm, A4[1] - 12 * mm, title)
    page_col, page_row = page_position
    total_cols, total_rows = page_grid
    header = (
        f"Chart {page_col + 1}/{total_cols} x {page_row + 1}/{total_rows} · "
        f"columns {col_start + 1}-{col_end} · rows {row_start + 1}-{row_end}"
    )
    canvas.setFont(font, 8)
    canvas.drawString(12 * mm, A4[1] - 19 * mm, header)

    for local_row, row in enumerate(range(row_start, row_end)):
        y = chart_top - (local_row + 1) * cell
        for local_col, col in enumerate(range(col_start, col_end)):
            recipe_index = cells[row * width + col]
            if recipe_index == 0:
                continue
            recipe = recipe_by_index[recipe_index]
            component = recipe["components"][0]
            material = material_by_id[component["material_id"]]
            display_color = material["color"]["display_srgb"] or "#FFFFFF"
            x = chart_left + local_col * cell
            if color:
                canvas.setFillColor(HexColor(display_color))
                canvas.rect(x, y, cell, cell, stroke=0, fill=1)
            rgb = tuple(int(display_color[offset : offset + 2], 16) for offset in (1, 3, 5))
            luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
            canvas.setFillColor(white if color and luminance < 100 else black)
            canvas.setFont(font, 5.7)
            symbol = symbol_for_id(legend_by_recipe[recipe["id"]]["symbol_id"])
            canvas.drawCentredString(x + cell / 2, y + cell * 0.25, symbol)

    canvas.setFillColor(black)
    for local_col in range(columns + 1):
        global_col = col_start + local_col
        canvas.setLineWidth(0.8 if global_col % 10 == 0 else 0.2)
        x = chart_left + local_col * cell
        canvas.line(x, chart_top, x, chart_top - rows * cell)
    for local_row in range(rows + 1):
        global_row = row_start + local_row
        canvas.setLineWidth(0.8 if global_row % 10 == 0 else 0.2)
        y = chart_top - local_row * cell
        canvas.line(chart_left, y, chart_left + columns * cell, y)

    canvas.setFont(font, 5.5)
    for local_col in range(columns + 1):
        global_col = col_start + local_col
        if global_col % 10 == 0 or local_col in (0, columns):
            x = chart_left + local_col * cell
            canvas.drawCentredString(x, chart_top + 1.8 * mm, str(global_col))
    for local_row in range(rows + 1):
        global_row = row_start + local_row
        if global_row % 10 == 0 or local_row in (0, rows):
            y = chart_top - local_row * cell
            canvas.drawRightString(chart_left - 1.2 * mm, y - 0.7 * mm, str(global_row))

    _page_footer(canvas, font, page_number)
    canvas.showPage()


def export_chart_pdf(
    document: dict[str, Any],
    output_path: str | Path,
    *,
    color: bool,
    font_path: str | Path | None = None,
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    font, unicode_supported = _register_text_font(font_path)
    canvas = Canvas(str(target), pagesize=A4, pageCompression=1, invariant=1)
    canvas.setTitle(_safe_text(document["metadata"]["title"], unicode_supported))
    canvas.setAuthor("StitchForge")
    page_number = 1
    _draw_summary_page(canvas, document, font, unicode_supported, page_number, color)
    page_number += 1
    column_ranges = page_ranges(document["grid"]["width"], PAGE_COLS)
    row_ranges = page_ranges(document["grid"]["height"], PAGE_ROWS)
    for page_row, row_range in enumerate(row_ranges):
        for page_col, col_range in enumerate(column_ranges):
            _draw_chart_page(
                canvas,
                document,
                col_range,
                row_range,
                (page_col, page_row),
                (len(column_ranges), len(row_ranges)),
                font,
                unicode_supported,
                page_number,
                color,
            )
            page_number += 1
    canvas.save()
    return target
