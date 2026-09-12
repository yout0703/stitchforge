from __future__ import annotations

import io
import json
import re
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from PIL import Image, ImageColor
from pydantic import BaseModel, ConfigDict, Field, field_validator
from stitchforge.catalog import load_catalog
from stitchforge.commands import (
    CellPatch,
    CommandValidationError,
    LockConflictError,
    apply_cell_updates,
    replay_patch,
)
from stitchforge.errors import DocumentValidationError
from stitchforge.fabric import MAX_GRID_CELLS, MAX_GRID_SIDE
from stitchforge.fidelity import measure_motif_resolution
from stitchforge.imaging import (
    MAX_IMAGE_FILE_BYTES,
    ImageInputError,
    normalize_image,
    sample_normalized_image,
)
from stitchforge.optimize import optimize_small_components
from stitchforge.oxs import MAX_OXS_BYTES, SUPPORTED_COORDINATE_PROFILE, import_oxs
from stitchforge.palette import (
    PaletteConstraintError,
    build_pattern_document,
    select_clustered_palette,
    select_palette,
)
from stitchforge.recovery import recover_rendered_stitch_colors
from stitchforge.segmentation import remove_connected_border_background
from stitchforge.stats import calculate_statistics
from stitchforge_storage import (
    ContentAddressedArtifactStore,
    SQLiteStore,
    StorageConflictError,
    StorageNotFoundError,
)

from .planning import plan_import

LOCAL_ORIGIN = re.compile(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?$")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectRequest(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    fabric_count: int = Field(default=14, ge=6, le=40)
    fabric_color: str = Field(default="#DDD5C5", pattern=r"^#[0-9A-F]{6}$")


class RenameProjectRequest(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    expected_head_revision_id: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("图纸名称不能为空")
        return value.strip()


class CreateRevisionRequest(StrictModel):
    expected_head_revision_id: str
    document: dict[str, Any]


class EnqueueJobRequest(StrictModel):
    job_type: str = Field(min_length=1, max_length=64)
    parameters: Any = Field(default_factory=dict)
    input_revision_id: str | None = None
    max_attempts: int = Field(default=2, ge=1, le=10)
    retryable: bool = True


class CellUpdateRequest(StrictModel):
    index: int = Field(ge=0)
    recipe_index: int = Field(ge=0)


class ApplyCommandRequest(StrictModel):
    command_id: str = Field(min_length=1, max_length=128)
    base_revision_id: str = Field(min_length=1, max_length=128)
    type: Literal["paint_cells"]
    updates: list[CellUpdateRequest] = Field(min_length=1, max_length=5_000)


class ReplayCommandRequest(StrictModel):
    command_id: str = Field(min_length=1, max_length=128)
    base_revision_id: str = Field(min_length=1, max_length=128)


class ConversionRequestError(ValueError):
    pass


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _empty_document(
    *, project_id: str, title: str, fabric_count: int, fabric_color: str
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "required_features": ["full_cross"],
        "metadata": {
            "pattern_id": project_id,
            "title": title,
            "author": "",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "purpose": "user_design",
        },
        "grid": {"width": 1, "height": 1, "origin": "top_left", "subgrid_scale": 2},
        "fabric": {
            "type": "aida",
            "count": fabric_count,
            "count_unit": "cells_per_inch",
            "worked_over": 1,
            "display_srgb": fabric_color,
            "margins_mm": {"top": 50, "right": 50, "bottom": 50, "left": 50},
        },
        "materials": [],
        "recipes": [],
        "full_stitches": {"encoding": "row-major-u16", "top_leg": "slash", "cells": [0]},
        "regions": [],
        "legend": [],
        "constraints": {"color_budget": 1, "metric_profile": "quality_4n_v1"},
        "provenance": {
            "source_asset_hashes": [],
            "engine_version": "stitchforge-0.1.0",
            "catalog_snapshot_id": "none",
            "rule_profile": "mvp_full_cross_v1",
            "generation_config_hash": None,
            "random_seed": None,
            "ai_proposal_hash": None,
            "sources": [],
        },
    }


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", _identifier("req"))


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "details": details,
            "retryable": retryable,
            "request_id": _request_id(request),
        },
    )


