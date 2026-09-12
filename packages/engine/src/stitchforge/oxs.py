from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as XmlWriter

from defusedxml import ElementTree as SafeXml
from defusedxml.common import DefusedXmlException

from .fabric import MAX_AIDA_COUNT, MAX_GRID_SIDE, MIN_AIDA_COUNT
from .validation import validate_document

MAX_OXS_BYTES = 16 * 1024 * 1024
SUPPORTED_COORDINATE_PROFILE = "oxs-v1-zero-based"
_HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]+")


@dataclass(frozen=True, slots=True)
class OxsIssue:
    code: str
    severity: str
    path: str
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


@dataclass(slots=True)
class OxsReport:
    direction: str
    coordinate_profile: str
    issues: list[OxsIssue] = field(default_factory=list)
    palette_index_map: dict[int, int] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def status(self) -> str:
        if self.blocked:
            return "blocked"
        if any(issue.severity == "warning" for issue in self.issues):
            return "success_with_reported_loss"
        return "success"

    def add(
        self,
        code: str,
        message: str,
        *,
        path: str = "$",
        severity: str = "error",
        **details: Any,
    ) -> None:
        self.issues.append(OxsIssue(code, severity, path, message, details=details or None))

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "OXS",
            "direction": self.direction,
            "coordinate_profile": self.coordinate_profile,
            "status": self.status,
            "blocked": self.blocked,
            "issues": [issue.as_dict() for issue in self.issues],
            "palette_index_map": {
                str(source): target for source, target in sorted(self.palette_index_map.items())
            },
        }


@dataclass(frozen=True, slots=True)
class OxsImportResult:
    document: dict[str, Any] | None
    report: OxsReport


def _safe_id(value: str, fallback: str) -> str:
    cleaned = _SAFE_ID.sub("_", value.strip()).strip("_.:-")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = fallback
    return cleaned[:128]


def _parse_int(
    value: str | None,
    *,
    name: str,
    path: str,
    report: OxsReport,
    minimum: int,
    maximum: int,
) -> int | None:
    try:
        parsed = int(value) if value is not None else None
    except ValueError:
        parsed = None
    if parsed is None or not minimum <= parsed <= maximum:
        report.add(
            "INVALID_INTEGER",
            f"{name} 必须是 {minimum} 到 {maximum} 的整数",
            path=path,
            value=value,
        )
        return None
    return parsed


def _single_child(root: Any, name: str, report: OxsReport) -> Any | None:
    matches = root.findall(name)
    if len(matches) != 1:
        report.add(
            "REQUIRED_ELEMENT",
            f"OXS 必须且只能包含一个 <{name}> 元素",
            path=f"$.{name}",
            count=len(matches),
        )
        return None
    return matches[0]


def _unsupported_content(root: Any, report: OxsReport) -> None:
    blocking_sections = {
        "partstitches": "UNSUPPORTED_PART_STITCH",
        "backstitches": "UNSUPPORTED_BACKSTITCH",
        "ornaments_inc_knots_and_beads": "UNSUPPORTED_ORNAMENT",
    }
    for section, code in blocking_sections.items():
        for element in root.findall(section):
            if len(element):
                report.add(
                    code,
                    f"当前版本不能无损导入非全针内容 <{section}>",
                    path=f"$.{section}",
                    count=len(element),
                )

    commentboxes = root.findall("commentboxes")
    if any(len(element) for element in commentboxes):
        report.add(
            "COMMENT_BOXES_NOT_IMPORTED",
            "批注框不会进入 PatternDocument",
            path="$.commentboxes",
            severity="warning",
        )

    known = {
        "format",
        "properties",
        "palette",
        "fullstitches",
        "partstitches",
        "backstitches",
        "ornaments_inc_knots_and_beads",
        "commentboxes",
    }
    for child in root:
        if child.tag not in known:
            report.add(
                "UNSUPPORTED_EXTENSION",
                f"不能判断扩展元素 <{child.tag}> 是否包含针法语义",
                path=f"$.{child.tag}",
            )


