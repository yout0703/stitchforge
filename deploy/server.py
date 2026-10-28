"""Single-process production API, supervised image worker and prebuilt web shell."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import uvicorn
from fastapi import Response
from fastapi.staticfiles import StaticFiles
from stitchforge_api import create_app


class WebFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        )
        return response


def production_app():
    if not os.environ.get("STITCHFORGE_PUBLIC_ORIGIN", "").startswith("https://"):
        raise RuntimeError("Production requires STITCHFORGE_PUBLIC_ORIGIN=https://<hostname>")
    data = Path(os.environ.get("STITCHFORGE_DATA_DIR", "/data"))
    app = create_app(data, start_worker=True)

    @app.get("/api/v1/ready", include_in_schema=False)
    def ready(response: Response):
        try:
            with app.state.store._reader() as connection:
                connection.execute("SELECT 1").fetchone()
            healthy = (
                app.state.worker_process.is_alive()
                and shutil.disk_usage(data).free > 128 * 1024 * 1024
                and Path(os.environ["STITCHFORGE_CHART_FONT"]).is_file()
            )
        except (OSError, AttributeError, sqlite3.DatabaseError):
            healthy = False
        response.status_code = 200 if healthy else 503
        return {"status": "ok" if healthy else "unavailable"}

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    app.mount("/", WebFiles(directory="/app/web", html=True), name="web")
    return app


def main():
    uvicorn.run(
        production_app(),
        host="127.0.0.1",
        port=int(os.environ.get("STITCHFORGE_PORT", "18765")),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1",
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    main()
