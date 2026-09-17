"""Durable image uploads and account-scoped generation request recovery."""

import hashlib
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool
from stitchforge.fabric import MAX_GRID_SIDE
from stitchforge.imaging import MAX_IMAGE_FILE_BYTES
from stitchforge_storage.accounts import AccountError, now_ms

from .accounts import guard_resources
from .conversion import ConversionOptions, ConversionRequestError


async def read_image_upload(request: Request) -> bytes:
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_IMAGE_FILE_BYTES:
            raise AccountError("UPLOAD_TOO_LARGE", "图片文件超过 20 MiB 限制", 413)
        payload.extend(chunk)
    if not payload:
        raise ConversionRequestError("图片内容不能为空")
    return bytes(payload)


def generation_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(guard_resources)])

    @router.get("/generation-requests/{request_key}")
    def recover(request_key: str, request: Request):
        return asdict(
            request.app.state.store.get_generation_request(
                request.state.user["user_id"], request_key
            )
        )

    @router.post("/projects/{project_id}/image-jobs", status_code=202)
    async def submit(
        project_id: str,
        request: Request,
        expected_head_revision_id: Annotated[str, Query(min_length=1, max_length=128)],
        request_key: Annotated[str, Query(min_length=16, max_length=128)],
        grid_width: Annotated[int | None, Query(ge=1, le=MAX_GRID_SIDE)] = None,
        grid_height: Annotated[int | None, Query(ge=1, le=MAX_GRID_SIDE)] = None,
        color_budget: Annotated[int, Query(ge=1, le=64)] = 24,
        fabric_count: Annotated[int, Query(ge=6, le=40)] = 14,
        strands: Annotated[int, Query(ge=1, le=6)] = 2,
        remove_border_background: bool = True,
        optimize: bool = True,
        source_profile: Literal["artwork", "rendered_stitch"] = "artwork",
        palette_strategy: Literal["greedy", "clustered"] = "clustered",
    ):
        values = locals()
        try:
            options = ConversionOptions(
                **{key: values[key] for key in ConversionOptions.model_fields}
            )
        except ValueError as error:
            raise ConversionRequestError(str(error)) from error
        await run_in_threadpool(
            request.app.state.store.preflight_generation,
            project_id,
            request.state.user["user_id"],
            request_key,
            expected_head_revision_id,
        )
        payload = await read_image_upload(request)

        def enqueue():
            return request.app.state.store.enqueue_job(
                "job_" + uuid4().hex,
                project_id=project_id,
                job_type="image_conversion",
                input_revision_id=expected_head_revision_id,
                request_key=request_key,
                parameters={
                    "source_sha256": hashlib.sha256(payload).hexdigest(),
                    "options": options.model_dump(),
                },
                now_ms=now_ms(),
                prepare_input=lambda: request.app.state.artifact_store.put_bytes(
                    payload, media_type="application/octet-stream"
                ),
            )

        return asdict(await run_in_threadpool(enqueue))

    return router
