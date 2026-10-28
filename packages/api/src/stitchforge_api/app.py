from __future__ import annotations

import io
import os
import re
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import BoundedSemaphore
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from PIL import Image, ImageColor
from pydantic import BaseModel, ConfigDict, Field, field_validator
from stitchforge.chart_image import render_download_chart
from stitchforge.commands import (
    CellPatch,
    CommandValidationError,
    LockConflictError,
    apply_cell_updates,
    replay_patch,
)
from stitchforge.errors import DocumentValidationError
from stitchforge.fabric import MAX_GRID_SIDE
from stitchforge.oxs import MAX_OXS_BYTES, SUPPORTED_COORDINATE_PROFILE, export_oxs, import_oxs
from stitchforge.xsd import MAX_XSD_BYTES, export_xsd, import_xsd
from stitchforge_storage import (
    ContentAddressedArtifactStore,
    SQLiteStore,
    StorageConflictError,
    StorageNotFoundError,
)
from stitchforge_storage.accounts import AccountError, AccountStore
from stitchforge_storage.payments import PaymentStore

from .accounts import account_router, guard_resources, load_packs
from .conversion import ConversionOptions, ConversionRequestError, build_conversion
from .generation import generation_router, read_image_upload
from .payments import load_providers
from .payments.routes import WEBHOOK_PATHS, payment_router
from .resources import computation_slot
from .thumbnails import ThumbnailCache

LOCAL_ORIGIN = re.compile(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)?$")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Bound raster memory per API process; other requests remain available.
CHART_EXPORT_SLOT = BoundedSemaphore(1)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectRequest(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    width: int = Field(default=64, ge=1, le=MAX_GRID_SIDE)
    height: int = Field(default=64, ge=1, le=MAX_GRID_SIDE)
    fabric_count: int = Field(default=14, ge=6, le=40)
    fabric_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-F]{6}$")


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


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _empty_document(
    *,
    project_id: str,
    title: str,
    fabric_count: int,
    fabric_color: str,
    width: int = 64,
    height: int = 64,
) -> dict[str, Any]:
    colors = [
        ("墨黑", "#292524"),
        ("暖白", "#FFFDF7"),
        ("砖红", "#B6533E"),
        ("金黄", "#D5A43D"),
        ("草绿", "#53795A"),
        ("湖蓝", "#527E9B"),
        ("淡紫", "#947AA3"),
        ("浅粉", "#D69A9B"),
    ]
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
        "grid": {"width": width, "height": height, "origin": "top_left", "subgrid_scale": 2},
        "fabric": {
            "type": "aida",
            "count": fabric_count,
            "count_unit": "cells_per_inch",
            "worked_over": 1,
            "display_srgb": fabric_color,
            "margins_mm": {"top": 50, "right": 50, "bottom": 50, "left": 50},
        },
        "materials": [
            {
                "id": f"m_basic_{index}",
                "brand": "自定义",
                "series": "基础配色",
                "code": f"B{index:02}",
                "name": name,
                "color": {"display_srgb": color, "status": "synthetic", "source_ref": "src_basic"},
            }
            for index, (name, color) in enumerate(colors, 1)
        ],
        "recipes": [
            {
                "id": f"r_basic_{index}",
                "index": index,
                "components": [{"material_id": f"m_basic_{index}", "strands": 2}],
            }
            for index in range(1, len(colors) + 1)
        ],
        "full_stitches": {
            "encoding": "row-major-u16",
            "top_leg": "slash",
            "cells": [0] * (width * height),
        },
        "regions": [],
        "legend": [
            {"recipe_id": f"r_basic_{index}", "symbol_id": f"s{index:03}"}
            for index in range(1, len(colors) + 1)
        ],
        "constraints": {"color_budget": len(colors), "metric_profile": "quality_4n_v1"},
        "provenance": {
            "source_asset_hashes": [],
            "engine_version": "stitchforge-0.1.0",
            "catalog_snapshot_id": "none",
            "rule_profile": "mvp_full_cross_v1",
            "generation_config_hash": None,
            "random_seed": None,
            "ai_proposal_hash": None,
            "sources": [
                {
                    "id": "src_basic",
                    "locator": "stitchforge:basic-palette",
                    "description": "自定义数字基础配色，不对应实体品牌绣线色号。",
                    "permission_status": "redistributable",
                }
            ],
        },
    }


