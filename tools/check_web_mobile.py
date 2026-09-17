"""Mobile browser regressions for the StitchForge workbench.

Run with the web dev server at 127.0.0.1:5173:
uv run --with playwright python tools/check_web_mobile.py
"""

import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PATTERN = json.loads((ROOT / "examples/minimal-pattern.json").read_text())
PATTERN["grid"].update(width=220, height=330)
PATTERN["full_stitches"]["cells"] = [1] * (220 * 330)
PATTERN["provenance"] = {
    "sources": [{"id": "src_image", "locator": "/api/v1/assets/mobile-source"}]
}


def install_api(page: Page) -> None:
    def api(route):
        url = route.request.url
        path = urlparse(url).path
        if "/auth/me" in url:
            route.fulfill(
                json={"user_id": "mobile-user", "email": "mobile@example.com", "credits": 10}
            )
        elif "/assets/" in url:
            route.fulfill(
                content_type="image/svg+xml",
                body=(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1400">'
                    '<rect width="900" height="1400" fill="#d8c9b4"/></svg>'
                ),
            )
        elif "/revisions/" in url:
            route.fulfill(json={"revision_id": "r-mobile", "document": PATTERN})
        elif path == "/api/v1/projects" and route.request.method == "GET":
            route.fulfill(
                json={
                    "items": [
                        {
                            "project_id": "mobile-test",
                            "head_revision_id": "r-mobile",
                            "name": "移动端布局测试图纸",
                            "created_at_ms": 1789632000000,
                            "updated_at_ms": 1789632000000,
                            "deleted_at_ms": None,
                            "width": 220,
                            "height": 330,
                            "fabric_count": 14,
                            "color_count": 2,
                            "source_kind": "image",
                        }
                    ],
                    "total": 1,
                }
            )
        else:
            route.fulfill(
                json={"project_id": "mobile-test", "head_revision_id": "r-mobile"}
            )

    page.route("**/api/v1/**", api)


def assert_inside_viewport(page: Page, selector: str, width: int, height: int) -> None:
    box = page.locator(selector).bounding_box()
    assert box, f"{selector} has no bounding box"
    assert box["x"] >= -1 and box["y"] >= -1, box
    assert box["x"] + box["width"] <= width + 1, box
    assert box["y"] + box["height"] <= height + 1, box


def assert_no_page_overflow(page: Page) -> None:
    metrics = page.evaluate(
        "() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth, "
        "scrollX, scrollY, scrollHeight: document.documentElement.scrollHeight, height: innerHeight})"
    )
    assert metrics["scrollWidth"] <= metrics["width"] + 1, metrics
    assert metrics["scrollHeight"] <= metrics["height"] + 1, metrics
    assert metrics["scrollX"] == 0 and metrics["scrollY"] == 0, metrics


def assert_touch_targets(page: Page, selector: str) -> None:
    sizes = page.locator(selector).evaluate_all(
        "elements => elements.filter(e => {const s=getComputedStyle(e); "
        "return e.getClientRects().length && s.display !== 'none' && s.visibility !== 'hidden'}).map(e => {"
        "const r=e.getBoundingClientRect(); return {label:e.getAttribute('aria-label') || "
        "e.textContent.trim(), width:r.width, height:r.height}})"
    )
    too_small = [item for item in sizes if item["width"] < 44 or item["height"] < 44]
    assert not too_small, too_small


