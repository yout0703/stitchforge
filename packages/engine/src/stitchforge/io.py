from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_document(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise ValueError("PatternDocument 顶层必须是 JSON 对象")
    return document


def write_json(path: str | Path, value: Any, *, pretty: bool = True) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
