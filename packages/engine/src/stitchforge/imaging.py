from __future__ import annotations

import hashlib
import warnings
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageCms, ImageOps, PngImagePlugin, UnidentifiedImageError

from .fabric import MAX_GRID_CELLS, MAX_GRID_SIDE

MAX_IMAGE_FILE_BYTES = 20 * 1024 * 1024
MAX_DECODED_PIXELS = 40_000_000
SUPPORTED_IMAGE_FORMATS = ("JPEG", "PNG")


class ImageInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ImageNormalizationReport:
    source_sha256: str
    normalized_sha256: str
    source_format: str
    source_width: int
    source_height: int
    normalized_width: int
    normalized_height: int
    exif_orientation_applied: bool
    color_profile: str
    alpha_present: bool
    assumptions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["assumptions"] = list(self.assumptions)
        return result


@dataclass(frozen=True, slots=True)
class GridSample:
    width: int
    height: int
    coverage_threshold_ppm: int
    coverage_ppm: tuple[int, ...]
    display_srgb: tuple[str | None, ...]
    source_sha256: str | None = None
    source_locator: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "stitchforge-grid-sample-0.1.0",
            "width": self.width,
            "height": self.height,
            "coverage_threshold_ppm": self.coverage_threshold_ppm,
            "coverage_ppm": list(self.coverage_ppm),
            "display_srgb": list(self.display_srgb),
            "source_sha256": self.source_sha256,
            "source_locator": self.source_locator,
        }


@dataclass(frozen=True, slots=True)
class ImageTransformReport:
    operation: str
    input_sha256: str
    output_sha256: str
    input_width: int
    input_height: int
    output_width: int
    output_height: int
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _png_info(**metadata: str) -> PngImagePlugin.PngInfo:
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("stitchforge_normalized", "srgb-v1")
    for key, value in sorted(metadata.items()):
        png_info.add_text(key, value)
    return png_info


def _save_work_image(image: Image.Image, target: Path, **metadata: str) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    srgb_bytes = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image.save(
        temporary,
        format="PNG",
        icc_profile=srgb_bytes,
        pnginfo=_png_info(**metadata),
        optimize=False,
    )
    payload = temporary.read_bytes()
    temporary.replace(target)
    return hashlib.sha256(payload).hexdigest()


def _load_work_image(path: str | Path) -> tuple[Image.Image, bytes]:
    source = Path(path)
    try:
        payload = source.read_bytes()
        with Image.open(BytesIO(payload), formats=["PNG"]) as image:
            if image.info.get("stitchforge_normalized") != "srgb-v1":
                raise ImageInputError("图片缺少 StitchForge sRGB 规范化标记")
            if image.width * image.height > MAX_DECODED_PIXELS:
                raise ImageInputError("图片解码尺寸超过 40 MP 限制")
            image.load()
            return image.copy(), payload
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ImageInputError("规范化工作图必须是有效 PNG") from error


def _open_image(source: Path) -> tuple[Image.Image, bytes, str, tuple[int, int]]:
    try:
        file_size = source.stat().st_size
    except OSError as error:
        raise ImageInputError(f"无法读取图片: {error}") from error
    if file_size > MAX_IMAGE_FILE_BYTES:
        raise ImageInputError("图片文件超过 20 MiB 限制")
    payload = source.read_bytes()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            opened = Image.open(BytesIO(payload), formats=list(SUPPORTED_IMAGE_FORMATS))
            source_format = opened.format or ""
            source_size = opened.size
            if getattr(opened, "is_animated", False) or getattr(opened, "n_frames", 1) != 1:
                raise ImageInputError("当前版本不接受动画或多帧图片")
            if opened.width * opened.height > MAX_DECODED_PIXELS:
                raise ImageInputError("图片解码尺寸超过 40 MP 限制")
            opened.load()
            return opened.copy(), payload, source_format, source_size
    except ImageInputError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ImageInputError("图片触发解压尺寸安全限制") from error
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ImageInputError("文件不是可解码的 JPG 或 PNG") from error


