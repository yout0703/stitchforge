from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .canonical import document_hash
from .catalog import load_catalog
from .errors import DocumentValidationError, ProjectFormatError
from .export import export_bundle
from .fabric import plan_physical_fit
from .imaging import (
    apply_alpha_mask,
    crop_normalized_image,
    normalize_image,
    sample_normalized_image,
)
from .io import load_document, write_json
from .optimize import optimize_small_components
from .oxs import SUPPORTED_COORDINATE_PROFILE, export_oxs, import_oxs
from .palette import (
    build_pattern_document,
    load_grid_sample,
    select_clustered_palette,
    select_palette,
)
from .project import read_project
from .recovery import recover_rendered_stitch_colors
from .segmentation import remove_connected_border_background
from .stats import calculate_statistics
from .validation import validate_document
from .xsd import export_xsd, import_xsd


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _load_input(path: Path) -> dict[str, Any]:
    if path.suffix == ".stitchforge":
        return read_project(path)
    return load_document(path)


def _grid_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", maxsplit=1)
        return int(width_text), int(height_text)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("网格格式应为 宽x高，例如 160x240") from error


def _crop_box(value: str) -> tuple[int, int, int, int]:
    try:
        values = tuple(int(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("裁切框格式应为 left,top,right,bottom") from error
    if len(values) != 4:
        raise argparse.ArgumentTypeError("裁切框格式应为 left,top,right,bottom")
    return values


def _integer_list(value: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("CT 候选应为逗号分隔整数，例如 14,16,18") from error
    if not values:
        raise argparse.ArgumentTypeError("至少提供一个 CT 候选")
    return values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stitchforge", description="StitchForge 确定性图案引擎")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="校验 PatternDocument 或工程文件")
    validate_parser.add_argument("input", type=Path)
    validate_parser.add_argument("--release", action="store_true", help="同时执行正式交付门禁")
    validate_parser.add_argument("--allow-test-fixture", action="store_true")

    stats_parser = subparsers.add_parser("stats", help="计算针脚和物理尺寸统计")
    stats_parser.add_argument("input", type=Path)

    hash_parser = subparsers.add_parser("hash", help="计算规范 PatternDocument 哈希")
    hash_parser.add_argument("input", type=Path)

    export_parser = subparsers.add_parser("export", help="生成完整本地交付包")
    export_parser.add_argument("input", type=Path)
    export_parser.add_argument("--output-dir", type=Path, required=True)
    export_parser.add_argument("--allow-test-fixture", action="store_true")

    import_oxs_parser = subparsers.add_parser("import-oxs", help="导入 OXS 全针子集")
    import_oxs_parser.add_argument("input", type=Path)
    import_oxs_parser.add_argument("--output", type=Path, required=True)
    import_oxs_parser.add_argument("--report", type=Path)
    import_oxs_parser.add_argument(
        "--coordinate-profile",
        required=True,
        choices=[SUPPORTED_COORDINATE_PROFILE],
    )

    export_oxs_parser = subparsers.add_parser("export-oxs", help="导出 OXS 全针子集")
    export_oxs_parser.add_argument("input", type=Path)
    export_oxs_parser.add_argument("--output", type=Path, required=True)
    export_oxs_parser.add_argument("--report", type=Path)
    export_oxs_parser.add_argument(
        "--coordinate-profile",
        required=True,
        choices=[SUPPORTED_COORDINATE_PROFILE],
    )
    export_oxs_parser.add_argument(
        "--confirm-loss",
        action="store_true",
        help="确认 OXS 无法保存顶层针方向、来源和区域策略",
    )

    import_xsd_parser = subparsers.add_parser(
        "import-xsd", help="导入 Pattern Maker v4 XSD 全针图纸"
    )
    import_xsd_parser.add_argument("input", type=Path)
    import_xsd_parser.add_argument("--output", type=Path, required=True)
    import_xsd_parser.add_argument("--report", type=Path)
    import_xsd_parser.add_argument(
        "--legacy-encoding", choices=["cp1251", "gb18030", "cp1252"], default="cp1251"
    )
    export_xsd_parser = subparsers.add_parser(
        "export-xsd", help="导出 Pattern Maker v4 XSD 全针图纸"
    )
    export_xsd_parser.add_argument("input", type=Path)
    export_xsd_parser.add_argument("--output", type=Path, required=True)
    export_xsd_parser.add_argument("--report", type=Path)
    export_xsd_parser.add_argument("--confirm-loss", action="store_true")

    normalize_parser = subparsers.add_parser("normalize-image", help="规范化 JPG/PNG 工作图")
    normalize_parser.add_argument("input", type=Path)
    normalize_parser.add_argument("--output", type=Path, required=True)
    normalize_parser.add_argument("--report", type=Path)

    sample_parser = subparsers.add_parser("sample-image", help="将规范化 PNG 采样为针格目标色")
    sample_parser.add_argument("input", type=Path)
    sample_parser.add_argument("--grid", type=_grid_size, required=True)
    sample_parser.add_argument("--coverage-threshold", type=float, default=0.5)
    sample_parser.add_argument(
        "--remove-border-background",
        action="store_true",
        help="移除与网格边缘连通且接近边缘中位色的底布",
    )
    sample_parser.add_argument("--background-delta-e", type=float, default=2.0)
    sample_parser.add_argument("--background-report", type=Path)
    sample_parser.add_argument(
        "--source-profile",
        choices=["artwork", "rendered-stitch"],
        default="artwork",
        help="刺绣效果图模式会补偿线缝阴影造成的浅色偏暗",
    )
    sample_parser.add_argument("--rendered-stitch-lightness-lift", type=float, default=5.0)
    sample_parser.add_argument("--recovery-report", type=Path)
    sample_parser.add_argument("--output", type=Path, required=True)

    crop_parser = subparsers.add_parser("crop-image", help="按半开像素坐标裁切规范化工作图")
    crop_parser.add_argument("input", type=Path)
    crop_parser.add_argument("--box", type=_crop_box, required=True)
    crop_parser.add_argument("--output", type=Path, required=True)
    crop_parser.add_argument("--report", type=Path)

    mask_parser = subparsers.add_parser("mask-image", help="把单帧 PNG 蒙版乘入工作图 alpha")
    mask_parser.add_argument("input", type=Path)
    mask_parser.add_argument("--mask", type=Path, required=True)
    mask_parser.add_argument("--output", type=Path, required=True)
    mask_parser.add_argument("--report", type=Path)

    pattern_parser = subparsers.add_parser("build-pattern", help="使用材料目录把网格样本配色")
    pattern_parser.add_argument("input", type=Path, help="sample-image 生成的 JSON")
    pattern_parser.add_argument("--catalog", type=Path, required=True)
    pattern_parser.add_argument("--color-budget", type=int, required=True)
    pattern_parser.add_argument("--required-material", action="append", default=[])
    pattern_parser.add_argument("--minimum-gain", type=float, default=0.05)
    pattern_parser.add_argument(
        "--palette-strategy",
        choices=["greedy", "clustered"],
        default="greedy",
    )
    pattern_parser.add_argument("--pattern-id", required=True)
    pattern_parser.add_argument("--title", required=True)
    pattern_parser.add_argument("--author", default="")
    pattern_parser.add_argument("--fabric-count", type=int, default=14)
    pattern_parser.add_argument(
        "--strands",
        type=int,
        help="覆盖材料目录默认值；1–6 股。未提供时使用目录建议值",
    )
    pattern_parser.add_argument("--purpose", required=True, choices=["user_design", "test_fixture"])
    pattern_parser.add_argument("--output", type=Path, required=True)
    pattern_parser.add_argument("--report", type=Path)

    plan_parser = subparsers.add_parser(
        "plan-grid", help="按图片比例、成品尺寸和 CT 列出可比较的针格方案"
    )
    plan_parser.add_argument("--image-width", type=int, required=True)
    plan_parser.add_argument("--image-height", type=int, required=True)
    plan_parser.add_argument("--max-width-mm", type=float, required=True)
    plan_parser.add_argument("--max-height-mm", type=float, required=True)
    plan_parser.add_argument("--fabric-counts", type=_integer_list, default=(14, 16, 18))

    optimize_parser = subparsers.add_parser("optimize-pattern", help="在锁定约束下简化小连通域")
    optimize_parser.add_argument("input", type=Path, help="PatternDocument JSON")
    optimize_parser.add_argument("--sample", type=Path, required=True)
    optimize_parser.add_argument("--edge-lambda", type=float, default=0.75)
    optimize_parser.add_argument("--small-mu", type=float, default=0.5)
    optimize_parser.add_argument("--max-rounds", type=int, default=5)
    optimize_parser.add_argument("--preserve-max-delta-e", type=float, default=12.0)
    optimize_parser.add_argument("--output", type=Path, required=True)
    optimize_parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan-grid":
            plans = plan_physical_fit(
                args.image_width,
                args.image_height,
                max_width_mm=args.max_width_mm,
                max_height_mm=args.max_height_mm,
                fabric_counts=args.fabric_counts,
            )
            _print({"plans": [plan.as_dict() for plan in plans]})
            return 0
        if args.command == "normalize-image":
            report = normalize_image(args.input, args.output).as_dict()
            if args.report:
                write_json(args.report, report)
            _print(report)
            return 0
        if args.command == "sample-image":
            width, height = args.grid
            sample = sample_normalized_image(
                args.input,
                width=width,
                height=height,
                coverage_threshold=args.coverage_threshold,
            )
            if args.remove_border_background:
                sample, background_report = remove_connected_border_background(
                    sample,
                    delta_e00_threshold=args.background_delta_e,
                )
                if args.background_report:
                    write_json(args.background_report, background_report.as_dict())
            elif args.background_report:
                raise ValueError("--background-report 需要同时提供 --remove-border-background")
            if args.source_profile == "rendered-stitch":
                sample, recovery_report = recover_rendered_stitch_colors(
                    sample,
                    lightness_lift=args.rendered_stitch_lightness_lift,
                )
                if args.recovery_report:
                    write_json(args.recovery_report, recovery_report.as_dict())
            elif args.recovery_report:
                raise ValueError("--recovery-report 需要使用 --source-profile rendered-stitch")
            write_json(args.output, sample.as_dict())
            _print(sample.as_dict())
            return 0
        if args.command == "crop-image":
            report = crop_normalized_image(args.input, args.output, box=args.box).as_dict()
            if args.report:
                write_json(args.report, report)
            _print(report)
            return 0
        if args.command == "mask-image":
            report = apply_alpha_mask(args.input, args.mask, args.output).as_dict()
            if args.report:
                write_json(args.report, report)
            _print(report)
            return 0
        if args.command == "build-pattern":
            sample = load_grid_sample(args.input)
            catalog = load_catalog(args.catalog)
            palette_selector = (
                select_clustered_palette if args.palette_strategy == "clustered" else select_palette
            )
            palette_options: dict[str, Any] = {
                "color_budget": args.color_budget,
                "required_material_ids": tuple(args.required_material),
            }
            if args.palette_strategy == "greedy":
                palette_options["minimum_gain"] = args.minimum_gain
            palette = palette_selector(
                sample,
                catalog,
                **palette_options,
            )
            document = build_pattern_document(
                sample,
                catalog,
                palette,
                pattern_id=args.pattern_id,
                title=args.title,
                author=args.author,
                fabric_count=args.fabric_count,
                strands=args.strands,
                color_budget=args.color_budget,
                purpose=args.purpose,
            )
            write_json(args.output, document)
            if args.report:
                write_json(args.report, palette.as_dict())
            _print(palette.as_dict())
            return 0
        if args.command == "optimize-pattern":
            document = _load_input(args.input)
            sample = load_grid_sample(args.sample)
            result = optimize_small_components(
                document,
                sample,
                edge_lambda=args.edge_lambda,
                small_mu=args.small_mu,
                max_rounds=args.max_rounds,
                preserve_max_delta_e00=args.preserve_max_delta_e,
            )
            write_json(args.output, result.document)
            if args.report:
                write_json(args.report, result.report.as_dict())
            _print(result.report.as_dict())
            return 0
        if args.command in {"import-oxs", "import-xsd"}:
            result = (
                import_oxs(
                    args.input,
                    coordinate_profile=args.coordinate_profile,
                )
                if args.command == "import-oxs"
                else import_xsd(
                    args.input,
                    legacy_encoding=args.legacy_encoding,
                )
            )
            report = result.report.as_dict()
            if args.report:
                write_json(args.report, report)
            _print(report)
            if result.document is None:
                return 2
            write_json(args.output, result.document)
            return 0

        document = _load_input(args.input)
        if args.command == "validate":
            report = validate_document(
                document,
                release=args.release,
                allow_test_fixture=args.allow_test_fixture,
            )
            _print(report.as_dict())
            return 0 if report.valid else 2
        if args.command == "stats":
            validate_document(document).raise_for_errors()
            _print(calculate_statistics(document).as_dict())
            return 0
        if args.command == "hash":
            validate_document(document).raise_for_errors()
            print(document_hash(document))
            return 0
        if args.command == "export":
            manifest = export_bundle(
                document,
                args.output_dir,
                allow_test_fixture=args.allow_test_fixture,
            )
            _print(manifest)
            return 0
        if args.command == "export-oxs":
            report = export_oxs(
                document,
                args.output,
                coordinate_profile=args.coordinate_profile,
                confirm_loss=args.confirm_loss,
            )
            report_dict = report.as_dict()
            if args.report:
                write_json(args.report, report_dict)
            _print(report_dict)
            return 0 if not report.blocked else 2
        if args.command == "export-xsd":
            report = export_xsd(document, args.output, confirm_loss=args.confirm_loss)
            report_dict = report.as_dict()
            if args.report:
                write_json(args.report, report_dict)
            _print(report_dict)
            return 0 if not report.blocked else 2
    except (
        DocumentValidationError,
        ProjectFormatError,
        ValueError,
        OSError,
    ) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
