from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from stitchforge.oxs import (
    SUPPORTED_COORDINATE_PROFILE,
    export_oxs,
    import_oxs,
)

PROFILE = SUPPORTED_COORDINATE_PROFILE
FIXED_TIME = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _oxs(
    *,
    stitches: str = '<stitch x="0" y="0" palindex="1"/><stitch x="2" y="1" palindex="2"/>',
    backstitches: str = "",
    partstitches: str = "",
    ornaments: str = "",
    extra: str = "",
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<chart>
  <properties oxsversion="1.0" software="Test" software_version="1"
    chartheight="2" chartwidth="3" charttitle="非对称 OXS" author="Tester"
    stitchesperinch="14" stitchesperinch_y="14" palettecount="2"/>
  <palette>
    <palette_item index="0" number="cloth" name="cloth" color="DDD5C5" strands="2"/>
    <palette_item index="1" number="DMC 310" name="Black" color="000000" strands="2"/>
    <palette_item index="2" number="DMC B5200" name="White" color="FFFFFF" strands="1"/>
  </palette>
  <fullstitches>{stitches}</fullstitches>
  <partstitches>{partstitches}</partstitches>
  <backstitches>{backstitches}</backstitches>
  <ornaments_inc_knots_and_beads>{ornaments}</ornaments_inc_knots_and_beads>
  {extra}
</chart>
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_import_oxs_maps_coordinates_palette_and_provenance(tmp_path: Path) -> None:
    source = _write(tmp_path / "asymmetric.oxs", _oxs())

    result = import_oxs(source, coordinate_profile=PROFILE, imported_at=FIXED_TIME)

    assert result.document is not None
    assert result.report.status == "success_with_reported_loss"
    assert result.document["grid"] == {
        "width": 3,
        "height": 2,
        "origin": "top_left",
        "subgrid_scale": 2,
    }
    assert result.document["full_stitches"]["cells"] == [1, 0, 0, 0, 0, 2]
    assert [(item["brand"], item["code"]) for item in result.document["materials"]] == [
        ("DMC", "310"),
        ("DMC", "B5200"),
    ]
    assert [item["components"][0]["strands"] for item in result.document["recipes"]] == [
        2,
        1,
    ]
    assert result.document["metadata"]["created_at"] == "2026-09-11T12:00:00Z"
    assert len(result.document["provenance"]["source_asset_hashes"][0]) == 64
    assert _codes(result.report) == {"TOP_LEG_DEFAULTED", "MARGINS_DEFAULTED"}


@pytest.mark.parametrize(
    ("section", "code"),
    [
        ("part", "UNSUPPORTED_PART_STITCH"),
        ("back", "UNSUPPORTED_BACKSTITCH"),
        ("ornament", "UNSUPPORTED_ORNAMENT"),
        ("extension", "UNSUPPORTED_EXTENSION"),
    ],
)
def test_import_blocks_unsupported_stitch_semantics(
    tmp_path: Path, section: str, code: str
) -> None:
    changes = {
        "part": {"partstitches": '<partstitch x="0" y="0" palindex="1"/>'},
        "back": {"backstitches": '<backstitch x1="0" y1="0" x2="1" y2="1" palindex="1"/>'},
        "ornament": {"ornaments": '<object x1="0" y1="0" palindex="1" objecttype="knot"/>'},
        "extension": {"extra": "<vendor_stitches><stitch/></vendor_stitches>"},
    }[section]
    source = _write(tmp_path / f"{section}.oxs", _oxs(**changes))

    result = import_oxs(source, coordinate_profile=PROFILE, imported_at=FIXED_TIME)

    assert result.document is None
    assert result.report.blocked
    assert code in _codes(result.report)


def test_import_rejects_dtd_and_entity(tmp_path: Path) -> None:
    malicious = """<?xml version="1.0"?>
<!DOCTYPE chart [<!ENTITY leak SYSTEM "file:///etc/passwd">]>
<chart><properties chartwidth="1" chartheight="1" stitchesperinch="14"
stitchesperinch_y="14"/><palette/><fullstitches>&leak;</fullstitches><backstitches/></chart>
"""
    source = _write(tmp_path / "entity.oxs", malicious)

    result = import_oxs(source, coordinate_profile=PROFILE, imported_at=FIXED_TIME)

    assert result.document is None
    assert _codes(result.report) == {"UNSAFE_OR_INVALID_XML"}


@pytest.mark.parametrize(
    ("stitches", "code"),
    [
        (
            '<stitch x="0" y="0" palindex="1"/><stitch x="0" y="0" palindex="1"/>',
            "DUPLICATE_STITCH",
        ),
        ('<stitch x="3" y="0" palindex="1"/>', "INVALID_INTEGER"),
        ('<stitch x="0" y="0" palindex="99"/>', "UNKNOWN_PALETTE_INDEX"),
    ],
)
def test_import_rejects_invalid_stitches(tmp_path: Path, stitches: str, code: str) -> None:
    source = _write(tmp_path / "invalid.oxs", _oxs(stitches=stitches))

    result = import_oxs(source, coordinate_profile=PROFILE, imported_at=FIXED_TIME)

    assert result.document is None
    assert code in _codes(result.report)


def test_import_rejects_wrong_coordinate_profile(tmp_path: Path) -> None:
    source = _write(tmp_path / "profile.oxs", _oxs())

    result = import_oxs(source, coordinate_profile="guessed")

    assert result.document is None
    assert _codes(result.report) == {"UNSUPPORTED_COORDINATE_PROFILE"}


def test_export_requires_explicit_loss_confirmation(minimal_document: dict, tmp_path: Path) -> None:
    target = tmp_path / "blocked.oxs"

    report = export_oxs(
        minimal_document,
        target,
        coordinate_profile=PROFILE,
    )

    assert report.blocked
    assert "LOSS_CONFIRMATION_REQUIRED" in _codes(report)
    assert not target.exists()


def test_export_and_reimport_preserve_full_stitch_geometry_and_materials(
    minimal_document: dict, tmp_path: Path
) -> None:
    target = tmp_path / "roundtrip.oxs"
    export_report = export_oxs(
        minimal_document,
        target,
        coordinate_profile=PROFILE,
        confirm_loss=True,
    )

    assert not export_report.blocked
    assert export_report.status == "success_with_reported_loss"
    content = target.read_text(encoding="utf-8")
    assert "<format " in content
    assert "<partstitches />" in content
    assert "<backstitches />" in content
    assert "<ornaments_inc_knots_and_beads />" in content
    assert "<commentboxes />" in content
    assert 'bsstrands="1"' in content
    assert '<stitch x="0" y="0" palindex="1" />' in content
    assert '<stitch x="3" y="2" palindex="1" />' in content

    imported = import_oxs(target, coordinate_profile=PROFILE, imported_at=FIXED_TIME)
    assert imported.document is not None
    assert imported.document["grid"] == minimal_document["grid"]
    assert imported.document["full_stitches"]["cells"] == minimal_document["full_stitches"]["cells"]
    assert [item["code"] for item in imported.document["materials"]] == ["BLACK", "WHITE"]


def test_oxs_input_size_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = _write(tmp_path / "large.oxs", _oxs())
    monkeypatch.setattr("stitchforge.oxs.MAX_OXS_BYTES", 10)

    result = import_oxs(source, coordinate_profile=PROFILE)

    assert result.document is None
    assert _codes(result.report) == {"INPUT_TOO_LARGE"}


def test_large_oxs_import_export_retains_last_row_and_column(tmp_path: Path) -> None:
    text = _oxs(stitches='<stitch x="479" y="303" palindex="2"/><stitch x="0" y="0" palindex="1"/>')
    text = text.replace('chartwidth="3"', 'chartwidth="480"').replace(
        'chartheight="2"', 'chartheight="304"'
    )
    source = _write(tmp_path / "large.oxs", text)
    result = import_oxs(source, coordinate_profile=PROFILE)
    assert not result.report.blocked
    document = result.document
    assert document is not None
    assert len(document["full_stitches"]["cells"]) == 145_920
    assert document["full_stitches"]["cells"][0] == 1
    assert document["full_stitches"]["cells"][-1] == 2
    target = tmp_path / "large-roundtrip.oxs"
    report = export_oxs(document, target, coordinate_profile=PROFILE, confirm_loss=True)
    assert not report.blocked
    roundtrip = import_oxs(target, coordinate_profile=PROFILE)
    assert roundtrip.document is not None
    assert roundtrip.document["full_stitches"] == document["full_stitches"]