with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    for width, height in ((390, 844), (430, 932)):
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on(
            "console",
            lambda message: failures.append(message.text) if message.type == "error" else None,
        )
        page.add_init_script(
            "localStorage.setItem('stitchforge.lastProjectId.mobile-user', 'mobile-test')"
        )
        install_api(page)
        page.goto("http://127.0.0.1:5173")
        page.locator("canvas").wait_for()
        page.get_by_text("220 × 330 格 · 已保存", exact=True).wait_for()

        assert_no_page_overflow(page)
        page.get_by_role("button", name="图纸库", exact=True).click()
        page.get_by_text("移动端布局测试图纸", exact=True).wait_for()
        assert_no_page_overflow(page)
        assert_touch_targets(
            page,
            ".library-app-header .header-actions button, .library-controls button, "
            ".library-controls input, .library-controls select",
        )
        for selector in (".library-app-header .header-actions", ".library-controls", ".project-library"):
            overflow = page.locator(selector).evaluate(
                "element => ({client: element.clientWidth, scroll: element.scrollWidth})"
            )
            assert overflow["scroll"] <= overflow["client"] + 1, (selector, overflow)
        assert page.locator(".project-library").evaluate(
            "element => getComputedStyle(element).overflowY"
        ) == "visible"
        assert page.locator(".workbench-shell").evaluate(
            "element => getComputedStyle(element).overflowY"
        ) == "auto"
        tabs = page.locator(".library-controls > .view-tabs")
        assert tabs.locator("button").count() == 2
        assert tabs.locator("button").evaluate_all(
            "buttons => buttons.every(button => button.scrollWidth <= button.clientWidth "
            "&& button.scrollHeight <= button.clientHeight)"
        )
        refresh_box = page.locator(".refresh-button").bounding_box()
        tabs_box = tabs.bounding_box()
        search_box = page.locator(".search-box-wrapper").bounding_box()
        select_box = page.locator(".select-wrapper").bounding_box()
        assert refresh_box and tabs_box
        assert abs(
            refresh_box["y"] + refresh_box["height"] / 2
            - (tabs_box["y"] + tabs_box["height"] / 2)
        ) < 2
        assert search_box and select_box and abs(search_box["y"] - select_box["y"]) < 2
        card_box = page.locator(".project-card").bounding_box()
        assert card_box and card_box["y"] < height * 0.55, card_box

        create_button = page.get_by_role("button", name="创建图纸", exact=True)
        create_button.click()
        create_dialog = page.get_by_role("dialog", name="创建图纸")
        create_dialog.wait_for()
        page.wait_for_function(
            "() => document.querySelector('.mobile-create-sheet').getBoundingClientRect().bottom "
            "<= innerHeight + 1"
        )
        assert_inside_viewport(page, ".mobile-create-sheet", width, height)
        assert_touch_targets(page, ".mobile-create-sheet button")
        page.keyboard.press("Escape")
        assert not create_dialog.is_visible()
        assert create_button.evaluate("element => element === document.activeElement")

        page.get_by_role("button", name="打开图纸", exact=True).click()
        page.locator("canvas").wait_for()

        canvas_box = page.locator(".canvas-viewport").bounding_box()
        assert canvas_box and canvas_box["width"] >= width - 2, canvas_box
        assert canvas_box["height"] >= height * 0.45, canvas_box
        assert_touch_targets(page, ".header-actions button, .tool-rail button, .canvas-toolbar button")

        toggle = page.get_by_role("button", name="线色与信息", exact=True)
        inspector = page.locator("#pattern-inspector")
        assert toggle.get_attribute("aria-expanded") == "false"
        assert inspector.get_attribute("aria-hidden") == "true"
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "true"
        inspector.wait_for(state="visible")
        page.wait_for_function(
            "() => {const r=document.querySelector('#pattern-inspector').getBoundingClientRect(); "
            "return r.bottom <= innerHeight + 1}"
        )
        assert_inside_viewport(page, "#pattern-inspector", width, height)
        assert_touch_targets(page, "#pattern-inspector button")
        page.get_by_role("button", name="关闭线色与信息").last.click()
        assert inspector.get_attribute("aria-hidden") == "true"

        toggle.click()
        page.keyboard.press("Escape")
        assert inspector.get_attribute("aria-hidden") == "true"
        assert toggle.evaluate("element => element === document.activeElement")

        page.get_by_role("button", name="原图对照", exact=True).click()
        page.locator(".source-preview img").wait_for()
        assert page.locator(".pattern-pane").evaluate("e => getComputedStyle(e).display") == "none"
        assert_inside_viewport(page, ".comparison-pane:not(.pattern-pane)", width, height)
        page.get_by_role("button", name="针格", exact=True).click()

        page.get_by_role("button", name="编辑模式", exact=True).click()
        page.locator(".tool-button", has_text="橡皮擦").click()
        canvas = page.locator("canvas")
        canvas_box = canvas.bounding_box()
        assert canvas_box
        center_x = canvas_box["x"] + canvas_box["width"] / 2
        center_y = canvas_box["y"] + canvas_box["height"] / 2
        cdp = context.new_cdp_session(page)
        awaitable_points = [
            {"x": center_x - 28, "y": center_y, "id": 0, "radiusX": 4, "radiusY": 4},
            {"x": center_x + 28, "y": center_y, "id": 1, "radiusX": 4, "radiusY": 4},
        ]
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": awaitable_points})
        cdp.send(
            "Input.dispatchTouchEvent",
            {
                "type": "touchMove",
                "touchPoints": [
                    {**awaitable_points[0], "x": center_x - 48},
                    {**awaitable_points[1], "x": center_x + 48},
                ],
            },
        )
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        save_button = page.get_by_role("button", name="保存版本", exact=True)
        assert save_button.is_disabled(), "pinch gesture must not edit a stitch"
        page.touchscreen.tap(center_x, center_y)
        page.wait_for_function(
            "() => ![...document.querySelectorAll('button')].find(b => b.textContent === '保存版本').disabled"
        )

        page.get_by_role("button", name="导入图片", exact=True).click()
        dialog = page.get_by_role("dialog", name="从图片自动生成图纸")
        dialog.wait_for()
        assert_inside_viewport(page, ".import-dialog", width, height)
        assert_touch_targets(page, ".import-dialog button")
        assert_no_page_overflow(page)
        dialog.get_by_role("button", name="关闭").click()
        assert not dialog.is_visible()
        assert not failures, failures

        print(f"PASS: mobile workbench, drawer and dialog at {width}x{height}")
        context.close()
    browser.close()

print("Mobile browser regressions passed; no overflow or console errors.")
