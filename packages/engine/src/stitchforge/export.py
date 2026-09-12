from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

from .canonical import document_hash
from .io import write_json
from .pdf import export_chart_pdf
from .project import write_project
from .render import render_preview, render_symbol_chart
from .stats import calculate_statistics
from .symbols import symbol_for_id
from .validation import validate_document


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_materials_csv(document: dict[str, Any], output_path: Path) -> None:
    stats = calculate_statistics(document)
    materials = {item["id"]: item for item in document["materials"]}
    recipes = {item["id"]: item for item in document["recipes"]}
    legend = {item["recipe_id"]: item for item in document["legend"]}
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "symbol",
                "brand",
                "series",
                "code",
                "name",
                "strands",
                "full_stitches",
                "color_status",
            ]
        )
        for recipe_id, stitch_count in stats.recipe_stitch_counts.items():
            recipe = recipes[recipe_id]
            component = recipe["components"][0]
            material = materials[component["material_id"]]
            writer.writerow(
                [
                    symbol_for_id(legend[recipe_id]["symbol_id"]),
                    material["brand"],
                    material["series"],
                    material["code"],
                    material["name"],
                    component["strands"],
                    stitch_count,
                    material["color"]["status"],
                ]
            )
    temporary.replace(output_path)


def export_bundle(
    document: dict[str, Any],
    output_dir: str | Path,
    *,
    allow_test_fixture: bool = False,
) -> dict[str, Any]:
    report = validate_document(document, release=True, allow_test_fixture=allow_test_fixture)
    report.raise_for_errors()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    validation_path = target / "validation-report.json"
    write_json(validation_path, report.as_dict())
    stats_path = target / "statistics.json"
    write_json(stats_path, calculate_statistics(document).as_dict())
    project_path = write_project(document, target / "pattern.stitchforge")
    preview_path = render_preview(document, target / "preview.png")
    chart_color_png = render_symbol_chart(document, target / "chart-color.png", color=True)
    chart_bw_png = render_symbol_chart(document, target / "chart-bw.png", color=False)
    color_pdf = export_chart_pdf(document, target / "chart-color.pdf", color=True)
    bw_pdf = export_chart_pdf(document, target / "chart-bw.pdf", color=False)
    materials_path = target / "materials.csv"
    _write_materials_csv(document, materials_path)

    artifacts = [
        validation_path,
        stats_path,
        project_path,
        preview_path,
        chart_color_png,
        chart_bw_png,
        color_pdf,
        bw_pdf,
        materials_path,
    ]
    manifest = {
        "release_format_version": 1,
        "document_hash": document_hash(document),
        "test_fixture": document["metadata"]["purpose"] == "test_fixture",
        "files": [
            {
                "path": path.name,
                "sha256": _sha256(path),
                "byte_length": path.stat().st_size,
            }
            for path in artifacts
        ],
    }
    write_json(target / "manifest.json", manifest)
    return manifest
