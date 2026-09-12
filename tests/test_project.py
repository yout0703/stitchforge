from __future__ import annotations

import json
import stat
import zipfile

import pytest
from stitchforge.canonical import document_hash
from stitchforge.errors import ProjectFormatError
from stitchforge.project import read_project, write_project


def _write_archive(path, manifest, files=None, *, member_infos=None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, payload in (files or {}).items():
            archive.writestr(name, payload)
        for info, payload in member_infos or []:
            archive.writestr(info, payload)


def test_project_round_trip_is_stable(tmp_path, minimal_document: dict) -> None:
    output = tmp_path / "sample.stitchforge"
    write_project(minimal_document, output)
    restored = read_project(output)
    assert restored == minimal_document
    assert document_hash(restored) == document_hash(minimal_document)


def test_project_rejects_path_traversal(tmp_path) -> None:
    output = tmp_path / "unsafe.stitchforge"
    manifest = {
        "container_version": 1,
        "files": [{"path": "../outside", "sha256": "0" * 64, "byte_length": 0}],
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("../outside", b"")
    with pytest.raises(ProjectFormatError, match="不安全路径"):
        read_project(output)


def test_project_rejects_invalid_zip(tmp_path) -> None:
    output = tmp_path / "invalid.stitchforge"
    output.write_bytes(b"not a zip")
    with pytest.raises(ProjectFormatError, match="有效 ZIP"):
        read_project(output)


def test_project_rejects_duplicate_member_names(tmp_path) -> None:
    output = tmp_path / "duplicate.stitchforge"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("pattern.json", "{}")
            archive.writestr("pattern.json", "{}")
    with pytest.raises(ProjectFormatError, match="重复文件名"):
        read_project(output)


def test_project_rejects_symlink_member(tmp_path) -> None:
    output = tmp_path / "symlink.stitchforge"
    info = zipfile.ZipInfo("pattern.json")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    manifest = {"container_version": 1, "files": []}
    _write_archive(output, manifest, member_infos=[(info, b"target")])
    with pytest.raises(ProjectFormatError, match="符号链接"):
        read_project(output)


def test_project_rejects_oversized_manifest(monkeypatch, tmp_path) -> None:
    output = tmp_path / "manifest-limit.stitchforge"
    _write_archive(output, {"container_version": 1, "files": []})
    monkeypatch.setattr("stitchforge.project.MAX_MANIFEST_BYTES", 1)
    with pytest.raises(ProjectFormatError, match="manifest.json 超过"):
        read_project(output)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("byte_length", 1, "文件长度不匹配"),
        ("sha256", "f" * 64, "文件哈希不匹配"),
        ("sha256", "bad", "文件哈希声明无效"),
    ],
)
def test_project_rejects_bad_file_integrity(tmp_path, field, value, message) -> None:
    output = tmp_path / f"bad-{field}.stitchforge"
    record = {"path": "pattern.json", "sha256": "0" * 64, "byte_length": 0}
    record[field] = value
    manifest = {
        "container_version": 1,
        "document_path": "pattern.json",
        "files": [record],
    }
    _write_archive(output, manifest, {"pattern.json": b""})
    with pytest.raises(ProjectFormatError, match=message):
        read_project(output)


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ([], "顶层必须是对象"),
        ({"container_version": 2, "files": []}, "容器版本"),
        ({"container_version": 1, "files": "bad"}, "必须是数组"),
        ({"container_version": 1, "files": [None]}, "记录格式错误"),
        (
            {
                "container_version": 1,
                "files": [
                    {"path": "same", "sha256": "0" * 64, "byte_length": 0},
                    {"path": "same", "sha256": "0" * 64, "byte_length": 0},
                ],
            },
            "重复 path",
        ),
    ],
)
def test_project_rejects_malformed_manifest(tmp_path, manifest, message) -> None:
    output = tmp_path / "bad-manifest.stitchforge"
    _write_archive(output, manifest)
    with pytest.raises(ProjectFormatError, match=message):
        read_project(output)
