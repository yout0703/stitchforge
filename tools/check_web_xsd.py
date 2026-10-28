"""XSD browser roundtrip against a temporary API, without user data writes.

Start pnpm dev, then: uv run --with playwright python tools/check_web_xsd.py
Optionally pass a real XSD path to exercise that design in isolated storage.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from stitchforge.xsd import export_xsd, import_xsd

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="stitchforge-xsd-e2e-") as directory:
    work = Path(directory)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
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
        env={**os.environ, "STITCHFORGE_PUBLIC_ORIGIN": ""},
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
        if len(sys.argv) > 1:
            source = Path(sys.argv[1]).resolve()
            parsed = import_xsd(source)
            assert parsed.document is not None, parsed.report.as_dict()
            document = parsed.document
        else:
            document = json.loads((ROOT / "examples/minimal-pattern.json").read_text())
            document["metadata"]["title"] = "XSD 浏览器验证"
            document["regions"] = []
            source = work / "test.XSD"
            assert not export_xsd(document, source, confirm_loss=True).blocked
        invalid = work / "invalid.xsd"
        invalid.write_text("<xs:schema/>")
        with sync_playwright() as runner:
            browser = runner.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            registered = page.request.post(
                f"http://127.0.0.1:{port}/api/v1/auth/register",
                data={"email": "xsd-browser@example.com", "password": "browser-test-password"},
                headers={"X-StitchForge-Request": "1"},
            )
            assert registered.status == 201

            def api(route):
                path = urlsplit(route.request.url)
                target = f"http://127.0.0.1:{port}{path.path}"
                if path.query:
                    target += "?" + path.query
                route.fulfill(response=route.fetch(url=target))

            page.route("**/api/v1/**", api)
            page.goto(os.environ.get("STITCHFORGE_WEB_URL", "http://127.0.0.1:5173"))
            page.get_by_role("button", name="导入 OXS / XSD").click()
            dialog = page.get_by_role("dialog")
            dialog.locator("input[type=file]").set_input_files(source)
            dialog.get_by_label("XSD 旧版文字编码").select_option("gb18030")
            dialog.get_by_role("button", name="免费导入图纸").click()
            expect(dialog).not_to_be_visible()
            dimensions = f"{document['grid']['width']} × {document['grid']['height']} 格"
            expect(page.get_by_text(dimensions, exact=True).first).to_be_visible()
            expect(page.get_by_text("XSD 内置线色", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="原图对照")).to_be_disabled()
            first_material = document["materials"][0]
            expect(page.locator('[data-recipe-index="1"]')).to_contain_text(
                f"{first_material['brand']} {first_material['code']}"
            )
            expect(page.locator(".recipe-symbol")).to_have_count(len(document["recipes"]))
            symbols = page.locator(".recipe-symbol").all_text_contents()
            assert len(set(symbols)) == len(document["recipes"])
            page.reload()
            expect(page.get_by_text("XSD 内置线色", exact=True)).to_be_visible()
            print("PASS: XSD upload, encoding selector, embedded palette and reload")

            # Edit one cell through the accessible canvas keyboard controls.
            page.get_by_role("button", name="编辑图纸", exact=True).click()
            target_recipe = 2 if document["full_stitches"]["cells"][1] == 1 else 1
            page.locator(f'[data-recipe-index="{target_recipe}"]').click()
            canvas = page.get_by_label("可编辑十字绣针格")
            canvas.focus()
            canvas.press("ArrowRight")
            canvas.press("Enter")
            expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_enabled()
            page.get_by_role("button", name="下载图纸", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_text("下载前会先保存当前修改。")).to_be_visible()
            screenshot_dir = Path(os.environ.get("STITCHFORGE_XSD_SCREENSHOT_DIR", work))
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_dir / "xsd-download-desktop.png"))
            with page.expect_download() as download_event:
                dialog.get_by_role("button", name="下载 XSD", exact=True).click()
            download = download_event.value
            output = work / "download.xsd"
            download.save_as(output)
            assert download.suggested_filename.endswith(".xsd")
            imported = import_xsd(output).document
            expected = list(document["full_stitches"]["cells"])
            expected[1] = target_recipe
            assert imported["full_stitches"]["cells"] == expected
            print("PASS: download saves dirty edits and preserves all cells on reimport")
            dialog.get_by_role("button", name="关闭下载").click()

            page.get_by_role("button", name="图纸库", exact=True).first.click()
            expect(page.get_by_text("XSD 导入", exact=True).first).to_be_visible()
            page.get_by_role("button", name="导入 OXS / XSD").click()
            dialog = page.get_by_role("dialog")
            dialog.locator("input[type=file]").set_input_files(invalid)
            dialog.get_by_role("button", name="免费导入图纸").click()
            expect(dialog.locator(".import-error")).to_contain_text("Pattern Maker v4")
            dialog.get_by_role("button", name="关闭", exact=True).click()
            print("PASS: library source badge and invalid-XSD error")

            # OXS remains available through the shared picker.
            page.get_by_role("button", name="导入 OXS / XSD").click()
            dialog = page.get_by_role("dialog")
            dialog.locator("input[type=file]").set_input_files(
                ROOT / "examples/minimal-pattern.oxs"
            )
            expect(dialog.get_by_label("XSD 旧版文字编码")).to_have_count(0)
            dialog.get_by_role("button", name="免费导入图纸").click()
            expect(dialog).not_to_be_visible()
            expect(page.get_by_text("OXS 内置线色", exact=True)).to_be_visible()
            balance = page.request.get(f"http://127.0.0.1:{port}/api/v1/billing").json()
            assert balance["credits"] == 0
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("navigation", name="移动端主导航").get_by_role(
                "button", name="下载", exact=True
            ).click()
            dialog = page.get_by_role("dialog")
            button = dialog.get_by_role("button", name="下载 XSD", exact=True)
            button.scroll_into_view_if_needed()
            expect(button).to_be_visible()
            assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth")
            page.screenshot(path=str(screenshot_dir / "xsd-download-mobile.png"))
            assert errors == [], errors
            print(
                "PASS: OXS regression, zero-credit import/download, mobile layout, no page errors"
            )
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