def _store(request: Request) -> SQLiteStore:
    return request.app.state.store


StoreDependency = Annotated[SQLiteStore, Depends(_store)]


def _artifact_store(request: Request) -> ContentAddressedArtifactStore:
    return request.app.state.artifact_store


ArtifactStoreDependency = Annotated[ContentAddressedArtifactStore, Depends(_artifact_store)]


def _router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/assets/{artifact_hash}")
    def get_asset(
        artifact_hash: str,
        artifact_store: ArtifactStoreDependency,
    ) -> Response:
        if re.fullmatch(r"[0-9a-f]{64}", artifact_hash) is None:
            raise StorageNotFoundError("素材不存在")
        try:
            payload = artifact_store.read_bytes(artifact_hash)
        except FileNotFoundError as error:
            raise StorageNotFoundError("素材不存在") from error
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif payload.startswith(b"\xff\xd8\xff"):
            media_type = "image/jpeg"
        else:
            media_type = "application/octet-stream"
        return Response(
            payload,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    def create_project(body: CreateProjectRequest, store: StoreDependency) -> dict[str, Any]:
        project_id = _identifier("project")
        revision_id = _identifier("revision")
        document = _empty_document(
            project_id=project_id,
            title=body.name,
            fabric_count=body.fabric_count,
            fabric_color=body.fabric_color,
        )
        project, revision = store.create_project_with_revision(
            project_id,
            workspace_id="local",
            name=body.name,
            revision_id=revision_id,
            document=document,
            now_ms=_now_ms(),
        )
        return {"project": asdict(project), "initial_revision": asdict(revision)}

    @router.post("/projects/import-oxs", status_code=status.HTTP_201_CREATED)
    def import_oxs_project(
        request: Request,
        store: StoreDependency,
        artifact_store: ArtifactStoreDependency,
        payload: Annotated[bytes, Body(media_type="application/octet-stream")],
    ) -> Any:
        if not payload or len(payload) > MAX_OXS_BYTES:
            return _error_response(
                request,
                status_code=422,
                code="INVALID_OXS",
                message="请选择非空且不超过 16 MiB 的 OXS 文件",
            )
        with tempfile.TemporaryDirectory(prefix="oxs-", dir=request.app.state.work_dir) as name:
            path = Path(name) / "source.oxs"
            path.write_bytes(payload)
            imported = import_oxs(path, coordinate_profile=SUPPORTED_COORDINATE_PROFILE)
        if imported.document is None or imported.report.blocked:
            issues = imported.report.as_dict()
            errors = [issue for issue in imported.report.issues if issue.severity == "error"]
            message = "；".join(issue.message[:400] for issue in errors[:3])
            return _error_response(
                request,
                status_code=422,
                code="INVALID_OXS",
                message=message or "OXS 导入失败",
                details=issues,
            )
        document = imported.document
        artifact = artifact_store.put_bytes(payload, media_type="application/xml")
        for source in document["provenance"].get("sources", []):
            source["locator"] = f"/api/v1/assets/{artifact.artifact_hash}"
        project_id = _identifier("project")
        document["metadata"]["pattern_id"] = project_id
        project, revision = store.create_project_with_revision(
            project_id,
            workspace_id="local",
            name=document["metadata"]["title"] or "OXS 图纸",
            revision_id=_identifier("revision"),
            document=document,
            now_ms=_now_ms(),
        )
        return {
            "project": asdict(project),
            "initial_revision": asdict(revision),
            "report": imported.report.as_dict(),
            "source_sha256": artifact.artifact_hash,
        }

    @router.get("/projects")
    def list_projects(
        store: StoreDependency,
        search: Annotated[str, Query(max_length=256)] = "",
        deleted: bool = False,
        sort: Literal["updated", "created", "name"] = "updated",
        limit: Annotated[int, Query(ge=1, le=100)] = 24,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return store.list_projects(
            workspace_id="local",
            search=search.strip(),
            deleted=deleted,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    @router.patch("/projects/{project_id}")
    def rename_project(
        project_id: str,
        body: RenameProjectRequest,
        store: StoreDependency,
    ) -> dict[str, Any]:
        base = store.get_revision(body.expected_head_revision_id)
        if base.project_id != project_id:
            raise StorageNotFoundError("版本不属于此项目")
        document = base.document
        document["metadata"]["title"] = body.name
        revision = store.add_revision(
            _identifier("revision"),
            project_id=project_id,
            expected_head_revision_id=body.expected_head_revision_id,
            document=document,
            now_ms=_now_ms(),
        )
        return {"revision_id": revision.revision_id}

    @router.post("/projects/{project_id}/restore")
    def restore_project(project_id: str, store: StoreDependency) -> dict[str, Any]:
        return asdict(store.restore_project(project_id, now_ms=_now_ms()))

    @router.get("/projects/{project_id}/thumbnail")
    def project_thumbnail(
        project_id: str,
        revision_id: str,
        store: StoreDependency,
    ) -> Response:
        revision = store.get_revision(revision_id)
        if revision.project_id != project_id:
            raise StorageNotFoundError("版本不属于此项目")
        document = revision.document
        background = ImageColor.getrgb(document["fabric"]["display_srgb"])
        materials = {item["id"]: item for item in document["materials"]}
        colors = {0: background}
        for recipe in document["recipes"]:
            material = materials[recipe["components"][0]["material_id"]]
            colors[recipe["index"]] = ImageColor.getrgb(
                material["color"]["display_srgb"] or "#888888"
            )
        size = (document["grid"]["width"], document["grid"]["height"])
        image = Image.new("RGB", size, background)
        image.putdata([colors[index] for index in document["full_stitches"]["cells"]])
        scale = 320 / max(size)
        image = image.resize(
            (max(1, round(size[0] * scale)), max(1, round(size[1] * scale))),
            resample=Image.Resampling.NEAREST,
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Response(
            buffer.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, store: StoreDependency) -> dict[str, Any]:
        return asdict(store.get_project(project_id))

    @router.delete("/projects/{project_id}", status_code=status.HTTP_202_ACCEPTED)
    def delete_project(project_id: str, store: StoreDependency) -> dict[str, Any]:
        return asdict(store.soft_delete_project(project_id, now_ms=_now_ms()))

    @router.get("/projects/{project_id}/revisions/{revision_id}")
    def get_revision(project_id: str, revision_id: str, store: StoreDependency) -> dict[str, Any]:
        revision = store.get_revision(revision_id)
        if revision.project_id != project_id:
            raise StorageNotFoundError(f"版本不属于项目: {revision_id}")
        return asdict(revision)

    @router.post("/projects/{project_id}/revisions", status_code=status.HTTP_201_CREATED)
    def create_revision(
        project_id: str, body: CreateRevisionRequest, store: StoreDependency
    ) -> dict[str, Any]:
        revision = store.add_revision(
            _identifier("revision"),
            project_id=project_id,
            expected_head_revision_id=body.expected_head_revision_id,
            document=body.document,
            now_ms=_now_ms(),
        )
        return asdict(revision)

    @router.post(
        "/projects/{project_id}/convert-image",
        status_code=status.HTTP_201_CREATED,
    )
    def convert_image(
        project_id: str,
        request: Request,
        store: StoreDependency,
        artifact_store: ArtifactStoreDependency,
        payload: Annotated[bytes, Body(media_type="application/octet-stream")],
        expected_head_revision_id: Annotated[str, Query(min_length=1, max_length=128)],
        grid_width: Annotated[int | None, Query(ge=1, le=MAX_GRID_SIDE)] = None,
        grid_height: Annotated[int | None, Query(ge=1, le=MAX_GRID_SIDE)] = None,
        color_budget: Annotated[int, Query(ge=1, le=64)] = 24,
        fabric_count: Annotated[int, Query(ge=6, le=40)] = 14,
        strands: Annotated[int, Query(ge=1, le=6)] = 2,
        remove_border_background: bool = True,
        optimize: bool = True,
        source_profile: Literal["artwork", "rendered_stitch"] = "artwork",
        palette_strategy: Literal["greedy", "clustered"] = "clustered",
    ) -> dict[str, Any]:
        if (grid_width is None) != (grid_height is None):
            raise ConversionRequestError("手动网格宽高需要同时提供")
        automatic = grid_width is None
        planning_report = None
        if (
            grid_width is not None
            and grid_height is not None
            and grid_width * grid_height > MAX_GRID_CELLS
        ):
            raise ConversionRequestError(f"网格总数不能超过 {MAX_GRID_CELLS:,}")
        if not payload:
            raise ConversionRequestError("图片内容不能为空")
        if len(payload) > MAX_IMAGE_FILE_BYTES:
            raise ConversionRequestError("图片文件超过 20 MiB 限制")

        project = store.get_project(project_id)
        if project.head_revision_id != expected_head_revision_id:
            raise StorageConflictError(f"REVISION_CONFLICT: current={project.head_revision_id!r}")
        base = store.get_revision(expected_head_revision_id)
        if base.project_id != project_id:
            raise StorageNotFoundError(f"版本不属于项目: {expected_head_revision_id}")

        work_root = request.app.state.work_dir
        try:
            with tempfile.TemporaryDirectory(prefix="conversion-", dir=work_root) as work_name:
                work = Path(work_name)
                source_path = work / "source-image"
                normalized_path = work / "normalized.png"
                source_path.write_bytes(payload)
                normalization = normalize_image(source_path, normalized_path)
                if automatic:
                    planning_report = plan_import(normalized_path, work)
                    plan = planning_report["plan"]
                    grid_width, grid_height = plan["grid_width"], plan["grid_height"]
                    color_budget, fabric_count = plan["color_budget"], plan["fabric_count"]
                    strands = plan["strands"]
                    source_profile = plan["source_profile"]
                    remove_border_background = plan["remove_border_background"]
                media_type = "image/jpeg" if normalization.source_format == "JPEG" else "image/png"
                source_artifact = artifact_store.put_bytes(payload, media_type=media_type)
                normalized_artifact = artifact_store.put_bytes(
                    normalized_path.read_bytes(), media_type="image/png"
                )
                sampled = sample_normalized_image(
                    normalized_path,
                    width=grid_width,
                    height=grid_height,
                )
                sampled = replace(
                    sampled,
                    source_sha256=normalized_artifact.artifact_hash,
                    source_locator=f"/api/v1/assets/{normalized_artifact.artifact_hash}",
                )
                background_report = None
                if remove_border_background:
                    sampled, background_report = remove_connected_border_background(sampled)
                recovery_report = None
                if source_profile == "rendered_stitch":
                    sampled, recovery_report = recover_rendered_stitch_colors(sampled)

                catalog = load_catalog(request.app.state.catalog_path)
                palette_selector = (
                    select_clustered_palette if palette_strategy == "clustered" else select_palette
                )
                palette_options: dict[str, Any] = {"color_budget": color_budget}
                if palette_strategy == "greedy":
                    palette_options["minimum_gain"] = 0.0
                palette = palette_selector(
                    sampled,
                    catalog,
                    **palette_options,
                )
                document = build_pattern_document(
                    sampled,
                    catalog,
                    palette,
                    pattern_id=project_id,
                    title=project.name,
                    author="",
                    fabric_count=fabric_count,
                    fabric_color=base.document["fabric"]["display_srgb"],
                    strands=strands,
                    color_budget=color_budget,
                    purpose="user_design",
                )
                if planning_report is not None:
                    plan_artifact = artifact_store.put_bytes(
                        json.dumps(planning_report, ensure_ascii=False, sort_keys=True).encode(),
                        media_type="application/json",
                    )
                    planning_report["artifact_sha256"] = plan_artifact.artifact_hash
                    document.setdefault("extensions", {})["stitchforge.import_plan"] = (
                        planning_report
                    )
                    if planning_report["provider"] == "codex":
                        document["provenance"]["ai_proposal_hash"] = plan_artifact.artifact_hash
                optimization_report = None
                if optimize:
                    optimization = optimize_small_components(document, sampled)
                    document = optimization.document
                    optimization_report = optimization.report.as_dict()
        except (ImageInputError, PaletteConstraintError, ValueError, OSError) as error:
            raise ConversionRequestError(str(error)) from error

        revision = store.add_revision(
            _identifier("revision"),
            project_id=project_id,
            expected_head_revision_id=expected_head_revision_id,
            document=document,
            now_ms=_now_ms(),
        )
        return {
            "revision": asdict(revision),
            "source_asset": {
                "sha256": source_artifact.artifact_hash,
                "url": f"/api/v1/assets/{source_artifact.artifact_hash}",
                "media_type": source_artifact.media_type,
            },
            "normalized_asset": {
                "sha256": normalized_artifact.artifact_hash,
                "url": f"/api/v1/assets/{normalized_artifact.artifact_hash}",
                "media_type": normalized_artifact.media_type,
            },
            "planning_report": planning_report,
            "normalization_report": normalization.as_dict(),
            "background_report": (
                background_report.as_dict() if background_report is not None else None
            ),
            "recovery_report": (recovery_report.as_dict() if recovery_report is not None else None),
            "palette_report": palette.as_dict(),
            "optimization_report": optimization_report,
            "statistics": calculate_statistics(document).as_dict(),
            "motif_resolution": measure_motif_resolution(document).as_dict(),
        }

    @router.post("/projects/{project_id}/jobs", status_code=status.HTTP_202_ACCEPTED)
    def enqueue_job(
        project_id: str, body: EnqueueJobRequest, store: StoreDependency
    ) -> dict[str, Any]:
        job = store.enqueue_job(
            _identifier("job"),
            project_id=project_id,
            job_type=body.job_type,
            parameters=body.parameters,
            input_revision_id=body.input_revision_id,
            max_attempts=body.max_attempts,
            retryable=body.retryable,
            now_ms=_now_ms(),
        )
        return asdict(job)

    @router.post("/projects/{project_id}/commands", status_code=status.HTTP_201_CREATED)
    def apply_command(
        project_id: str, body: ApplyCommandRequest, store: StoreDependency
    ) -> dict[str, Any]:
        base = store.get_revision(body.base_revision_id)
        if base.project_id != project_id:
            raise StorageNotFoundError(f"版本不属于项目: {body.base_revision_id}")
        request = {
            "type": body.type,
            "updates": [item.model_dump() for item in body.updates],
        }
        result = apply_cell_updates(
            base.document,
            [(item.index, item.recipe_index) for item in body.updates],
        )
        command, revision = store.commit_command(
            body.command_id,
            project_id=project_id,
            base_revision_id=body.base_revision_id,
            result_revision_id=_identifier("revision"),
            command_type=body.type,
            request=request,
            document=result.document,
            patch=[item.as_dict() for item in result.patch],
            inverse_patch=[item.as_dict() for item in result.inverse_patch],
            now_ms=_now_ms(),
        )
        return {
            "command": asdict(command),
            "revision": asdict(revision),
            "affected_cells": len(result.patch),
        }

    def replay_command(
        project_id: str,
        source_command_id: str,
        direction: Literal["undo", "redo"],
        body: ReplayCommandRequest,
        store: SQLiteStore,
    ) -> dict[str, Any]:
        source = store.get_command(source_command_id)
        if source.project_id != project_id:
            raise StorageNotFoundError(f"命令不属于项目: {source_command_id}")
        base = store.get_revision(body.base_revision_id)
        if base.project_id != project_id:
            raise StorageNotFoundError(f"版本不属于项目: {body.base_revision_id}")
        raw_patch = source.inverse_patch if direction == "undo" else source.patch
        result = replay_patch(base.document, [CellPatch(**item) for item in raw_patch])
        request = {"direction": direction, "source_command_id": source_command_id}
        command, revision = store.commit_command(
            body.command_id,
            project_id=project_id,
            base_revision_id=body.base_revision_id,
            result_revision_id=_identifier("revision"),
            command_type=direction,
            request=request,
            document=result.document,
            patch=[item.as_dict() for item in result.patch],
            inverse_patch=[item.as_dict() for item in result.inverse_patch],
            now_ms=_now_ms(),
        )
        return {
            "command": asdict(command),
            "revision": asdict(revision),
            "affected_cells": len(result.patch),
        }

    @router.post(
        "/projects/{project_id}/commands/{source_command_id}/undo",
        status_code=status.HTTP_201_CREATED,
    )
    def undo_command(
        project_id: str,
        source_command_id: str,
        body: ReplayCommandRequest,
        store: StoreDependency,
    ) -> dict[str, Any]:
        return replay_command(project_id, source_command_id, "undo", body, store)

    @router.post(
        "/projects/{project_id}/commands/{source_command_id}/redo",
        status_code=status.HTTP_201_CREATED,
    )
    def redo_command(
        project_id: str,
        source_command_id: str,
        body: ReplayCommandRequest,
        store: StoreDependency,
    ) -> dict[str, Any]:
        return replay_command(project_id, source_command_id, "redo", body, store)

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, store: StoreDependency) -> dict[str, Any]:
        return asdict(store.get_job(job_id))

    @router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel_job(job_id: str, store: StoreDependency) -> dict[str, Any]:
        return asdict(store.request_cancel(job_id, now_ms=_now_ms()))

    return router


def create_app(data_dir: str | Path, *, catalog_path: str | Path | None = None) -> FastAPI:
    data_path = Path(data_dir)
    resolved_catalog_path = (
        Path(catalog_path)
        if catalog_path
        else Path("catalogs/dmc-117-2021-quiltysolid-2025-07.json")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = SQLiteStore(data_path / "metadata.sqlite3")
        store.ensure_workspace("local", now_ms=_now_ms())
        app.state.store = store
        app.state.artifact_store = ContentAddressedArtifactStore(data_path / "artifacts")
        app.state.work_dir = data_path / "work"
        app.state.work_dir.mkdir(parents=True, exist_ok=True)
        app.state.catalog_path = resolved_catalog_path
        yield

    app = FastAPI(title="StitchForge Local API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def local_origin_and_request_id(request: Request, call_next):
        request.state.request_id = _identifier("req")
        origin = request.headers.get("origin")
        if (
            request.method in UNSAFE_METHODS
            and origin is not None
            and LOCAL_ORIGIN.fullmatch(origin) is None
        ):
            response = _error_response(
                request,
                status_code=403,
                code="ORIGIN_FORBIDDEN",
                message="本地工作台拒绝跨站写请求",
            )
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(StorageNotFoundError)
    async def storage_not_found(request: Request, error: StorageNotFoundError):
        return _error_response(request, status_code=404, code="NOT_FOUND", message=str(error))

    @app.exception_handler(StorageConflictError)
    async def storage_conflict(request: Request, error: StorageConflictError):
        text = str(error)
        code: Literal["REVISION_CONFLICT", "CONFLICT"] = (
            "REVISION_CONFLICT" if text.startswith("REVISION_CONFLICT") else "CONFLICT"
        )
        return _error_response(request, status_code=409, code=code, message=text)

    @app.exception_handler(DocumentValidationError)
    async def invalid_document(request: Request, error: DocumentValidationError):
        return _error_response(
            request,
            status_code=422,
            code="INVALID_DOCUMENT",
            message="PatternDocument 校验失败",
            details=[item.as_dict() for item in error.diagnostics],
        )

    @app.exception_handler(LockConflictError)
    async def lock_conflict(request: Request, error: LockConflictError):
        return _error_response(
            request,
            status_code=409,
            code="LOCK_CONFLICT",
            message=str(error),
            details={"indices": error.indices},
        )

    @app.exception_handler(CommandValidationError)
    async def invalid_command(request: Request, error: CommandValidationError):
        return _error_response(
            request,
            status_code=422,
            code="INVALID_COMMAND",
            message=str(error),
        )

    @app.exception_handler(ConversionRequestError)
    async def invalid_conversion(request: Request, error: ConversionRequestError):
        return _error_response(
            request,
            status_code=422,
            code="INVALID_IMAGE_CONVERSION",
            message=str(error),
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        return _error_response(
            request,
            status_code=422,
            code="INVALID_REQUEST",
            message="请求参数校验失败",
            details=jsonable_encoder(error.errors()),
        )

    app.include_router(_router())
    return app
