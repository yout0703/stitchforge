from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .canonical import canonical_document_bytes, document_hash
from .errors import ProjectFormatError
from .validation import validate_document

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_PATTERN_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_FILES = 1_000
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def write_project(document: dict[str, Any], output_path: str | Path) -> Path:
    validate_document(document).raise_for_errors()
    pattern_bytes = canonical_document_bytes(document)
    pattern_sha = hashlib.sha256(pattern_bytes).hexdigest()
    manifest = {
        "container_version": 1,
        "document_path": "pattern.json",
        "document_schema_version": document["schema_version"],
        "document_hash": document_hash(document),
        "capabilities": document["required_features"],
        "source_included": False,
        "files": [
            {
                "path": "pattern.json",
                "sha256": pattern_sha,
                "byte_length": len(pattern_bytes),
                "media_type": "application/json",
                "role": "pattern_document",
            }
        ],
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr(_zip_info("manifest.json"), _canonical_json_bytes(manifest))
        archive.writestr(_zip_info("pattern.json"), pattern_bytes)
    temporary.replace(target)
    return target


def _validate_member_name(name: str) -> None:
    pure = PurePosixPath(name)
    if not name or name.startswith("/") or "\\" in name or ".." in pure.parts or pure.is_absolute():
        raise ProjectFormatError(f"工程包包含不安全路径: {name!r}")


def read_project(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ProjectFormatError("工程包超过 100 MiB 限制")
    try:
        archive = zipfile.ZipFile(source, "r")
    except zipfile.BadZipFile as error:
        raise ProjectFormatError("工程包不是有效 ZIP") from error
    with archive:
        try:
            members = archive.infolist()
        except zipfile.BadZipFile as error:
            raise ProjectFormatError("工程包 ZIP 目录损坏") from error
        if len(members) > MAX_FILES:
            raise ProjectFormatError("工程包文件数量超过限制")
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise ProjectFormatError("工程包包含重复文件名")
        if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
            raise ProjectFormatError("工程包解压总量超过限制")
        for member in members:
            _validate_member_name(member.filename)
            file_type = member.external_attr >> 16
            if stat.S_ISLNK(file_type):
                raise ProjectFormatError(f"工程包不能包含符号链接: {member.filename}")
        if "manifest.json" not in names:
            raise ProjectFormatError("工程包缺少 manifest.json")
        if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
            raise ProjectFormatError("manifest.json 超过 1 MiB 限制")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
            raise ProjectFormatError("manifest.json 不是有效 UTF-8 JSON") from error
        if not isinstance(manifest, dict):
            raise ProjectFormatError("manifest.json 顶层必须是对象")
        if manifest.get("container_version") != 1:
            raise ProjectFormatError(f"不支持的工程容器版本: {manifest.get('container_version')!r}")
        declared = manifest.get("files")
        if not isinstance(declared, list):
            raise ProjectFormatError("manifest files 必须是数组")
        if any(not isinstance(item, dict) for item in declared):
            raise ProjectFormatError("manifest 文件记录格式错误")
        declared_names = [item.get("path") for item in declared]
        if any(not isinstance(name, str) for name in declared_names):
            raise ProjectFormatError("manifest 文件记录必须包含字符串 path")
        if len(declared_names) != len(set(declared_names)):
            raise ProjectFormatError("manifest files 包含重复 path")
        expected_names = {"manifest.json", *declared_names}
        if set(names) != expected_names:
            raise ProjectFormatError("工程包实际文件与 manifest 声明不一致")
        for item in declared:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ProjectFormatError("manifest 文件记录格式错误")
            _validate_member_name(item["path"])
            declared_length = item.get("byte_length")
            declared_hash = item.get("sha256")
            if not isinstance(declared_length, int) or declared_length < 0:
                raise ProjectFormatError(f"文件长度声明无效: {item['path']}")
            if (
                not isinstance(declared_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None
            ):
                raise ProjectFormatError(f"文件哈希声明无效: {item['path']}")
            try:
                payload = archive.read(item["path"])
            except (KeyError, zipfile.BadZipFile) as error:
                raise ProjectFormatError(f"无法读取工程包文件: {item['path']}") from error
            if len(payload) != item.get("byte_length"):
                raise ProjectFormatError(f"文件长度不匹配: {item['path']}")
            if hashlib.sha256(payload).hexdigest() != declared_hash:
                raise ProjectFormatError(f"文件哈希不匹配: {item['path']}")
        document_path = manifest.get("document_path")
        if not isinstance(document_path, str) or document_path not in declared_names:
            raise ProjectFormatError("document_path 未在 files 中声明")
        pattern_info = archive.getinfo(document_path)
        if pattern_info.file_size > MAX_PATTERN_BYTES:
            raise ProjectFormatError("pattern.json 超过 32 MiB 限制")
        try:
            document = json.load(io.TextIOWrapper(archive.open(document_path), encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, zipfile.BadZipFile) as error:
            raise ProjectFormatError("pattern.json 不是有效 UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ProjectFormatError("pattern.json 顶层必须是对象")
    validate_document(document).raise_for_errors()
    if document_hash(document) != manifest.get("document_hash"):
        raise ProjectFormatError("PatternDocument 规范哈希与 manifest 不一致")
    return document
