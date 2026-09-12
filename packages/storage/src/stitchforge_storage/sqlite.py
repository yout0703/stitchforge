from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from stitchforge.canonical import canonical_document_bytes, document_hash
from stitchforge.validation import validate_document

from .artifacts import SHA256_PATTERN, ContentAddressedArtifactStore, StoredArtifact

JobState = Literal[
    "QUEUED",
    "RUNNING",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "SUCCEEDED",
    "FAILED",
]
TERMINAL_JOB_STATES = frozenset({"CANCELLED", "SUCCEEDED", "FAILED"})


class StorageConflictError(RuntimeError):
    pass


class StorageNotFoundError(LookupError):
    pass


class JobLeaseLostError(StorageConflictError):
    pass


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_value(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def _parameters_hash(parameters: Any) -> str:
    return hashlib.sha256(_json_text(parameters).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    workspace_id: str
    name: str
    head_revision_id: str | None
    deleted_at_ms: int | None
    created_at_ms: int
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    revision_id: str
    project_id: str
    parent_revision_id: str | None
    document_hash: str
    schema_version: str
    document: dict[str, Any]
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    workspace_id: str
    project_id: str
    job_type: str
    state: JobState
    stage: str
    parameters: Any
    parameters_hash: str
    input_revision_id: str | None
    attempt_count: int
    max_attempts: int
    retryable: bool
    lease_owner: str | None
    lease_epoch: int
    heartbeat_at_ms: int | None
    lease_expires_at_ms: int | None
    cancel_requested_at_ms: int | None
    result: Any
    error: Any
    external_outcome_unknown: bool
    created_at_ms: int
    updated_at_ms: int

    @property
    def cancel_requested(self) -> bool:
        return self.state == "CANCEL_REQUESTED"


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    workspace_id: str
    artifact_hash: str
    byte_length: int
    media_type: str
    storage_key: str
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class JobStageRecord:
    job_id: str
    stage: str
    stage_key: str
    result: Any
    artifacts: tuple[ArtifactRecord, ...]
    completed_at_ms: int


@dataclass(frozen=True, slots=True)
class CommandRecord:
    command_id: str
    project_id: str
    base_revision_id: str
    result_revision_id: str
    command_type: str
    request_hash: str
    patch: Any
    inverse_patch: Any
    created_at_ms: int


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise RuntimeError(f"不支持的 SQLite schema 版本: {version}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    created_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                    name TEXT NOT NULL,
                    head_revision_id TEXT,
                    deleted_at_ms INTEGER,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS revisions (
                    revision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    parent_revision_id TEXT REFERENCES revisions(revision_id),
                    document_hash TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    job_type TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'QUEUED', 'RUNNING', 'CANCEL_REQUESTED',
                        'CANCELLED', 'SUCCEEDED', 'FAILED'
                    )),
                    stage TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    parameters_hash TEXT NOT NULL,
                    input_revision_id TEXT REFERENCES revisions(revision_id),
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
                    retryable INTEGER NOT NULL CHECK(retryable IN (0, 1)),
                    lease_owner TEXT,
                    lease_epoch INTEGER NOT NULL DEFAULT 0 CHECK(lease_epoch >= 0),
                    heartbeat_at_ms INTEGER,
                    lease_expires_at_ms INTEGER,
                    cancel_requested_at_ms INTEGER,
                    result_json TEXT,
                    error_json TEXT,
                    external_outcome_unknown INTEGER NOT NULL DEFAULT 0
                        CHECK(external_outcome_unknown IN (0, 1)),
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS job_stages (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    stage TEXT NOT NULL,
                    stage_key TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    completed_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(job_id, stage)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    base_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
                    result_revision_id TEXT NOT NULL UNIQUE REFERENCES revisions(revision_id),
                    command_type TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    patch_json TEXT NOT NULL,
                    inverse_patch_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                    artifact_hash TEXT NOT NULL,
                    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
                    media_type TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(workspace_id, artifact_hash)
                );

                CREATE TABLE IF NOT EXISTS job_stage_artifacts (
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    workspace_id TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL,
                    PRIMARY KEY(job_id, stage, ordinal),
                    UNIQUE(job_id, stage, artifact_hash),
                    FOREIGN KEY(job_id, stage) REFERENCES job_stages(job_id, stage),
                    FOREIGN KEY(workspace_id, artifact_hash)
                        REFERENCES artifacts(workspace_id, artifact_hash)
                );

                CREATE INDEX IF NOT EXISTS projects_library
                    ON projects(workspace_id, deleted_at_ms, updated_at_ms);
                CREATE INDEX IF NOT EXISTS jobs_claimable
                    ON jobs(state, created_at_ms, job_id);
                CREATE INDEX IF NOT EXISTS jobs_lease_expiry
                    ON jobs(state, lease_expires_at_ms);
                CREATE INDEX IF NOT EXISTS jobs_project
                    ON jobs(project_id, state);
                CREATE INDEX IF NOT EXISTS artifacts_workspace
                    ON artifacts(workspace_id, created_at_ms);
                PRAGMA user_version = 1;
                """
            )

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=row["project_id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            head_revision_id=row["head_revision_id"],
            deleted_at_ms=row["deleted_at_ms"],
            created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"],
        )

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> RevisionRecord:
        return RevisionRecord(
            revision_id=row["revision_id"],
            project_id=row["project_id"],
            parent_revision_id=row["parent_revision_id"],
            document_hash=row["document_hash"],
            schema_version=row["schema_version"],
            document=_json_value(row["document_json"]),
            created_at_ms=row["created_at_ms"],
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            workspace_id=row["workspace_id"],
            project_id=row["project_id"],
            job_type=row["job_type"],
            state=row["state"],
            stage=row["stage"],
            parameters=_json_value(row["parameters_json"]),
            parameters_hash=row["parameters_hash"],
            input_revision_id=row["input_revision_id"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            retryable=bool(row["retryable"]),
            lease_owner=row["lease_owner"],
            lease_epoch=row["lease_epoch"],
            heartbeat_at_ms=row["heartbeat_at_ms"],
            lease_expires_at_ms=row["lease_expires_at_ms"],
            cancel_requested_at_ms=row["cancel_requested_at_ms"],
            result=_json_value(row["result_json"]),
            error=_json_value(row["error_json"]),
            external_outcome_unknown=bool(row["external_outcome_unknown"]),
            created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"],
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            workspace_id=row["workspace_id"],
            artifact_hash=row["artifact_hash"],
            byte_length=row["byte_length"],
            media_type=row["media_type"],
            storage_key=row["storage_key"],
            created_at_ms=row["created_at_ms"],
        )

    @staticmethod
    def _command_from_row(row: sqlite3.Row) -> CommandRecord:
        return CommandRecord(
            command_id=row["command_id"],
            project_id=row["project_id"],
            base_revision_id=row["base_revision_id"],
            result_revision_id=row["result_revision_id"],
            command_type=row["command_type"],
            request_hash=row["request_hash"],
            patch=_json_value(row["patch_json"]),
            inverse_patch=_json_value(row["inverse_patch_json"]),
            created_at_ms=row["created_at_ms"],
        )

    @classmethod
    def _stage_from_row(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> JobStageRecord:
        artifact_rows = connection.execute(
            """
            SELECT artifacts.*
            FROM job_stage_artifacts
            JOIN artifacts
              ON artifacts.workspace_id = job_stage_artifacts.workspace_id
             AND artifacts.artifact_hash = job_stage_artifacts.artifact_hash
            WHERE job_stage_artifacts.job_id = ? AND job_stage_artifacts.stage = ?
            ORDER BY job_stage_artifacts.ordinal
            """,
            (row["job_id"], row["stage"]),
        ).fetchall()
        return JobStageRecord(
            job_id=row["job_id"],
            stage=row["stage"],
            stage_key=row["stage_key"],
            result=_json_value(row["result_json"]),
            artifacts=tuple(cls._artifact_from_row(item) for item in artifact_rows),
            completed_at_ms=row["completed_at_ms"],
        )

    def create_workspace(self, workspace_id: str, *, now_ms: int) -> None:
        with self._transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO workspaces(workspace_id, created_at_ms) VALUES (?, ?)",
                    (workspace_id, now_ms),
                )
            except sqlite3.IntegrityError as error:
                raise StorageConflictError(f"工作区已存在: {workspace_id}") from error

    def ensure_workspace(self, workspace_id: str, *, now_ms: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO workspaces(workspace_id, created_at_ms)
                VALUES (?, ?)
                ON CONFLICT(workspace_id) DO NOTHING
                """,
                (workspace_id, now_ms),
            )

    def create_project_with_revision(
        self,
        project_id: str,
        *,
        workspace_id: str,
        name: str,
        revision_id: str,
        document: dict[str, Any],
        now_ms: int,
    ) -> tuple[ProjectRecord, RevisionRecord]:
        validate_document(document).raise_for_errors()
        payload = canonical_document_bytes(document).decode("utf-8")
        digest = document_hash(document)
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id, workspace_id, name, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, workspace_id, name, now_ms, now_ms),
                )
                connection.execute(
                    """
                    INSERT INTO revisions(
                        revision_id, project_id, parent_revision_id, document_hash,
                        schema_version, document_json, created_at_ms
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        project_id,
                        digest,
                        document["schema_version"],
                        payload,
                        now_ms,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StorageConflictError(f"无法创建项目: {project_id}") from error
            connection.execute(
                """
                UPDATE projects
                SET head_revision_id = ?, updated_at_ms = ?
                WHERE project_id = ?
                """,
                (revision_id, now_ms, project_id),
            )
            project_row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            revision_row = connection.execute(
                "SELECT * FROM revisions WHERE revision_id = ?", (revision_id,)
            ).fetchone()
            assert project_row is not None and revision_row is not None
            return (
                self._project_from_row(project_row),
                self._revision_from_row(revision_row),
            )

    def create_project(
        self,
        project_id: str,
        *,
        workspace_id: str,
        name: str,
        now_ms: int,
    ) -> ProjectRecord:
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO projects(
                        project_id, workspace_id, name, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, workspace_id, name, now_ms, now_ms),
                )
            except sqlite3.IntegrityError as error:
                raise StorageConflictError(f"无法创建项目: {project_id}") from error
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            assert row is not None
            return self._project_from_row(row)

    def list_projects(
        self,
        *,
        workspace_id: str,
        search: str = "",
        deleted: bool = False,
        sort: str = "updated",
        limit: int = 24,
        offset: int = 0,
    ) -> dict[str, Any]:
        ordering = {
            "updated": "p.updated_at_ms DESC, p.project_id ASC",
            "created": "p.created_at_ms DESC, p.project_id ASC",
            "name": "p.name COLLATE NOCASE ASC, p.project_id ASC",
        }[sort]
        where = (
            "p.workspace_id = ? AND (p.deleted_at_ms IS NOT NULL) = ? "
            "AND instr(lower(p.name), lower(?)) > 0"
        )
        parameters = (workspace_id, int(deleted), search)
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            total = connection.execute(
                f"SELECT COUNT(*) FROM projects p WHERE {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""SELECT p.*,
                    json_extract(r.document_json, '$.grid.width') AS width,
                    json_extract(r.document_json, '$.grid.height') AS height,
                    json_extract(r.document_json, '$.fabric.count') AS fabric_count,
                    json_array_length(r.document_json, '$.recipes') AS color_count,
                    CASE WHEN json_extract(r.document_json, '$.provenance.catalog_snapshot_id')
                        = 'oxs_embedded_palette' THEN 'oxs'
                        WHEN json_array_length(r.document_json, '$.provenance.source_asset_hashes')
                        > 0 THEN 'image' ELSE 'blank' END AS source_kind
                    FROM projects p LEFT JOIN revisions r ON r.revision_id = p.head_revision_id
                    WHERE {where} ORDER BY {ordering} LIMIT ? OFFSET ?""",
                (*parameters, limit, offset),
            ).fetchall()
            return {"items": [dict(row) for row in rows], "total": total}
        finally:
            connection.close()

    def restore_project(self, project_id: str, *, now_ms: int) -> ProjectRecord:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise StorageNotFoundError(f"项目不存在: {project_id}")
            if row["deleted_at_ms"] is not None:
                connection.execute(
                    "UPDATE projects SET deleted_at_ms = NULL, updated_at_ms = ? "
                    "WHERE project_id = ?",
                    (now_ms, project_id),
                )
            return self._project_from_row(
                connection.execute(
                    "SELECT * FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone()
            )

    def get_project(self, project_id: str) -> ProjectRecord:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise StorageNotFoundError(f"项目不存在: {project_id}")
        return self._project_from_row(row)

    def add_revision(
        self,
        revision_id: str,
        *,
        project_id: str,
        expected_head_revision_id: str | None,
        document: dict[str, Any],
        now_ms: int,
    ) -> RevisionRecord:
        validate_document(document).raise_for_errors()
        payload = canonical_document_bytes(document).decode("utf-8")
        digest = document_hash(document)
        with self._transaction() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise StorageNotFoundError(f"项目不存在: {project_id}")
            if project["deleted_at_ms"] is not None:
                raise StorageConflictError("已删除项目不能新增版本")
            if project["head_revision_id"] != expected_head_revision_id:
                raise StorageConflictError(
                    f"REVISION_CONFLICT: current={project['head_revision_id']!r}"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO revisions(
                        revision_id, project_id, parent_revision_id, document_hash,
                        schema_version, document_json, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        project_id,
                        expected_head_revision_id,
                        digest,
                        document["schema_version"],
                        payload,
                        now_ms,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StorageConflictError(f"无法创建版本: {revision_id}") from error
            connection.execute(
                """
                UPDATE projects
                SET head_revision_id = ?, updated_at_ms = ?, name = ?
                WHERE project_id = ?
                """,
                (revision_id, now_ms, document["metadata"]["title"], project_id),
            )
            row = connection.execute(
                "SELECT * FROM revisions WHERE revision_id = ?", (revision_id,)
            ).fetchone()
            assert row is not None
            return self._revision_from_row(row)

    def get_revision(self, revision_id: str) -> RevisionRecord:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM revisions WHERE revision_id = ?", (revision_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise StorageNotFoundError(f"版本不存在: {revision_id}")
        return self._revision_from_row(row)

    def get_command(self, command_id: str) -> CommandRecord:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise StorageNotFoundError(f"命令不存在: {command_id}")
        return self._command_from_row(row)

    def commit_command(
        self,
        command_id: str,
        *,
        project_id: str,
        base_revision_id: str,
        result_revision_id: str,
        command_type: str,
        request: Any,
        document: dict[str, Any],
        patch: Any,
        inverse_patch: Any,
        now_ms: int,
    ) -> tuple[CommandRecord, RevisionRecord]:
        validate_document(document).raise_for_errors()
        payload = canonical_document_bytes(document).decode("utf-8")
        digest = document_hash(document)
        request_hash = _parameters_hash(
            {
                "project_id": project_id,
                "base_revision_id": base_revision_id,
                "command_type": command_type,
                "request": request,
            }
        )
        patch_text = _json_text(patch)
        inverse_text = _json_text(inverse_patch)
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            if existing is not None:
                command = self._command_from_row(existing)
                if command.request_hash != request_hash:
                    raise StorageConflictError(f"命令 ID 已用于不同请求: {command_id}")
                revision_row = connection.execute(
                    "SELECT * FROM revisions WHERE revision_id = ?",
                    (command.result_revision_id,),
                ).fetchone()
                assert revision_row is not None
                return command, self._revision_from_row(revision_row)

            project = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise StorageNotFoundError(f"项目不存在: {project_id}")
            if project["deleted_at_ms"] is not None:
                raise StorageConflictError("已删除项目不能提交命令")
            if project["head_revision_id"] != base_revision_id:
                raise StorageConflictError(
                    f"REVISION_CONFLICT: current={project['head_revision_id']!r}"
                )
            base = connection.execute(
                "SELECT project_id FROM revisions WHERE revision_id = ?",
                (base_revision_id,),
            ).fetchone()
            if base is None or base["project_id"] != project_id:
                raise StorageConflictError("基础版本不属于该项目")
            try:
                connection.execute(
                    """
                    INSERT INTO revisions(
                        revision_id, project_id, parent_revision_id, document_hash,
                        schema_version, document_json, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_revision_id,
                        project_id,
                        base_revision_id,
                        digest,
                        document["schema_version"],
                        payload,
                        now_ms,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO commands(
                        command_id, project_id, base_revision_id, result_revision_id,
                        command_type, request_hash, patch_json, inverse_patch_json,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        command_id,
                        project_id,
                        base_revision_id,
                        result_revision_id,
                        command_type,
                        request_hash,
                        patch_text,
                        inverse_text,
                        now_ms,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StorageConflictError(f"无法提交命令: {command_id}") from error
            connection.execute(
                """
                UPDATE projects SET head_revision_id = ?, updated_at_ms = ?
                WHERE project_id = ?
                """,
                (result_revision_id, now_ms, project_id),
            )
            command_row = connection.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            revision_row = connection.execute(
                "SELECT * FROM revisions WHERE revision_id = ?", (result_revision_id,)
            ).fetchone()
            assert command_row is not None and revision_row is not None
            return (
                self._command_from_row(command_row),
                self._revision_from_row(revision_row),
            )

    def enqueue_job(
        self,
        job_id: str,
        *,
        project_id: str,
        job_type: str,
        parameters: Any,
        input_revision_id: str | None = None,
        max_attempts: int = 2,
        retryable: bool = True,
        now_ms: int,
    ) -> JobRecord:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        parameters_text = _json_text(parameters)
        with self._transaction() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise StorageNotFoundError(f"项目不存在: {project_id}")
            if project["deleted_at_ms"] is not None:
                raise StorageConflictError("已删除项目不能创建任务")
            if input_revision_id is not None:
                revision = connection.execute(
                    "SELECT project_id FROM revisions WHERE revision_id = ?",
                    (input_revision_id,),
                ).fetchone()
                if revision is None or revision["project_id"] != project_id:
                    raise StorageConflictError("输入版本不属于该项目")
            try:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, workspace_id, project_id, job_type, state, stage,
                        parameters_json, parameters_hash, input_revision_id,
                        max_attempts, retryable, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, 'QUEUED', 'queued', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        project["workspace_id"],
                        project_id,
                        job_type,
                        parameters_text,
                        _parameters_hash(parameters),
                        input_revision_id,
                        max_attempts,
                        int(retryable),
                        now_ms,
                        now_ms,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StorageConflictError(f"无法创建任务: {job_id}") from error
            return self._get_job(connection, job_id)

    def _get_job(self, connection: sqlite3.Connection, job_id: str) -> JobRecord:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise StorageNotFoundError(f"任务不存在: {job_id}")
        return self._job_from_row(row)

    def get_job(self, job_id: str) -> JobRecord:
        connection = self._connect()
        try:
            return self._get_job(connection, job_id)
        finally:
            connection.close()

    @staticmethod
    def _recover_expired(connection: sqlite3.Connection, *, now_ms: int) -> int:
        cancelled = connection.execute(
            """
            UPDATE jobs
            SET state = 'CANCELLED', stage = 'cancelled', lease_owner = NULL,
                heartbeat_at_ms = NULL, lease_expires_at_ms = NULL, updated_at_ms = ?
            WHERE state = 'CANCEL_REQUESTED'
              AND lease_expires_at_ms IS NOT NULL
              AND lease_expires_at_ms <= ?
            """,
            (now_ms, now_ms),
        ).rowcount
        deleted = connection.execute(
            """
            UPDATE jobs
            SET state = 'CANCELLED', stage = 'cancelled', lease_owner = NULL,
                heartbeat_at_ms = NULL, lease_expires_at_ms = NULL, updated_at_ms = ?
            WHERE state = 'RUNNING'
              AND lease_expires_at_ms <= ?
              AND EXISTS (
                  SELECT 1 FROM projects
                  WHERE projects.project_id = jobs.project_id
                    AND projects.deleted_at_ms IS NOT NULL
              )
            """,
            (now_ms, now_ms),
        ).rowcount
        requeued = connection.execute(
            """
            UPDATE jobs
            SET state = 'QUEUED', stage = 'queued', lease_owner = NULL,
                heartbeat_at_ms = NULL, lease_expires_at_ms = NULL, updated_at_ms = ?
            WHERE state = 'RUNNING'
              AND lease_expires_at_ms <= ?
              AND retryable = 1
              AND external_outcome_unknown = 0
              AND attempt_count < max_attempts
            """,
            (now_ms, now_ms),
        ).rowcount
        failure = _json_text(
            {
                "code": "WORKER_LEASE_EXPIRED",
                "message": "任务租约过期且不能自动重试",
                "retryable": False,
            }
        )
        failed = connection.execute(
            """
            UPDATE jobs
            SET state = 'FAILED', stage = 'failed', lease_owner = NULL,
                heartbeat_at_ms = NULL, lease_expires_at_ms = NULL,
                error_json = ?, updated_at_ms = ?
            WHERE state = 'RUNNING' AND lease_expires_at_ms <= ?
            """,
            (failure, now_ms, now_ms),
        ).rowcount
        return cancelled + deleted + requeued + failed

    def recover_expired_jobs(self, *, now_ms: int) -> int:
        with self._transaction() as connection:
            return self._recover_expired(connection, now_ms=now_ms)

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now_ms: int,
    ) -> JobRecord | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds 必须至少为 1")
        with self._transaction() as connection:
            self._recover_expired(connection, now_ms=now_ms)
            row = connection.execute(
                """
                SELECT jobs.job_id
                FROM jobs
                JOIN projects ON projects.project_id = jobs.project_id
                WHERE jobs.state = 'QUEUED' AND projects.deleted_at_ms IS NULL
                ORDER BY jobs.created_at_ms, jobs.job_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            job_id = row["job_id"]
            updated = connection.execute(
                """
                UPDATE jobs
                SET state = 'RUNNING',
                    stage = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM job_stages
                            WHERE job_stages.job_id = jobs.job_id
                        ) THEN 'resuming'
                        ELSE 'starting'
                    END,
                    lease_owner = ?,
                    lease_epoch = lease_epoch + 1, attempt_count = attempt_count + 1,
                    heartbeat_at_ms = ?, lease_expires_at_ms = ?, updated_at_ms = ?
                WHERE job_id = ? AND state = 'QUEUED'
                """,
                (worker_id, now_ms, now_ms + lease_seconds * 1000, now_ms, job_id),
            )
            if updated.rowcount != 1:
                raise StorageConflictError("任务领取竞争失败")
            return self._get_job(connection, job_id)

    @staticmethod
    def _guard_lease(
        connection: sqlite3.Connection,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        now_ms: int,
        *,
        states: tuple[str, ...],
    ) -> JobRecord:
        row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise StorageNotFoundError(f"任务不存在: {job_id}")
        job = SQLiteStore._job_from_row(row)
        if (
            job.state not in states
            or job.lease_owner != worker_id
            or job.lease_epoch != lease_epoch
            or job.lease_expires_at_ms is None
            or job.lease_expires_at_ms <= now_ms
        ):
            raise JobLeaseLostError(f"任务租约失效: {job_id}")
        return job

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        lease_seconds: int = 60,
        now_ms: int,
    ) -> JobRecord:
        if lease_seconds < 1:
            raise ValueError("lease_seconds 必须至少为 1")
        with self._transaction() as connection:
            self._guard_lease(
                connection,
                job_id,
                worker_id,
                lease_epoch,
                now_ms,
                states=("RUNNING", "CANCEL_REQUESTED"),
            )
            connection.execute(
                """
                UPDATE jobs
                SET heartbeat_at_ms = ?, lease_expires_at_ms = ?, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (now_ms, now_ms + lease_seconds * 1000, now_ms, job_id),
            )
            return self._get_job(connection, job_id)

    def update_stage(
        self,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        stage: str,
        now_ms: int,
    ) -> JobRecord:
        if not stage:
            raise ValueError("stage 不能为空")
        with self._transaction() as connection:
            self._guard_lease(
                connection,
                job_id,
                worker_id,
                lease_epoch,
                now_ms,
                states=("RUNNING",),
            )
            connection.execute(
                "UPDATE jobs SET stage = ?, updated_at_ms = ? WHERE job_id = ?",
                (stage, now_ms, job_id),
            )
            return self._get_job(connection, job_id)

    @staticmethod
    def _validate_artifact(artifact: StoredArtifact) -> None:
        if SHA256_PATTERN.fullmatch(artifact.artifact_hash) is None:
            raise ValueError("artifact_hash 必须是小写 SHA-256")
        if artifact.byte_length < 0:
            raise ValueError("artifact byte_length 不能为负数")
        if not artifact.media_type:
            raise ValueError("artifact media_type 不能为空")
        expected_key = ContentAddressedArtifactStore.storage_key(artifact.artifact_hash)
        if artifact.storage_key != expected_key:
            raise ValueError("artifact storage_key 与内容哈希不一致")

    def complete_stage(
        self,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        stage: str,
        stage_key: str,
        result: Any,
        artifacts: Sequence[StoredArtifact] = (),
        artifact_store: ContentAddressedArtifactStore | None = None,
        now_ms: int,
    ) -> JobStageRecord:
        if not stage:
            raise ValueError("stage 不能为空")
        if SHA256_PATTERN.fullmatch(stage_key) is None:
            raise ValueError("stage_key 必须是小写 SHA-256")
        artifact_list = tuple(artifacts)
        if len({item.artifact_hash for item in artifact_list}) != len(artifact_list):
            raise ValueError("同一阶段不能重复引用同一产物")
        for artifact in artifact_list:
            self._validate_artifact(artifact)
        if artifact_list and artifact_store is None:
            raise ValueError("登记阶段产物时必须提供 artifact_store 进行完整性校验")
        if artifact_store is not None:
            for artifact in artifact_list:
                artifact_store.verify(artifact)
        result_text = _json_text(result)

        with self._transaction() as connection:
            job = self._guard_lease(
                connection,
                job_id,
                worker_id,
                lease_epoch,
                now_ms,
                states=("RUNNING",),
            )
            existing = connection.execute(
                "SELECT * FROM job_stages WHERE job_id = ? AND stage = ?",
                (job_id, stage),
            ).fetchone()
            if existing is not None:
                record = self._stage_from_row(connection, existing)
                existing_artifacts = tuple(
                    (item.artifact_hash, item.byte_length, item.media_type, item.storage_key)
                    for item in record.artifacts
                )
                supplied_artifacts = tuple(
                    (
                        item.artifact_hash,
                        item.byte_length,
                        item.media_type,
                        item.storage_key,
                    )
                    for item in artifact_list
                )
                if (
                    record.stage_key != stage_key
                    or _json_text(record.result) != result_text
                    or existing_artifacts != supplied_artifacts
                ):
                    raise StorageConflictError(f"阶段检查点已存在且内容不同: {stage}")
                return record

            for artifact in artifact_list:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        workspace_id, artifact_hash, byte_length, media_type,
                        storage_key, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workspace_id, artifact_hash) DO NOTHING
                    """,
                    (
                        job.workspace_id,
                        artifact.artifact_hash,
                        artifact.byte_length,
                        artifact.media_type,
                        artifact.storage_key,
                        now_ms,
                    ),
                )
                stored = connection.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE workspace_id = ? AND artifact_hash = ?
                    """,
                    (job.workspace_id, artifact.artifact_hash),
                ).fetchone()
                assert stored is not None
                if (
                    stored["byte_length"] != artifact.byte_length
                    or stored["media_type"] != artifact.media_type
                    or stored["storage_key"] != artifact.storage_key
                ):
                    raise StorageConflictError(
                        f"相同哈希的产物元数据不一致: {artifact.artifact_hash}"
                    )

            connection.execute(
                """
                INSERT INTO job_stages(job_id, stage, stage_key, result_json, completed_at_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, stage, stage_key, result_text, now_ms),
            )
            for ordinal, artifact in enumerate(artifact_list):
                connection.execute(
                    """
                    INSERT INTO job_stage_artifacts(
                        job_id, stage, ordinal, workspace_id, artifact_hash
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        stage,
                        ordinal,
                        job.workspace_id,
                        artifact.artifact_hash,
                    ),
                )
            connection.execute(
                "UPDATE jobs SET stage = ?, updated_at_ms = ? WHERE job_id = ?",
                (stage, now_ms, job_id),
            )
            row = connection.execute(
                "SELECT * FROM job_stages WHERE job_id = ? AND stage = ?",
                (job_id, stage),
            ).fetchone()
            assert row is not None
            return self._stage_from_row(connection, row)

    def get_completed_stages(self, job_id: str) -> tuple[JobStageRecord, ...]:
        connection = self._connect()
        try:
            self._get_job(connection, job_id)
            rows = connection.execute(
                """
                SELECT * FROM job_stages
                WHERE job_id = ?
                ORDER BY completed_at_ms, stage
                """,
                (job_id,),
            ).fetchall()
            return tuple(self._stage_from_row(connection, row) for row in rows)
        finally:
            connection.close()

    def request_cancel(self, job_id: str, *, now_ms: int) -> JobRecord:
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            if job.state == "QUEUED":
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = 'CANCELLED', stage = 'cancelled',
                        cancel_requested_at_ms = ?, updated_at_ms = ?
                    WHERE job_id = ?
                    """,
                    (now_ms, now_ms, job_id),
                )
            elif job.state == "RUNNING":
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = 'CANCEL_REQUESTED', cancel_requested_at_ms = ?,
                        updated_at_ms = ?
                    WHERE job_id = ?
                    """,
                    (now_ms, now_ms, job_id),
                )
            return self._get_job(connection, job_id)

    def acknowledge_cancel(
        self,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        now_ms: int,
    ) -> JobRecord:
        with self._transaction() as connection:
            self._guard_lease(
                connection,
                job_id,
                worker_id,
                lease_epoch,
                now_ms,
                states=("CANCEL_REQUESTED",),
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = 'CANCELLED', stage = 'cancelled', lease_owner = NULL,
                    heartbeat_at_ms = NULL, lease_expires_at_ms = NULL, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (now_ms, job_id),
            )
            return self._get_job(connection, job_id)

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        result: Any,
        now_ms: int,
    ) -> JobRecord:
        result_text = _json_text(result)
        with self._transaction() as connection:
            job = self._guard_lease(
                connection,
                job_id,
                worker_id,
                lease_epoch,
                now_ms,
                states=("RUNNING",),
            )
            project = connection.execute(
                "SELECT deleted_at_ms FROM projects WHERE project_id = ?",
                (job.project_id,),
            ).fetchone()
            if project is None or project["deleted_at_ms"] is not None:
                raise JobLeaseLostError("已删除项目不能接收任务结果")
            connection.execute(
                """
                UPDATE jobs
                SET state = 'SUCCEEDED', stage = 'completed', result_json = ?,
                    lease_owner = NULL, heartbeat_at_ms = NULL,
                    lease_expires_at_ms = NULL, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (result_text, now_ms, job_id),
            )
            return self._get_job(connection, job_id)

    def fail_job(
        self,
        job_id: str,
        worker_id: str,
        lease_epoch: int,
        *,
        error: Any,
        external_outcome_unknown: bool = False,
        now_ms: int,
    ) -> JobRecord:
        error_text = _json_text(error)
        with self._transaction() as connection:
            self._guard_lease(
                connection,
                job_id,
                worker_id,
                lease_epoch,
                now_ms,
                states=("RUNNING",),
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = 'FAILED', stage = 'failed', error_json = ?,
                    external_outcome_unknown = ?, lease_owner = NULL,
                    heartbeat_at_ms = NULL, lease_expires_at_ms = NULL, updated_at_ms = ?
                WHERE job_id = ?
                """,
                (error_text, int(external_outcome_unknown), now_ms, job_id),
            )
            return self._get_job(connection, job_id)

    def soft_delete_project(self, project_id: str, *, now_ms: int) -> ProjectRecord:
        with self._transaction() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise StorageNotFoundError(f"项目不存在: {project_id}")
            if project["deleted_at_ms"] is None:
                connection.execute(
                    """
                    UPDATE projects
                    SET deleted_at_ms = ?, updated_at_ms = ?
                    WHERE project_id = ?
                    """,
                    (now_ms, now_ms, project_id),
                )
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = 'CANCELLED', stage = 'cancelled',
                        cancel_requested_at_ms = ?, updated_at_ms = ?
                    WHERE project_id = ? AND state = 'QUEUED'
                    """,
                    (now_ms, now_ms, project_id),
                )
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = 'CANCEL_REQUESTED', cancel_requested_at_ms = ?,
                        updated_at_ms = ?
                    WHERE project_id = ? AND state = 'RUNNING'
                    """,
                    (now_ms, now_ms, project_id),
                )
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            assert row is not None
            return self._project_from_row(row)
