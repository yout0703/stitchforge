from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from stitchforge.canonical import document_hash
from stitchforge_storage import (
    ContentAddressedArtifactStore,
    JobLeaseLostError,
    SQLiteStore,
    StorageConflictError,
    StorageNotFoundError,
)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    result = SQLiteStore(tmp_path / "metadata.sqlite3")
    result.create_workspace("local", now_ms=0)
    result.create_project("project", workspace_id="local", name="测试项目", now_ms=0)
    return result


def test_schema_initialization_is_repeatable_and_rejects_duplicate_workspace(
    store: SQLiteStore,
) -> None:
    SQLiteStore(store.path)
    store.ensure_workspace("local", now_ms=1)
    with pytest.raises(StorageConflictError, match="已存在"):
        store.create_workspace("local", now_ms=1)
    with pytest.raises(StorageNotFoundError):
        store.get_project("missing")


def test_project_and_initial_revision_are_created_atomically(
    store: SQLiteStore, minimal_document: dict
) -> None:
    store.add_revision(
        "revision-collision",
        project_id="project",
        expected_head_revision_id=None,
        document=minimal_document,
        now_ms=1,
    )

    with pytest.raises(StorageConflictError):
        store.create_project_with_revision(
            "new-project",
            workspace_id="local",
            name="不能留下半成品",
            revision_id="revision-collision",
            document=minimal_document,
            now_ms=2,
        )
    with pytest.raises(StorageNotFoundError):
        store.get_project("new-project")


def test_revisions_use_optimistic_head_and_remain_readable(
    store: SQLiteStore, minimal_document: dict
) -> None:
    first = store.add_revision(
        "rev1",
        project_id="project",
        expected_head_revision_id=None,
        document=minimal_document,
        now_ms=1,
    )
    second_document = deepcopy(minimal_document)
    second_document["metadata"]["title"] = "第二版"
    second = store.add_revision(
        "rev2",
        project_id="project",
        expected_head_revision_id="rev1",
        document=second_document,
        now_ms=2,
    )

    assert first.document_hash == document_hash(minimal_document)
    assert second.parent_revision_id == "rev1"
    assert store.get_project("project").head_revision_id == "rev2"
    assert store.get_revision("rev1").document == minimal_document
    with pytest.raises(StorageConflictError, match="REVISION_CONFLICT"):
        store.add_revision(
            "stale",
            project_id="project",
            expected_head_revision_id="rev1",
            document=second_document,
            now_ms=3,
        )


def test_command_commit_is_atomic_idempotent_and_head_fenced(
    store: SQLiteStore, minimal_document: dict
) -> None:
    store.add_revision(
        "rev1",
        project_id="project",
        expected_head_revision_id=None,
        document=minimal_document,
        now_ms=1,
    )
    changed = deepcopy(minimal_document)
    changed["full_stitches"]["cells"][1] = 1
    request = {"type": "paint_cells", "updates": [{"index": 1, "recipe_index": 1}]}
    command, revision = store.commit_command(
        "command-1",
        project_id="project",
        base_revision_id="rev1",
        result_revision_id="rev2",
        command_type="paint_cells",
        request=request,
        document=changed,
        patch=[{"index": 1, "before": 0, "after": 1}],
        inverse_patch=[{"index": 1, "before": 1, "after": 0}],
        now_ms=2,
    )
    repeated_command, repeated_revision = store.commit_command(
        "command-1",
        project_id="project",
        base_revision_id="rev1",
        result_revision_id="ignored",
        command_type="paint_cells",
        request=request,
        document=changed,
        patch=[],
        inverse_patch=[],
        now_ms=3,
    )

    assert repeated_command == command
    assert repeated_revision == revision
    assert store.get_command("command-1") == command
    assert store.get_project("project").head_revision_id == "rev2"
    with pytest.raises(StorageConflictError, match="不同请求"):
        store.commit_command(
            "command-1",
            project_id="project",
            base_revision_id="rev1",
            result_revision_id="rev3",
            command_type="paint_cells",
            request={"type": "paint_cells", "updates": []},
            document=changed,
            patch=[],
            inverse_patch=[],
            now_ms=4,
        )
    with pytest.raises(StorageConflictError, match="REVISION_CONFLICT"):
        store.commit_command(
            "command-2",
            project_id="project",
            base_revision_id="rev1",
            result_revision_id="rev3",
            command_type="paint_cells",
            request=request,
            document=changed,
            patch=[],
            inverse_patch=[],
            now_ms=4,
        )


