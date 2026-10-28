"""Browser regression for durable generation recovery and cancellation; mocked API only."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

pattern = json.loads(Path("examples/minimal-pattern.json").read_text())
pattern["regions"] = []
pattern.setdefault("extensions", {})["stitchforge.rare_colors"] = {
    "version": 1,
    "colors_before": 3,
    "colors_after": 2,
    "changed_stitches": 4,
    "retained_colors": 1,
}
job = None
submissions = 0
with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def api(route):
        global job, submissions
        url, method = route.request.url, route.request.method
        if "/auth/me" in url:
            route.fulfill(
                json={"user_id": "generation-user", "email": "g@example.com", "credits": 10}
            )
        elif "/image-jobs" in url:
            submissions += 1
            job = {
                "job_id": f"job-{submissions}",
                "project_id": "generated",
                "state": "QUEUED",
                "stage": "queued",
                "result": None,
                "error": None,
            }
            route.fulfill(status=202, json=job)
        elif url.endswith("/cancel"):
            job["state"] = "CANCELLED"
            route.fulfill(json=job)
        elif "/generation-requests/" in url or "/jobs/" in url:
            route.fulfill(json=job)
        elif url.endswith("/projects") and method == "POST":
            route.fulfill(
                status=201,
                json={
                    "project": {"project_id": "generated", "head_revision_id": "r0"},
                    "initial_revision": {"revision_id": "r0", "document": pattern},
                },
            )
        elif "/revisions/" in url:
            route.fulfill(json={"revision_id": "r1", "document": pattern})
        elif "/projects?" in url:
            route.fulfill(json={"items": [], "total": 0})
        else:
            route.fulfill(json={"project_id": "generated", "head_revision_id": "r1"})

    page.route("**/api/v1/**", api)
    page.goto(os.environ.get("STITCHFORGE_WEB_URL", "http://127.0.0.1:5173"))
    page.locator(".sidebar-task-button").filter(has_text="图片转图纸").click()
    page.locator("input[type=file]").set_input_files(
        {"name": "sample.png", "mimeType": "image/png", "buffer": b"image-test"}
    )
    page.get_by_role("button", name="生成图纸 · 1 次", exact=True).click()
    expect(page.get_by_role("button", name="取消生成", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="取消生成", exact=True)).to_be_visible()
    assert submissions == 1
    job.update(
        state="SUCCEEDED",
        stage="completed",
        result={"revision": {"revision_id": "r1", "document": pattern}},
    )
    open_result = page.get_by_role("button", name="打开生成图纸", exact=True)
    grid = page.get_by_text("4 × 3 格", exact=True)
    open_result.or_(grid).wait_for(timeout=10000)
    if open_result.is_visible():
        open_result.click()
    grid.wait_for()
    expect(page.get_by_text("生成时合并了 1 种近似线色，调整 4 针。", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_text("生成时合并了 1 种近似线色，调整 4 针。", exact=True)).to_be_visible()
    assert submissions == 1
    page.locator(".sidebar-task-button").filter(has_text="图片转图纸").click()
    page.locator("input[type=file]").set_input_files(
        {"name": "second.png", "mimeType": "image/png", "buffer": b"second-test"}
    )
    page.get_by_role("button", name="生成图纸 · 1 次", exact=True).click()
    page.get_by_role("button", name="取消生成", exact=True).click()
    expect(page.get_by_text("生成已取消，本次不扣次数。", exact=True)).to_be_visible()
    assert submissions == 2
    assert not errors, errors
    browser.close()
    print("PASS: queued upload, reload recovery without duplicate submission, open result, cancel")
