"""Real account/import/mobile regression on temporary data and a production web build.

pnpm --dir apps/web build
uv run --with playwright python tools/check_web_accounts.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from stitchforge.oxs import SUPPORTED_COORDINATE_PROFILE, import_oxs
from stitchforge_storage import SQLiteStore
from stitchforge_storage.accounts import AccountStore

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "account-review"
OUTPUT.mkdir(parents=True, exist_ok=True)


def open_import(page, kind):
    if page.viewport_size["width"] < 768:
        page.get_by_role("button", name="新建", exact=True).click()
        label = "图片生成" if kind == "image" else "导入 OXS / XSD"
        page.locator(".mobile-create-options button").filter(has_text=label).click()
    else:
        label = "图片转图纸" if kind == "image" else "导入 OXS / XSD"
        page.locator(".sidebar-task-button").filter(has_text=label).click()


def open_account(page):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.locator(".sidebar-account").click()


with tempfile.TemporaryDirectory(prefix="stitchforge-accounts-e2e-") as name:
    work = Path(name)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,uvicorn; from stitchforge_api import create_app; "
            "from fastapi.staticfiles import StaticFiles; "
            "app=create_app(sys.argv[1], start_worker=True); "
            "app.mount('/',StaticFiles(directory='apps/web/dist',html=True)); "
            "uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]))",
            str(work / "data"),
            str(port),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "STITCHFORGE_CODEX_ENABLED": "0",
            "STITCHFORGE_AI_PROVIDER": "local",
            "STITCHFORGE_PUBLIC_ORIGIN": "",
        },
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
        source = work / "flowers.png"
        Image.new("RGB", (20, 30), "#BA4422").save(source)
        with sync_playwright() as runner:
            browser = runner.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900}, has_touch=True)
            page = context.new_page()
            failures = []
            page.on("pageerror", lambda error: failures.append(str(error)))
            # Observe real canvas transforms without modifying application behavior.
            page.add_init_script("""
                const original = CanvasRenderingContext2D.prototype.scale;
                window.canvasScales = new WeakMap();
                CanvasRenderingContext2D.prototype.scale = function(x, y) {
                    window.canvasScales.set(this.canvas, x);
                    return original.call(this, x, y);
                };
                const translate = CanvasRenderingContext2D.prototype.translate;
                window.canvasOffsets = new WeakMap();
                CanvasRenderingContext2D.prototype.translate = function(x, y) {
                    window.canvasOffsets.set(this.canvas, {x,y});
                    return translate.call(this,x,y);
                };
            """)
            page.goto(f"http://127.0.0.1:{port}")
            expect(page.get_by_role("heading", name="登录你的工作台")).to_be_visible()
            page.screenshot(path=str(OUTPUT / "desktop-login.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUTPUT / "mobile-login.png"), full_page=True)
            page.get_by_role("button", name="还没有账号？免费注册").click()
            page.get_by_label("邮箱", exact=True).fill("alice@example.com")
            page.get_by_label("密码", exact=True).fill("correct-password-123")
            page.get_by_role("checkbox").check()
            page.get_by_role("button", name="注册并开始", exact=True).click()
            expect(page.get_by_role("heading", name="我的图纸", exact=True)).to_be_visible()
            open_import(page, "image")
            expect(page.get_by_role("button", name="生成图纸 · 1 次")).to_be_disabled()
            page.get_by_role("button", name="关闭", exact=True).click()
            open_import(page, "oxs")
            page.get_by_role("dialog").locator("input[type=file]").set_input_files(
                ROOT / "examples/minimal-pattern.oxs"
            )
            page.get_by_role("button", name="免费导入图纸", exact=True).click()
            expect(page.get_by_role("dialog")).not_to_be_visible()
            expect(page.get_by_text("看图模式", exact=True)).to_be_visible()
            canvas = page.locator("canvas")
            expect(canvas).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            box = canvas.bounding_box()
            assert box["height"] > 150, box
            page.screenshot(path=str(OUTPUT / "mobile-viewer.png"), full_page=True)
            print("PASS: mobile registration, free OXS import, insufficient-credit UI, no overflow")
            # CDP dispatches native touch events through the pointer handlers.
            cdp = context.new_cdp_session(page)
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            before = page.locator(".canvas-viewport").screenshot()

            def touches(distance):
                return [{"x": x - distance, "y": y, "id": 1}, {"x": x + distance, "y": y, "id": 2}]

            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": touches(30)})
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": touches(80)})
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            page.wait_for_timeout(100)
            assert page.locator(".canvas-viewport").screenshot() != before
            expect(page.get_by_text("看图模式", exact=True)).to_be_visible()
            print("PASS: native two-finger pinch changes the chart without editing")
            page.reload()
            expect(canvas).to_be_visible()
            open_account(page)
            expect(page.get_by_text("暂无使用记录", exact=True)).to_be_visible()
            page.get_by_role("button", name="退出登录", exact=True).click()
            expect(page.get_by_role("heading", name="登录你的工作台")).to_be_visible()
            page.get_by_role("button", name="还没有账号？免费注册").click()
            page.get_by_label("邮箱", exact=True).fill("bob@example.com")
            page.get_by_label("密码", exact=True).fill("correct-password-123")
            page.get_by_role("checkbox").check()
            page.get_by_role("button", name="注册并开始", exact=True).click()
            expect(page.get_by_role("heading", name="还没有图纸", exact=True)).to_be_visible()
            print("PASS: logout clears the workbench; another account has an empty library")
            accounts = AccountStore(SQLiteStore(work / "data" / "metadata.sqlite3"))
            with accounts.store._transaction() as connection:
                user_id = connection.execute(
                    "SELECT user_id FROM users WHERE email='bob@example.com'"
                ).fetchone()[0]
            order = accounts.create_order(
                user_id,
                {"id": "test", "credits": 2, "amount_minor": 100, "currency": "CNY"},
                "test-order",
            )
            accounts.settle_order(order["order_id"], "test-payment", 100, "CNY")
            page.reload()
            open_import(page, "image")
            page.get_by_role("dialog").locator("input[type=file]").set_input_files(source)
            page.get_by_role("button", name="生成图纸 · 1 次").click()
            expect(page.get_by_role("dialog")).not_to_be_visible(timeout=30000)
            expect(page.locator(".sidebar-account")).to_contain_text("剩余 1 次")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.screenshot(path=str(OUTPUT / "desktop-viewer.png"), full_page=True)
            # Export the actual generated document, then verify free repeat downloads on mobile.
            page.get_by_role("button", name="下载图纸", exact=True).click()
            with page.expect_download() as download:
                page.get_by_role("button", name="下载完整图片", exact=True).click()
            png = OUTPUT / "download-chart.png"
            download.value.save_as(png)
            with Image.open(png) as exported:
                assert exported.format == "PNG"
                exported.verify()
            expect(page.get_by_role("link", name="保存 PNG 图片")).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(OUTPUT / "mobile-download.png"), full_page=True)
            with page.expect_download() as download:
                page.get_by_role("button", name="下载 OXS", exact=True).click()
            oxs = work / "download.oxs"
            download.value.save_as(oxs)
            loaded = import_oxs(oxs, coordinate_profile=SUPPORTED_COORDINATE_PROFILE)
            assert loaded.document is not None
            assert any(loaded.document["full_stitches"]["cells"])
            page.get_by_role("button", name="关闭下载", exact=True).click()
            expect(page.locator(".sidebar-account")).to_contain_text("剩余 1 次")
            print("PASS: generated PNG and OXS download; mobile save links; no extra credit charge")
            # Confirm export saves and includes an unsaved edit.
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_role("button", name="编辑图纸", exact=True).click()
            page.get_by_role("button", name="适应窗口", exact=True).click()
            page.get_by_role("button", name="橡皮擦 E", exact=True).click()
            page.locator("canvas").click()
            expect(page.get_by_text("编辑中 · 有未保存修改", exact=True)).to_be_visible()
            page.get_by_role("button", name="下载图纸", exact=True).click()
            expect(page.get_by_text("下载前会先保存当前修改。", exact=True)).to_be_visible()

            # A save conflict must not silently download the old revision.
            def reject_save(route):
                route.fulfill(
                    status=409, json={"code": "REVISION_CONFLICT", "message": "测试保存冲突"}
                )

            page.route("**/revisions", reject_save)
            page.get_by_role("button", name="下载 OXS", exact=True).click()
            expect(page.get_by_role("dialog").get_by_role("alert")).to_have_text("测试保存冲突")
            expect(page.get_by_role("link", name="保存 OXS 文件")).not_to_be_visible()
            page.unroute("**/revisions", reject_save)
            with page.expect_download() as download:
                page.get_by_role("button", name="下载 OXS", exact=True).click()
            edited = work / "edited.oxs"
            download.value.save_as(edited)
            edited_document = import_oxs(
                edited, coordinate_profile=SUPPORTED_COORDINATE_PROFILE
            ).document
            assert (
                edited_document["full_stitches"]["cells"]
                != loaded.document["full_stitches"]["cells"]
            )
            page.get_by_role("button", name="关闭下载", exact=True).click()
            expect(page.get_by_text("编辑中 · 已保存到账号", exact=True)).to_be_visible()
            print("PASS: failed save blocks download; retry saves and exports the edited version")
            open_account(page)
            expect(page.get_by_text("-1", exact=True)).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(OUTPUT / "mobile-credits.png"), full_page=True)
            print("PASS: real image generation consumes one credit and displays the ledger")
            assert not failures, failures
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
print(f"Screenshots: {OUTPUT}")
