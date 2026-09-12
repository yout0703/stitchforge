from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .schema import load_catalog_schema


class CatalogValidationError(ValueError):
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("; ".join(messages))


@dataclass(frozen=True, slots=True)
class MaterialCatalog:
    snapshot_id: str
    source: dict[str, Any]
    materials: tuple[dict[str, Any], ...]

    def material_by_id(self) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in self.materials}

    def matchable_materials(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            item
            for item in self.materials
            if item["available"] and item["display_srgb"] is not None
        )


def validate_catalog(document: Any) -> None:
    validator = Draft202012Validator(load_catalog_schema())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    messages = [f"{list(error.absolute_path)}: {error.message}" for error in errors]
    if errors or not isinstance(document, dict):
        raise CatalogValidationError(messages or ["材料目录顶层必须是对象"])
    ids = [item["id"] for item in document["materials"]]
    duplicate_ids = sorted(item for item, count in Counter(ids).items() if count > 1)
    identities = [
        (item["brand"], item["series"], item["code"]) for item in document["materials"]
    ]
    duplicate_identities = sorted(
        identity for identity, count in Counter(identities).items() if count > 1
    )
    if duplicate_ids:
        messages.append(f"材料 ID 重复: {duplicate_ids}")
    if duplicate_identities:
        messages.append(f"材料品牌/系列/线号重复: {duplicate_identities}")
    for position, material in enumerate(document["materials"]):
        unknown = material["color_status"] == "unknown"
        if unknown != (material["display_srgb"] is None):
            messages.append(
                f"materials[{position}] unknown 状态必须且只能使用 null display_srgb"
            )
    if document["source"]["id"] == "src_image":
        messages.append("材料目录 source.id 不能使用保留值 src_image")
    if messages:
        raise CatalogValidationError(messages)


def load_catalog(path: str | Path) -> MaterialCatalog:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogValidationError([f"材料目录不是有效 UTF-8 JSON: {error}"]) from error
    validate_catalog(document)
    return MaterialCatalog(
        snapshot_id=document["snapshot_id"],
        source=document["source"],
        materials=tuple(document["materials"]),
    )
