from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from .errors import Diagnostic, DocumentValidationError
from .schema import load_schema


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    diagnostics: tuple[Diagnostic, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "diagnostics": [diagnostic.as_dict() for diagnostic in self.diagnostics],
        }

    def raise_for_errors(self) -> None:
        errors = [item for item in self.diagnostics if item.severity == "error"]
        if errors:
            raise DocumentValidationError(errors)


def _json_path(parts: Iterable[object]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _duplicates(values: Iterable[str | int]) -> set[str | int]:
    counts = Counter(values)
    return {value for value, count in counts.items() if count > 1}


def _error(
    diagnostics: list[Diagnostic],
    rule_id: str,
    message: str,
    path: str = "$",
    **details: Any,
) -> None:
    diagnostics.append(Diagnostic(rule_id, message, path, details=details or None))


def _schema_error_message(error: ValidationError) -> str:
    # jsonschema includes the entire instance in many default messages. A needle
    # array can contain hundreds of thousands of entries; report its size instead.
    path = _json_path(error.absolute_path)
    if error.validator in {"maxItems", "minItems"}:
        bound = "最多" if error.validator == "maxItems" else "至少"
        return f"{path} 包含 {len(error.instance):,} 项，{bound}允许 {error.validator_value:,} 项"
    if error.validator in {"maxLength", "minLength"}:
        bound = "最多" if error.validator == "maxLength" else "至少"
        return (
            f"{path} 包含 {len(error.instance):,} 个字符，"
            f"{bound}允许 {error.validator_value:,} 个字符"
        )
    message = error.message
    if len(message) > 320:
        message = message[:150] + "…（内容已省略）…" + message[-150:]
    return message


def _validate_schema(document: dict[str, Any]) -> list[Diagnostic]:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    diagnostics: list[Diagnostic] = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path)):
        diagnostics.append(
            Diagnostic(
                rule_id="SCHEMA_VALIDATION",
                message=_schema_error_message(error),
                path=_json_path(error.absolute_path),
                details={"validator": error.validator},
            )
        )
    return diagnostics


