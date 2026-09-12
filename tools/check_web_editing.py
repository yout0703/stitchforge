"""Isolated Chrome regression for viewer, split comparison and editing history.

Run: uv run --with playwright python tools/check_web_editing.py
Requires frontend at http://127.0.0.1:5173; all API requests are mocked.
"""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
pattern = json.loads((ROOT / "examples/minimal-pattern.json").read_text())
pattern["grid"].update(width=20, height=20)
pattern["full_stitches"]["cells"] = [1 if i % 20 < 10 else 2 for i in range(400)]
pattern["regions"] = []
pattern["provenance"] = {"sources": [{"id": "src_image", "locator": "/api/v1/assets/test"}]}

with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    errors = []
    saved = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("localStorage.setItem('stitchforge.lastProjectId', 'editing-test')")

    def api(route):
        if "/assets/" in route.request.url:
            route.fulfill(
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900">'
                '<rect width="600" height="900" fill="lightblue"/></svg>',
            )
        elif route.request.method == "POST":
            saved.append(route.request.post_data_json["document"])
            route.fulfill(json={"revision_id": f"r-{len(saved)}"})
        elif "/revisions/" in route.request.url:
            route.fulfill(json={"revision_id": "r-test", "document": pattern})
        else:
            route.fulfill(json={"project_id": "editing-test", "head_revision_id": "r-test"})

    page.route("**/api/v1/**", api)
    page.goto("http://127.0.0.1:5173")
    page.get_by_text("20 × 20 格", exact=True).wait_for()
    canvas = page.locator("canvas")
    page.wait_for_timeout(100)

    def click_cell(col, row):
        x, y = cell_position(col, row)
        page.mouse.click(x, y)

    def cell_position(col, row):
        box = canvas.bounding_box()
        size = min((box["width"] - 56) / 20, (box["height"] - 56) / 20, 132)
        return (
            box["x"] + (box["width"] - size * 20) / 2 + (col + 0.5) * size,
            box["y"] + (box["height"] - size * 20) / 2 + (row + 0.5) * size,
        )

    def pixels():
        page.wait_for_timeout(50)
        return canvas.evaluate("(c) => c.toDataURL()")

    assert page.get_by_role("button", name="保存版本").count() == 0
    assert page.get_by_role("button", name="撤销", exact=True).count() == 0
    before = pixels()
    click_cell(2, 2)
    expect(page.locator('[data-recipe-index="1"]')).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_text("当前颜色 200 针", exact=True)).to_be_visible()
    assert pixels() != before
    page.locator('[data-recipe-index="2"]').click()
    expect(page.locator('[data-recipe-index="2"]')).to_have_attribute("aria-pressed", "true")
    page.keyboard.press("Escape")
    assert pixels() == before
    page.keyboard.press("e")
    click_cell(3, 3)
    page.keyboard.press("Escape")
    assert pixels() == before
    print("PASS: default viewer is read-only; cell/palette highlighting and counts work")

    page.get_by_role("button", name="符号显示", exact=True).click()
    expect(page.locator('[data-recipe-index="1"] .recipe-symbol')).to_have_text("A")
    assert pixels() != before
    page.get_by_role("button", name="符号显示", exact=True).click()
    page.get_by_role("button", name="网格", exact=True).click()
    assert pixels() != before
    page.get_by_role("button", name="网格", exact=True).click()
    assert pixels() == before
    print("PASS: symbol legend and grid toggles change only the view")

    page.get_by_role("button", name="原图对照", exact=True).click()
    left = page.get_by_role("region", name="原图视图")
    right = page.get_by_role("region", name="图纸视图")
    assert left.bounding_box()["x"] < right.bounding_box()["x"]
    assert abs(left.bounding_box()["y"] - right.bounding_box()["y"]) < 1
    assert canvas.is_visible()
    image = left.locator("img")
    page.wait_for_function('document.querySelector(".source-preview img").complete')
    assert (
        image.bounding_box()["height"] <= page.locator(".source-preview").bounding_box()["height"]
    )
    print("PASS: left original and right chart remain visible together, original fits")

    page.get_by_role("button", name="编辑模式", exact=True).click()
    page.get_by_role("button", name="适应窗口", exact=True).click()
    page.wait_for_timeout(100)
    before = pixels()
    page.get_by_role("button", name="橡皮擦 E", exact=True).click()
    page.mouse.move(*cell_position(2, 2))
    page.mouse.down()
    page.mouse.move(*cell_position(6, 2), steps=20)
    page.mouse.up()
    painted = pixels()
    assert painted != before
    expect(page.locator(".history-panel li")).to_have_count(1)
    page.get_by_role("button", name="撤销", exact=True).click()
    assert pixels() == before
    expect(page.get_by_role("button", name="保存版本")).to_be_disabled()
    page.keyboard.press("Control+Shift+z")
    assert pixels() == painted
    page.get_by_role("button", name="保存版本").click()
    expect(page.get_by_role("button", name="保存版本")).to_be_disabled()
    assert saved[-1]["full_stitches"]["cells"][42:47] == [0] * 5
    print("PASS: entire stroke undo/redo, no accidental extra history, saved cells verified")

    page.get_by_role("button", name="选区 S", exact=True).click()
    page.mouse.move(*cell_position(3, 5))
    page.mouse.down()
    page.mouse.move(*cell_position(1, 3), steps=6)
    page.mouse.up()
    expect(page.locator(".history-panel li")).to_have_count(1)
    page.get_by_role("button", name="清空选区", exact=True).click()
    expect(page.locator(".history-panel li")).to_have_count(2)
    page.keyboard.press("Control+z")
    expect(page.locator(".history-panel li")).to_have_count(1)
    page.keyboard.press("Escape")
    assert pixels() == painted
    print("PASS: reverse selection never paints; clear selection is one undoable operation")

    page.locator('[data-recipe-index="2"]').click()
    page.get_by_role("button", name="填色 G", exact=True).click()
    click_cell(1, 1)
    page.get_by_role("button", name="保存版本").click()
    expect(page.get_by_role("button", name="保存版本")).to_be_disabled()
    assert all(cell == 2 or cell == 0 for cell in saved[-1]["full_stitches"]["cells"])
    expect(page.get_by_role("button", name="重做", exact=True)).to_be_disabled()
    print("PASS: connected fill respects boundaries and invalidates old redo branch")

    page.get_by_role("button", name="查看模式", exact=True).click()
    view_pixels = pixels()
    page.keyboard.press("Control+z")
    page.keyboard.press("Delete")
    assert pixels() == view_pixels
    page.get_by_role("button", name="编辑模式", exact=True).click()
    expect(page.get_by_role("button", name="保存版本")).to_be_disabled()
    page.keyboard.press("Meta+z")
    expect(page.get_by_role("button", name="保存版本")).to_be_enabled()
    page.keyboard.press("Meta+Shift+z")
    expect(page.get_by_role("button", name="保存版本")).to_be_disabled()
    print("PASS: mode switches preserve history; view mode blocks edit shortcuts; macOS undo works")
    assert not errors, errors
    browser.close()
print("Viewer and editing browser regressions passed.")