def import_oxs(
    path: str | Path,
    *,
    coordinate_profile: str,
    imported_at: datetime | None = None,
) -> OxsImportResult:
    report = OxsReport("import", coordinate_profile)
    if coordinate_profile != SUPPORTED_COORDINATE_PROFILE:
        report.add(
            "UNSUPPORTED_COORDINATE_PROFILE",
            f"当前只支持坐标配置 {SUPPORTED_COORDINATE_PROFILE}",
            path="$.coordinate_profile",
        )
        return OxsImportResult(None, report)

    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as error:
        report.add("READ_FAILED", f"无法读取 OXS: {error}")
        return OxsImportResult(None, report)
    if len(payload) > MAX_OXS_BYTES:
        report.add(
            "INPUT_TOO_LARGE",
            "OXS 超过 16 MiB 限制",
            byte_length=len(payload),
        )
        return OxsImportResult(None, report)

    try:
        root = SafeXml.fromstring(
            payload,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (SafeXml.ParseError, DefusedXmlException) as error:
        report.add("UNSAFE_OR_INVALID_XML", f"OXS XML 不安全或格式无效: {error}")
        return OxsImportResult(None, report)
    if root.tag != "chart":
        report.add("INVALID_ROOT", "OXS 根元素必须是 <chart>", path="$")
        return OxsImportResult(None, report)

    _unsupported_content(root, report)
    properties = _single_child(root, "properties", report)
    palette = _single_child(root, "palette", report)
    fullstitches = _single_child(root, "fullstitches", report)
    _single_child(root, "backstitches", report)
    if properties is None or palette is None or fullstitches is None:
        return OxsImportResult(None, report)

    width = _parse_int(
        properties.get("chartwidth"),
        name="chartwidth",
        path="$.properties.@chartwidth",
        report=report,
        minimum=1,
        maximum=MAX_GRID_SIDE,
    )
    height = _parse_int(
        properties.get("chartheight"),
        name="chartheight",
        path="$.properties.@chartheight",
        report=report,
        minimum=1,
        maximum=MAX_GRID_SIDE,
    )
    count_x = _parse_int(
        properties.get("stitchesperinch"),
        name="stitchesperinch",
        path="$.properties.@stitchesperinch",
        report=report,
        minimum=1,
        maximum=100,
    )
    count_y = _parse_int(
        properties.get("stitchesperinch_y"),
        name="stitchesperinch_y",
        path="$.properties.@stitchesperinch_y",
        report=report,
        minimum=1,
        maximum=100,
    )
    if count_x is not None and count_y is not None:
        if count_x != count_y:
            report.add(
                "UNSUPPORTED_FABRIC_ASPECT",
                "当前 PatternDocument 不支持横纵密度不同的布",
                path="$.properties",
                stitchesperinch=count_x,
                stitchesperinch_y=count_y,
            )
        if not MIN_AIDA_COUNT <= count_x <= MAX_AIDA_COUNT:
            report.add(
                "UNSUPPORTED_FABRIC_COUNT",
                f"当前版本只支持 {MIN_AIDA_COUNT}–{MAX_AIDA_COUNT} CT Aida",
                path="$.properties.@stitchesperinch",
                value=count_x,
            )

    palette_items: dict[int, dict[str, Any]] = {}
    for position, item in enumerate(palette):
        item_path = f"$.palette.palette_item[{position}]"
        if item.tag != "palette_item":
            report.add(
                "UNSUPPORTED_PALETTE_CONTENT",
                f"palette 中不支持 <{item.tag}>",
                path=item_path,
            )
            continue
        if len(item):
            report.add(
                "UNSUPPORTED_BLEND",
                "当前版本不支持 palette_item 内的混色或扩展节点",
                path=item_path,
            )
        blend_color = item.get("blendcolor")
        if blend_color and blend_color.lower() not in {"nil", "none"}:
            report.add(
                "UNSUPPORTED_BLEND",
                "当前版本不支持 OXS blendcolor",
                path=f"{item_path}.@blendcolor",
                value=blend_color,
            )
        index = _parse_int(
            item.get("index"),
            name="palette index",
            path=f"{item_path}.@index",
            report=report,
            minimum=0,
            maximum=65535,
        )
        color = item.get("color")
        if color is None or _HEX_COLOR.fullmatch(color) is None:
            report.add(
                "INVALID_COLOR",
                "palette color 必须是六位十六进制 RGB，且不能带 #",
                path=f"{item_path}.@color",
                value=color,
            )
        if index is None or color is None or _HEX_COLOR.fullmatch(color) is None:
            continue
        if index in palette_items:
            report.add(
                "DUPLICATE_PALETTE_INDEX",
                f"palette index {index} 重复",
                path=f"{item_path}.@index",
            )
            continue
        palette_items[index] = {
            "number": item.get("number"),
            "name": item.get("name", ""),
            "color": color.upper(),
            "strands": item.get("strands"),
        }

    if 0 not in palette_items:
        report.add("MISSING_CLOTH_COLOR", "palette 必须包含 index 0 的布色", path="$.palette")

    materials: list[dict[str, Any]] = []
    recipes: list[dict[str, Any]] = []
    recipe_id_by_palette: dict[int, str] = {}
    for index, item in sorted(palette_items.items()):
        if index == 0:
            continue
        number = item["number"]
        if not isinstance(number, str) or not number.strip():
            report.add(
                "MISSING_MATERIAL_NUMBER",
                "非布色 palette_item 必须提供 number",
                path=f"$.palette.index[{index}].@number",
            )
            continue
        strands = _parse_int(
            item["strands"],
            name="strands",
            path=f"$.palette.index[{index}].@strands",
            report=report,
            minimum=1,
            maximum=6,
        )
        if strands is None:
            continue
        parts = number.split(maxsplit=1)
        if len(parts) == 2:
            brand, code = parts
        else:
            brand, code = "OXS", parts[0]
            report.add(
                "MATERIAL_BRAND_DEFAULTED",
                f"材料 {number!r} 没有可分离的品牌，使用 OXS",
                path=f"$.palette.index[{index}].@number",
                severity="warning",
            )
        material_id = f"oxs_material_{index}"
        recipe_id = f"oxs_recipe_{index}"
        materials.append(
            {
                "id": material_id,
                "brand": brand[:128],
                "series": "Imported OXS",
                "code": code.strip()[:64],
                "name": item["name"][:256],
                "color": {
                    "display_srgb": f"#{item['color']}",
                    "status": "approximate",
                    "source_ref": "src_oxs",
                },
            }
        )
        recipes.append(
            {
                "id": recipe_id,
                "index": index,
                "components": [{"material_id": material_id, "strands": strands}],
            }
        )
        recipe_id_by_palette[index] = recipe_id

    cells = [0] * ((width or 0) * (height or 0))
    seen_coordinates: set[tuple[int, int]] = set()
    used_indices: set[int] = set()
    for position, stitch in enumerate(fullstitches):
        stitch_path = f"$.fullstitches.stitch[{position}]"
        if stitch.tag != "stitch":
            report.add(
                "UNSUPPORTED_FULL_STITCH_CONTENT",
                f"fullstitches 中不支持 <{stitch.tag}>",
                path=stitch_path,
            )
            continue
        x = _parse_int(
            stitch.get("x"),
            name="x",
            path=f"{stitch_path}.@x",
            report=report,
            minimum=0,
            maximum=max(0, (width or 1) - 1),
        )
        y = _parse_int(
            stitch.get("y"),
            name="y",
            path=f"{stitch_path}.@y",
            report=report,
            minimum=0,
            maximum=max(0, (height or 1) - 1),
        )
        palette_index = _parse_int(
            stitch.get("palindex"),
            name="palindex",
            path=f"{stitch_path}.@palindex",
            report=report,
            minimum=0,
            maximum=65535,
        )
        if x is None or y is None or palette_index is None:
            continue
        coordinate = (x, y)
        if coordinate in seen_coordinates:
            report.add(
                "DUPLICATE_STITCH",
                f"坐标 ({x}, {y}) 重复定义",
                path=stitch_path,
            )
            continue
        seen_coordinates.add(coordinate)
        if palette_index == 0:
            report.add(
                "CLOTH_STITCH_IGNORED",
                f"坐标 ({x}, {y}) 使用布色，按空格处理",
                path=stitch_path,
                severity="warning",
            )
            continue
        if palette_index not in recipe_id_by_palette:
            report.add(
                "UNKNOWN_PALETTE_INDEX",
                f"针格引用不存在或无效的 palette index {palette_index}",
                path=f"{stitch_path}.@palindex",
            )
            continue
        cells[y * (width or 0) + x] = palette_index
        used_indices.add(palette_index)

    if len(used_indices) > 64:
        report.add(
            "COLOR_BUDGET_EXCEEDED",
            "实际使用颜色超过 PatternDocument 的 64 色上限",
            path="$.fullstitches",
            used=len(used_indices),
        )
    if report.blocked or width is None or height is None or count_x is None or count_y is None:
        return OxsImportResult(None, report)

    report.add(
        "TOP_LEG_DEFAULTED",
        "OXS 未表达十字针顶层方向，导入时使用 slash",
        path="$.full_stitches.top_leg",
        severity="warning",
    )
    report.add(
        "MARGINS_DEFAULTED",
        "OXS 未表达裁布边距，导入时四边使用 50 mm",
        path="$.fabric.margins_mm",
        severity="warning",
    )
    title = properties.get("charttitle", "").strip()
    if not title:
        title = source.stem or "Imported OXS pattern"
        report.add(
            "TITLE_DEFAULTED",
            "OXS 标题为空，使用文件名",
            path="$.metadata.title",
            severity="warning",
        )
    timestamp = (imported_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    cloth_color = f"#{palette_items[0]['color']}"
    document: dict[str, Any] = {
        "schema_version": "0.1.0",
        "required_features": ["full_cross"],
        "metadata": {
            "pattern_id": _safe_id(f"oxs_{source.stem}", "oxs_import"),
            "title": title[:256],
            "author": properties.get("author", "")[:256],
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "purpose": "user_design",
        },
        "grid": {"width": width, "height": height, "origin": "top_left", "subgrid_scale": 2},
        "fabric": {
            "type": "aida",
            "count": count_x,
            "count_unit": "cells_per_inch",
            "worked_over": 1,
            "display_srgb": cloth_color,
            "margins_mm": {"top": 50, "right": 50, "bottom": 50, "left": 50},
        },
        "materials": materials,
        "recipes": recipes,
        "full_stitches": {"encoding": "row-major-u16", "top_leg": "slash", "cells": cells},
        "regions": [],
        "legend": [
            {"recipe_id": recipe_id_by_palette[index], "symbol_id": f"s{order:03d}"}
            for order, index in enumerate(sorted(used_indices), start=1)
        ],
        "constraints": {
            "color_budget": max(1, len(used_indices)),
            "metric_profile": "quality_4n_v1",
        },
        "provenance": {
            "source_asset_hashes": [hashlib.sha256(payload).hexdigest()],
            "engine_version": "stitchforge-0.1.0",
            "catalog_snapshot_id": "oxs_embedded_palette",
            "rule_profile": SUPPORTED_COORDINATE_PROFILE,
            "generation_config_hash": None,
            "random_seed": None,
            "ai_proposal_hash": None,
            "sources": [
                {
                    "id": "src_oxs",
                    "locator": str(source)[:2048],
                    "description": "OXS 文件内嵌 RGB；按显示近似色导入，未声明实体色卡测量来源。",
                    "permission_status": "private_use",
                }
            ],
        },
        "extensions": {
            "stitchforge.oxs_import": {
                "oxsversion": properties.get("oxsversion", ""),
                "software": properties.get("software", ""),
                "software_version": properties.get("software_version", ""),
                "coordinate_profile": coordinate_profile,
            }
        },
    }
    validation = validate_document(document)
    if not validation.valid:
        for diagnostic in validation.diagnostics:
            report.add(
                "IMPORTED_DOCUMENT_INVALID",
                diagnostic.message,
                path=diagnostic.path,
                rule_id=diagnostic.rule_id,
            )
        return OxsImportResult(None, report)
    report.palette_index_map = {index: index for index in sorted(recipe_id_by_palette)}
    return OxsImportResult(document, report)


def export_oxs(
    document: dict[str, Any],
    path: str | Path,
    *,
    coordinate_profile: str,
    confirm_loss: bool = False,
) -> OxsReport:
    report = OxsReport("export", coordinate_profile)
    if coordinate_profile != SUPPORTED_COORDINATE_PROFILE:
        report.add(
            "UNSUPPORTED_COORDINATE_PROFILE",
            f"当前只支持坐标配置 {SUPPORTED_COORDINATE_PROFILE}",
            path="$.coordinate_profile",
        )
        return report
    validation = validate_document(document)
    if not validation.valid:
        for diagnostic in validation.diagnostics:
            report.add(
                "DOCUMENT_INVALID",
                diagnostic.message,
                path=diagnostic.path,
                rule_id=diagnostic.rule_id,
            )
        return report

    losses = [
        (
            "TOP_LEG_NOT_EXPORTED",
            "OXS 1.0 没有当前实现可用的顶层针方向字段",
            "$.full_stitches.top_leg",
        ),
        (
            "PROVENANCE_NOT_EXPORTED",
            "OXS 1.0 不能完整保存来源、规则配置和哈希",
            "$.provenance",
        ),
    ]
    if document["regions"]:
        losses.append(
            (
                "REGIONS_NOT_EXPORTED",
                "OXS 1.0 不能保存 StitchForge 区域和锁定策略",
                "$.regions",
            )
        )
    if not confirm_loss:
        for code, message, issue_path in losses:
            report.add(code, message, path=issue_path)
        report.add(
            "LOSS_CONFIRMATION_REQUIRED",
            "确认损失报告后才能写入 OXS",
            path="$",
        )
        return report
    for code, message, issue_path in losses:
        report.add(code, message, path=issue_path, severity="warning")

    recipes = sorted(document["recipes"], key=lambda item: item["index"])
    material_by_id = {item["id"]: item for item in document["materials"]}
    export_index_by_recipe = {
        recipe["index"]: export_index for export_index, recipe in enumerate(recipes, start=1)
    }
    report.palette_index_map = export_index_by_recipe

    root = XmlWriter.Element("chart")
    XmlWriter.SubElement(
        root,
        "format",
        {
            "comments01": "Open Cross Stitch interchange file",
            "comments02": "Generated by StitchForge",
            "comments03": "Blank cells are omitted; palette item 0 is cloth",
            "comments04": "Full stitches and backstitches are explicit chart data",
        },
    )
    XmlWriter.SubElement(
        root,
        "properties",
        {
            "oxsversion": "1.0",
            "software": "StitchForge",
            "software_version": "0.1.0",
            "chartheight": str(document["grid"]["height"]),
            "chartwidth": str(document["grid"]["width"]),
            "charttitle": document["metadata"]["title"],
            "author": document["metadata"]["author"],
            "copyright": "",
            "instructions": "",
            "stitchesperinch": str(document["fabric"]["count"]),
            "stitchesperinch_y": str(document["fabric"]["count"]),
            "palettecount": str(len(recipes)),
        },
    )
    palette = XmlWriter.SubElement(root, "palette")
    cloth = document["fabric"]["display_srgb"].removeprefix("#")
    XmlWriter.SubElement(
        palette,
        "palette_item",
        {
            "index": "0",
            "number": "cloth",
            "name": "cloth",
            "color": cloth,
            "printcolor": cloth,
            "blendcolor": "nil",
            "comments": "",
            "strands": "2",
            "symbol": "100",
            "dashpattern": "",
            "misc1": "",
            "bsstrands": "1",
            "bscolor": cloth,
        },
    )
    for export_index, recipe in enumerate(recipes, start=1):
        component = recipe["components"][0]
        material = material_by_id[component["material_id"]]
        color = material["color"]["display_srgb"]
        if color is None:
            report.add(
                "UNKNOWN_COLOR_NOT_EXPORTABLE",
                f"材料 {material['id']} 没有可导出的 RGB",
                path=f"$.materials.{material['id']}.color.display_srgb",
            )
            return report
        rgb = color.removeprefix("#")
        XmlWriter.SubElement(
            palette,
            "palette_item",
            {
                "index": str(export_index),
                "number": f"{material['brand']} {material['code']}",
                "name": material["name"],
                "color": rgb,
                "printcolor": rgb,
                "blendcolor": "nil",
                "comments": "",
                "strands": str(component["strands"]),
                "symbol": str(export_index),
                "dashpattern": "",
                "misc1": "",
                "bsstrands": "1",
                "bscolor": rgb,
            },
        )

    stitches = XmlWriter.SubElement(root, "fullstitches")
    width = document["grid"]["width"]
    for offset, recipe_index in enumerate(document["full_stitches"]["cells"]):
        if recipe_index == 0:
            continue
        x, y = offset % width, offset // width
        XmlWriter.SubElement(
            stitches,
            "stitch",
            {
                "x": str(x),
                "y": str(y),
                "palindex": str(export_index_by_recipe[recipe_index]),
            },
        )
    XmlWriter.SubElement(root, "partstitches")
    XmlWriter.SubElement(root, "backstitches")
    XmlWriter.SubElement(root, "ornaments_inc_knots_and_beads")
    XmlWriter.SubElement(root, "commentboxes")
    XmlWriter.indent(root, space="  ")
    payload = XmlWriter.tostring(root, encoding="utf-8", xml_declaration=True)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return report
