from __future__ import annotations

import hashlib
import struct
from copy import deepcopy

import pytest
from conftest import AccountClient
from stitchforge.cli import main
from stitchforge.xsd import MAX_XSD_BYTES, export_xsd, import_xsd
from stitchforge_api import create_app


def native_sample() -> bytes:
    """Independent binary-layout fixture; deliberately does not use our writer.

    4x3, DMC 310/B5200, 16 CT, a nonzero cipher seed, asymmetric empty cells.
    Offsets describe a palette with no thread notes, unlike our exported files.
    """
    data = bytearray(49364)
    struct.pack_into("<H", data, 0, 0x0510)
    struct.pack_into("<4H", data, 6, 4, 4, 1, 0)
    struct.pack_into("<HHIHHH6xH", data, 741, 4, 3, 0, 0, 16, 16, 2)
    data[766:770] = b"310\0"
    data[777:783] = b"Black\0"
    data[889:895] = b"B5200\0"
    data[900:906] = b"White\0"
    data[941:944] = bytes.fromhex("FFFFFF")
    struct.pack_into("<H", data, 1049, 3)  # Palette 0 override: three strands.
    data[29662:29665] = bytes.fromhex("EAE3D1")
    data[29730:29740] = b"Reference\0"
    data[29771:29777] = b"Maker\0"
    data[32109:32114] = b"Aida\0"
    struct.pack_into("<H", data, 32356, 2)  # Palette 1 inherits two strands.
    # Fixed encrypted words from a nonzero 4-word seed, no run compression.
    stream = bytes.fromhex(
        "f22db41db55b5825bd0422642917f47f0c000000db0fefefcb40abc4ceaa74b1"
        "e8552caff8d971f7f394a47f827158dfb6eba8c80b268f5cfd425040680a8014ea5a365c"
    )
    return bytes(data) + stream + bytes(4)


def test_import_independent_encrypted_sample(tmp_path):
    path = tmp_path / "reference.xsd"
    path.write_bytes(native_sample())
    result = import_xsd(path)
    assert not result.report.blocked, result.report.as_dict()
    document = result.document
    assert document["grid"]["width"] == 4 and document["grid"]["height"] == 3
    assert document["full_stitches"]["cells"] == [1, 0, 2, 0, 0, 1, 2, 0, 2, 0, 0, 1]
    assert document["metadata"]["title"] == "Reference"
    assert document["metadata"]["author"] == "Maker"
    assert document["fabric"]["count"] == 16
    assert document["fabric"]["display_srgb"] == "#EAE3D1"
    assert [m["code"] for m in document["materials"]] == ["310", "B5200"]
    assert [m["brand"] for m in document["materials"]] == ["DMC", "DMC"]
    assert [r["components"][0]["strands"] for r in document["recipes"]] == [3, 2]
    assert document["provenance"]["source_asset_hashes"] == [
        hashlib.sha256(path.read_bytes()).hexdigest()
    ]


def test_xsd_77_colours_import_save_and_download(tmp_path, many_color_document):
    path = tmp_path / "77-colours.xsd"
    assert not export_xsd(many_color_document, path, confirm_loss=True).blocked
    imported = import_xsd(path)
    assert imported.document is not None, imported.report.as_dict()
    assert imported.document["constraints"]["color_budget"] == 77
    assert imported.document["full_stitches"]["cells"] == list(range(1, 78))
    with AccountClient(create_app(tmp_path / "data")) as client:
        response = client.post("/api/v1/projects/import-xsd", content=path.read_bytes())
        assert response.status_code == 201, response.text
        body = response.json()
        pid = body["project"]["project_id"]
        revision = body["initial_revision"]
        saved = client.post(
            f"/api/v1/projects/{pid}/revisions",
            json={
                "expected_head_revision_id": revision["revision_id"],
                "document": revision["document"],
            },
        )
        assert saved.status_code == 201, saved.text
        for format in ("xsd", "oxs", "png"):
            downloaded = client.get(
                f"/api/v1/projects/{pid}/revisions/{saved.json()['revision_id']}/download/{format}"
            )
            assert downloaded.status_code == 200, downloaded.text[:500]
            if format == "xsd":
                path.write_bytes(downloaded.content)
                assert import_xsd(path).document["full_stitches"]["cells"] == list(range(1, 78))


