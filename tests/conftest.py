from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def minimal_document() -> dict[str, Any]:
    with (ROOT / "examples/minimal-pattern.json").open("r", encoding="utf-8") as stream:
        return json.load(stream)
