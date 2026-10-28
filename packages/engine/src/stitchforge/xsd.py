"""Pattern Maker v4 binary interchange (0x0510), restricted to full crosses.

Layout reference: Embroiderly's independently documented Pattern Maker reader:
https://github.com/embroidery-space/embroiderly/blob/main/crates/embroiderly-parsers/src/pmaker/xsd.rs
This codec implements the binary layout directly; no external parser is bundled.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .fabric import MAX_AIDA_COUNT, MAX_GRID_CELLS, MAX_GRID_SIDE, MIN_AIDA_COUNT
from .validation import validate_document

MAX_XSD_BYTES = 16 * 1024 * 1024
XSD_PROFILE = "pattern-maker-v4-full-cross"
MAX_PALETTE = 240
_MASK = 0xFFFFFFFF
_EMPTY = 0x0F000000
# Identifiers in Pattern Maker's built-in thread catalog, not RGB approximations.
_BRANDS = {
    0: "DMC",
    1: "Anchor",
    2: "Madeira",
    3: "J&P Coats",
    4: "Caron Collection",
    9: "Mayflower",
    11: "Riolis Wool",
    23: "Weeks Dye Works",
    82: "PixelHobby",
    92: "Gamma",
    110: "DMC Colour Variation",
    111: "DMC Light Effects",
    112: "DMC Cebelia Crochet",
    113: "DMC Linen",
    116: "Olympus",
    123: "Sullivans",
    124: "Lecien Cosmo",
    125: "Lecien Seasons",
    142: "Ariadna Mouline",
    143: "PNK Kirova",
    149: "Dimensions",
    150: "DMC Cross",
    251: "Generic Color",
    253: "Custom Thread",
}
_NOTE_PREFIX = "StitchForge:"


@dataclass(slots=True)
class XsdReport:
    direction: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    palette_index_map: dict[int, int] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(issue["severity"] == "error" for issue in self.issues)

    def add(self, code: str, message: str, *, path: str = "$", warning: bool = False) -> None:
        self.issues.append(
            {
                "code": code,
                "message": message,
                "path": path,
                "severity": "warning" if warning else "error",
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "XSD",
            "direction": self.direction,
            "profile": XSD_PROFILE,
            "blocked": self.blocked,
            "status": "blocked"
            if self.blocked
            else ("success_with_reported_loss" if self.issues else "success"),
            "issues": self.issues,
            "palette_index_map": {str(k): v for k, v in self.palette_index_map.items()},
        }


@dataclass(frozen=True, slots=True)
class XsdImportResult:
    document: dict[str, Any] | None
    report: XsdReport


class _Reader:
    def __init__(self, payload: bytes, legacy_encoding: str):
        self.payload = payload
        self.position = 0
        self.legacy_encoding = legacy_encoding
        self.used_legacy_encoding = False

    def take(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self.payload):
            raise ValueError(f"XSD 数据截断或长度无效（字节 {self.position}）")
        start = self.position
        self.position += size
        return self.payload[start : self.position]

    def skip(self, size: int) -> None:
        self.take(size)

    def number(self, fmt: str = "H") -> int:
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def text(self, capacity: int) -> str:
        return self.decode(self.take(capacity + 1).split(b"\0", 1)[0])

    def decode(self, value: bytes) -> str:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            self.used_legacy_encoding = True
            return value.decode(self.legacy_encoding, errors="replace")


def _cipher_state(seeds: tuple[int, ...]) -> tuple[int, list[int]]:
    # The stream key uses one byte from each of the four little-endian seed words.
    key = (
        (seeds[0] & 255) << 24
        | ((seeds[1] >> 8) & 255) << 16
        | ((seeds[2] >> 16) & 255) << 8
        | (seeds[3] & 255)
    )
    rotations = [(word >> bit) % 32 for word in seeds for bit in range(4)]
    return key, rotations


def _rotate(value: int, shift: int) -> int:
    return ((value << shift) | (value >> ((32 - shift) % 32))) & _MASK


def _read_grid(reader: _Reader, count: int) -> list[int]:
    seeds = struct.unpack("<4I", reader.take(16))
    key, rotations = _cipher_state(seeds)
    counter, step = seeds[:2]
    word_index = 0
    cells: list[int] = []
    while len(cells) < count:
        length = reader.number("I")
        # Every encoded word must fit in the uploaded bytes. Empty blocks consume
        # input, and runs may never allocate beyond the validated canvas size.
        raw = reader.take(length * 4)
        repeat = None
        for (word,) in struct.iter_unpack("<I", raw):
            value = word ^ key ^ counter
            key = _rotate(key, rotations[word_index % 16])
            counter = (counter + step) & _MASK
            word_index += 1
            if repeat is None and value & 0x40000000:
                repeat = (value & 0x3FFFFFFF) >> 16
                if repeat == 0:
                    raise ValueError("XSD 压缩段的重复次数不能为零")
                continue
            size = repeat if repeat is not None else 1
            if len(cells) + size > count:
                raise ValueError("XSD 压缩段超出图纸尺寸")
            cells.extend([value] * size)
            repeat = None
        if repeat is not None:
            raise ValueError("XSD 压缩段缺少针格数据")
    return cells


def import_xsd(
    path: str | Path,
    *,
    legacy_encoding: str = "cp1251",
    imported_at: datetime | None = None,
) -> XsdImportResult:
    report = XsdReport("import")
    source = Path(path)
    if legacy_encoding not in {"cp1251", "gb18030", "cp1252"}:
        report.add("INVALID_ENCODING", "旧版文字编码仅支持 cp1251、gb18030 或 cp1252")
        return XsdImportResult(None, report)
    try:
        with source.open("rb") as stream:
            payload = stream.read(MAX_XSD_BYTES + 1)
        if len(payload) > MAX_XSD_BYTES:
            report.add("INPUT_TOO_LARGE", "XSD 文件超过 16 MiB 限制")
            return XsdImportResult(None, report)
        reader = _Reader(payload, legacy_encoding)
        if reader.number() != 0x0510:
            report.add("UNSUPPORTED_VERSION", "仅支持 Pattern Maker v4 二进制 XSD（0x0510）")
            return XsdImportResult(None, report)
        reader.skip(739)
        width, height = reader.number(), reader.number()
        if not (
            1 <= width <= MAX_GRID_SIDE
            and 1 <= height <= MAX_GRID_SIDE
            and width * height <= MAX_GRID_CELLS
        ):
            raise ValueError(f"XSD 图纸每边必须为 1–{MAX_GRID_SIDE} 格")
        small_count, joint_count = reader.number("I"), reader.number()
        if small_count or joint_count:
            report.add(
                "UNSUPPORTED_STITCH", "XSD 含半针、小十字、回针、结粒或其他复杂针法，当前仅支持全针"
            )
            return XsdImportResult(None, report)
        ct_x, ct_y = reader.number(), reader.number()
        if ct_x != ct_y or not MIN_AIDA_COUNT <= ct_x <= MAX_AIDA_COUNT:
            raise ValueError(f"仅支持经纬一致的 {MIN_AIDA_COUNT}–{MAX_AIDA_COUNT} CT 绣布")
        reader.skip(6)
        palette_size = reader.number()
        if palette_size > MAX_PALETTE:
            raise ValueError(f"XSD 调色板不能超过 {MAX_PALETTE} 色")
        palette = []
        for index in range(palette_size):
            record = reader.take(123)
            if (
                struct.unpack_from("<H", record, 59)[0]
                or struct.unpack_from("<I", record, 113)[0] == 1
            ):
                report.add(
                    "UNSUPPORTED_MATERIAL",
                    "XSD 含混线或珠绣材料，当前无法无损导入",
                    path=f"$.palette[{index}]",
                )
            palette.append(
                {
                    "brand": _BRANDS.get(record[2], f"Pattern Maker brand {record[2]}"),
                    "code": reader.decode(record[3:14].split(b"\0", 1)[0]),
                    "name": reader.decode(record[14:55].split(b"\0", 1)[0]),
                    "color": "#" + record[55:58].hex().upper(),
                }
            )
        reader.skip(palette_size * 2)
        for item in palette:
            for stitch_type in range(9):
                note = reader.take(reader.number())
                if stitch_type == 0 and note.startswith(_NOTE_PREFIX.encode()):
                    # Preserve custom brands and long names/codes in the standard
                    # per-thread notes field, without pretending they are DMC.
                    try:
                        identity = json.loads(note[len(_NOTE_PREFIX) :])
                        for name, limit in (("brand", 128), ("code", 64), ("name", 256)):
                            if isinstance(identity.get(name), str):
                                item[name] = identity[name][:limit]
                    except (ValueError, AttributeError, RecursionError):
                        report.add(
                            "INVALID_THREAD_NOTE", "忽略无效的 StitchForge 线色备注", warning=True
                        )
        for item in palette:
            item["strands"] = reader.number()
            reader.skip(14)
        # Six fixed-size style arrays plus font formats: 240 * (10*6 + 4 + 53).
        reader.skip(MAX_PALETTE * 117 + palette_size * 12)
        reader.skip(380 + 56)  # View/print settings, then screen/printer grid.
        cloth_name = reader.text(40)
        cloth_color = "#" + reader.take(3).hex().upper()
        reader.skip(65)
        title, author, company = reader.text(40), reader.text(40), reader.text(40)
        copyright_text, notes = reader.text(200), reader.text(2048)
        reader.skip(6)
        fabric_kind = reader.text(40)
        if fabric_kind.strip().lower() not in {"", "aida", "аида", "绣布", "十字绣布"}:
            report.add("UNSUPPORTED_FABRIC", f"仅支持 Aida 绣布，文件标记为 {fabric_kind!r}")
        reader.skip(206)
        default_strands = reader.number()
        reader.skip(50 + 32 + 16412 + 512)
        words = _read_grid(reader, width * height)
        reader.skip(2)
        if reader.number():
            report.add("UNSUPPORTED_SPECIAL_STITCH", "XSD 含特殊针法模型，当前无法无损导入")
        # Pattern Maker may append print/thumbnail data; it is not stitch data.
        if report.blocked:
            return XsdImportResult(None, report)
        cells = []
        for offset, word in enumerate(words):
            kind, index = word >> 24, (word >> 16) & 255
            if kind == 15:
                cells.append(0)
            elif kind == 0 and index < palette_size:
                cells.append(index + 1)
            else:
                raise ValueError(f"XSD 第 {offset + 1} 格含未知针法或无效线色索引")
        for item in palette:
            item["strands"] = item["strands"] or default_strands
            if not 1 <= item["strands"] <= 6:
                raise ValueError("XSD 全针股数必须为 1–6")
    except OSError as error:
        report.add("READ_FAILED", f"无法读取 XSD：{error}")
        return XsdImportResult(None, report)
    except (ValueError, struct.error) as error:
        report.add("INVALID_XSD", str(error))
        return XsdImportResult(None, report)

    stamp = (imported_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    document: dict[str, Any] = {
        "schema_version": "0.1.0",
        "required_features": ["full_cross"],
        "metadata": {
            "pattern_id": "xsd_" + hashlib.sha256(payload).hexdigest()[:24],
            "title": title.strip() or source.stem[:256] or "XSD 图纸",
            "author": author,
            "created_at": stamp.isoformat().replace("+00:00", "Z"),
            "purpose": "user_design",
        },
        "grid": {"width": width, "height": height, "origin": "top_left", "subgrid_scale": 2},
        "fabric": {
            "type": "aida",
            "count": ct_x,
            "count_unit": "cells_per_inch",
            "worked_over": 1,
            "display_srgb": cloth_color,
            "margins_mm": {side: 50 for side in ("top", "right", "bottom", "left")},
        },
        "materials": [
            {
                "id": f"xsd_m_{i}",
                "brand": item["brand"],
                "series": "Imported XSD",
                "code": item["code"] or str(i + 1),
                "name": item["name"],
                "color": {
                    "display_srgb": item["color"],
                    "status": "approximate",
                    "source_ref": "src_xsd",
                },
            }
            for i, item in enumerate(palette)
        ],
        "recipes": [
            {
                "id": f"xsd_r_{i}",
                "index": i + 1,
                "components": [{"material_id": f"xsd_m_{i}", "strands": item["strands"]}],
            }
            for i, item in enumerate(palette)
        ],
        "full_stitches": {"encoding": "row-major-u16", "top_leg": "slash", "cells": cells},
        "regions": [],
        "legend": [
            {"recipe_id": f"xsd_r_{i}", "symbol_id": f"s{i + 1:03d}"} for i in range(palette_size)
        ],
        "constraints": {
            "color_budget": max(1, len(set(cells) - {0})),
            "metric_profile": "quality_4n_v1",
        },
        "provenance": {
            "source_asset_hashes": [hashlib.sha256(payload).hexdigest()],
            "engine_version": "stitchforge-0.1.0",
            "catalog_snapshot_id": "xsd_embedded_palette",
            "rule_profile": XSD_PROFILE,
            "generation_config_hash": None,
            "random_seed": None,
            "ai_proposal_hash": None,
            "sources": [
                {
                    "id": "src_xsd",
                    "locator": str(source)[:2048],
                    "description": "XSD 内嵌 RGB，按屏幕近似色导入，未经实体色卡校准。",
                    "permission_status": "private_use",
                }
            ],
        },
        "extensions": {
            "stitchforge.xsd_import": {
                "profile": XSD_PROFILE,
                "company": company,
                "copyright": copyright_text,
                "notes": notes,
                "fabric_name": cloth_name,
                "legacy_encoding": legacy_encoding,
            }
        },
    }
    validation = validate_document(document)
    for issue in validation.diagnostics:
        report.add("IMPORTED_DOCUMENT_INVALID", issue.message, path=issue.path)
    if report.blocked:
        return XsdImportResult(None, report)
    report.add(
        "IMPORT_DEFAULTS",
        "已导入全针；针方向设为 slash，裁布边距设为 50 mm，使用本工作台符号。",
        warning=True,
    )
    if reader.used_legacy_encoding:
        report.add(
            "LEGACY_TEXT_ENCODING",
            f"旧版文字按 {legacy_encoding} 解码；乱码时请更换文字编码。",
            warning=True,
        )
    report.palette_index_map = {i: i + 1 for i in range(palette_size)}
    return XsdImportResult(document, report)


def _text(value: str, capacity: int) -> bytes:
    # UTF-8 is understood by independent modern readers. Never split a codepoint.
    return (
        value.replace("\0", "")
        .encode("utf-8")[:capacity]
        .decode("utf-8", errors="ignore")
        .encode()
        .ljust(capacity + 1, b"\0")
    )


def export_xsd(
    document: dict[str, Any],
    path: str | Path,
    *,
    confirm_loss: bool = False,
) -> XsdReport:
    report = XsdReport("export")
    validation = validate_document(document)
    for issue in validation.diagnostics:
        report.add("DOCUMENT_INVALID", issue.message, path=issue.path)
    if report.blocked:
        return report
    recipes = sorted(document["recipes"], key=lambda item: item["index"])
    materials = {item["id"]: item for item in document["materials"]}
    if len(recipes) > MAX_PALETTE:
        report.add("PALETTE_TOO_LARGE", f"Pattern Maker v4 调色板最多 {MAX_PALETTE} 色")
    for recipe in recipes:
        material = materials[recipe["components"][0]["material_id"]]
        if material["color"]["display_srgb"] is None:
            report.add("UNKNOWN_COLOR", f"线色 {material['code']} 没有可导出的 RGB")
    if report.blocked:
        return report
    report.add(
        "INTERCHANGE_LOSS",
        "XSD 不保留编辑历史、区域锁定、来源记录、裁布边距和针方向；符号由目标软件显示。",
        warning=confirm_loss,
    )
    for name in ("title", "author"):
        if len(document["metadata"][name].encode()) > 40:
            report.add("TEXT_TRUNCATED", f"XSD {name} 限制为 40 字节，已截短", warning=confirm_loss)
    if not confirm_loss:
        report.add("LOSS_CONFIRMATION_REQUIRED", "确认格式转换损失后才能导出 XSD")
        return report

    payload = bytearray(741)
    struct.pack_into("<H4x4H", payload, 0, 0x0510, 4, 4, 1, 0)
    grid, fabric = document["grid"], document["fabric"]
    payload.extend(
        struct.pack(
            "<HHIHHH6xH",
            grid["width"],
            grid["height"],
            0,
            0,
            fabric["count"],
            fabric["count"],
            len(recipes),
        )
    )
    brand_ids = {name.casefold(): i for i, name in _BRANDS.items()}
    index_map = {recipe["index"]: i for i, recipe in enumerate(recipes)}
    notes = []
    for recipe in recipes:
        item = materials[recipe["components"][0]["material_id"]]
        brand = brand_ids.get(item["brand"].casefold())
        match = re.fullmatch(r"Pattern Maker brand (\d{1,3})", item["brand"])
        if brand is None and match and int(match[1]) <= 255:
            brand = int(match[1])
        if brand is None:
            brand = 253
            report.add(
                "CUSTOM_THREAD",
                f"{item['brand']} {item['code']} 以自定义线色导出，完整身份保存在备注中",
                warning=True,
            )
        record = bytearray(123)
        record[2] = brand
        record[3:14] = _text(item["code"], 10)
        record[14:55] = _text(item["name"], 40)
        record[55:58] = bytes.fromhex(item["color"]["display_srgb"][1:])
        payload.extend(record)
        note = _NOTE_PREFIX + json.dumps(
            {k: item[k] for k in ("brand", "code", "name")}, ensure_ascii=False
        )
        notes.append(note.encode())
    payload.extend(struct.pack(f"<{len(recipes)}H", *range(len(recipes))))
    for note in notes:
        payload.extend(struct.pack("<H", len(note)) + note + bytes(16))
    for recipe in recipes:
        payload.extend(struct.pack("<H14x", recipe["components"][0]["strands"]))
    # Native style arrays have 240 slots regardless of the active palette size.
    payload.extend((struct.pack("<H4s4s", 0, b"\xff\xff\xff\0", bytes(4))) * MAX_PALETTE)
    line_style = struct.pack("<H4sHH", 0, bytes(4), 5, 10) * MAX_PALETTE
    payload.extend(line_style + bytes(MAX_PALETTE * 4) + line_style * 2)
    payload.extend(struct.pack("<H4sHH", 1, bytes(4), 0, 40) * MAX_PALETTE * 2)
    payload.extend(
        (_text("default", 32) + struct.pack("<HHB11xHH", 0, 400, 0, 100, 60)) * MAX_PALETTE
    )
    for i in range(len(recipes)):
        payload.extend(struct.pack("<6H", 33 + i, *([0xFFFF] * 5)))
    settings = bytearray(380)
    settings[:33] = _text("Arial", 32)
    settings[53:86] = _text("Arial", 32)
    struct.pack_into("<HH", settings, 86, 10, 400)
    struct.pack_into("<9H", settings, 102, 1, 100, 1, 1, 0, 0, 0, 0, 1)
    struct.pack_into("<6H", settings, 360, 100, 100, 100, 100, 50, 50)
    payload.extend(settings)
    payload.extend(
        struct.pack("<HH", 10, 0) + struct.pack("<HH3s3x", 1, 0, b"\x80\x80\x80") * 4 + bytes(12)
    )
    payload.extend(_text("White", 40) + bytes.fromhex(fabric["display_srgb"][1:]) + bytes(65))
    payload.extend(_text(document["metadata"]["title"], 40))
    payload.extend(_text(document["metadata"]["author"], 40) + _text("StitchForge", 40))
    payload.extend(_text("", 200) + _text("Exported by StitchForge (full crosses).", 2048))
    payload.extend(bytes(6) + _text("Aida", 40) + bytes(206))
    payload.extend(struct.pack("<7H", 2, 2, 2, 1, 2, 2, 1))
    payload.extend(struct.pack("<13H", *([10] * 13)) + bytes(12))
    payload.extend(struct.pack("<16H", 1, 1, 1, 1, 1, 1, 60, 0, 0, 0, 0, 0, 100, 0, 0, 0))
    payload.extend(bytes(16412 + 512))
    # A zero seed is a valid identity stream; no obfuscation is required. Keep
    # blocks bounded and encode runs so large blank canvases stay inexpensive.
    payload.extend(bytes(16))
    words = []
    cells = document["full_stitches"]["cells"]
    start = 0
    while start < len(cells):
        end = start + 1
        while end < len(cells) and cells[end] == cells[start] and end - start < 16383:
            end += 1
        value = _EMPTY if cells[start] == 0 else index_map[cells[start]] << 16
        if end - start > 1:
            words.append(0x40000000 | ((end - start) << 16))
        words.append(value)
        start = end
    payload.extend(struct.pack("<I", len(words)))
    payload.extend(struct.pack(f"<{len(words)}I", *words))
    payload.extend(bytes(4))  # No special stitch models or non-cross joints.
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    report.palette_index_map = index_map
    return report
