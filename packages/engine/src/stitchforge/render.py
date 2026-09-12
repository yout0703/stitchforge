from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw

from .symbols import symbol_for_id


def _maps(document: dict[str, Any]) -> tuple[dict[int, tuple[int, int, int]], dict[int, str]]:
    material_by_id = {material["id"]: material for material in document["materials"]}
    recipe_by_id = {recipe["id"]: recipe for recipe in document["recipes"]}
    color_by_index: dict[int, tuple[int, int, int]] = {}
    for recipe in document["recipes"]:
        material = material_by_id[recipe["components"][0]["material_id"]]
        color = material["color"]["display_srgb"]
        if color is None:
            raise ValueError(f"配方 {recipe['id']} 的材料没有显示色，无法渲染")
        color_by_index[recipe["index"]] = ImageColor.getrgb(color)

    symbol_by_index: dict[int, str] = {}
    for legend_entry in document["legend"]:
        recipe = recipe_by_id[legend_entry["recipe_id"]]
        symbol_by_index[recipe["index"]] = symbol_for_id(legend_entry["symbol_id"])
    return color_by_index, symbol_by_index


def render_preview(
    document: dict[str, Any],
    output_path: str | Path,
    *,
    cell_size: int = 32,
) -> Path:
    if not 8 <= cell_size <= 128:
        raise ValueError("cell_size 必须在 8 到 128 之间")
    width = document["grid"]["width"]
    height = document["grid"]["height"]
    cells = document["full_stitches"]["cells"]
    background = ImageColor.getrgb(document["fabric"]["display_srgb"])
    colors, _ = _maps(document)
    image = Image.new("RGB", (width * cell_size + 1, height * cell_size + 1), background)
    draw = ImageDraw.Draw(image)
    stroke = max(2, cell_size // 7)
    shadow = max(stroke + 2, cell_size // 5)
    padding = max(2, cell_size // 7)

    for offset, recipe_index in enumerate(cells):
        if recipe_index == 0:
            continue
        col, row = offset % width, offset // width
        x0, y0 = col * cell_size + padding, row * cell_size + padding
        x1, y1 = (col + 1) * cell_size - padding, (row + 1) * cell_size - padding
        color = colors[recipe_index]
        luminance = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
        outline = (245, 245, 245) if luminance < 90 else (70, 70, 70)
        draw.line((x0, y0, x1, y1), fill=outline, width=shadow)
        draw.line((x0, y1, x1, y0), fill=outline, width=shadow)
        draw.line((x0, y0, x1, y1), fill=color, width=stroke)
        draw.line((x0, y1, x1, y0), fill=color, width=stroke)

    grid_color = (120, 116, 108)
    major_color = (55, 52, 48)
    for col in range(width + 1):
        x = col * cell_size
        draw.line((x, 0, x, height * cell_size), fill=major_color if col % 10 == 0 else grid_color)
    for row in range(height + 1):
        y = row * cell_size
        draw.line((0, y, width * cell_size, y), fill=major_color if row % 10 == 0 else grid_color)

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target


def render_flat_preview(
    document: dict[str, Any],
    output_path: str | Path,
    *,
    cell_size: int = 8,
) -> Path:
    """Render exact material display colors without simulated thread lighting."""
    if not 1 <= cell_size <= 128:
        raise ValueError("cell_size 必须在 1 到 128 之间")
    width = document["grid"]["width"]
    height = document["grid"]["height"]
    background = ImageColor.getrgb(document["fabric"]["display_srgb"])
    colors, _ = _maps(document)
    image = Image.new("RGB", (width, height), background)
    pixels = image.load()
    for offset, recipe_index in enumerate(document["full_stitches"]["cells"]):
        if recipe_index:
            pixels[offset % width, offset // width] = colors[recipe_index]
    if cell_size > 1:
        image = image.resize(
            (width * cell_size, height * cell_size),
            resample=Image.Resampling.NEAREST,
        )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target


def render_symbol_chart(
    document: dict[str, Any],
    output_path: str | Path,
    *,
    cell_size: int = 32,
    color: bool = True,
) -> Path:
    width = document["grid"]["width"]
    height = document["grid"]["height"]
    cells = document["full_stitches"]["cells"]
    colors, symbols = _maps(document)
    image = Image.new("RGB", (width * cell_size + 1, height * cell_size + 1), "white")
    draw = ImageDraw.Draw(image)
    for offset, recipe_index in enumerate(cells):
        if recipe_index == 0:
            continue
        col, row = offset % width, offset // width
        x0, y0 = col * cell_size, row * cell_size
        if color:
            draw.rectangle(
                (x0 + 1, y0 + 1, x0 + cell_size - 1, y0 + cell_size - 1),
                fill=colors[recipe_index],
            )
        symbol = symbols[recipe_index]
        bbox = draw.textbbox((0, 0), symbol)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        symbol_color = "black"
        if color:
            rgb = colors[recipe_index]
            if 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2] < 100:
                symbol_color = "white"
        draw.text(
            (x0 + (cell_size - text_width) / 2, y0 + (cell_size - text_height) / 2 - bbox[1]),
            symbol,
            fill=symbol_color,
        )
    for col in range(width + 1):
        x = col * cell_size
        draw.line((x, 0, x, height * cell_size), fill="black", width=2 if col % 10 == 0 else 1)
    for row in range(height + 1):
        y = row * cell_size
        draw.line((0, y, width * cell_size, y), fill="black", width=2 if row % 10 == 0 else 1)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target
