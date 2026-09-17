"""Isolated regressions for rename preservation and safe failed-upload abandonment."""

import copy
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("STITCHFORGE_WEB_URL", "http://127.0.0.1:5173")
PATTERN = json.loads(Path("examples/minimal-pattern.json").read_text())
PATTERN["regions"] = []
PENDING_KEY = "stitchforge.pendingGeneration.review"


def read_draft(page):
    return page.evaluate("""() => new Promise(resolve => {
      const r = indexedDB.open('stitchforge-drafts', 1);
      r.onsuccess = () => { const db = r.result;
        const q = db.transaction('drafts').objectStore('drafts').get(['review','p1']);
        q.onsuccess = () => { resolve(q.result ?? null); db.close() };
      };
    })""")


with sync_playwright() as runner:
    browser = runner.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    server_pattern = copy.deepcopy(PATTERN)
    state = {"head": "r1", "recovery": "missing", "delete_conflict": False}
    requests = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())

    def api(route):
        url = urlparse(route.request.url)
        path, method = url.path, route.request.method
        requests.append((method, path, url.query))
        if path.endswith("/auth/me"):
            route.fulfill(json={"user_id": "review", "email": "review@example.com", "credits": 10})
        elif path.endswith("/image-jobs"):
            route.fulfill(status=503, json={"code": "UNAVAILABLE", "message": "模拟上传失败"})
        elif "/generation-requests/" in path:
            if state["recovery"] == "running":
                route.fulfill(
                    json={
                        "job_id": "j1",
                        "project_id": "p2",
                        "state": "RUNNING",
                        "stage": "planning",
                    }
                )
            else:
                offline = state["recovery"] == "offline"
                route.fulfill(
                    status=503 if offline else 404,
                    json={"code": "UNAVAILABLE" if offline else "NOT_FOUND", "message": "查询失败"},
                )
        elif path == "/api/v1/projects" and method == "POST":
            route.fulfill(
                json={
                    "project": {"project_id": "p2"},
                    "initial_revision": {"revision_id": "r0", "document": PATTERN},
                }
            )
        elif path == "/api/v1/projects" and method == "GET":
            route.fulfill(
                json={
                    "items": [
                        {
                            "project_id": "p1",
                            "name": server_pattern["metadata"]["title"],
                            "head_revision_id": state["head"],
                            "width": 4,
                            "height": 3,
                            "fabric_count": 14,
                            "color_count": 2,
                            "source_kind": "oxs",
                            "created_at_ms": 1,
                            "updated_at_ms": 1,
                        }
                    ],
                    "total": 1,
                }
            )
        elif path == "/api/v1/projects/p1" and method == "PATCH":
            server_pattern["metadata"]["title"] = route.request.post_data_json["name"]
            state["head"] = "r2"
            route.fulfill(json={"revision_id": "r2"})
        elif path == "/api/v1/projects/p2" and method == "DELETE":
            assert parse_qs(url.query)["expected_head_revision_id"] == ["r0"]
            if state["delete_conflict"]:
                route.fulfill(
                    status=409, json={"code": "REVISION_CONFLICT", "message": "已有生成结果"}
                )
            else:
                route.fulfill(json={"project_id": "p2", "deleted_at_ms": 1})
        elif "/revisions/" in path:
            route.fulfill(json={"revision_id": state["head"], "document": server_pattern})
        elif "/thumbnail" in path:
            route.fulfill(status=204)
        else:
            route.fulfill(json={"project_id": "p1", "head_revision_id": state["head"]})

    page.route("**/api/v1/**", api)
    page.goto(BASE_URL)
    changed = copy.deepcopy(PATTERN)
    changed["full_stitches"]["cells"][0] = 0
    draft = {
        "version": 1,
        "userId": "review",
        "projectId": "p1",
        "baseRevisionId": "r1",
        "document": changed,
        "savedDocument": PATTERN,
        "past": [{"label": "画笔", "changes": [{"index": 0, "before": 1, "after": 0}]}],
        "future": [],
        "updatedAt": 1,
    }
    page.evaluate(
        """draft => new Promise((resolve,reject) => {
      const r=indexedDB.open('stitchforge-drafts',1);
      r.onupgradeneeded=()=>r.result.createObjectStore('drafts',{keyPath:['userId','projectId']});
      r.onsuccess=()=>{const db=r.result;const tx=db.transaction('drafts','readwrite');
        tx.objectStore('drafts').put(draft);tx.oncomplete=()=>{db.close();
        localStorage.setItem('stitchforge.lastProjectId.review','p1');resolve()};tx.onerror=reject};
      r.onerror=reject;
    })""",
        draft,
    )
    page.goto(BASE_URL + "/#pattern")
    page.reload()
    expect(page.get_by_text("有未保存修改", exact=True)).to_be_visible()
    page.get_by_role("button", name="图纸库", exact=True).click()
    page.get_by_role("button", name="重命名 " + PATTERN["metadata"]["title"], exact=True).click()
    page.locator(".rename-field input").fill("重命名后保留草稿")
    page.get_by_role("button", name="保存名称", exact=True).click()
    expect(page.locator(".project-card-title")).to_have_text("重命名后保留草稿")
    page.wait_for_function("""() => new Promise(resolve => {
      const r=indexedDB.open('stitchforge-drafts',1); r.onsuccess=()=>{
        const db=r.result;
        const q=db.transaction('drafts').objectStore('drafts').get(['review','p1']);
        q.onsuccess=()=>{resolve(q.result?.baseRevisionId==='r2');db.close()};};})""")
    remaining = read_draft(page)
    assert remaining["document"]["full_stitches"]["cells"][0] == 0
    assert remaining["savedDocument"]["full_stitches"]["cells"][0] == 1
    assert remaining["past"] == draft["past"]
    assert not any(method == "POST" and path.endswith("/revisions") for method, path, _ in requests)
    page.get_by_role("button", name="打开图纸", exact=True).click()
    page.get_by_role("button", name="编辑模式", exact=True).click()
    expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_enabled()
    page.reload()
    page.get_by_role("button", name="编辑模式", exact=True).click()
    expect(page.get_by_role("button", name="撤销", exact=True)).to_be_enabled()
    page.get_by_role("button", name="撤销", exact=True).click()
    expect(page.get_by_role("button", name="保存版本", exact=True)).to_be_disabled()
    print("PASS: rename preserves dirty cells, updated base and undo history through reload")

    def upload(name, content):
        page.get_by_role("button", name="导入图片", exact=True).click()
        page.locator("input[type=file]").set_input_files(
            {"name": name, "mimeType": "image/png", "buffer": content}
        )
        page.get_by_role("button", name="生成图纸 · 1 次", exact=True).click()
        expect(page.locator(".import-error")).to_be_visible()
        page.get_by_role("button", name="关闭", exact=True).click()
        expect(page.get_by_role("button", name="放弃本次上传", exact=True)).to_be_visible()

    upload("first.png", b"first")
    page.reload()
    abandon = page.get_by_role("button", name="放弃本次上传", exact=True)
    expect(abandon).to_be_visible()
    state["recovery"] = "offline"
    abandon.click()
    expect(
        page.get_by_text("暂时无法确认上传状态，记录已保留，请恢复连接后重试。", exact=True)
    ).to_be_visible()
    assert page.evaluate("key => localStorage.getItem(key)", PENDING_KEY)
    assert not any(method == "DELETE" for method, _, _ in requests)
    state["recovery"] = "missing"
    state["delete_conflict"] = True
    abandon.click()
    expect(abandon).to_be_enabled()
    assert page.evaluate("key => localStorage.getItem(key)", PENDING_KEY)
    state["delete_conflict"] = False
    abandon.click()
    expect(page.get_by_text("本次上传已放弃，可以重新选择图片。", exact=True)).to_be_visible()
    assert page.evaluate("key => localStorage.getItem(key)", PENDING_KEY) is None
    print("PASS: offline/head-conflict preserve pending; confirmed abandonment clears it safely")

    upload("second.png", b"second")
    assert sum(path.endswith("/image-jobs") for _, path, _ in requests) == 2
    deletes_before = sum(method == "DELETE" for method, _, _ in requests)
    state["recovery"] = "running"
    abandon.click()
    expect(page.get_by_role("button", name="取消生成", exact=True)).to_be_visible()
    assert page.evaluate("key => localStorage.getItem(key)", PENDING_KEY)
    assert sum(method == "DELETE" for method, _, _ in requests) == deletes_before
    print(
        "PASS: replacing image works after abandonment; recovered running task is never discarded"
    )
    assert not errors, errors
    browser.close()
