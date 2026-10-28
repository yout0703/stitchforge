"""A complete, legible chart and material legend in one downloadable PNG."""

from __future__ import annotations

import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from .render import _maps
from .validation import validate_document

MAX_CHART_PIXELS = 64_000_000


def _text_font(size: int) -> tuple[ImageFont.FreeTypeFont, bool]:
    configured = os.environ.get("STITCHFORGE_CHART_FONT")
    candidates = (
        [configured]
        if configured
        else [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size), True
    if configured:
        raise ValueError("图纸字体配置无效，请管理员检查 STITCHFORGE_CHART_FONT")
    return ImageFont.load_default(size=size), False


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for char in text.replace("\n", " ").replace("\r", " "):
        if line and font.getlength(line + char) > width:
            lines.append(line)
            line = ""
        line += char
    return [*lines, line]


def _ink(rgb: tuple[int, int, int]) -> str:
    # Choose the greater WCAG contrast against the exact cell colour.
    linear = [v / 255 / 12.92 if v <= 10 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in rgb]
    luminance = sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
    return "black" if luminance > 0.179 else "white"


def _symbol_font(symbol: str, size: int, available_width: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.load_default(size=size)
    if font.getlength(symbol) > available_width:
        font = ImageFont.load_default(
            size=max(1, int(size * available_width / font.getlength(symbol)))
        )
    return font


def render_download_chart(document: dict[str, Any], output_path: str | Path) -> Path:
    validate_document(document).raise_for_errors()
    width, height = document["grid"]["width"], document["grid"]["height"]
    cells = document["full_stitches"]["cells"]
    counts = Counter(cells)
    colors, symbols = _maps(document)
    # Keep 600 x 600 charts legible without allocating an unbounded raster.
    cell = min(28, max(12, math.floor(math.sqrt(50_000_000 / (width * height)))))
    text_font, unicode_supported = _text_font(18)
    title_font, _ = _text_font(26)
    ruler_font = ImageFont.load_default(size=12)
    materials = {item["id"]: item for item in document["materials"]}
    entries = []
    for recipe in sorted(document["recipes"], key=lambda item: item["index"]):
        index = recipe["index"]
        if not counts[index]:
            continue
        component = recipe["components"][0]
        material = materials[component["material_id"]]
        entries.append(
            (
                index,
                f"{material['brand']} {material['code']}",
                f"{component['strands']} strands / {counts[index]:,} stitches",
            )
        )
    title = document["metadata"]["title"]
    if not unicode_supported and any(ord(c) > 127 for c in title + "".join(e[1] for e in entries)):
        raise ValueError(
            "服务器缺少中文图纸字体，请管理员安装 Noto Sans CJK 并设置 STITCHFORGE_CHART_FONT"
        )

    margin, left = 32, 64
    image_width = max(800, left + width * cell + margin + 1)
    columns = min(4, max(1, (image_width - 2 * margin) // 360))
    column_width = (image_width - 2 * margin) // columns
    titles = _wrap(title, title_font, image_width - 2 * margin)
    header_height = margin + len(titles) * 36 + 68
    chart_top = header_height + 24
    legend_top = chart_top + height * cell + 64
    labels = [_wrap(label, text_font, column_width - 60) for _, label, _ in entries]
    row_heights = [
        max(len(lines) for lines in labels[start : start + columns]) * 26 + 44
        for start in range(0, len(entries), columns)
    ]
    image_height = legend_top + 48 + sum(row_heights) + 72
    if image_width * image_height > MAX_CHART_PIXELS:
        raise ValueError("完整图纸图片尺寸过大，请下载 OXS 图纸")

    image = Image.new("RGB", (image_width, image_height), "white")
    try:
        draw = ImageDraw.Draw(image)
        y = margin
        for line in titles:
            draw.text((margin, y), line, font=title_font, fill="#172333", anchor="lt")
            y += 36
        count = len(cells) - counts[0]
        subtitle = (
            f"{width} x {height} cells  |  {document['fabric']['count']} CT  |  "
            f"{len(entries)} colours  |  {count:,} stitches"
        )
        draw.text((margin, y + 10), subtitle, font=text_font, fill="#455364", anchor="lt")
        # Render each symbol once, then paste tiles: fast even for 360,000 stitches.
        tiles = {}
        for index, _, _ in entries:
            tile = Image.new("RGB", (cell, cell), colors[index])
            ImageDraw.Draw(tile).text(
                (cell / 2, cell / 2),
                symbols[index],
                font=_symbol_font(symbols[index], max(10, round(cell * 0.66)), cell - 4),
                anchor="mm",
                fill=_ink(colors[index]),
            )
            tiles[index] = tile
        for offset, index in enumerate(cells):
            if index:
                image.paste(
                    tiles[index], (left + offset % width * cell, chart_top + offset // width * cell)
                )
        for tile in tiles.values():
            tile.close()
        for col in range(width + 1):
            x = left + col * cell
            major = col % 10 == 0 or col == width
            draw.line(
                (x, chart_top, x, chart_top + height * cell),
                fill="#344050" if major else "#92999F",
                width=2 if major else 1,
            )
            if col % 10 == 0 or col == width and width % 10 >= 3:
                draw.text(
                    (x, chart_top - 12), str(col), font=ruler_font, anchor="mm", fill="#172333"
                )
        for row in range(height + 1):
            y = chart_top + row * cell
            major = row % 10 == 0 or row == height
            draw.line(
                (left, y, left + width * cell, y),
                fill="#344050" if major else "#92999F",
                width=2 if major else 1,
            )
            if row % 10 == 0 or row == height and height % 10 >= 3:
                draw.text((left - 10, y), str(row), font=ruler_font, anchor="rm", fill="#172333")
        legend_heading = "符号 / 色号对照表" if unicode_supported else "Symbols / Thread codes"
        draw.text(
            (margin, legend_top), legend_heading, font=title_font, fill="#172333", anchor="lt"
        )
        y = legend_top + 48
        for position, (index, _, detail) in enumerate(entries):
            col = position % columns
            x = margin + col * column_width
            draw.rectangle((x, y, x + 32, y + 32), fill=colors[index], outline="#344050")
            draw.text(
                (x + 16, y + 16),
                symbols[index],
                font=_symbol_font(symbols[index], 21, 28),
                fill=_ink(colors[index]),
                anchor="mm",
            )
            for line_number, line in enumerate(labels[position]):
                draw.text(
                    (x + 44, y + line_number * 26),
                    line,
                    font=text_font,
                    fill="#172333",
                    anchor="lt",
                )
            draw.text(
                (x + 44, y + len(labels[position]) * 26),
                detail,
                font=ImageFont.load_default(size=14),
                fill="#455364",
                anchor="lt",
            )
            if col == columns - 1 or position == len(entries) - 1:
                y += row_heights[position // columns]
        note = (
            "空白格不绣；每 10 格加粗。请以绣线色号为准。"
            if unicode_supported
            else "Blank cells: no stitch. Bold grid: 10 cells. Match threads by code."
        )
        draw.text((margin, image_height - 44), note, font=text_font, fill="#455364", anchor="lt")
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Title", title)
        metadata.add_text("Software", "StitchForge")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG", pnginfo=metadata, dpi=(300, 300))
        return target
    finally:
        image.close()