def validate_document(
    document: dict[str, Any],
    *,
    release: bool = False,
    allow_test_fixture: bool = False,
) -> ValidationReport:
    diagnostics = _validate_schema(document)
    if diagnostics:
        return ValidationReport(False, tuple(diagnostics))

    grid = document["grid"]
    width, height = grid["width"], grid["height"]
    cells = document["full_stitches"]["cells"]
    if len(cells) != width * height:
        _error(
            diagnostics,
            "GRID_SHAPE",
            f"针格数组长度 {len(cells)} 与 {width}×{height} 不一致",
            "$.full_stitches.cells",
            expected=width * height,
            actual=len(cells),
        )

    materials = document["materials"]
    recipes = document["recipes"]
    sources = document["provenance"]["sources"]
    material_ids = [item["id"] for item in materials]
    recipe_ids = [item["id"] for item in recipes]
    recipe_indices = [item["index"] for item in recipes]
    source_ids = [item["id"] for item in sources]
    region_ids = [item["id"] for item in document["regions"]]

    for label, values, path in (
        ("材料 ID", material_ids, "$.materials"),
        ("配方 ID", recipe_ids, "$.recipes"),
        ("配方 index", recipe_indices, "$.recipes"),
        ("来源 ID", source_ids, "$.provenance.sources"),
        ("区域 ID", region_ids, "$.regions"),
    ):
        duplicate_values = sorted(_duplicates(values), key=str)
        if duplicate_values:
            _error(
                diagnostics,
                "UNIQUE_IDS",
                f"{label} 重复: {duplicate_values}",
                path,
                duplicates=duplicate_values,
            )

    material_by_id = {item["id"]: item for item in materials}
    recipe_by_index = {item["index"]: item for item in recipes}
    recipe_by_id = {item["id"]: item for item in recipes}
    source_id_set = set(source_ids)

    normalized_recipes: dict[tuple[tuple[str, int], ...], str] = {}
    for position, recipe in enumerate(recipes):
        composition = tuple(
            sorted(
                (component["material_id"], component["strands"])
                for component in recipe["components"]
            )
        )
        if composition in normalized_recipes:
            _error(
                diagnostics,
                "UNIQUE_IDS",
                f"配方 {recipe['id']} 与 {normalized_recipes[composition]} 组成相同",
                f"$.recipes[{position}]",
            )
        normalized_recipes[composition] = recipe["id"]
        for component_index, component in enumerate(recipe["components"]):
            if component["material_id"] not in material_by_id:
                _error(
                    diagnostics,
                    "RECIPE_EXISTS",
                    f"材料 {component['material_id']} 不存在",
                    f"$.recipes[{position}].components[{component_index}].material_id",
                )

    for position, material in enumerate(materials):
        color = material["color"]
        if color["status"] == "unknown":
            if color["display_srgb"] is not None or color["source_ref"] is not None:
                _error(
                    diagnostics,
                    "MATERIAL_COLOR_STATE",
                    "unknown 材料不能带显示色或颜色来源",
                    f"$.materials[{position}].color",
                )
        else:
            if color["display_srgb"] is None or color["source_ref"] is None:
                _error(
                    diagnostics,
                    "MATERIAL_COLOR_STATE",
                    f"{color['status']} 材料必须有显示色和来源",
                    f"$.materials[{position}].color",
                )
            elif color["source_ref"] not in source_id_set:
                _error(
                    diagnostics,
                    "MATERIAL_SOURCE_EXISTS",
                    f"颜色来源 {color['source_ref']} 不存在",
                    f"$.materials[{position}].color.source_ref",
                )

    unknown_indices = sorted({cell for cell in cells if cell != 0 and cell not in recipe_by_index})
    if unknown_indices:
        _error(
            diagnostics,
            "RECIPE_EXISTS",
            f"针格引用不存在的配方 index: {unknown_indices}",
            "$.full_stitches.cells",
            indices=unknown_indices,
        )

    used_indices = {cell for cell in cells if cell != 0}
    used_recipe_ids = {
        recipe_by_index[index]["id"] for index in used_indices if index in recipe_by_index
    }
    if len(used_recipe_ids) > document["constraints"]["color_budget"]:
        _error(
            diagnostics,
            "PALETTE_BUDGET",
            "实际使用配方数超过颜色预算",
            "$.constraints.color_budget",
            used=len(used_recipe_ids),
            budget=document["constraints"]["color_budget"],
        )

    legend = document["legend"]
    legend_recipe_ids = [item["recipe_id"] for item in legend]
    for position, item in enumerate(legend):
        if item["recipe_id"] not in recipe_by_id:
            _error(
                diagnostics,
                "LEGEND_COMPLETE",
                f"图例引用不存在的配方 {item['recipe_id']}",
                f"$.legend[{position}].recipe_id",
            )
    missing_legend = sorted(used_recipe_ids - set(legend_recipe_ids))
    duplicate_legend = sorted(_duplicates(legend_recipe_ids), key=str)
    duplicate_symbols = sorted(_duplicates(item["symbol_id"] for item in legend), key=str)
    if missing_legend or duplicate_legend or duplicate_symbols:
        _error(
            diagnostics,
            "LEGEND_COMPLETE",
            "实际使用配方必须有唯一图例，图例符号也必须唯一",
            "$.legend",
            missing=missing_legend,
            duplicate_recipes=duplicate_legend,
            duplicate_symbols=duplicate_symbols,
        )

    for region_position, region in enumerate(document["regions"]):
        previous: tuple[int, int, int] | None = None
        for span_position, span in enumerate(region["spans"]):
            current = (span["row"], span["start_col"], span["end_col"])
            path = f"$.regions[{region_position}].spans[{span_position}]"
            if not (
                0 <= span["row"] < height and 0 <= span["start_col"] < span["end_col"] <= width
            ):
                _error(diagnostics, "REGION_BOUNDS", "区域行区间越界或为空", path)
            if previous is not None:
                ordered = current > previous
                separated = current[0] != previous[0] or current[1] > previous[2]
                if not ordered or not separated:
                    _error(
                        diagnostics,
                        "REGION_BOUNDS",
                        "区域区间必须排序，同行相邻或交叠区间应先合并",
                        path,
                    )
            previous = current

    if release:
        if not used_indices:
            _error(diagnostics, "NONEMPTY_RELEASE", "空图不能生成正式交付包")
        purpose = document["metadata"]["purpose"]
        synthetic = [item["id"] for item in materials if item["color"]["status"] == "synthetic"]
        if (purpose == "test_fixture" or synthetic) and not allow_test_fixture:
            _error(
                diagnostics,
                "PRODUCTION_MATERIALS",
                "测试工程或 synthetic 材料不能生成正式交付包",
                synthetic_materials=synthetic,
            )

    valid = not any(item.severity == "error" for item in diagnostics)
    return ValidationReport(valid, tuple(diagnostics))
