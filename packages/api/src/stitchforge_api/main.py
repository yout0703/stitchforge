from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from .app import create_app


def run() -> None:
    data_dir = Path(os.environ.get("STITCHFORGE_DATA_DIR", ".stitchforge-data"))
    catalog_path = Path(
        os.environ.get(
            "STITCHFORGE_DMC_CATALOG",
            "catalogs/dmc-117-2021-quiltysolid-2025-07.json",
        )
    )
    uvicorn.run(create_app(data_dir, catalog_path=catalog_path), host="127.0.0.1", port=8765)
