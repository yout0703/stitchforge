"""Mobile review regressions, mocked API and isolated browser storage.

Requires the frontend at 127.0.0.1:5173. Run:
uv run --with playwright python tools/check_review_mobile_fixes.py
"""

import copy
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PATTERN = json.loads((ROOT / "examples/minimal-pattern.json").read_text())
PROJECT = {
    "project_id": "review-mobile",
    "head_revision_id": "r1",
    "name": "移动回归样例",
    "created_at_ms": 1789632000000,
    "updated_at_ms": 1789632000000,
    "deleted_at_ms": None,
    "width": 4,
    "height": 3,
    "fabric_count": 14,
    "color_count": 2,
    "source_kind": "oxs",
}


def blank_document(body):
    document = copy.deepcopy(PATTERN)
    document["metadata"]["title"] = body["name"]
    document["grid"].update(width=body["width"], height=body["height"])
    document["full_stitches"]["cells"] = [0] * (body["width"] * body["height"])
    document["regions"] = []
    document["materials"] = []
    document["recipes"] = []
    document["legend"] = []
    document["constraints"]["color_budget"] = 8
    colors = [
        "#292524",
        "#FFFFFF",
        "#C2410C",
        "#D97706",
        "#CA8A04",
        "#4D7C0F",
        "#0369A1",
        "#7E22CE",
    ]
    for index, color in enumerate(colors, start=1):
        material = copy.deepcopy(PATTERN["materials"][0])
        material.update(
            id=f"base_{index}", brand="自定义", code=f"B{index}", name=f"基础色 {index}"
        )
        material["color"]["display_srgb"] = color
        document["materials"].append(material)
        document["recipes"].append(
            {
                "id": f"recipe_{index}",
                "index": index,
                "components": [{"material_id": material["id"], "strands": 2}],
            }
        )
        document["legend"].append({"recipe_id": f"recipe_{index}", "symbol_id": f"s{index:03d}"})
    return document


def install_api(page, calls):
    documents = {"review-mobile": PATTERN}

    def api(route):
        request = route.request
        path = urlparse(request.url).path
        if path.endswith("/auth/me"):
            route.fulfill(
                json={"user_id": "review-mobile", "email": "review@example.com", "credits": 10}
            )
        elif path == "/api/v1/projects" and request.method == "POST":
            body = request.post_data_json
            calls["create"] = body
            document = blank_document(body)
            documents["created-mobile"] = document
            route.fulfill(
                json={
                    "project": {**PROJECT, "project_id": "created-mobile"},
                    "initial_revision": {"revision_id": "blank-r1", "document": document},
                }
            )
        elif path.endswith("/revisions") and request.method == "POST":
            calls["save"] = request.post_data_json
            route.fulfill(json={"revision_id": "saved-r2"})
        elif "/revisions/" in path:
            project_id = path.split("/")[4]
            route.fulfill(json={"revision_id": "r1", "document": documents[project_id]})
        elif path == "/api/v1/projects":
            route.fulfill(json={"items": [PROJECT], "total": 1})
        elif "/thumbnail" in path:
            route.fulfill(
                content_type="image/svg+xml",
                body='<svg xmlns="http://www.w3.org/2000/svg" width="4" height="3"/>',
            )
        else:
            route.fulfill(json=PROJECT)

    page.route("**/api/v1/**", api)


def settle(page):
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )


def tap_cell(page, col, row, width, height):
    canvas = page.locator("canvas")
    expect(canvas).to_be_visible()
    settle(page)
    box = canvas.bounding_box()
    size = min(6 * 22, (box["width"] - 56) / width, (box["height"] - 56) / height)
    x = box["x"] + (box["width"] - width * size) / 2 + (col + 0.5) * size
    y = box["y"] + (box["height"] - height * size) / 2 + (row + 0.5) * size
    page.touchscreen.tap(x, y)


def assert_focus_inside(page):
    assert page.evaluate(
        "document.querySelector('#pattern-inspector').contains(document.activeElement)"
    )


with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    page = context.new_page()
    install_api(page, {})
    page.add_init_script(
        "localStorage.setItem('stitchforge.lastProjectId.review-mobile', 'review-mobile')"
    )
    page.goto("http://127.0.0.1:5173")
    page.get_by_text(PATTERN["metadata"]["title"], exact=True).first.wait_for(state="attached")
    tap_cell(page, 2, 0, 4, 3)
    expect(page.locator(".status-bar > span").first).to_have_text("列 3，行 1")
    toggle = page.locator(".mobile-inspector-toggle")
    toggle.click()
    panel = page.get_by_role("dialog", name="线色与图纸信息")
    expect(panel).to_be_visible()
    expect(panel.locator(".live-cell-position")).to_have_text("列 3 · 行 1")
    expect(panel.locator(".current-cell-panel p")).to_contain_text("TEST WHITE")
    assert_focus_inside(page)
    print("PASS: mobile touch selection keeps coordinates and thread details after release/open")

    count = panel.locator("button:not(:disabled), a[href], input:not(:disabled)").count()
    for key in ["Tab", "Shift+Tab"]:
        for _ in range(count + 2):
            page.keyboard.press(key)
            assert_focus_inside(page)
    page.keyboard.press("Escape")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(toggle).to_be_focused()
    print("PASS: inspector traps Tab/Shift+Tab and restores trigger focus on Escape")
    context.close()

    context = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    page = context.new_page()
    calls = {}
    install_api(page, calls)
    page.add_init_script("""
        const original = IDBObjectStore.prototype.put;
        IDBObjectStore.prototype.put = function(...args) {
            if (this.name === 'drafts') {
                throw new DOMException('Test quota exceeded', 'QuotaExceededError');
            }
            return original.apply(this, args);
        };
    """)
    page.goto("http://127.0.0.1:5173/#library")
    page.get_by_role("button", name="创建图纸", exact=True).click()
    page.get_by_role("button", name="空白图纸 从一张空白针格开始", exact=True).click()
    dialog = page.get_by_role("dialog", name="新建空白图纸")
    dialog.get_by_label("图纸名称").fill("手机空白回归")
    dialog.get_by_label("宽度（格）").fill("20")
    dialog.get_by_label("高度（格）").fill("16")
    dialog.get_by_role("button", name="创建并开始绘制").click()
    expect(dialog).not_to_be_visible()
    assert calls["create"]["width"] == 20 and calls["create"]["height"] == 16
    assert calls["create"]["name"] == "手机空白回归"
    expect(page.locator("canvas")).to_have_attribute("aria-label", "可编辑十字绣针格")
    page.locator(".mobile-inspector-toggle").click()
    expect(page.locator("#pattern-inspector .palette-row")).to_have_count(8)
    page.keyboard.press("Escape")
    tap_cell(page, 9, 7, 20, 16)
    error = page.locator(".workbench-notices [role='alert']").filter(has_text="本地草稿保存失败")
    expect(error).to_be_visible()
    box = error.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 844, box
    expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_enabled()
    print(
        "PASS: blank form submits dimensions, exposes eight colors; touch painting becomes dirty"
    )
    print("PASS: IndexedDB write failure is visibly announced on mobile")
    page.get_by_role("button", name="保存版本", exact=True).click()
    page.wait_for_function(
        "document.querySelector('.mobile-document-title span').textContent.includes('已保存')"
    )
    saved = calls["save"]["document"]
    assert saved["full_stitches"]["cells"][7 * 20 + 9] == 1
    assert sum(cell != 0 for cell in saved["full_stitches"]["cells"]) == 1
    print("PASS: server save receives exactly the painted needle despite local draft failure")
    context.close()
    browser.close()
