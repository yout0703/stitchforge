"""Isolated browser regression: drafts survive reload and do not overwrite remote edits."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

pattern = json.loads(Path("examples/minimal-pattern.json").read_text())
pattern["grid"].update(width=20, height=20)
pattern["full_stitches"]["cells"] = [1] * 400
pattern["regions"] = []
head = "r1"
fail_revision = False

with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    page.add_init_script(
        "localStorage.setItem('stitchforge.lastProjectId.draft-user', 'draft-project')"
    )

    def api(route):
        url = route.request.url
        if "/auth/me" in url:
            route.fulfill(
                json={"user_id": "draft-user", "email": "draft@example.com", "credits": 10}
            )
        elif "/revisions/" in url and fail_revision:
            route.fulfill(status=503, json={"code": "UNAVAILABLE", "message": "服务器暂不可用"})
        elif "/revisions/" in url:
            route.fulfill(json={"revision_id": head, "document": pattern})
        elif route.request.method == "POST":
            route.fulfill(
                status=409, json={"code": "REVISION_CONFLICT", "message": "图纸已有新版本"}
            )
        else:
            route.fulfill(json={"project_id": "draft-project", "head_revision_id": head})

    page.route("**/api/v1/**", api)
    page.goto(os.environ.get("STITCHFORGE_WEB_URL", "http://127.0.0.1:5173"))
    page.get_by_text("20 × 20 格", exact=True).wait_for()
    page.get_by_role("button", name="编辑模式", exact=True).click()
    page.get_by_role("button", name="橡皮擦 E", exact=True).click()
    canvas = page.locator("canvas")
    page.wait_for_function("document.querySelector('canvas').width > 100")
    page.wait_for_timeout(100)
    box = canvas.bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_enabled()
    expect(page.get_by_text("本地草稿已保存", exact=True)).to_be_visible()
    page.reload()
    page.get_by_text("20 × 20 格", exact=True).wait_for()
    page.get_by_role("button", name="编辑模式", exact=True).click()
    expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="撤销", exact=True)).to_be_enabled()
    head = "r2"
    page.reload()
    expect(page.get_by_text("服务器已有新版本，本地草稿已保留。", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="另存为新图纸", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="载入服务器版本", exact=True)).to_be_visible()
    fail_revision = True
    page.get_by_role("button", name="载入服务器版本", exact=True).click()
    expect(page.locator(".draft-status.api-error")).to_contain_text("服务器暂不可用")
    fail_revision = False
    page.reload()
    expect(page.get_by_text("服务器已有新版本，本地草稿已保留。", exact=True)).to_be_visible()
    page.get_by_role("button", name="载入服务器版本", exact=True).click()
    page.get_by_role("button", name="编辑模式", exact=True).click()
    expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_disabled()
    assert not errors, errors
    browser.close()
    print("PASS: draft persistence, undo recovery, conflict preservation, explicit server reload")
