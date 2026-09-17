"""Isolated canvas regressions; requires the frontend at 127.0.0.1:5173.

Run: uv run --with playwright python tools/check_canvas_review_fixes.py
All API requests are mocked; no user data is modified.
"""

import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PATTERN = json.loads((ROOT / "examples/minimal-pattern.json").read_text())
PATTERN["grid"].update(width=100, height=120)
PATTERN["full_stitches"]["cells"] = [1] * 12000
PATTERN["provenance"] = {
    "sources": [{"id": "src_image", "locator": "/api/v1/assets/canvas-review"}]
}


def install(page):
    page.add_init_script("""
        localStorage.setItem('stitchforge.lastProjectId.canvas-review', 'canvas-review');
        window.canvasViews = new WeakMap();
        const translate = CanvasRenderingContext2D.prototype.translate;
        const fillRect = CanvasRenderingContext2D.prototype.fillRect;
        CanvasRenderingContext2D.prototype.translate = function(x, y) {
            this.reviewView = {x, y};
            return translate.call(this, x, y);
        };
        CanvasRenderingContext2D.prototype.fillRect = function(x, y, width, height) {
            if (this.reviewView) {
                window.canvasViews.set(this.canvas, {...this.reviewView, width, height});
                this.reviewView = null;
            }
            return fillRect.call(this, x, y, width, height);
        };
    """)

    def api(route):
        url = route.request.url
        if "/auth/me" in url:
            route.fulfill(
                json={"user_id": "canvas-review", "email": "test@example.com", "credits": 10}
            )
        elif "/revisions/" in url:
            route.fulfill(json={"revision_id": "r1", "document": PATTERN})
        elif "/assets/" in url:
            route.fulfill(
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"/>',
            )
        else:
            route.fulfill(json={"project_id": "canvas-review", "head_revision_id": "r1"})

    page.route("**/api/v1/**", api)
    page.goto("http://127.0.0.1:5173")
    page.get_by_text("100 × 120 格", exact=True).wait_for(state="attached")
    page.wait_for_function('window.canvasViews.has(document.querySelector("canvas"))')


def settle(page):
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )


def view(canvas):
    return canvas.evaluate("canvas => window.canvasViews.get(canvas)")


with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    page = context.new_page()
    install(page)
    canvas = page.locator("canvas")
    initial = view(canvas)
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, -700)
    page.wait_for_function(
        "initial => window.canvasViews.get(document.querySelector('canvas')).width > initial",
        arg=initial["width"],
    )
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 30, box["y"] + box["height"] / 2 + 55, steps=5)
    page.mouse.up()
    settle(page)
    before = view(canvas)
    page.get_by_role("button", name="原图对照", exact=True).click()
    expect(canvas).to_be_hidden()
    settle(page)
    page.get_by_role("button", name="针格", exact=True).click()
    expect(canvas).to_be_visible()
    settle(page)
    assert view(canvas) == before, (before, view(canvas))
    print("PASS: mobile source comparison preserves zoom and pan")

    old_box = canvas.bounding_box()
    page.set_viewport_size({"width": 430, "height": 932})
    page.wait_for_function(
        "width => document.querySelector('canvas').clientWidth !== width", arg=old_box["width"]
    )
    settle(page)
    new_box = canvas.bounding_box()
    after = view(canvas)
    assert after["width"] == before["width"] and after["height"] == before["height"]
    assert abs(after["x"] - before["x"] - (new_box["width"] - old_box["width"]) / 2) < 1
    assert abs(after["y"] - before["y"] - (new_box["height"] - old_box["height"]) / 2) < 1
    print("PASS: resizing preserves scale and center needle position")
    context.close()

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    install(page)
    canvas = page.locator("canvas")
    edit = page.get_by_role("button", name="编辑模式", exact=True)
    edit.focus()
    page.keyboard.press("Space")
    expect(canvas).to_have_attribute("aria-label", "可编辑十字绣针格")
    canvas.focus()
    page.keyboard.down("Space")
    expect(canvas).to_have_class(re.compile("temporary-pan"))
    page.get_by_role("button", name="查看模式", exact=True).focus()
    expect(canvas).not_to_have_class(re.compile("temporary-pan"))
    page.keyboard.up("Space")
    page.keyboard.press("Space")
    expect(canvas).to_have_attribute("aria-label", "十字绣图纸，拖动平移，双指缩放，点击查看线色")
    print("PASS: toolbar Space activation works; canvas pan clears on blur")
    browser.close()
