from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .app import create_app


def run() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    data_dir = Path(os.environ.get("STITCHFORGE_DATA_DIR", ".stitchforge-data"))
    catalog_path = Path(
        os.environ.get(
            "STITCHFORGE_DMC_CATALOG",
            "catalogs/dmc-117-2021-quiltysolid-2025-07.json",
        )
    )
    uvicorn.run(
        create_app(
            data_dir,
            catalog_path=catalog_path,
            start_worker=os.environ.get("STITCHFORGE_WORKER_ENABLED", "1") != "0",
        ),
        host="127.0.0.1",
        port=8765,
    )