def test_roundtrip_sparse_indices_and_custom_thread_identity(tmp_path, minimal_document):
    document = deepcopy(minimal_document)
    document["metadata"]["title"] = "非对称测试"
    document["recipes"][1]["index"] = 99
    document["recipes"][1]["components"][0]["strands"] = 4
    document["materials"][1]["code"] = "CUSTOM-CODE-LONGER-THAN-TEN"
    document["full_stitches"]["cells"] = [
        99 if v == 2 else v for v in document["full_stitches"]["cells"]
    ]
    path = tmp_path / "roundtrip.xsd"
    report = export_xsd(document, path, confirm_loss=True)
    assert not report.blocked
    assert report.palette_index_map == {1: 0, 99: 1}
    assert path.read_bytes().startswith(b"\x10\x05")
    loaded = import_xsd(path).document
    assert loaded["full_stitches"]["cells"] == minimal_document["full_stitches"]["cells"]
    assert loaded["grid"] == document["grid"]
    assert loaded["fabric"] == document["fabric"]
    assert loaded["metadata"]["title"] == document["metadata"]["title"]
    for original, actual in zip(document["materials"], loaded["materials"], strict=True):
        for field in ("brand", "code", "name"):
            assert original[field] == actual[field]
        assert original["color"]["display_srgb"] == actual["color"]["display_srgb"]
    assert [r["components"][0]["strands"] for r in loaded["recipes"]] == [2, 4]


@pytest.mark.parametrize("empty_palette", [False, True])
def test_large_runs_and_empty_palette(tmp_path, minimal_document, empty_palette):
    document = deepcopy(minimal_document)
    document["grid"].update(width=600, height=600)
    document["full_stitches"]["cells"] = [0 if empty_palette else 1] * 360000
    document["regions"] = []
    if empty_palette:
        document["materials"] = []
        document["recipes"] = []
        document["legend"] = []
    path = tmp_path / "blank.xsd"
    assert not export_xsd(document, path, confirm_loss=True).blocked
    assert path.stat().st_size < 60000
    result = import_xsd(path)
    assert result.document is not None, result.report.as_dict()
    assert result.document["full_stitches"]["cells"] == document["full_stitches"]["cells"]


@pytest.mark.parametrize(
    "offset,fmt,value",
    [
        (0, "H", 0x0511),
        (741, "H", 601),
        (745, "I", 1),
        (749, "H", 1),
        (751, "H", 5),
        (753, "H", 14),
        (761, "H", 241),
        (822, "H", 2),
        (876, "I", 1),
        (1049, "H", 7),
        (49380, "I", 0xFFFFFFFF),
    ],
)
def test_rejects_unsupported_and_malformed_data(tmp_path, offset, fmt, value):
    data = bytearray(native_sample())
    struct.pack_into("<" + fmt, data, offset, value)
    path = tmp_path / "bad.xsd"
    path.write_bytes(data)
    result = import_xsd(path)
    assert result.document is None
    assert result.report.blocked


@pytest.mark.parametrize("length", [0, 1, 700, 762, 1000, 49363, 49382, 49433])
def test_truncated_uploads_return_report(tmp_path, length):
    path = tmp_path / "truncated.xsd"
    path.write_bytes(native_sample()[:length])
    assert import_xsd(path).report.blocked


@pytest.mark.parametrize(
    "words",
    [
        [0x40000000, 0],
        [0x400D0000, 0],
        [0x40010000],
        [0x02000000] * 12,
        [0x00020000] * 12,
    ],
)
def test_run_bombs_and_invalid_palette_are_blocked(tmp_path, words):
    data = native_sample()[:49364] + bytes(16) + struct.pack("<I", len(words))
    data += struct.pack(f"<{len(words)}I", *words) + bytes(4)
    path = tmp_path / "bad-runs.xsd"
    path.write_bytes(data)
    assert import_xsd(path).report.blocked


