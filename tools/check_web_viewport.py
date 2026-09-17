"""Browser regressions: uv run --with playwright python tools/check_web_viewport.py.

Requires the web dev server at 127.0.0.1:5173 and installed Google Chrome.
Uses an isolated browser profile and mocked API; does not change user projects.
"""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
pattern = json.loads((ROOT / "examples/minimal-pattern.json").read_text())
pattern["grid"].update(width=220, height=330)
pattern["full_stitches"]["cells"] = [1] * (220 * 330)
pattern["provenance"] = {"sources": [{"id": "src_image", "locator": "/api/v1/assets/test-source"}]}

# Long palette reproduces the case where coordinates used to disappear below the fold.
for index in range(3, 25):
    pattern["recipes"].append(
        {
            "id": f"test_recipe_{index}",
            "index": index,
            "components": [{"material_id": pattern["materials"][0]["id"], "strands": 2}],
        }
    )

with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.on(
        "console",
        lambda message: failures.append(message.text) if message.type == "error" else None,
    )
    page.add_init_script("""
        localStorage.setItem('stitchforge.lastProjectId.test-user', 'viewport-test');
        const original = CanvasRenderingContext2D.prototype.translate;
        window.canvasTranslations = new WeakMap();
        CanvasRenderingContext2D.prototype.translate = function(x, y) {
            window.canvasTranslations.set(this.canvas, {x, y});
            return original.call(this, x, y);
        };
    """)

    def api(route):
        if "/auth/me" in route.request.url:
            route.fulfill(json={"user_id": "test-user", "email": "test@example.com", "credits": 10})
            return
        path = route.request.url
        if "/assets/" in path:
            route.fulfill(
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="4000">'
                '<rect width="1000" height="4000" fill="lightblue"/>'
                '<text x="100" y="3980">BOTTOM</text></svg>',
            )
        elif "/revisions/" in path:
            route.fulfill(json={"revision_id": "r-test", "document": pattern})
        else:
            route.fulfill(json={"project_id": "viewport-test", "head_revision_id": "r-test"})

    page.route("**/api/v1/**", api)
    page.goto("http://127.0.0.1:5173")
    page.get_by_text("220 × 330 格", exact=True).wait_for()
    page.get_by_role("button", name="编辑模式", exact=True).click()
    canvas = page.locator("canvas")
    page.wait_for_function('window.canvasTranslations.has(document.querySelector("canvas"))')

    def position():
        return canvas.evaluate("(c) => window.canvasTranslations.get(c)")

    def header_visible():
        assert page.locator(".app-header").bounding_box()["y"] == 0
        assert page.evaluate("window.scrollY") == 0
        assert page.evaluate("document.documentElement.scrollHeight <= innerHeight")

    before = position()
    box = canvas.bounding_box()
    assert box["y"] > 0 and box["y"] + box["height"] < 900
    # Start from empty canvas margin, traverse in both directions and repeat.
    canvas.focus()
    page.keyboard.down("Space")
    for dx, dy in [(130, 80), (-210, -110), (60, 40)]:
        start = position()
        x, y = box["x"] + 230, box["y"] + 230
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + dx, y + dy, steps=12)
        page.mouse.up()
        page.wait_for_function(
            '([x,y]) => {const p=window.canvasTranslations.get(document.querySelector("canvas"));'
            " return Math.abs(p.x-x)<1 && Math.abs(p.y-y)<1}",
            arg=[start["x"] + dx, start["y"] + dy],
        )
        header_visible()
    page.keyboard.up("Space")
    assert "temporary-pan" not in canvas.get_attribute("class")
    assert page.get_by_role("button", name="保存版本").is_disabled()
    assert page.get_by_role("button", name="画笔 B").get_attribute("aria-pressed") == "true"
    print("PASS: repeated space drags move full distance in both axes without painting")

    # Pointer capture keeps the gesture active beyond the canvas into the toolbar.
    start = position()
    x, y = box["x"] + 250, box["y"] + 120
    page.mouse.move(x, y)
    page.keyboard.down("Space")
    page.mouse.down()
    page.mouse.move(x + 100, 80, steps=12)
    page.mouse.up()
    page.keyboard.up("Space")
    page.wait_for_function(
        '([x,y]) => {const p=window.canvasTranslations.get(document.querySelector("canvas"));'
        " return Math.abs(p.x-x)<1 && Math.abs(p.y-y)<1}",
        arg=[start["x"] + 100, start["y"] + 80 - y],
    )
    header_visible()
    print("PASS: dragging beyond canvas boundary continues and header stays visible")

    page.get_by_role("button", name="适应窗口").click()
    page.wait_for_function('window.canvasTranslations.has(document.querySelector("canvas"))')
    fitted = position()
    assert abs(fitted["x"] - before["x"]) < 1 and abs(fitted["y"] - before["y"]) < 1
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    for delta in [-600, 400, 600]:
        page.mouse.wheel(0, delta)
        page.wait_for_timeout(100)
        header_visible()
    assert position() != fitted
    print("PASS: wheel zoom and fit button operate without scrolling document")

    page.get_by_role("button", name="适应窗口", exact=True).click()
    page.wait_for_timeout(100)
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    info = page.get_by_role("region", name="实时针格信息")
    # section labels need not expose the region role in every browser; use stable label.
    info = page.locator('[aria-label="实时针格信息"]')
    expect_text = page.locator(".live-cell-position").inner_text()
    assert "列" in expect_text and "行" in expect_text
    inspector = page.locator(".inspector")
    inspector.evaluate("(e) => e.scrollTop = e.scrollHeight")
    page.wait_for_timeout(50)
    assert abs(info.bounding_box()["y"] - inspector.bounding_box()["y"]) < 2
    assert page.locator(".live-cell-position").inner_text() == expect_text
    header_visible()
    print("PASS: live coordinates stay pinned above a scrolling 24-color palette")

    for width, height in [(1440, 900), (1024, 700), (800, 700), (600, 800)]:
        page.set_viewport_size({"width": width, "height": height})
        page.get_by_role("button", name="原图对照").click()
        enlarge = page.get_by_role("button", name="放大查看", exact=True)
        if enlarge.count():
            enlarge.click()
        source = page.locator(".source-preview")
        source.locator("img").wait_for()
        page.wait_for_function('document.querySelector(".source-preview img").complete')
        source.hover()
        initial = source.evaluate(
            "(e) => ({h:e.clientHeight, full:e.scrollHeight, top:e.scrollTop})"
        )
        assert initial["full"] > initial["h"] > 0, initial
        page.mouse.wheel(0, 500)
        page.wait_for_function('document.querySelector(".source-preview").scrollTop > 0')
        # Real wheel reaches the end; verify the image bottom can enter the viewport.
        page.mouse.wheel(0, 100000)
        page.wait_for_function(
            '(() => {const e=document.querySelector(".source-preview");'
            " return e.scrollTop+e.clientHeight >= e.scrollHeight-1})()"
        )
        assert source.evaluate(
            '(e) => e.querySelector("img").getBoundingClientRect().bottom'
            " <= e.getBoundingClientRect().bottom"
        )
        header_visible()
        page.get_by_role("button", name="针格", exact=True).click()
        print(f"PASS: source scrolls to image bottom, header fixed at {width}x{height}")
    assert not failures, failures
    browser.close()
print("Browser regressions passed; no console errors.")
