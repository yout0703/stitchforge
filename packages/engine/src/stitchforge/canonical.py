from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


def _normalise_spans(spans: list[dict[str, Any]]) -> list[dict[str, int]]:
    ordered = sorted(spans, key=lambda span: (span["row"], span["start_col"], span["end_col"]))
    merged: list[dict[str, int]] = []
    for span in ordered:
        current = {
            "row": int(span["row"]),
            "start_col": int(span["start_col"]),
            "end_col": int(span["end_col"]),
        }
        if (
            merged
            and merged[-1]["row"] == current["row"]
            and current["start_col"] <= merged[-1]["end_col"]
        ):
            merged[-1]["end_col"] = max(merged[-1]["end_col"], current["end_col"])
        else:
            merged.append(current)
    return merged


def normalize_document(document: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(document)
    normalized["materials"] = sorted(normalized.get("materials", []), key=lambda item: item["id"])
    normalized["recipes"] = sorted(normalized.get("recipes", []), key=lambda item: item["index"])
    for recipe in normalized["recipes"]:
        recipe["components"] = sorted(
            recipe.get("components", []), key=lambda item: (item["material_id"], item["strands"])
        )
    normalized["regions"] = sorted(normalized.get("regions", []), key=lambda item: item["id"])
    for region in normalized["regions"]:
        region["spans"] = _normalise_spans(region.get("spans", []))
    normalized["legend"] = sorted(normalized.get("legend", []), key=lambda item: item["recipe_id"])
    provenance = normalized.get("provenance", {})
    provenance["source_asset_hashes"] = sorted(provenance.get("source_asset_hashes", []))
    provenance["sources"] = sorted(provenance.get("sources", []), key=lambda item: item["id"])
    return normalized


def canonical_document_bytes(document: dict[str, Any]) -> bytes:
    normalized = normalize_document(document)
    text = json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return text.encode("utf-8")


def document_hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_document_bytes(document)).hexdigest()
