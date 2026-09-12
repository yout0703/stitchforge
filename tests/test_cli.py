from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from stitchforge.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_cli_validate_stats_and_hash(capsys) -> None:
    source = ROOT / "examples/minimal-pattern.json"

    assert main(["validate", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(["stats", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["full_stitch_count"] == 6

    assert main(["hash", str(source)]) == 0
    assert len(capsys.readouterr().out.strip()) == 64


def test_cli_oxs_import_export_and_reports(tmp_path, capsys) -> None:
    oxs_source = ROOT / "examples/minimal-pattern.oxs"
    imported = tmp_path / "imported.json"
    import_report = tmp_path / "import-report.json"
    profile = "oxs-v1-zero-based"

    assert (
        main(
            [
                "import-oxs",
                str(oxs_source),
                "--output",
                str(imported),
                "--report",
                str(import_report),
                "--coordinate-profile",
                profile,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert imported.exists()
    assert json.loads(import_report.read_text(encoding="utf-8"))["blocked"] is False

    blocked_output = tmp_path / "blocked.oxs"
    assert (
        main(
            [
                "export-oxs",
                str(imported),
                "--output",
                str(blocked_output),
                "--coordinate-profile",
                profile,
            ]
        )
        == 2
    )
    assert not blocked_output.exists()
    capsys.readouterr()

    exported = tmp_path / "exported.oxs"
    export_report = tmp_path / "export-report.json"
    assert (
        main(
            [
                "export-oxs",
                str(imported),
                "--output",
                str(exported),
                "--report",
                str(export_report),
                "--coordinate-profile",
                profile,
                "--confirm-loss",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert exported.exists()
    assert json.loads(export_report.read_text(encoding="utf-8"))["status"] == (
        "success_with_reported_loss"
    )


def test_cli_returns_error_for_invalid_document(tmp_path, capsys) -> None:
    source = tmp_path / "invalid.json"
    source.write_text("{}", encoding="utf-8")

    assert main(["hash", str(source)]) == 2
    assert "错误:" in capsys.readouterr().err


def test_cli_exports_complete_bundle(tmp_path, capsys) -> None:
    source = ROOT / "examples/minimal-pattern.json"
    output = tmp_path / "bundle"

    assert (
        main(
            [
                "export",
                str(source),
                "--output-dir",
                str(output),
                "--allow-test-fixture",
            ]
        )
        == 0
    )
    manifest = json.loads(capsys.readouterr().out)
    assert len(manifest["files"]) == 9
    assert (output / "chart-bw.pdf").exists()


def test_cli_normalizes_and_samples_image(tmp_path, capsys) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    report = tmp_path / "normalization.json"
    sample = tmp_path / "sample.json"
    cropped = tmp_path / "cropped.png"
    masked = tmp_path / "masked.png"
    mask = tmp_path / "mask.png"
    Image.new("RGBA", (4, 2), (255, 0, 0, 255)).save(source)
    Image.new("L", (4, 2), 255).save(mask)

    assert (
        main(
            [
                "normalize-image",
                str(source),
                "--output",
                str(normalized),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert json.loads(report.read_text(encoding="utf-8"))["normalized_width"] == 4

    assert (
        main(
            [
                "crop-image",
                str(normalized),
                "--box",
                "0,0,4,2",
                "--output",
                str(cropped),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "mask-image",
                str(cropped),
                "--mask",
                str(mask),
                "--output",
                str(masked),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "sample-image",
                str(masked),
                "--grid",
                "2x1",
                "--output",
                str(sample),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert json.loads(sample.read_text(encoding="utf-8"))["display_srgb"] == [
        "#FF0000",
        "#FF0000",
    ]

    pattern = tmp_path / "pattern.json"
    palette_report = tmp_path / "palette.json"
    assert (
        main(
            [
                "build-pattern",
                str(sample),
                "--catalog",
                str(ROOT / "examples/synthetic-material-catalog.json"),
                "--color-budget",
                "2",
                "--minimum-gain",
                "0",
                "--pattern-id",
                "cli_generated",
                "--title",
                "CLI 图片打版",
                "--fabric-count",
                "20",
                "--strands",
                "1",
                "--purpose",
                "test_fixture",
                "--output",
                str(pattern),
                "--report",
                str(palette_report),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["validate", str(pattern)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    document = json.loads(pattern.read_text(encoding="utf-8"))
    assert document["fabric"]["count"] == 20
    assert document["recipes"][0]["components"][0]["strands"] == 1


def test_cli_plans_grid_candidates(capsys) -> None:
    assert (
        main(
            [
                "plan-grid",
                "--image-width",
                "4",
                "--image-height",
                "3",
                "--max-width-mm",
                "254",
                "--max-height-mm",
                "254",
                "--fabric-counts",
                "10,20",
            ]
        )
        == 0
    )
    plans = json.loads(capsys.readouterr().out)["plans"]
    assert [(item["fabric_count"], item["width_cells"]) for item in plans] == [
        (10, 100),
        (20, 200),
    ]


def test_cli_optionally_removes_connected_border_background(tmp_path, capsys) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    sample = tmp_path / "sample.json"
    background_report = tmp_path / "background.json"
    image = Image.new("RGB", (5, 5), "#EEEEEE")
    image.putpixel((2, 2), (180, 20, 20))
    image.save(source)
    assert main(["normalize-image", str(source), "--output", str(normalized)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "sample-image",
                str(normalized),
                "--grid",
                "5x5",
                "--remove-border-background",
                "--background-report",
                str(background_report),
                "--output",
                str(sample),
            ]
        )
        == 0
    )
    capsys.readouterr()
    sampled = json.loads(sample.read_text(encoding="utf-8"))
    report = json.loads(background_report.read_text(encoding="utf-8"))
    assert sum(color is not None for color in sampled["display_srgb"]) == 1
    assert report["removed_cells"] == 24


def test_cli_background_report_requires_removal_flag(tmp_path, capsys) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    Image.new("RGB", (2, 2), "white").save(source)
    assert main(["normalize-image", str(source), "--output", str(normalized)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "sample-image",
                str(normalized),
                "--grid",
                "2x2",
                "--background-report",
                str(tmp_path / "report.json"),
                "--output",
                str(tmp_path / "sample.json"),
            ]
        )
        == 2
    )
    assert "--background-report" in capsys.readouterr().err


def test_cli_recovers_rendered_stitch_colors_and_builds_clustered_palette(
    tmp_path, capsys
) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    sample = tmp_path / "sample.json"
    recovery_report = tmp_path / "recovery.json"
    pattern = tmp_path / "pattern.json"
    palette_report = tmp_path / "palette.json"
    Image.new("RGB", (2, 1), "#E0D8CC").save(source)
    assert main(["normalize-image", str(source), "--output", str(normalized)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "sample-image",
                str(normalized),
                "--grid",
                "2x1",
                "--source-profile",
                "rendered-stitch",
                "--recovery-report",
                str(recovery_report),
                "--output",
                str(sample),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert json.loads(recovery_report.read_text(encoding="utf-8"))["adjusted_cells"] == 2

    assert (
        main(
            [
                "build-pattern",
                str(sample),
                "--catalog",
                str(ROOT / "examples/synthetic-material-catalog.json"),
                "--color-budget",
                "2",
                "--palette-strategy",
                "clustered",
                "--pattern-id",
                "clustered-cli",
                "--title",
                "聚类配色",
                "--purpose",
                "test_fixture",
                "--output",
                str(pattern),
                "--report",
                str(palette_report),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert json.loads(palette_report.read_text(encoding="utf-8"))["algorithm"] == (
        "lab-cluster-then-material-ciede2000-v1"
    )
