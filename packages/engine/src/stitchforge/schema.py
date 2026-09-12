from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

SCHEMA_FILENAME = "pattern-document.v0.1.schema.json"
CATALOG_SCHEMA_FILENAME = "material-catalog.v0.1.schema.json"


def _schema_candidates(filename: str = SCHEMA_FILENAME) -> list[Path]:
    module_path = Path(__file__).resolve()
    candidates = [Path.cwd() / "schemas" / filename]
    candidates.extend(parent / "schemas" / filename for parent in module_path.parents)
    return candidates


@lru_cache(maxsize=4)
def _load_schema_file(filename: str) -> dict[str, Any]:
    packaged = files("stitchforge").joinpath("resources", filename)
    if packaged.is_file():
        with packaged.open("r", encoding="utf-8") as stream:
            schema = json.load(stream)
        if not isinstance(schema, dict):
            raise RuntimeError(f"打包的 Schema 不是 JSON 对象: {packaged}")
        return schema
    for candidate in _schema_candidates(filename):
        if candidate.is_file():
            with candidate.open("r", encoding="utf-8") as stream:
                schema = json.load(stream)
            if not isinstance(schema, dict):
                raise RuntimeError(f"Schema 不是 JSON 对象: {candidate}")
            return schema
    checked = ", ".join(str(path) for path in _schema_candidates(filename))
    raise FileNotFoundError(f"找不到 {filename}，已检查: {checked}")


def load_schema() -> dict[str, Any]:
    return _load_schema_file(SCHEMA_FILENAME)


def load_catalog_schema() -> dict[str, Any]:
    return _load_schema_file(CATALOG_SCHEMA_FILENAME)
