"""Exercise an isolated preflight instance; do not seed credits against production."""

from __future__ import annotations

import io
import secrets
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

ORIGIN = "https://stichforge.0703.pro"


def main():
    url = sys.argv[1]
    headers = {"Origin": ORIGIN, "X-StitchForge-Request": "1"}
    with httpx.Client(base_url=url, headers=headers, timeout=60) as client:
        assert client.get("/api/v1/ready").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get(
            "/", headers={"If-Modified-Since": "Thu, 01 Jan 1970 00:00:00 GMT"}
        ).status_code == 200, "Release HTML must be newer than an epoch-normalized archive"
        assert client.get("/missing-page").status_code == 404
        assert client.get("/api/v1/auth/me").status_code == 401
        credentials = {
            "email": f"deployment-{secrets.token_hex(6)}@example.com",
            "password": secrets.token_urlsafe(24),
        }
        registered = client.post("/api/v1/auth/register", json=credentials)
        assert registered.status_code == 201, registered.text
        cookie = registered.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        # HTTP is used only over the SSH/local loopback connection, so send the secure
        # session explicitly. The public browser always receives the cookie via HTTPS.
        if url.startswith("http://"):
            client.headers["Cookie"] = cookie.split(";", 1)[0]
        assert client.get("/api/v1/auth/me").status_code == 200
        imported = client.post(
            "/api/v1/projects/import-oxs",
            content=Path("examples/minimal-pattern.oxs").read_bytes(),
            headers={"Content-Type": "application/xml"},
        )
        assert imported.status_code == 201, imported.text
        result = imported.json()
        project = result["project"]["project_id"]
        revision = result["initial_revision"]["revision_id"]
        for fmt in ("png", "oxs", "xsd"):
            download = client.get(f"/api/v1/projects/{project}/revisions/{revision}/download/{fmt}")
            assert download.status_code == 200, download.text[:200]
            assert download.headers["cache-control"] == "no-store"
            if fmt == "png":
                Image.open(io.BytesIO(download.content)).verify()
        forbidden = client.post(
            "/api/v1/projects", json={"name": "blocked"}, headers={"Origin": "https://evil.example"}
        )
        assert forbidden.status_code == 403
        billing = client.get("/api/v1/billing").json()
        assert billing["checkout_enabled"] is False
        print("PASS: secure session, OXS import, PNG/OXS/XSD downloads, CSRF and payment defaults")

        client.post("/api/v1/auth/logout")
        if "Cookie" in client.headers:
            del client.headers["Cookie"]
        assert client.get("/api/v1/auth/me").status_code == 401
        signed_in = client.post("/api/v1/auth/login", json=credentials)
        assert signed_in.status_code == 200
        if url.startswith("http://"):
            client.headers["Cookie"] = signed_in.headers["set-cookie"].split(";", 1)[0]
        saved = client.get(f"/api/v1/projects/{project}")
        assert saved.status_code == 200
        print("PASS: logout revokes session; login restores saved account data")

        # This is an isolated instance. The operator seeds this test account separately.
        if "--generation" in sys.argv:
            print(f"PREFLIGHT_USER={registered.json()['user_id']}", flush=True)
            deadline = time.monotonic() + 60
            while client.get("/api/v1/auth/me").json()["credits"] == 0:
                if time.monotonic() > deadline:
                    raise TimeoutError("Waiting for isolated test credits")
                time.sleep(0.5)
            created = client.post("/api/v1/projects", json={"name": "部署图片生成验证"}).json()
            project = created["project"]["project_id"]
            revision = created["initial_revision"]["revision_id"]
            image = io.BytesIO()
            Image.new("RGB", (40, 30), "#BA4422").save(image, format="PNG")
            queued = client.post(
                f"/api/v1/projects/{project}/image-jobs",
                params={
                    "expected_head_revision_id": revision,
                    "request_key": secrets.token_hex(16),
                    "grid_width": 16,
                    "grid_height": 12,
                    "color_budget": 4,
                    "remove_border_background": "false",
                },
                content=image.getvalue(),
                headers={"Content-Type": "image/png"},
            )
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["job_id"]
            deadline = time.monotonic() + 90
            while True:
                job = client.get(f"/api/v1/jobs/{job_id}").json()
                if job["state"] == "SUCCEEDED":
                    break
                assert job["state"] not in {"FAILED", "CANCELLED"}, job
                if time.monotonic() > deadline:
                    raise TimeoutError("Image generation did not complete")
                time.sleep(0.5)
            assert client.get("/api/v1/auth/me").json()["credits"] == 1
            print("PASS: durable image worker completed; exactly one test credit consumed")


if __name__ == "__main__":
    main()
