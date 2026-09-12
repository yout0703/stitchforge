from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from stitchforge.catalog import CatalogValidationError, load_catalog, validate_catalog

ROOT = Path(__file__).resolve().parents[1]


def _catalog_document() -> dict:
    return json.loads(
        (ROOT / "examples/synthetic-material-catalog.json").read_text(encoding="utf-8")
    )


def test_load_catalog_filters_unmatchable_colors() -> None:
    catalog = load_catalog(ROOT / "examples/synthetic-material-catalog.json")
    assert catalog.snapshot_id == "synthetic_basic_v1"
    assert len(catalog.materials) == 7
    assert len(catalog.matchable_materials()) == 6
    assert "test_unknown" not in {item["id"] for item in catalog.matchable_materials()}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["materials"].__setitem__(
                1, deepcopy(value["materials"][0])
            ),
            "ID 重复",
        ),
        (
            lambda value: value["materials"][0].__setitem__("display_srgb", None),
            "unknown 状态",
        ),
        (lambda value: value["source"].__setitem__("id", "src_image"), "保留值"),
    ],
)
def test_catalog_semantic_errors_are_rejected(mutation, message) -> None:
    document = _catalog_document()
    mutation(document)
    with pytest.raises(CatalogValidationError, match=message):
        validate_catalog(document)


def test_catalog_schema_and_invalid_json_are_rejected(tmp_path: Path) -> None:
    document = _catalog_document()
    document["materials"][0]["display_srgb"] = "#bad"
    with pytest.raises(CatalogValidationError, match="not valid"):
        validate_catalog(document)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(CatalogValidationError, match="UTF-8 JSON"):
        load_catalog(invalid)