async def read_oxs_upload(request: Request) -> bytes:
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_OXS_BYTES:
            raise AccountError("UPLOAD_TOO_LARGE", "OXS 文件超过 16 MiB 限制", 413)
        payload.extend(chunk)
    if not payload:
        raise AccountError("INVALID_OXS", "请选择非空的 OXS 文件", 422)
    return bytes(payload)


async def read_xsd_upload(request: Request) -> bytes:
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_XSD_BYTES:
            raise AccountError("UPLOAD_TOO_LARGE", "XSD 文件超过 16 MiB 限制", 413)
        payload.extend(chunk)
    if not payload:
        raise AccountError("INVALID_XSD", "请选择非空的 XSD 文件", 422)
    return bytes(payload)


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
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(guard_resources)])

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
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    def create_project(
        body: CreateProjectRequest, store: StoreDependency, request: Request
    ) -> dict[str, Any]:
        project_id = _identifier("project")
        revision_id = _identifier("revision")
        document = _empty_document(
            project_id=project_id,
            title=body.name,
            fabric_count=body.fabric_count,
            fabric_color=body.fabric_color,
            width=body.width,
            height=body.height,
        )
        project, revision = store.create_project_with_revision(
            project_id,
            workspace_id=request.state.user["user_id"],
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
        payload: Annotated[bytes, Depends(read_oxs_upload)],
    ) -> Any:
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
        request.app.state.accounts.grant_assets(
            request.state.user["user_id"], [artifact.artifact_hash]
        )
        for source in document["provenance"].get("sources", []):
            source["locator"] = f"/api/v1/assets/{artifact.artifact_hash}"
        project_id = _identifier("project")
        document["metadata"]["pattern_id"] = project_id
        project, revision = store.create_project_with_revision(
            project_id,
            workspace_id=request.state.user["user_id"],
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

    @router.post("/projects/import-xsd", status_code=status.HTTP_201_CREATED)
    def import_xsd_project(
        request: Request,
        store: StoreDependency,
        artifact_store: ArtifactStoreDependency,
        payload: Annotated[bytes, Depends(read_xsd_upload)],
        legacy_encoding: Literal["cp1251", "gb18030", "cp1252"] = "cp1251",
    ) -> Any:
        with tempfile.TemporaryDirectory(prefix="xsd-", dir=request.app.state.work_dir) as name:
            path = Path(name) / "source.xsd"
            path.write_bytes(payload)
            imported = import_xsd(path, legacy_encoding=legacy_encoding)
        if imported.document is None or imported.report.blocked:
            errors = [issue for issue in imported.report.issues if issue["severity"] == "error"]
            return _error_response(
                request,
                status_code=422,
                code="INVALID_XSD",
                message="；".join(issue["message"][:400] for issue in errors[:3]) or "XSD 导入失败",
                details=imported.report.as_dict(),
            )
        document = imported.document
        artifact = artifact_store.put_bytes(payload, media_type="application/octet-stream")
        request.app.state.accounts.grant_assets(
            request.state.user["user_id"], [artifact.artifact_hash]
        )
        for source in document["provenance"].get("sources", []):
            source["locator"] = f"/api/v1/assets/{artifact.artifact_hash}"
        project_id = _identifier("project")
        document["metadata"]["pattern_id"] = project_id
        project, revision = store.create_project_with_revision(
            project_id,
            workspace_id=request.state.user["user_id"],
            name=document["metadata"]["title"] or "XSD 图纸",
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
        request: Request,
        store: StoreDependency,
        search: Annotated[str, Query(max_length=256)] = "",
        deleted: bool = False,
        sort: Literal["updated", "created", "name"] = "updated",
        limit: Annotated[int, Query(ge=1, le=100)] = 24,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return store.list_projects(
            workspace_id=request.state.user["user_id"],
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
        request: Request,
        store: StoreDependency,
    ) -> Response:
        # Router ownership dependencies run before every cache lookup or 304 response.
        def render():
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
            return buffer.getvalue(), f'"{revision.document_hash}"'

        payload, etag = request.app.state.thumbnails.get((project_id, revision_id), render)
        headers = {"Cache-Control": "private, no-cache", "ETag": etag, "Vary": "Cookie"}
        candidates = request.headers.get("if-none-match", "").split(",")
        if any(candidate.strip().removeprefix("W/") in {etag, "*"} for candidate in candidates):
            return Response(status_code=304, headers=headers)
        return Response(payload, media_type="image/png", headers=headers)

    @router.get("/projects/{project_id}")
    def get_project(project_id: str, store: StoreDependency) -> dict[str, Any]:
        return asdict(store.get_project(project_id))

    @router.delete("/projects/{project_id}", status_code=status.HTTP_202_ACCEPTED)
    def delete_project(
        project_id: str,
        store: StoreDependency,
        expected_head_revision_id: Annotated[
            str | None, Query(min_length=1, max_length=128)
        ] = None,
    ) -> dict[str, Any]:
        return asdict(
            store.soft_delete_project(
                project_id,
                now_ms=_now_ms(),
                expected_head_revision_id=expected_head_revision_id,
            )
        )

    @router.get("/projects/{project_id}/revisions/{revision_id}")
    def get_revision(project_id: str, revision_id: str, store: StoreDependency) -> dict[str, Any]:
        revision = store.get_revision(revision_id)
        if revision.project_id != project_id:
            raise StorageNotFoundError(f"版本不属于项目: {revision_id}")
        return asdict(revision)

    @router.get("/projects/{project_id}/revisions/{revision_id}/download/{format}")
    def download_revision(
        project_id: str,
        revision_id: str,
        format: Literal["oxs", "xsd", "png"],
        request: Request,
        store: StoreDependency,
    ) -> Response:
        revision = store.get_revision(revision_id)
        if revision.project_id != project_id:
            raise StorageNotFoundError("版本不属于此图纸")
        if store.get_project(project_id).deleted_at_ms is not None:
            raise StorageNotFoundError("请先从回收站恢复图纸")
        request.app.state.accounts.throttle(f"export:{request.state.user['user_id']}", limit=60)
        if not CHART_EXPORT_SLOT.acquire(blocking=False):
            raise AccountError("EXPORT_BUSY", "正在处理其他下载，请稍后重试。", 503)
        try:
            with tempfile.TemporaryDirectory(prefix="stitchforge-export-") as directory:
                path = Path(directory) / f"chart.{format}"
                if format == "oxs":
                    report = export_oxs(
                        revision.document,
                        path,
                        coordinate_profile=SUPPORTED_COORDINATE_PROFILE,
                        confirm_loss=True,
                    )
                    if report.blocked:
                        raise AccountError(
                            "EXPORT_FAILED", "此图纸无法导出 OXS，请检查线色数据。", 422
                        )
                elif format == "xsd":
                    report = export_xsd(revision.document, path, confirm_loss=True)
                    if report.blocked:
                        raise AccountError(
                            "EXPORT_FAILED",
                            "；".join(
                                issue["message"]
                                for issue in report.issues
                                if issue["severity"] == "error"
                            )[:1200],
                            422,
                        )
                else:
                    render_download_chart(revision.document, path)
                payload = path.read_bytes()
        except ValueError as error:
            raise AccountError("EXPORT_FAILED", str(error), 422) from error
        finally:
            CHART_EXPORT_SLOT.release()
        title = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "_", revision.document["metadata"]["title"])
        filename = f"{title[:120].strip() or 'chart'}.{format}"
        return Response(
            payload,
            media_type={
                "png": "image/png",
                "oxs": "application/xml",
                "xsd": "application/octet-stream",
            }[format],
            headers={
                "Content-Disposition": f"attachment; filename=chart.{format}; "
                f"filename*=UTF-8''{quote(filename, safe='')}"
            },
        )

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
    async def convert_image(
        project_id: str,
        request: Request,
        store: StoreDependency,
        artifact_store: ArtifactStoreDependency,
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
        from starlette.concurrency import run_in_threadpool

        values = locals()
        payload = await read_image_upload(request)

        def execute():
            project = store.get_project(project_id)
            if project.head_revision_id != expected_head_revision_id:
                raise StorageConflictError(
                    f"REVISION_CONFLICT: current={project.head_revision_id!r}"
                )
            user_id = request.state.user["user_id"]
            if request.app.state.accounts.user(user_id)["credits"] < 1:
                raise AccountError(
                    "INSUFFICIENT_CREDITS", "生成次数不足，请先充值；OXS 导入免费。", 402
                )
            try:
                options = ConversionOptions(
                    **{key: values[key] for key in ConversionOptions.model_fields}
                )
            except ValueError as error:
                raise ConversionRequestError(str(error)) from error
            with computation_slot(request.app.state.work_dir) as acquired:
                if not acquired:
                    raise AccountError("GENERATION_BUSY", "正在处理其他生成任务，请稍后重试。", 429)
                base = store.get_revision(expected_head_revision_id)
                built = build_conversion(
                    project_id=project_id,
                    title=project.name,
                    fabric_color=base.document["fabric"]["display_srgb"],
                    payload=payload,
                    artifact_store=artifact_store,
                    work_root=request.app.state.work_dir,
                    catalog_path=request.app.state.catalog_path,
                    options=options,
                )
                revision = store.add_revision(
                    _identifier("revision"),
                    project_id=project_id,
                    expected_head_revision_id=expected_head_revision_id,
                    document=built["document"],
                    now_ms=_now_ms(),
                    charge_user_id=user_id,
                    owned_asset_hashes=built["owned_asset_hashes"],
                )
                return {"revision": asdict(revision), **built["response"]}

        return await run_in_threadpool(execute)

    @router.post("/projects/{project_id}/jobs", status_code=status.HTTP_202_ACCEPTED)
    def enqueue_job(
        project_id: str, body: EnqueueJobRequest, store: StoreDependency
    ) -> dict[str, Any]:
        if body.job_type == "image_conversion":
            raise ConversionRequestError("图片任务请使用图片上传入口")
        raise AccountError("UNSUPPORTED_JOB_TYPE", "不支持此任务类型，请使用图片上传入口。", 422)

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


def create_app(
    data_dir: str | Path, *, catalog_path: str | Path | None = None, start_worker: bool = False
) -> FastAPI:
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
        app.state.accounts = AccountStore(store)
        app.state.credit_packs = load_packs()
        app.state.public_origin = os.environ.get("STITCHFORGE_PUBLIC_ORIGIN", "").rstrip("/")
        app.state.secure_cookies = app.state.public_origin.startswith("https://")
        if app.state.public_origin and not app.state.secure_cookies:
            raise ValueError("STITCHFORGE_PUBLIC_ORIGIN 必须使用 HTTPS")
        app.state.payments = PaymentStore(app.state.accounts)
        app.state.payment_providers = load_providers(app.state.public_origin)
        app.state.artifact_store = ContentAddressedArtifactStore(data_path / "artifacts")
        app.state.work_dir = data_path / "work"
        app.state.work_dir.mkdir(parents=True, exist_ok=True)
        app.state.catalog_path = resolved_catalog_path
        app.state.thumbnails = ThumbnailCache()
        process = None
        if start_worker:
            import multiprocessing

            from .worker import run_worker

            context = multiprocessing.get_context("spawn")
            stop = context.Event()
            process = context.Process(
                target=run_worker,
                args=(str(data_path.resolve()), str(resolved_catalog_path.resolve()), stop),
            )
            process.start()
            app.state.worker_process = process
        try:
            yield
        finally:
            if process is not None:
                from starlette.concurrency import run_in_threadpool

                stop.set()
                await run_in_threadpool(process.join, 5)
                if process.is_alive():
                    process.terminate()
                    await run_in_threadpool(process.join, 3)
                if process.is_alive():
                    process.kill()
                    await run_in_threadpool(process.join)

    app = FastAPI(title="StitchForge API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def local_origin_and_request_id(request: Request, call_next):
        request.state.request_id = _identifier("req")
        origin = request.headers.get("origin")
        is_webhook = request.method == "POST" and request.url.path in WEBHOOK_PATHS
        if (
            not is_webhook
            and request.method in UNSAFE_METHODS
            and (
                request.headers.get("sec-fetch-site") == "cross-site"
                or (
                    origin is not None
                    and (
                        origin != request.app.state.public_origin
                        if request.app.state.public_origin
                        else LOCAL_ORIGIN.fullmatch(origin) is None
                    )
                )
            )
        ):
            response = _error_response(
                request,
                status_code=403,
                code="ORIGIN_FORBIDDEN",
                message="拒绝跨站写请求",
            )
        elif (
            not is_webhook
            and request.method in UNSAFE_METHODS
            and request.headers.get("x-stitchforge-request") != "1"
        ):
            response = _error_response(
                request, status_code=403, code="CSRF_REQUIRED", message="请求缺少安全校验头"
            )
        else:
            response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(AccountError)
    async def account_error(request: Request, error: AccountError):
        return _error_response(
            request, status_code=error.status, code=error.code, message=str(error)
        )

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
            details=[
                {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
                for item in error.errors()
            ],
        )

    app.include_router(account_router())
    app.include_router(generation_router())
    app.include_router(payment_router())
    app.include_router(_router())
    return app
