import io
from pathlib import Path

from conftest import AccountClient
from PIL import Image
from stitchforge_api import create_app

CATALOG = Path("catalogs/dmc-117-2021-quiltysolid-2025-07.json").resolve()


def png():
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "#d02030").save(out, format="PNG")
    return out.getvalue()


def enqueue(client, key="test-generation-request-01", payload=None, created=None):
    created = created or client.post("/api/v1/projects", json={"name": "异步生成"}).json()
    pid = created["project"]["project_id"]
    response = client.post(
        f"/api/v1/projects/{pid}/image-jobs",
        params={
            "expected_head_revision_id": created["initial_revision"]["revision_id"],
            "request_key": key,
            "grid_width": 4,
            "grid_height": 4,
            "remove_border_background": "false",
            "optimize": "false",
        },
        content=png() if payload is None else payload,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 202, response.text
    return created, response.json()


def test_job_is_durable_idempotent_and_charged_only_at_worker_commit(tmp_path):
    with AccountClient(create_app(tmp_path)) as client:
        credits = client.get("/api/v1/auth/me").json()["credits"]
        created, job = enqueue(client)
        _, repeated = enqueue(client, created=created)
        assert repeated["job_id"] == job["job_id"]
        assert job["state"] == "QUEUED"
        assert client.get("/api/v1/auth/me").json()["credits"] == credits
        assert (
            client.get("/api/v1/generation-requests/test-generation-request-01").json()["job_id"]
            == job["job_id"]
        )
    from stitchforge_api.worker import run_once

    assert run_once(tmp_path, CATALOG)
    with AccountClient(create_app(tmp_path)) as client:
        finished = client.get(f"/api/v1/jobs/{job['job_id']}").json()
        assert finished["state"] == "SUCCEEDED", finished
        result = finished["result"]
        assert client.get("/api/v1/auth/me").json()["credits"] == credits - 1
        doc = result["revision"]["document"]
        reference = doc["extensions"]["stitchforge.conversion"]
        manifest = client.get("/api/v1/assets/" + reference["artifact_sha256"]).json()
        assert manifest["reports"]["normalization_report"]
        assert manifest["reports"]["palette_report"]
        assert manifest["input"]["source_sha256"] == result["source_asset"]["sha256"]
        assert manifest["catalog_sha256"]
        assert manifest["timings_ms"]["total"] >= 0
        assert client.get("/api/v1/assets/" + reference["artifact_sha256"]).status_code == 200
        _, repeated = enqueue(client, created=created)
        assert repeated["state"] == "SUCCEEDED"
        assert repeated["result"]["revision"]["revision_id"] == result["revision"]["revision_id"]
    assert not run_once(tmp_path, CATALOG)


def test_changed_request_key_and_second_pending_job_are_rejected(tmp_path):
    with AccountClient(create_app(tmp_path)) as client:
        created, job = enqueue(client)
        pid = created["project"]["project_id"]
        params = {
            "request_key": "test-generation-request-01",
            "expected_head_revision_id": created["initial_revision"]["revision_id"],
        }
        assert (
            client.post(
                f"/api/v1/projects/{pid}/image-jobs", params=params, content=b"different"
            ).status_code
            == 409
        )
        params["request_key"] = "another-generation-key"
        assert (
            client.post(
                f"/api/v1/projects/{pid}/image-jobs", params=params, content=png()
            ).status_code
            == 429
        )
        client.post(f"/api/v1/jobs/{job['job_id']}/cancel")
        assert client.get(f"/api/v1/jobs/{job['job_id']}").json()["state"] == "CANCELLED"


def test_bad_image_and_stale_head_do_not_charge(tmp_path):
    from stitchforge_api.worker import run_once

    with AccountClient(create_app(tmp_path)) as client:
        credits = client.get("/api/v1/auth/me").json()["credits"]
        _, bad = enqueue(client, payload=b"invalid-image")
        assert run_once(tmp_path, CATALOG)
        assert client.get(f"/api/v1/jobs/{bad['job_id']}").json()["state"] == "FAILED"
        created, stale = enqueue(client, key="test-generation-request-02")
        document = created["initial_revision"]["document"]
        document["metadata"]["title"] = "较新的编辑"
        pid = created["project"]["project_id"]
        saved = client.post(
            f"/api/v1/projects/{pid}/revisions",
            json={
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
                "document": document,
            },
        ).json()
        assert run_once(tmp_path, CATALOG)
        assert client.get(f"/api/v1/jobs/{stale['job_id']}").json()["state"] == "FAILED"
        assert client.get("/api/v1/auth/me").json()["credits"] == credits
        assert (
            client.get(f"/api/v1/projects/{pid}").json()["head_revision_id"] == saved["revision_id"]
        )


def test_cancelled_lease_cannot_publish_or_charge(tmp_path):
    with AccountClient(create_app(tmp_path)) as client:
        created, job = enqueue(client)
        store = client.app.state.store
        from stitchforge_storage.accounts import now_ms

        claimed = store.claim_next_job("worker-test", now_ms=now_ms())
        store.request_cancel(job["job_id"], now_ms=now_ms())
        user = client.get("/api/v1/auth/me").json()
        import pytest
        from stitchforge_storage.sqlite import JobLeaseLostError

        with pytest.raises(JobLeaseLostError):
            store.add_revision(
                "cancelled-result",
                project_id=created["project"]["project_id"],
                expected_head_revision_id=created["initial_revision"]["revision_id"],
                document=created["initial_revision"]["document"],
                now_ms=now_ms(),
                charge_user_id=user["user_id"],
                job_lease=(job["job_id"], "worker-test", claimed.lease_epoch),
                job_result={},
            )
        assert client.get("/api/v1/auth/me").json()["credits"] == user["credits"]


def test_worker_claim_limit_is_shared_across_processes(tmp_path):
    with AccountClient(create_app(tmp_path)) as client:
        created, first = enqueue(client)
        store = client.app.state.store
        # Generic storage jobs exercise the same global running limit without user throttling.
        store.enqueue_job(
            "second",
            project_id=created["project"]["project_id"],
            job_type="image_conversion",
            parameters={},
            now_ms=2,
        )
        claimed = store.claim_next_job("one", now_ms=10, job_type="image_conversion", max_running=1)
        assert claimed is not None
        assert (
            store.claim_next_job("two", now_ms=11, job_type="image_conversion", max_running=1)
            is None
        )


def test_api_lifespan_starts_worker_and_stops_it_cleanly(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("STITCHFORGE_AI_PROVIDER", "local")
    with AccountClient(create_app(tmp_path, start_worker=True)) as client:
        _, job = enqueue(client)
        process = client.app.state.worker_process
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            finished = client.get(f"/api/v1/jobs/{job['job_id']}").json()
            if finished["state"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.1)
        assert finished["state"] == "SUCCEEDED", finished
    assert not process.is_alive()


def test_completed_checkpoint_resumes_after_a_lost_worker(tmp_path, minimal_document):
    from stitchforge_api.worker import run_once
    from stitchforge_storage.accounts import now_ms

    with AccountClient(create_app(tmp_path)) as client:
        _, job = enqueue(client, payload=b"checkpoint-means-no-redecode-needed")
        store = client.app.state.store
        timestamp = now_ms()
        claim = store.claim_next_job("lost-worker", now_ms=timestamp, lease_seconds=1)
        store.complete_stage(
            job["job_id"],
            "lost-worker",
            claim.lease_epoch,
            stage="conversion",
            stage_key=claim.parameters_hash,
            result={"document": minimal_document, "response": {}, "owned_asset_hashes": []},
            now_ms=timestamp,
        )
        store.recover_expired_jobs(now_ms=timestamp + 1001)
        assert run_once(tmp_path, CATALOG)
        finished = client.get(f"/api/v1/jobs/{job['job_id']}").json()
        assert finished["state"] == "SUCCEEDED", finished
        assert finished["attempt_count"] == 2
        assert (
            finished["result"]["revision"]["document"]["full_stitches"]
            == minimal_document["full_stitches"]
        )


def test_upload_limit_stops_consuming_stream_before_the_tail(monkeypatch):
    import asyncio

    import pytest
    from starlette.requests import Request
    from stitchforge_api import generation
    from stitchforge_storage.accounts import AccountError

    monkeypatch.setattr(generation, "MAX_IMAGE_FILE_BYTES", 4)
    chunks = iter([b"123", b"45", b"never-read"])
    received = []

    async def receive():
        chunk = next(chunks)
        received.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    with pytest.raises(AccountError) as failure:
        asyncio.run(generation.read_image_upload(Request({"type": "http"}, receive)))
    assert failure.value.status == 413
    assert received == [b"123", b"45"]


def idle_computation(*args):
    import time

    time.sleep(30)


def test_worker_can_interrupt_a_running_computation_without_charging(tmp_path):
    import threading
    import time

    from stitchforge_api.worker import run_once

    with AccountClient(create_app(tmp_path)) as client:
        _, job = enqueue(client)
        credits = client.get("/api/v1/auth/me").json()["credits"]
        store = client.app.state.store
        from stitchforge_storage.accounts import now_ms

        timer = threading.Timer(0.5, lambda: store.request_cancel(job["job_id"], now_ms=now_ms()))
        timer.start()
        started = time.monotonic()
        try:
            assert run_once(tmp_path, CATALOG, target=idle_computation)
        finally:
            timer.join()
        assert time.monotonic() - started < 5
        assert store.get_job(job["job_id"]).state == "CANCELLED"
        assert client.get("/api/v1/auth/me").json()["credits"] == credits


def test_worker_timeout_is_terminal_and_does_not_charge(tmp_path):
    from stitchforge_api.worker import run_once

    with AccountClient(create_app(tmp_path)) as client:
        _, job = enqueue(client)
        credits = client.get("/api/v1/auth/me").json()["credits"]
        assert run_once(tmp_path, CATALOG, timeout_seconds=0.5, target=idle_computation)
        finished = client.app.state.store.get_job(job["job_id"])
        assert finished.state == "FAILED"
        assert finished.error["code"] == "GENERATION_TIMEOUT"
        assert client.get("/api/v1/auth/me").json()["credits"] == credits


def test_compatibility_conversion_shares_worker_resource_limit(tmp_path):
    from stitchforge_api.resources import computation_slot

    with AccountClient(create_app(tmp_path)) as client:
        created = client.post("/api/v1/projects", json={"name": "并发限制"}).json()
        pid = created["project"]["project_id"]
        with computation_slot(tmp_path / "work") as acquired:
            assert acquired
            response = client.post(
                f"/api/v1/projects/{pid}/convert-image",
                params={
                    "expected_head_revision_id": created["initial_revision"]["revision_id"],
                    "grid_width": 4,
                    "grid_height": 4,
                },
                content=png(),
                headers={"Content-Type": "image/png"},
            )
            assert response.status_code == 429


def test_rejected_upload_does_not_write_an_unowned_blob(tmp_path):
    with AccountClient(create_app(tmp_path)) as client:
        created = client.post("/api/v1/projects", json={"name": "拒绝上传"}).json()
        with client.app.state.store._transaction() as connection:
            connection.execute("UPDATE users SET credits = 0")
        before = list((tmp_path / "artifacts" / "sha256").rglob("*"))
        response = client.post(
            f"/api/v1/projects/{created['project']['project_id']}/image-jobs",
            params={
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
                "request_key": "rejected-generation-key",
            },
            content=png(),
            headers={"Content-Type": "image/png"},
        )
        assert response.status_code == 402
        assert list((tmp_path / "artifacts" / "sha256").rglob("*")) == before
