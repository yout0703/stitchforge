"""One bounded image computation at a time, outside the web server process."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import shutil
import time
from pathlib import Path
from uuid import uuid4

from stitchforge.commands import LockConflictError
from stitchforge_storage import ContentAddressedArtifactStore, SQLiteStore, StorageConflictError
from stitchforge_storage.accounts import AccountError, now_ms
from stitchforge_storage.sqlite import JobLeaseLostError

from .conversion import ConversionOptions, ConversionRequestError, build_conversion
from .resources import computation_slot

log = logging.getLogger(__name__)


def execute_job(data_dir: str, catalog_path: str, job_id: str, worker_id: str, epoch: int):
    store = SQLiteStore(Path(data_dir) / "metadata.sqlite3")
    try:
        job = store.get_job(job_id)
        project = store.get_project(job.project_id)
        if project.head_revision_id != job.input_revision_id:
            raise StorageConflictError("REVISION_CONFLICT: 图纸已更新，生成结果不能覆盖新版本")
        stages = store.get_completed_stages(job_id)
        checkpoint = next((stage for stage in stages if stage.stage == "conversion"), None)
        if checkpoint:
            built = checkpoint.result
        else:
            artifacts = ContentAddressedArtifactStore(Path(data_dir) / "artifacts")
            work = Path(data_dir) / "work" / job_id
            work.mkdir(parents=True, exist_ok=True)

            def progress(stage):
                store.update_stage(job_id, worker_id, epoch, stage=stage, now_ms=now_ms())

            built = build_conversion(
                project_id=job.project_id,
                title=project.name,
                fabric_color=store.get_revision(job.input_revision_id).document["fabric"][
                    "display_srgb"
                ],
                payload=artifacts.read_bytes(job.parameters["source_sha256"]),
                artifact_store=artifacts,
                work_root=work,
                catalog_path=Path(catalog_path),
                options=ConversionOptions.model_validate(job.parameters["options"]),
                progress=progress,
            )
            store.complete_stage(
                job_id,
                worker_id,
                epoch,
                stage="conversion",
                stage_key=job.parameters_hash,
                result=built,
                now_ms=now_ms(),
            )
        store.add_revision(
            "revision_" + uuid4().hex,
            project_id=job.project_id,
            expected_head_revision_id=job.input_revision_id,
            document=built["document"],
            charge_user_id=job.workspace_id,
            owned_asset_hashes=built["owned_asset_hashes"],
            job_lease=(job_id, worker_id, epoch),
            job_result=built["response"],
            now_ms=now_ms(),
        )
    except JobLeaseLostError:
        # Cancellation, deletion or another lease owns publication now.
        pass
    except Exception as error:
        if isinstance(error, AccountError):
            code, message = error.code, str(error)
        elif isinstance(error, LockConflictError):
            code, message = "LOCK_CONFLICT", str(error)
        elif isinstance(error, StorageConflictError):
            code, message = "REVISION_CONFLICT", str(error)
        elif isinstance(error, (ConversionRequestError, ValueError)):
            code, message = "INVALID_IMAGE_CONVERSION", str(error)
        else:
            code, message = "GENERATION_FAILED", "生成失败，请重试；本次不扣次数。"
            log.exception("Generation failed: %s", job_id)
        try:
            store.fail_job(
                job_id,
                worker_id,
                epoch,
                error={"code": code, "message": message, "retryable": False},
                now_ms=now_ms(),
            )
        except JobLeaseLostError:
            pass
    finally:
        shutil.rmtree(Path(data_dir) / "work" / job_id, ignore_errors=True)


def _run_once(
    data_dir: str | Path,
    catalog_path: str | Path,
    *,
    stop=None,
    timeout_seconds: float = 600,
    target=execute_job,
) -> bool:
    """Claim atomically; supervise a killable child with a fenced publication lease."""
    store = SQLiteStore(Path(data_dir) / "metadata.sqlite3")
    worker_id = "worker_" + uuid4().hex
    job = store.claim_next_job(
        worker_id, now_ms=now_ms(), job_type="image_conversion", max_running=1
    )
    if job is None:
        return False
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=target,
        args=(str(data_dir), str(catalog_path), job.job_id, worker_id, job.lease_epoch),
    )
    started, heartbeat = time.monotonic(), time.monotonic()
    try:
        child.start()
        while child.is_alive():
            child.join(timeout=0.1)
            current = store.get_job(job.job_id)
            stopping = stop is not None and stop.is_set()
            timed_out = time.monotonic() - started > timeout_seconds
            if current.state == "CANCEL_REQUESTED" or stopping or timed_out:
                child.terminate()
                child.join(timeout=3)
                if child.is_alive():
                    child.kill()
                    child.join()
                if current.state == "CANCEL_REQUESTED":
                    store.acknowledge_cancel(
                        job.job_id, worker_id, job.lease_epoch, now_ms=now_ms()
                    )
                elif stopping:
                    # Leave the lease for recovery; an existing conversion checkpoint is reusable.
                    store.heartbeat(
                        job.job_id, worker_id, job.lease_epoch, lease_seconds=1, now_ms=now_ms()
                    )
                elif current.state == "RUNNING":
                    store.fail_job(
                        job.job_id,
                        worker_id,
                        job.lease_epoch,
                        error={"code": "GENERATION_TIMEOUT", "message": "生成超时，本次不扣次数。"},
                        now_ms=now_ms(),
                    )
                return True
            if time.monotonic() - heartbeat >= 5 and current.state == "RUNNING":
                store.heartbeat(job.job_id, worker_id, job.lease_epoch, now_ms=now_ms())
                heartbeat = time.monotonic()
        current = store.get_job(job.job_id)
        if current.state == "RUNNING":
            store.fail_job(
                job.job_id,
                worker_id,
                job.lease_epoch,
                error={"code": "WORKER_EXITED", "message": "计算进程异常结束，本次不扣次数。"},
                now_ms=now_ms(),
            )
        elif current.state == "CANCEL_REQUESTED":
            store.acknowledge_cancel(job.job_id, worker_id, job.lease_epoch, now_ms=now_ms())
        return True
    except JobLeaseLostError:
        return True
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=3)
            if child.is_alive():
                child.kill()
                child.join()
        shutil.rmtree(Path(data_dir) / "work" / job.job_id, ignore_errors=True)


def run_once(
    data_dir: str | Path,
    catalog_path: str | Path,
    *,
    stop=None,
    timeout_seconds: float = 600,
    target=execute_job,
) -> bool:
    with computation_slot(Path(data_dir) / "work") as acquired:
        if not acquired:
            return False
        return _run_once(
            data_dir, catalog_path, stop=stop, timeout_seconds=timeout_seconds, target=target
        )


def run_worker(data_dir: str, catalog_path: str, stop):
    while not stop.is_set():
        try:
            if not run_once(data_dir, catalog_path, stop=stop):
                stop.wait(0.5)
        except Exception:
            log.exception("Worker supervisor failed")
            stop.wait(1)


def run():
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    data_dir = os.environ.get("STITCHFORGE_DATA_DIR", ".stitchforge-data")
    catalog = os.environ.get(
        "STITCHFORGE_DMC_CATALOG", "catalogs/dmc-117-2021-quiltysolid-2025-07.json"
    )
    if args.once:
        run_once(data_dir, catalog)
    else:
        run_worker(data_dir, catalog, multiprocessing.get_context("spawn").Event())


if __name__ == "__main__":
    run()