def test_job_claim_heartbeat_stage_and_completion_are_fenced(store: SQLiteStore) -> None:
    queued = store.enqueue_job(
        "job1",
        project_id="project",
        job_type="conversion",
        parameters={"grid": [100, 80]},
        now_ms=0,
    )
    claimed = store.claim_next_job("worker-a", lease_seconds=60, now_ms=10)

    assert claimed is not None
    assert queued.parameters_hash == claimed.parameters_hash
    assert (claimed.state, claimed.lease_epoch, claimed.attempt_count) == ("RUNNING", 1, 1)
    staged = store.update_stage(
        "job1", "worker-a", 1, stage="sampling", now_ms=20
    )
    assert staged.stage == "sampling"
    heartbeat = store.heartbeat("job1", "worker-a", 1, lease_seconds=60, now_ms=30)
    assert heartbeat.lease_expires_at_ms == 60_030
    completed = store.complete_job(
        "job1", "worker-a", 1, result={"candidate_ids": ["c1"]}, now_ms=40
    )
    assert completed.state == "SUCCEEDED"
    assert completed.result == {"candidate_ids": ["c1"]}
    assert store.claim_next_job("worker-b", now_ms=50) is None


def test_stage_checkpoint_survives_retry_and_is_idempotent(
    store: SQLiteStore, tmp_path: Path
) -> None:
    artifacts = ContentAddressedArtifactStore(tmp_path / "artifacts")
    output = artifacts.put_bytes(b"normalized image", media_type="image/png")
    store.enqueue_job(
        "job1",
        project_id="project",
        job_type="conversion",
        parameters={},
        max_attempts=2,
        now_ms=0,
    )
    first = store.claim_next_job("worker-a", lease_seconds=1, now_ms=0)
    assert first is not None
    checkpoint = store.complete_stage(
        "job1",
        "worker-a",
        first.lease_epoch,
        stage="input_normalized",
        stage_key="a" * 64,
        result={"width": 20, "height": 10},
        artifacts=[output],
        artifact_store=artifacts,
        now_ms=10,
    )
    repeated = store.complete_stage(
        "job1",
        "worker-a",
        first.lease_epoch,
        stage="input_normalized",
        stage_key="a" * 64,
        result={"height": 10, "width": 20},
        artifacts=[output],
        artifact_store=artifacts,
        now_ms=11,
    )
    assert repeated == checkpoint

    second = store.claim_next_job("worker-b", lease_seconds=60, now_ms=1_001)
    assert second is not None
    assert second.stage == "resuming"
    assert store.get_completed_stages("job1") == (checkpoint,)
    with pytest.raises(JobLeaseLostError):
        store.complete_stage(
            "job1",
            "worker-a",
            first.lease_epoch,
            stage="grid_sampled",
            stage_key="b" * 64,
            result={},
            now_ms=1_002,
        )
    with pytest.raises(StorageConflictError, match="内容不同"):
        store.complete_stage(
            "job1",
            "worker-b",
            second.lease_epoch,
            stage="input_normalized",
            stage_key="a" * 64,
            result={"width": 999},
            artifacts=[output],
            artifact_store=artifacts,
            now_ms=1_003,
        )


def test_unregistered_file_is_an_orphan_not_a_completed_stage(
    store: SQLiteStore, tmp_path: Path
) -> None:
    artifacts = ContentAddressedArtifactStore(tmp_path / "artifacts")
    orphan = artifacts.put_bytes(b"written before crash", media_type="application/json")
    store.enqueue_job(
        "job1", project_id="project", job_type="conversion", parameters={}, now_ms=0
    )

    assert artifacts.read_bytes(orphan.artifact_hash) == b"written before crash"
    assert store.get_completed_stages("job1") == ()


def test_stage_rejects_artifact_that_was_not_published(store: SQLiteStore) -> None:
    from stitchforge_storage import StoredArtifact

    store.enqueue_job(
        "job1", project_id="project", job_type="conversion", parameters={}, now_ms=0
    )
    claimed = store.claim_next_job("worker", now_ms=1)
    assert claimed is not None
    missing = StoredArtifact(
        artifact_hash="f" * 64,
        byte_length=1,
        media_type="application/octet-stream",
        storage_key="sha256/ff/" + "f" * 62,
    )
    with pytest.raises(ValueError, match="artifact_store"):
        store.complete_stage(
            "job1",
            "worker",
            claimed.lease_epoch,
            stage="sampled",
            stage_key="a" * 64,
            result={},
            artifacts=[missing],
            now_ms=2,
        )