def test_import_text_encoding_and_limits(tmp_path):
    path = tmp_path / "legacy.xsd"
    data = bytearray(native_sample())
    data[29730:29771] = "中文图纸".encode("gb18030").ljust(41, b"\0")
    path.write_bytes(data)
    assert import_xsd(path, legacy_encoding="gb18030").document["metadata"]["title"] == "中文图纸"
    assert import_xsd(path, legacy_encoding="invalid").report.blocked
    path.write_bytes(bytes(MAX_XSD_BYTES + 1))
    assert import_xsd(path).report.issues[0]["code"] == "INPUT_TOO_LARGE"


def test_export_validation_and_loss_report_do_not_overwrite(tmp_path, minimal_document):
    path = tmp_path / "keep.xsd"
    path.write_bytes(b"keep")
    assert export_xsd(minimal_document, path).blocked
    assert path.read_bytes() == b"keep"
    document = deepcopy(minimal_document)
    document["materials"][0]["color"].update(display_srgb=None, status="unknown", source_ref=None)
    assert export_xsd(document, path, confirm_loss=True).blocked
    assert path.read_bytes() == b"keep"
    document = deepcopy(minimal_document)
    document["metadata"]["title"] = "中文" * 40
    report = export_xsd(document, path, confirm_loss=True)
    assert any(i["code"] == "TEXT_TRUNCATED" for i in report.issues)
    title = import_xsd(path).document["metadata"]["title"]
    assert "\ufffd" not in title and len(title.encode()) <= 40


def test_xsd_import_api_is_free_private_and_downloadable(tmp_path):
    with AccountClient(create_app(tmp_path / "data")) as client:
        balance = client.get("/api/v1/billing").json()["credits"]
        response = client.post("/api/v1/projects/import-xsd", content=native_sample())
        assert response.status_code == 201, response.text
        body = response.json()
        pid, rid = body["project"]["project_id"], body["initial_revision"]["revision_id"]
        asset = body["source_sha256"]
        assert client.get(f"/api/v1/assets/{asset}").content == native_sample()
        assert client.get("/api/v1/projects").json()["items"][0]["source_kind"] == "xsd"
        url = f"/api/v1/projects/{pid}/revisions/{rid}/download/xsd"
        downloaded = client.get(url)
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == "application/octet-stream"
        path = tmp_path / "download.xsd"
        path.write_bytes(downloaded.content)
        loaded = import_xsd(path).document
        assert loaded["full_stitches"] == body["initial_revision"]["document"]["full_stitches"]
        assert client.get("/api/v1/billing").json()["credits"] == balance
        before = client.get("/api/v1/projects").json()["total"]
        for bad in (b"", b"<xs:schema/>", native_sample()[:800]):
            assert client.post("/api/v1/projects/import-xsd", content=bad).status_code == 422
        assert (
            client.post("/api/v1/projects/import-xsd", content=bytes(MAX_XSD_BYTES + 1)).status_code
            == 413
        )
        assert client.get("/api/v1/projects").json()["total"] == before
        client.post("/api/v1/auth/logout")
        assert client.get(url).status_code == 401
        client.post(
            "/api/v1/auth/register",
            json={"email": "second@example.com", "password": "another-password"},
        )
        assert client.get(url).status_code == 404
        assert client.get(f"/api/v1/assets/{asset}").status_code == 404


def test_cli_xsd_import_export(tmp_path, capsys):
    source = tmp_path / "source.xsd"
    source.write_bytes(native_sample())
    document = tmp_path / "pattern.json"
    report = tmp_path / "report.json"
    assert (
        main(["import-xsd", str(source), "--output", str(document), "--report", str(report)]) == 0
    )
    assert report.exists()
    target = tmp_path / "result.xsd"
    assert main(["export-xsd", str(document), "--output", str(target)]) == 2
    assert not target.exists()
    assert main(["export-xsd", str(document), "--output", str(target), "--confirm-loss"]) == 0
    assert import_xsd(target).document["grid"] == {
        "width": 4,
        "height": 3,
        "origin": "top_left",
        "subgrid_scale": 2,
    }
