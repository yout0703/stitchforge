from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps
from stitchforge.catalog import load_catalog
from stitchforge.imaging import normalize_image, sample_normalized_image
from stitchforge.io import write_json
from stitchforge.optimize import optimize_small_components
from stitchforge.palette import build_pattern_document, select_clustered_palette
from stitchforge.recovery import recover_rendered_stitch_colors
from stitchforge.render import render_flat_preview, render_preview
from stitchforge.segmentation import remove_connected_border_background
from stitchforge.stats import calculate_statistics


@dataclass(frozen=True, slots=True)
class SampleConfig:
    sample_id: str
    title: str
    width: int
    height: int
    color_budget: int


SAMPLES = (
    SampleConfig("parakeets", "春日双鹦鹉", 120, 180, 30),
    SampleConfig("folk-owl", "民俗花园猫头鹰", 140, 210, 23),
    SampleConfig("lucky-rabbit", "幸运小兔与四叶草", 120, 180, 23),
)


def _legacy_summary(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    fidelity = report["color_fidelity"]
    return {
        "full_stitches": report["full_stitches"],
        "palette_count": report["palette_count"],
        "mean_delta_e00": fidelity["mean_delta_e"],
        "p90_delta_e00": fidelity["p90_delta_e"],
        "tone_correlation": fidelity["tone_correlation"],
    }


def _comparison_image(fixture: Path, preview: Path, output: Path) -> None:
    paths = (fixture / "source.png", fixture / "legacy-effect.png", preview)
    labels = ("INPUT", "LEGACY EFFECT", "STITCHFORGE")
    tile_size = (360, 540)
    header = 36
    canvas = Image.new("RGB", (tile_size[0] * 3, tile_size[1] + header), "#D8D5CF")
    draw = ImageDraw.Draw(canvas)
    for index, (path, label) in enumerate(zip(paths, labels, strict=True)):
        with Image.open(path) as opened:
            image = ImageOps.contain(opened.convert("RGB"), tile_size)
        x = index * tile_size[0] + (tile_size[0] - image.width) // 2
        y = header + (tile_size[1] - image.height) // 2
        canvas.paste(image, (x, y))
        draw.text((index * tile_size[0] + 12, 11), label, fill="#272522")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=92, optimize=True)


def _contact_sheet(comparisons: list[Path], output: Path) -> None:
    images = []
    for path in comparisons:
        with Image.open(path) as opened:
            images.append(opened.copy())
    canvas = Image.new("RGB", (images[0].width, sum(image.height for image in images)), "white")
    y = 0
    for image in images:
        canvas.paste(image, (0, y))
        y += image.height
    canvas.save(output, quality=90, optimize=True)


def _markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "# 真实效果图转换基准",
        "",
        "三张输入图来自 `amazing-images-creator` 的既有十字绣实验。StitchForge 使用当前",
        "DMC 目录、渲染线体浅色恢复、Lab 聚类后 CIEDE2000 映射、保守的边缘连通底色移除",
        "（ΔE00 ≤ 2）与小色块优化。",
        "旧流程指标只用于横向观察；两边色库和实现不同，色差数字不能直接判定算法胜负。",
        "",
        "![三组效果对照](comparison-contact-sheet.jpg)",
        "",
        "| 样本 | 网格 | StitchForge 针数 | 旧流程针数 | 差值 | DMC 色数 | "
        "平均 ΔE00 | P95 ΔE00 | 孤立针（优化前→后） |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in results:
        lines.append(
            "| {sample_id} | {width}×{height} | {stitches} | {legacy_stitches} | {delta:+d} | "
            "{colors} | {mean:.3f} | {p95:.3f} | {isolated_before}→{isolated_after} |".format(
                **item
            )
        )
    lines.extend(
        [
            "",
            "## 观察",
            "",
            "- 三张 RGB 图的底布都能自动留空，针数与旧流程处于同一量级；不再把整张画布误判为满绣。",
            "- 兔子样本证明底色阈值必须保守。提高到 ΔE00 约 8 以后，浅色兔毛会开始与"
            "底布一起被删除。",
            "- 聚类配色会先保留差异明显的小色组，再映射 DMC；三张样本的 P95 色差均低于"
            "原先直接贪心版本，兔子不再由大量近似米黄色阶挤占色位。",
            "- `flat-preview.png` 直接显示 DMC 近似屏幕色，用于判断配色；`preview.png` 是规则化"
            "全针 X 预览。不同软件的线体光照会改变观看明暗，不能与材料色误差混为一个指标。",
            "- 下一轮提升应加入关键区域权重和主体感知的柔性蒙版；回针需要单独设计和验收。",
            "",
            "每组目录中的 `comparison.jpg`、`preview.png`、`pattern.json`、`palette-report.json`、",
            "`flat-preview.png`、`background-report.json` 和 `optimization-report.json` 可用于"
            "逐项复核。",
            "",
        ]
    )
    return "\n".join(lines)


