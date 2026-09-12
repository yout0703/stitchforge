"""Test a supplied OXS in isolated storage and a real browser.

Usage: uv run --with playwright python tools/check_web_large_oxs.py /path/to/chart.oxs
Requires the frontend at localhost:5173. Does not modify user projects.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from defusedxml import ElementTree
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]).resolve()
root = ElementTree.parse(source).getroot()
properties = root.find("properties")
width, height = int(properties.get("chartwidth")), int(properties.get("chartheight"))
with tempfile.TemporaryDirectory(prefix="stitchforge-import-e2e-") as name:
    work = Path(name)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, "STITCHFORGE_CODEX_ENABLED": "0"}
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,uvicorn; from stitchforge_api import create_app; "
            'uvicorn.run(create_app(sys.argv[1]),host="127.0.0.1",port=int(sys.argv[2]))',
            str(work / "data"),
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1).close()
                break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError("Test API failed to start") from None
                time.sleep(0.1)
        with sync_playwright() as runner:
            browser = runner.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 950})
            errors = []
            captured = {}
            page.on("pageerror", lambda error: errors.append(str(error)))

            def api(route):
                path = urlsplit(route.request.url)
                response = route.fetch(
                    url=f"http://127.0.0.1:{port}{path.path}"
                    + (f"?{path.query}" if path.query else "")
                )
                if route.request.method == "POST" and response.status == 201:
                    captured[path.path] = response.json()
                route.fulfill(response=response)

            page.route("**/api/v1/**", api)
            page.goto("http://127.0.0.1:5173/#library")
            page.get_by_role("button", name="导入 OXS", exact=True).click()
            page.get_by_role("dialog").locator("input[type=file]").set_input_files(source)
            with page.expect_response("**/api/v1/projects/import-oxs") as response:
                page.get_by_role("button", name="导入图纸", exact=True).click()
            assert response.value.status == 201
            imported = captured["/api/v1/projects/import-oxs"]
            document = imported["initial_revision"]["document"]
            index_map = imported["report"]["palette_index_map"]
            expected_cells = [0] * (width * height)
            for stitch in root.find("fullstitches"):
                x, y = int(stitch.get("x")), int(stitch.get("y"))
                expected_cells[y * width + x] = index_map[stitch.get("palindex")]
            assert document["full_stitches"]["cells"] == expected_cells
            expect(page.get_by_role("dialog")).not_to_be_visible()
            expect(page.get_by_text(f"{width} × {height} 格", exact=True)).to_be_visible()
            page.reload()
            expect(page.get_by_text(f"{width} × {height} 格", exact=True)).to_be_visible()
            print(
                f"PASS: {width}×{height}, {len(document['recipes'])} colors; "
                "all original stitches match after import"
            )

            index = next(index for index, recipe in enumerate(expected_cells) if recipe)
            col, row = index % width, index // width
            canvas = page.locator("canvas")
            box = canvas.bounding_box()
            size = min((box["width"] - 56) / width, (box["height"] - 56) / height, 132)
            x = box["x"] + (box["width"] - size * width) / 2 + (col + 0.5) * size
            y = box["y"] + (box["height"] - size * height) / 2 + (row + 0.5) * size
            page.mouse.click(x, y)
            expect(
                page.locator(f'[data-recipe-index="{expected_cells[index]}"]')
            ).to_have_attribute("aria-pressed", "true")
            page.keyboard.press("Escape")
            page.get_by_role("button", name="编辑模式", exact=True).click()
            page.keyboard.press("e")
            page.mouse.click(x, y)
            expect(page.get_by_text("有未保存修改", exact=True)).to_be_visible()
            with page.expect_response("**/revisions") as saved:
                page.get_by_role("button", name="保存版本", exact=True).click()
            assert saved.value.status == 201
            expected_cells[index] = 0
            assert (
                captured[f"/api/v1/projects/{imported['project']['project_id']}/revisions"][
                    "document"
                ]["full_stitches"]["cells"]
                == expected_cells
            )
            page.reload()
            expect(page.get_by_text(f"{width} × {height} 格", exact=True)).to_be_visible()
            page.get_by_role("button", name="图纸库", exact=True).click()
            expect(page.locator(".project-card")).to_have_count(1)
            page.wait_for_function("document.querySelector('.project-card img')?.naturalWidth > 0")
            page.locator(".project-card").get_by_role("button", name="打开图纸", exact=True).click()
            expect(page.get_by_text(f"{width} × {height} 格", exact=True)).to_be_visible()
            screenshot = ROOT / "tmp/web/large-oxs-import.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.wait_for_function("""() => {
                const canvas = document.querySelector('canvas');
                if (!canvas || !canvas.width || !canvas.height) return false;
                const ctx = canvas.getContext('2d');
                const colors = new Set();
                for (let y = 1; y < 20; y++) for (let x = 1; x < 20; x++) {
                    colors.add(Array.from(ctx.getImageData(
                        Math.floor(canvas.width * x / 20),
                        Math.floor(canvas.height * y / 20), 1, 1).data).join(','));
                }
                return colors.size > 8;
            }""")
            page.screenshot(path=str(screenshot))
            assert not errors, errors
            print("PASS: color picking, edit/save, reload and library thumbnail; no browser errors")
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