def _convert_to_srgb(image: Image.Image) -> tuple[Image.Image, str, tuple[str, ...]]:
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    color = image.convert("RGB")
    embedded_profile = image.info.get("icc_profile")
    assumptions: list[str] = []
    if embedded_profile:
        try:
            source_profile = ImageCms.ImageCmsProfile(BytesIO(embedded_profile))
            srgb_profile = ImageCms.createProfile("sRGB")
            color = ImageCms.profileToProfile(
                color,
                source_profile,
                srgb_profile,
                outputMode="RGB",
            )
        except (ImageCms.PyCMSError, OSError, TypeError, ValueError) as error:
            raise ImageInputError("图片内嵌 ICC 配置无法解析或转换") from error
        profile_status = "embedded_to_srgb"
    else:
        profile_status = "assumed_srgb"
        assumptions.append("source_without_icc_assumed_srgb")
    if alpha is not None:
        color.putalpha(alpha)
    return color, profile_status, tuple(assumptions)


def normalize_image(source_path: str | Path, output_path: str | Path) -> ImageNormalizationReport:
    source = Path(source_path)
    image, source_bytes, source_format, source_size = _open_image(source)
    orientation_before = image.getexif().get(274, 1)
    oriented = ImageOps.exif_transpose(image)
    normalized, profile_status, assumptions = _convert_to_srgb(oriented)
    target = Path(output_path)
    normalized_hash = _save_work_image(normalized, target)
    return ImageNormalizationReport(
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        normalized_sha256=normalized_hash,
        source_format=source_format,
        source_width=source_size[0],
        source_height=source_size[1],
        normalized_width=normalized.width,
        normalized_height=normalized.height,
        exif_orientation_applied=orientation_before not in (None, 1),
        color_profile=profile_status,
        alpha_present="A" in normalized.getbands(),
        assumptions=assumptions,
    )


