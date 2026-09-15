"""Image conversion application service, shared by HTTP compatibility and workers."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from stitchforge import rare_colors as rare_color_engine
from stitchforge.catalog import load_catalog
from stitchforge.counted_stitches import check_stitch_structure, counted_stitch_candidates
from stitchforge.fabric import MAX_GRID_CELLS, MAX_GRID_SIDE
from stitchforge.fidelity import measure_motif_resolution
from stitchforge.imaging import (
    MAX_IMAGE_FILE_BYTES,
    ImageInputError,
    crop_normalized_image,
    normalize_image,
    sample_normalized_image,
)
from stitchforge.optimize import optimize_small_components
from stitchforge.palette import (
    PaletteConstraintError,
    build_pattern_document,
    select_clustered_palette,
    select_palette,
)
from stitchforge.rare_colors import merge_rare_colors
from stitchforge.recovery import recover_rendered_stitch_colors
from stitchforge.segmentation import (
    BackgroundRemovalReport,
    remove_connected_border_background,
    sample_fabric_foreground,
    trim_blank_margins,
)
from stitchforge.stats import calculate_statistics
from stitchforge_storage import ContentAddressedArtifactStore

from .planning import ImportPlan, plan_import, validate_stitch_plan
from .structure_review import review_counted_candidates


class ConversionRequestError(ValueError):
    pass


class ConversionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grid_width: int | None = Field(default=None, ge=1, le=MAX_GRID_SIDE)
    grid_height: int | None = Field(default=None, ge=1, le=MAX_GRID_SIDE)
    color_budget: int = Field(default=24, ge=1, le=64)
    fabric_count: int = Field(default=14, ge=6, le=40)
    strands: int = Field(default=2, ge=1, le=6)
    remove_border_background: bool = True
    optimize: bool = True
    source_profile: Literal["artwork", "rendered_stitch"] = "artwork"
    palette_strategy: Literal["greedy", "clustered"] = "clustered"

    @model_validator(mode="after")
    def geometry(self):
        if (self.grid_width is None) != (self.grid_height is None):
            raise ValueError("手动网格宽高需要同时提供")
        return self


def build_conversion(
    *,
    project_id: str,
    title: str,
    fabric_color: str,
    payload: bytes,
    artifact_store: ContentAddressedArtifactStore,
    work_root: Path,
    catalog_path: Path,
    options: ConversionOptions,
    progress: Callable[[str], None] = lambda _: None,
) -> dict:
    started = time.perf_counter()
    previous = started
    previous_stage = None
    timings = {}

    def phase(name):
        nonlocal previous, previous_stage
        current = time.perf_counter()
        if previous_stage is not None:
            timings[previous_stage] = round((current - previous) * 1000, 3)
        previous, previous_stage = current, name
        progress(name)

    grid_width, grid_height = options.grid_width, options.grid_height
    color_budget, fabric_count, strands = (
        options.color_budget,
        options.fabric_count,
        options.strands,
    )
    remove_border_background, optimize = options.remove_border_background, options.optimize
    source_profile, palette_strategy = options.source_profile, options.palette_strategy
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

    phase("normalization")
    try:
        with tempfile.TemporaryDirectory(prefix="conversion-", dir=work_root) as work_name:
            work = Path(work_name)
            source_path = work / "source-image"
            normalized_path = work / "normalized.png"
            source_path.write_bytes(payload)
            normalization = normalize_image(source_path, normalized_path)
            phase("planning")
            counted = None
            structure_review = None
            if automatic:
                candidates, evidence = counted_stitch_candidates(normalized_path)
                if candidates:
                    structure_review = review_counted_candidates(
                        normalized_path,
                        candidates,
                        catalog_path,
                    )
                    if structure_review["status"] in {"rejected", "invalid"}:
                        raise ValueError(
                            "图纸未通过原图结构复核，已停止生成，本次不扣次数。"
                            "请使用更清晰、正对拍摄的图片。"
                        )
                    if structure_review["status"] == "passed":
                        counted = candidates[structure_review["selected_index"]]
                    elif candidates[0].report["requires_visual_review"]:
                        raise ValueError(
                            "当前针距需要 AI 结构复核，但复核服务暂不可用。"
                            "本次不扣次数，请稍后重试。"
                        )
                    else:
                        counted = candidates[0]
                if counted is not None:
                    detected = counted.report
                    planning_report = {
                        "provider": "local",
                        "method": "counted_stitches",
                        "fallback_reason": None,
                        "structure_review": structure_review,
                        "subject_evidence": evidence,
                        "plan": ImportPlan(
                            grid_width=counted.sample.width,
                            grid_height=counted.sample.height,
                            color_budget=detected["thread_colours"],
                            fabric_count=16,
                            strands=2,
                            source_profile="rendered_stitch",
                            remove_border_background=True,
                            explanation=(
                                f"按照片中重复的针距还原，主体约 {detected['subject_columns']}×"
                                f"{detected['subject_rows']} 格，"
                                f"{detected['stitch_count']} 针、"
                                f"{detected['thread_colours']} 色；每个完整十字对应一格，"
                                "合并同一线色的明暗，四周留 2 格空白。"
                                "针格为图像估计，16 CT、2 股为制作建议。"
                            ),
                        ).model_dump(),
                    }
                else:
                    planning_report = plan_import(normalized_path, work)
                    validate_stitch_plan(planning_report["plan"], evidence)
                plan = planning_report["plan"]
                grid_width, grid_height = plan["grid_width"], plan["grid_height"]
                color_budget, fabric_count = plan["color_budget"], plan["fabric_count"]
                strands = plan["strands"]
                source_profile = plan["source_profile"]
                remove_border_background = plan["remove_border_background"]
            phase("sampling")
            media_type = "image/jpeg" if normalization.source_format == "JPEG" else "image/png"
            source_artifact = artifact_store.put_bytes(payload, media_type=media_type)
            normalized_artifact = artifact_store.put_bytes(
                normalized_path.read_bytes(), media_type="image/png"
            )
            sampled = (
                counted.sample
                if counted
                else sample_normalized_image(
                    normalized_path,
                    width=grid_width,
                    height=grid_height,
                )
            )
            background_report = None
            if counted is not None:
                total = sampled.width * sampled.height
                stitches = counted.report["stitch_count"]
                background_report = BackgroundRemovalReport(
                    "counted-stitch-blank-cells-v1",
                    "#FFFFFF",
                    0,
                    0,
                    total,
                    total - stitches,
                    stitches,
                )
            elif remove_border_background and source_profile == "rendered_stitch":
                sampled, background_report = sample_fabric_foreground(
                    normalized_path,
                    width=grid_width,
                    height=grid_height,
                )
            elif remove_border_background:
                cleaned, background_report = remove_connected_border_background(
                    sampled,
                )
                # A uniform image offers no evidence of a separate subject.
                # Keep it usable rather than deleting every stitch.
                if background_report.retained_cells:
                    sampled = cleaned
                else:
                    background_report = replace(
                        background_report,
                        removed_cells=0,
                        retained_cells=background_report.original_nonempty_cells,
                    )
            sampled = replace(
                sampled,
                source_sha256=normalized_artifact.artifact_hash,
                source_locator=f"/api/v1/assets/{normalized_artifact.artifact_hash}",
            )
            crop_report = None
            aligned_artifact = normalized_artifact
            if counted is not None:
                cropped_path = work / "counted-source.png"
                crop_report = crop_normalized_image(
                    normalized_path,
                    cropped_path,
                    box=counted.crop_box,
                ).as_dict()
                aligned_artifact = artifact_store.put_bytes(
                    cropped_path.read_bytes(),
                    media_type="image/png",
                )
                sampled = replace(
                    sampled,
                    source_sha256=aligned_artifact.artifact_hash,
                    source_locator=f"/api/v1/assets/{aligned_artifact.artifact_hash}",
                )
            elif automatic and remove_border_background:
                original_grid = (sampled.width, sampled.height)
                sampled, grid_box = trim_blank_margins(sampled)
                if grid_box is not None:
                    # Keep comparison mode aligned with the trimmed chart;
                    # retain both the full source and the cropped source as assets.
                    pixel_box = tuple(
                        round(value * size / grid_size)
                        for value, size, grid_size in zip(
                            grid_box,
                            (
                                normalization.normalized_width,
                                normalization.normalized_height,
                                normalization.normalized_width,
                                normalization.normalized_height,
                            ),
                            original_grid * 2,
                            strict=True,
                        )
                    )
                    cropped_path = work / "cropped.png"
                    crop_report = crop_normalized_image(
                        normalized_path,
                        cropped_path,
                        box=pixel_box,
                    ).as_dict()
                    crop_report["original_grid"] = list(original_grid)
                    crop_report["grid_box"] = list(grid_box)
                    aligned_artifact = artifact_store.put_bytes(
                        cropped_path.read_bytes(),
                        media_type="image/png",
                    )
                    sampled = replace(
                        sampled,
                        source_sha256=aligned_artifact.artifact_hash,
                        source_locator=f"/api/v1/assets/{aligned_artifact.artifact_hash}",
                    )
            recovery_report = None
            if source_profile == "rendered_stitch":
                sampled, recovery_report = recover_rendered_stitch_colors(sampled)

            phase("palette")
            catalog = load_catalog(catalog_path)
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
                title=title,
                author="",
                fabric_count=fabric_count,
                fabric_color=fabric_color,
                strands=strands,
                color_budget=color_budget,
                purpose="user_design",
            )
            phase("quality")
            if planning_report is not None:
                plan_artifact = artifact_store.put_bytes(
                    json.dumps(planning_report, ensure_ascii=False, sort_keys=True).encode(),
                    media_type="application/json",
                )
                planning_report["artifact_sha256"] = plan_artifact.artifact_hash
                document.setdefault("extensions", {})["stitchforge.import_plan"] = planning_report
                if planning_report["provider"] in {"codex", "deepseek"} or (
                    structure_review and structure_review["status"] == "passed"
                ):
                    document["provenance"]["ai_proposal_hash"] = plan_artifact.artifact_hash
            if crop_report is not None:
                document.setdefault("extensions", {})["stitchforge.background_crop"] = crop_report
            if counted is not None:
                materials = {m["id"]: m["color"]["display_srgb"] for m in document["materials"]}
                recipe_colours = {
                    r["index"]: materials[r["components"][0]["material_id"]]
                    for r in document["recipes"]
                }
                final_sample = replace(
                    sampled,
                    display_srgb=tuple(
                        recipe_colours.get(index) for index in document["full_stitches"]["cells"]
                    ),
                )
                final_check = check_stitch_structure(
                    normalized_path,
                    replace(counted, sample=final_sample),
                )
                if not final_check["passed"]:
                    raise ValueError("最终图纸未通过轮廓复核，本次不扣次数。")
                used_colours = set(document["full_stitches"]["cells"]) - {0}
                if len(used_colours) < counted.report["thread_colours"]:
                    raise ValueError("最终线色合并丢失了独立线色，本次不扣次数。")
                counted.report["final_structure_check"] = final_check
                document.setdefault("extensions", {})["stitchforge.counted_stitches"] = (
                    counted.report
                )
            optimization_report = None
            rare_color_report = None
            if optimize and counted is None:
                optimization = optimize_small_components(document, sampled)
                document = optimization.document
                optimization_report = optimization.report.as_dict()
                reduction = merge_rare_colors(document, sampled)
                document, rare_color_report = reduction.document, reduction.report
                document.setdefault("extensions", {})["stitchforge.rare_colors"] = {
                    "version": 1,
                    "colors_before": rare_color_report["colors_before"],
                    "colors_after": rare_color_report["colors_after"],
                    "changed_stitches": rare_color_report["changed_stitches"],
                    "retained_colors": len(rare_color_report["retained"]),
                }
    except (ImageInputError, PaletteConstraintError, ValueError, OSError) as error:
        raise ConversionRequestError(str(error)) from error

    response = {
        "source_asset": {
            "sha256": source_artifact.artifact_hash,
            "url": f"/api/v1/assets/{source_artifact.artifact_hash}",
            "media_type": source_artifact.media_type,
        },
        "normalized_asset": {
            "sha256": aligned_artifact.artifact_hash,
            "url": f"/api/v1/assets/{aligned_artifact.artifact_hash}",
            "media_type": aligned_artifact.media_type,
        },
        "crop_report": crop_report,
        "planning_report": planning_report,
        "normalization_report": normalization.as_dict(),
        "background_report": (
            background_report.as_dict() if background_report is not None else None
        ),
        "recovery_report": (recovery_report.as_dict() if recovery_report is not None else None),
        "palette_report": palette.as_dict(),
        "optimization_report": optimization_report,
        "rare_color_report": rare_color_report,
        "statistics": calculate_statistics(document).as_dict(),
        "motif_resolution": measure_motif_resolution(
            document,
            minimum_effective_width=1 if counted is not None else 160,
        ).as_dict(),
    }

    phase("persisting")
    timings["total"] = round((time.perf_counter() - started) * 1000, 3)
    manifest = {
        "version": 1,
        "input": {"source_sha256": source_artifact.artifact_hash, "options": options.model_dump()},
        "effective_options": {
            "grid_width": sampled.width,
            "grid_height": sampled.height,
            "color_budget": color_budget,
            "fabric_count": fabric_count,
            "strands": strands,
            "source_profile": source_profile,
            "remove_border_background": remove_border_background,
            "optimize": optimize and counted is None,
            "palette_strategy": palette_strategy,
        },
        "catalog_sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "engine_version": document["provenance"]["engine_version"],
        "implementation_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("conversion.py", "planning.py", "structure_review.py")
        },
        "rare_color_implementation_sha256": hashlib.sha256(
            Path(rare_color_engine.__file__).read_bytes()
        ).hexdigest(),
        "reports": response,
        "timings_ms": timings,
    }
    manifest_artifact = artifact_store.put_bytes(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode(),
        media_type="application/json",
    )
    manifest["artifact_sha256"] = manifest_artifact.artifact_hash
    document.setdefault("extensions", {})["stitchforge.conversion"] = {
        "version": 1,
        "artifact_sha256": manifest_artifact.artifact_hash,
        "source_sha256": source_artifact.artifact_hash,
        "catalog_sha256": manifest["catalog_sha256"],
        "timings_ms": {key: round(value) for key, value in timings.items()},
    }
    return {
        "document": document,
        "response": response,
        "owned_asset_hashes": list(
            dict.fromkeys(
                [
                    source_artifact.artifact_hash,
                    normalized_artifact.artifact_hash,
                    aligned_artifact.artifact_hash,
                    manifest_artifact.artifact_hash,
                    *([plan_artifact.artifact_hash] if planning_report is not None else []),
                ]
            )
        ),
    }
