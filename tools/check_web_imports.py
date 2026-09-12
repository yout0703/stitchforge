"""Real API + browser import checks in temporary storage; no user project writes.

Run with frontend active: uv run --with playwright python tools/check_web_imports.py
Codex is disabled only in the test API, so fallback conversion remains deterministic.
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

from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
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
        source = work / "sample.png"
        Image.new("RGB", (20, 30), "#BA4422").save(source)
        bad = work / "invalid.oxs"
        bad.write_text("invalid xml")
        with sync_playwright() as runner:
            browser = runner.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            def api(route):
                path = urlsplit(route.request.url)
                url = f"http://127.0.0.1:{port}{path.path}" + (
                    f"?{path.query}" if path.query else ""
                )
                response = route.fetch(url=url)
                route.fulfill(response=response)

            page.route("**/api/v1/**", api)
            page.goto("http://127.0.0.1:5173")
            page.get_by_role("button", name="导入图片", exact=True).click()
            dialog = page.get_by_role("dialog")
            assert dialog.get_by_role("spinbutton").count() == 0
            dialog.locator("input[type=file]").set_input_files(source)
            dialog.get_by_role("button", name="自动生成图纸").click()
            expect(dialog).not_to_be_visible(timeout=30000)
            expect(page.get_by_text("20 × 30 格", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="查看模式")).to_have_attribute(
                "aria-pressed", "true"
            )
            expect(page.get_by_text("本地分析", exact=True)).to_be_visible()
            page.reload()
            expect(page.get_by_text("20 × 30 格", exact=True)).to_be_visible()
            expect(page.get_by_text("本地分析", exact=True)).to_be_visible()
            print("PASS: automatic image conversion and plan persist on reload")

            page.get_by_role("button", name="导入 OXS", exact=True).click()
            page.get_by_role("dialog").locator("input[type=file]").set_input_files(
                ROOT / "examples/minimal-pattern.oxs"
            )
            page.get_by_role("button", name="导入图纸", exact=True).click()
            expect(page.get_by_role("dialog")).not_to_be_visible()
            expect(page.get_by_text("4 × 3 格", exact=True)).to_be_visible()
            expect(page.get_by_text("14 CT", exact=True).first).to_be_visible()
            expect(page.get_by_role("button", name="原图对照")).to_be_disabled()
            expect(page.get_by_role("button", name="查看模式")).to_have_attribute(
                "aria-pressed", "true"
            )
            expect(page.locator('[data-recipe-index="1"]')).to_contain_text("TEST BLACK")
            page.reload()
            expect(page.get_by_text("4 × 3 格", exact=True)).to_be_visible()
            print("PASS: OXS preserves grid, CT and thread brand, reload works")

            page.get_by_role("button", name="导入 OXS", exact=True).click()
            page.get_by_role("dialog").locator("input[type=file]").set_input_files(bad)
            page.get_by_role("button", name="导入图纸", exact=True).click()
            expect(page.locator(".import-error")).to_be_visible()
            expect(page.get_by_text("4 × 3 格", exact=True)).to_be_visible()
            print("PASS: invalid OXS shows an error without replacing the current document")
            page.get_by_role("button", name="关闭", exact=True).click()
            page.get_by_role("button", name="图纸库", exact=True).click()
            library = page.get_by_role("region", name="图纸库", exact=True)
            cards = page.locator(".project-card")
            expect(cards).to_have_count(2)
            expect(library).to_be_visible()
            oxs_card = cards.filter(has_text="OXS 导入")
            oxs_card.get_by_role("button", name="重命名", exact=True).click()
            page.get_by_role("textbox", name="图纸名称").fill("我的灯塔图纸")
            page.get_by_role("button", name="保存名称", exact=True).click()
            expect(page.get_by_role("dialog")).not_to_be_visible()
            expect(cards.filter(has_text="我的灯塔图纸")).to_have_count(1)
            page.reload()
            expect(library).to_be_visible()
            expect(cards.filter(has_text="我的灯塔图纸")).to_have_count(1)
            cards.filter(has_text="我的灯塔图纸").get_by_role(
                "button", name="打开图纸", exact=True
            ).click()
            expect(page.locator(".document-title strong")).to_have_text("我的灯塔图纸")
            expect(page.get_by_text("4 × 3 格", exact=True)).to_be_visible()
            print("PASS: library lists image/OXS, rename persists, refresh stays in library")

            page.get_by_role("button", name="导入图片", exact=True).click()
            page.get_by_role("dialog").locator("input[type=file]").set_input_files(
                {
                    "name": "另一份作品.png",
                    "mimeType": "image/png",
                    "buffer": source.read_bytes(),
                }
            )
            page.get_by_role("button", name="自动生成图纸", exact=True).click()
            expect(page.get_by_role("dialog")).not_to_be_visible(timeout=30000)
            expect(page.locator(".document-title strong")).to_have_text("另一份作品")
            page.get_by_role("button", name="图纸库", exact=True).click()
            expect(cards).to_have_count(3)
            expect(cards.filter(has_text="我的灯塔图纸")).to_contain_text("4 × 3 格")
            print("PASS: image imports create independent projects without overwriting OXS")

            search = page.get_by_role("searchbox", name="搜索图纸")
            search.fill("灯塔")
            expect(cards).to_have_count(1)
            expect(cards).to_contain_text("我的灯塔图纸")
            search.fill("没有的名字")
            expect(page.get_by_role("heading", name="没有找到匹配的图纸")).to_be_visible()
            search.fill("")
            expect(cards).to_have_count(3)
            page.get_by_label("图纸排序").select_option("name")
            expect(cards).to_have_count(3)
            page.once("dialog", lambda dialog: dialog.accept())
            cards.filter(has_text="我的灯塔图纸").get_by_role(
                "button", name="移入回收站", exact=True
            ).click()
            expect(cards).to_have_count(2)
            page.get_by_role("button", name="回收站", exact=True).click()
            expect(cards).to_have_count(1)
            expect(cards).to_contain_text("我的灯塔图纸")
            cards.get_by_role("button", name="恢复图纸", exact=True).click()
            expect(page.get_by_role("heading", name="回收站是空的")).to_be_visible()
            page.get_by_role("button", name="全部图纸", exact=True).click()
            expect(cards).to_have_count(3)
            cards.filter(has_text="我的灯塔图纸").get_by_role(
                "button", name="打开图纸", exact=True
            ).click()
            expect(page.get_by_text("4 × 3 格", exact=True)).to_be_visible()
            print("PASS: search, sorting, recycle and restore preserve the pattern")
            page.get_by_role("button", name="编辑模式", exact=True).click()
            page.keyboard.press("Control+a")
            page.get_by_role("button", name="清空选区", exact=True).click()
            expect(page.get_by_text("有未保存修改", exact=True)).to_be_visible()
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("button", name="图纸库", exact=True).click()
            expect(library).not_to_be_visible()
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="图纸库", exact=True).click()
            expect(cards).to_have_count(3)
            page.get_by_role("button", name="全部图纸", exact=True).click()
            expect(cards).to_have_count(3)
            cards.filter(has_text="我的灯塔图纸").get_by_role(
                "button", name="打开图纸", exact=True
            ).click()
            expect(page.get_by_text("有未保存修改", exact=True)).not_to_be_visible()
            expect(page.locator('[data-recipe-index="1"]')).to_contain_text("TEST BLACK")
            expect(page.get_by_role("button", name="查看模式")).to_have_attribute(
                "aria-pressed", "true"
            )
            print("PASS: library navigation confirms unsaved changes; reopen resets to saved view")

            # Invalid image input must leave the currently open project intact.
            page.get_by_role("button", name="导入图片", exact=True).click()
            page.get_by_role("dialog").locator("input[type=file]").set_input_files(
                {
                    "name": "错误图片.png",
                    "mimeType": "image/png",
                    "buffer": b"not an image",
                }
            )
            page.get_by_role("button", name="自动生成图纸", exact=True).click()
            expect(page.locator(".import-error")).to_be_visible()
            expect(page.locator(".document-title strong")).to_have_text("我的灯塔图纸")
            page.get_by_role("button", name="关闭", exact=True).click()
            page.get_by_role("button", name="图纸库", exact=True).click()
            expect(cards).to_have_count(3)
            print("PASS: failed image conversion keeps current project and cleans up empty entry")

            for index in range(23):
                response = page.request.post(
                    f"http://127.0.0.1:{port}/api/v1/projects",
                    data={"name": f"分页测试 {index:02}"},
                )
                assert response.status == 201
            page.get_by_role("button", name="刷新列表", exact=True).click()
            expect(cards).to_have_count(24)
            page.get_by_role("button", name="下一页", exact=True).click()
            expect(cards).to_have_count(2)
            page.get_by_role("button", name="上一页", exact=True).click()
            expect(cards).to_have_count(24)
            page.set_viewport_size({"width": 600, "height": 800})
            library.evaluate("element => element.scrollTop = element.scrollHeight")
            header = page.locator(".app-header").bounding_box()
            assert header and header["y"] == 0
            expect(page.get_by_role("button", name="导入图片", exact=True)).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            print("PASS: pagination and narrow-screen scrolling keep header actions visible")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_role("button", name="新建图纸", exact=True).click()
            expect(page.get_by_text("1 × 1 格", exact=True)).to_be_visible()
            page.get_by_role("button", name="图纸库", exact=True).click()
            page.get_by_role("searchbox", name="搜索图纸").fill("未命名图纸")
            expect(cards).to_have_count(1)
            expect(cards).to_contain_text("新建")
            print("PASS: newly created pattern is available in the library")
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
print("Real API and browser import checks passed.")