def run(root: Path, output_root: Path) -> None:
    fixtures = root / "fixtures/fidelity/amazing-images-creator"
    catalog = load_catalog(root / "catalogs/dmc-117-2021-quiltysolid-2025-07.json")
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    comparisons: list[Path] = []

    for config in SAMPLES:
        fixture = fixtures / config.sample_id
        target = output_root / config.sample_id
        target.mkdir(parents=True, exist_ok=True)
        normalized = target / "normalized.png"
        normalization = normalize_image(fixture / "source.png", normalized)
        write_json(target / "normalization-report.json", normalization.as_dict())

        sampled = sample_normalized_image(
            normalized,
            width=config.width,
            height=config.height,
        )
        cleaned, background = remove_connected_border_background(sampled)
        cleaned, recovery = recover_rendered_stitch_colors(cleaned)
        write_json(target / "sample.json", cleaned.as_dict())
        write_json(target / "background-report.json", background.as_dict())
        write_json(target / "recovery-report.json", recovery.as_dict())

        palette = select_clustered_palette(
            cleaned,
            catalog,
            color_budget=config.color_budget,
        )
        write_json(target / "palette-report.json", palette.as_dict())
        document = build_pattern_document(
            cleaned,
            catalog,
            palette,
            pattern_id=f"fidelity-{config.sample_id}",
            title=config.title,
            author="StitchForge benchmark",
            fabric_count=14,
            fabric_color=background.background_display_srgb,
            strands=2,
            color_budget=config.color_budget,
            purpose="user_design",
            created_at=datetime(2026, 9, 12, tzinfo=UTC),
        )
        write_json(target / "pattern-before-optimization.json", document)
        before_stats = calculate_statistics(document)
        optimized = optimize_small_components(document, cleaned)
        write_json(target / "pattern.json", optimized.document)
        write_json(target / "optimization-report.json", optimized.report.as_dict())
        after_stats = calculate_statistics(optimized.document)
        preview = render_preview(optimized.document, target / "preview.png", cell_size=8)
        render_flat_preview(optimized.document, target / "flat-preview.png", cell_size=8)
        comparison = target / "comparison.jpg"
        _comparison_image(fixture, preview, comparison)
        comparisons.append(comparison)

        legacy = _legacy_summary(fixture / "legacy-report.json")
        results.append(
            {
                "sample_id": config.sample_id,
                "width": config.width,
                "height": config.height,
                "stitches": after_stats.full_stitch_count,
                "legacy_stitches": legacy["full_stitches"],
                "delta": after_stats.full_stitch_count - legacy["full_stitches"],
                "colors": palette.quality.selected_materials,
                "mean": palette.quality.weighted_mean_delta_e00,
                "p95": palette.quality.p95_delta_e00,
                "isolated_before": before_stats.isolated_stitches_4,
                "isolated_after": after_stats.isolated_stitches_4,
                "background": background.as_dict(),
                "legacy": legacy,
            }
        )

    _contact_sheet(comparisons, output_root / "comparison-contact-sheet.jpg")
    write_json(output_root / "benchmark-results.json", {"samples": results})
    (output_root / "README.md").write_text(_markdown(results), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output/fidelity-benchmark"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run(root, root / args.output if not args.output.is_absolute() else args.output)


if __name__ == "__main__":
    main()