def test_only_one_worker_can_claim_a_single_job(store: SQLiteStore) -> None:
    store.enqueue_job(
        "job1", project_id="project", job_type="conversion", parameters={}, now_ms=0
    )
    competing_stores = (SQLiteStore(store.path), SQLiteStore(store.path))

    def claim(index: int):
        return competing_stores[index].claim_next_job(f"worker-{index}", now_ms=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, range(2)))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].lease_epoch == 1


def test_expired_job_is_retried_and_old_worker_cannot_commit(store: SQLiteStore) -> None:
    store.enqueue_job(
        "job1",
        project_id="project",
        job_type="conversion",
        parameters={},
        max_attempts=2,
        now_ms=0,
    )
    first = store.claim_next_job("worker-a", lease_seconds=1, now_ms=0)
    second = store.claim_next_job("worker-b", lease_seconds=1, now_ms=1_001)

    assert first is not None and second is not None
    assert (second.lease_epoch, second.attempt_count) == (2, 2)
    with pytest.raises(JobLeaseLostError):
        store.complete_job("job1", "worker-a", 1, result={}, now_ms=1_002)
    assert store.recover_expired_jobs(now_ms=2_002) == 1
    failed = store.get_job("job1")
    assert failed.state == "FAILED"
    assert failed.error["code"] == "WORKER_LEASE_EXPIRED"


def test_nonretryable_expired_job_fails_without_second_claim(store: SQLiteStore) -> None:
    store.enqueue_job(
        "job1",
        project_id="project",
        job_type="external-model",
        parameters={},
        retryable=False,
        now_ms=0,
    )
    assert store.claim_next_job("worker-a", lease_seconds=1, now_ms=0) is not None
    assert store.claim_next_job("worker-b", now_ms=1_001) is None
    assert store.get_job("job1").state == "FAILED"


def test_queued_and_running_cancellation_reach_terminal_state(store: SQLiteStore) -> None:
    store.enqueue_job(
        "queued", project_id="project", job_type="conversion", parameters={}, now_ms=0
    )
    assert store.request_cancel("queued", now_ms=1).state == "CANCELLED"

    store.enqueue_job(
        "running", project_id="project", job_type="conversion", parameters={}, now_ms=2
    )
    claimed = store.claim_next_job("worker", lease_seconds=60, now_ms=3)
    assert claimed is not None
    requested = store.request_cancel("running", now_ms=4)
    assert requested.cancel_requested
    with pytest.raises(JobLeaseLostError):
        store.complete_job("running", "worker", claimed.lease_epoch, result={}, now_ms=5)
    cancelled = store.acknowledge_cancel(
        "running", "worker", claimed.lease_epoch, now_ms=6
    )
    assert cancelled.state == "CANCELLED"


def test_failure_records_unknown_external_outcome(store: SQLiteStore) -> None:
    store.enqueue_job(
        "job1", project_id="project", job_type="external-model", parameters={}, now_ms=0
    )
    claimed = store.claim_next_job("worker", now_ms=1)
    assert claimed is not None
    failed = store.fail_job(
        "job1",
        "worker",
        claimed.lease_epoch,
        error={"code": "MODEL_TIMEOUT"},
        external_outcome_unknown=True,
        now_ms=2,
    )
    assert failed.state == "FAILED"
    assert failed.external_outcome_unknown is True
    assert failed.error == {"code": "MODEL_TIMEOUT"}


def test_project_deletion_cancels_jobs_and_blocks_late_writes(
    store: SQLiteStore, minimal_document: dict
) -> None:
    store.enqueue_job(
        "running", project_id="project", job_type="conversion", parameters={}, now_ms=0
    )
    claimed = store.claim_next_job("worker", now_ms=1)
    assert claimed is not None
    store.enqueue_job(
        "queued", project_id="project", job_type="conversion", parameters={}, now_ms=2
    )

    deleted = store.soft_delete_project("project", now_ms=3)
    assert deleted.deleted_at_ms == 3
    assert store.get_job("queued").state == "CANCELLED"
    assert store.get_job("running").state == "CANCEL_REQUESTED"
    with pytest.raises(JobLeaseLostError):
        store.complete_job("running", "worker", claimed.lease_epoch, result={}, now_ms=4)
    with pytest.raises(StorageConflictError, match="已删除"):
        store.enqueue_job(
            "late", project_id="project", job_type="conversion", parameters={}, now_ms=5
        )
    with pytest.raises(StorageConflictError, match="已删除"):
        store.add_revision(
            "late-revision",
            project_id="project",
            expected_head_revision_id=None,
            document=minimal_document,
            now_ms=5,
        )