def crop_normalized_image(
    source_path: str | Path,
    output_path: str | Path,
    *,
    box: tuple[int, int, int, int],
) -> ImageTransformReport:
    image, payload = _load_work_image(source_path)
    left, top, right, bottom = box
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise ValueError("裁切框必须是位于图片内的非空半开区间")
    cropped = image.crop(box)
    output_hash = _save_work_image(
        cropped,
        Path(output_path),
        stitchforge_crop_box=f"{left},{top},{right},{bottom}",
        stitchforge_parent_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return ImageTransformReport(
        operation="crop",
        input_sha256=hashlib.sha256(payload).hexdigest(),
        output_sha256=output_hash,
        input_width=image.width,
        input_height=image.height,
        output_width=cropped.width,
        output_height=cropped.height,
        details={"box": [left, top, right, bottom], "coordinate_rule": "pixel-half-open-v1"},
    )


def apply_alpha_mask(
    source_path: str | Path,
    mask_path: str | Path,
    output_path: str | Path,
) -> ImageTransformReport:
    image, payload = _load_work_image(source_path)
    mask_source = Path(mask_path)
    try:
        mask_payload = mask_source.read_bytes()
        with Image.open(BytesIO(mask_payload), formats=["PNG"]) as opened_mask:
            if getattr(opened_mask, "is_animated", False):
                raise ImageInputError("蒙版不能是动画或多帧图片")
            opened_mask.load()
            mask = (
                opened_mask.getchannel("A")
                if "A" in opened_mask.getbands()
                else opened_mask.convert("L")
            )
    except ImageInputError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ImageInputError("蒙版必须是有效的单帧 PNG") from error
    if mask.size != image.size:
        raise ValueError("蒙版尺寸必须与规范化图片一致")
    rgba = image.convert("RGBA")
    source_alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint16)
    mask_alpha = np.asarray(mask, dtype=np.uint16)
    combined = ((source_alpha * mask_alpha + 127) // 255).astype(np.uint8)
    rgba.putalpha(Image.fromarray(combined, mode="L"))
    mask_hash = hashlib.sha256(mask_payload).hexdigest()
    output_hash = _save_work_image(
        rgba,
        Path(output_path),
        stitchforge_mask_sha256=mask_hash,
        stitchforge_parent_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return ImageTransformReport(
        operation="alpha_mask",
        input_sha256=hashlib.sha256(payload).hexdigest(),
        output_sha256=output_hash,
        input_width=image.width,
        input_height=image.height,
        output_width=image.width,
        output_height=image.height,
        details={"mask_sha256": mask_hash, "combination_rule": "multiply-alpha-u8-v1"},
    )


def _srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    normalized = rgb.astype(np.float64) / 255.0
    return np.where(
        normalized <= 0.04045,
        normalized / 12.92,
        ((normalized + 0.055) / 1.055) ** 2.4,
    )


def _linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    encoded = np.where(
        linear <= 0.0031308,
        linear * 12.92,
        1.055 * np.power(linear, 1 / 2.4) - 0.055,
    )
    return np.clip(np.floor(encoded * 255.0 + 0.5), 0, 255).astype(np.uint8)


def sample_grid(
    image: Image.Image,
    *,
    width: int,
    height: int,
    coverage_threshold: float = 0.5,
    source_sha256: str | None = None,
    source_locator: str | None = None,
) -> GridSample:
    if (
        not 1 <= width <= MAX_GRID_SIDE
        or not 1 <= height <= MAX_GRID_SIDE
        or width * height > MAX_GRID_CELLS
    ):
        raise ValueError(
            f"网格必须为 1–{MAX_GRID_SIDE} 格且总数不超过 {MAX_GRID_CELLS:,}"
        )
    if not 0.0 <= coverage_threshold <= 1.0:
        raise ValueError("coverage_threshold 必须在 0 到 1 之间")
    if image.width < width or image.height < height:
        raise ValueError("规范化图片像素尺寸不能小于目标网格")

    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    linear = _srgb_to_linear(rgba[..., :3])
    alpha = rgba[..., 3].astype(np.float64) / 255.0
    coverage_values: list[int] = []
    colors: list[str | None] = []
    for grid_y in range(height):
        y0 = grid_y * image.height // height
        y1 = (grid_y + 1) * image.height // height
        for grid_x in range(width):
            x0 = grid_x * image.width // width
            x1 = (grid_x + 1) * image.width // width
            cell_alpha = alpha[y0:y1, x0:x1]
            coverage = float(cell_alpha.mean())
            coverage_ppm = int(coverage * 1_000_000 + 0.5)
            coverage_values.append(coverage_ppm)
            if coverage + 1e-12 < coverage_threshold or cell_alpha.sum() == 0:
                colors.append(None)
                continue
            weights = cell_alpha[..., np.newaxis]
            average_linear = (linear[y0:y1, x0:x1] * weights).sum(axis=(0, 1)) / weights.sum()
            red, green, blue = _linear_to_srgb(average_linear)
            colors.append(f"#{red:02X}{green:02X}{blue:02X}")
    return GridSample(
        width=width,
        height=height,
        coverage_threshold_ppm=int(coverage_threshold * 1_000_000 + 0.5),
        coverage_ppm=tuple(coverage_values),
        display_srgb=tuple(colors),
        source_sha256=source_sha256,
        source_locator=source_locator,
    )


def sample_normalized_image(
    path: str | Path,
    *,
    width: int,
    height: int,
    coverage_threshold: float = 0.5,
) -> GridSample:
    source = Path(path)
    image, payload = _load_work_image(source)
    return sample_grid(
        image,
        width=width,
        height=height,
        coverage_threshold=coverage_threshold,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        source_locator=str(source),
    )
