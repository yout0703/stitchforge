from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageCms
from stitchforge.imaging import (
    ImageInputError,
    apply_alpha_mask,
    crop_normalized_image,
    normalize_image,
    sample_grid,
    sample_normalized_image,
)
from stitchforge.recovery import recover_rendered_stitch_colors
from stitchforge.segmentation import remove_connected_border_background


def test_normalize_png_preserves_alpha_and_records_missing_icc(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "normalized.png"
    Image.new("RGBA", (3, 2), (10, 20, 30, 0)).save(source)

    report = normalize_image(source, output)

    assert report.source_format == "PNG"
    assert report.alpha_present
    assert report.color_profile == "assumed_srgb"
    assert report.assumptions == ("source_without_icc_assumed_srgb",)
    assert report.source_sha256 != report.normalized_sha256
    with Image.open(output) as image:
        assert image.mode == "RGBA"
        assert image.size == (3, 2)
        assert image.getpixel((0, 0))[3] == 0
        assert image.info["icc_profile"]
        assert image.info["stitchforge_normalized"] == "srgb-v1"


def test_normalize_applies_exif_orientation(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    output = tmp_path / "normalized.png"
    image = Image.new("RGB", (3, 2), "red")
    exif = image.getexif()
    exif[274] = 6
    image.save(source, exif=exif)

    report = normalize_image(source, output)

    assert report.exif_orientation_applied
    assert (report.source_width, report.source_height) == (3, 2)
    assert (report.normalized_width, report.normalized_height) == (2, 3)


def test_normalize_converts_embedded_icc_to_srgb(tmp_path: Path) -> None:
    source = tmp_path / "profiled.png"
    output = tmp_path / "normalized.png"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (2, 2), "#336699").save(source, icc_profile=profile)

    report = normalize_image(source, output)

    assert report.color_profile == "embedded_to_srgb"
    assert report.assumptions == ()


def test_normalize_rejects_animated_png(tmp_path: Path) -> None:
    source = tmp_path / "animated.png"
    frames = [Image.new("RGBA", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(source, save_all=True, append_images=frames[1:], duration=100, loop=0)

    with pytest.raises(ImageInputError, match="动画或多帧"):
        normalize_image(source, tmp_path / "out.png")


def test_normalize_rejects_invalid_file_and_resource_limits(monkeypatch, tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not an image")
    with pytest.raises(ImageInputError, match="JPG 或 PNG"):
        normalize_image(invalid, tmp_path / "invalid-out.png")

    source = tmp_path / "small.png"
    Image.new("RGB", (2, 2)).save(source)
    monkeypatch.setattr("stitchforge.imaging.MAX_IMAGE_FILE_BYTES", 1)
    with pytest.raises(ImageInputError, match="20 MiB"):
        normalize_image(source, tmp_path / "large-out.png")


def test_normalize_rejects_decoded_pixel_limit(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "pixels.png"
    Image.new("RGB", (2, 2)).save(source)
    monkeypatch.setattr("stitchforge.imaging.MAX_DECODED_PIXELS", 3)

    with pytest.raises(ImageInputError, match="40 MP"):
        normalize_image(source, tmp_path / "out.png")


def test_grid_sampling_uses_alpha_coverage_and_ignores_hidden_rgb() -> None:
    image = Image.new("RGBA", (4, 1))
    image.putdata(
        [
            (255, 0, 0, 255),
            (0, 0, 255, 0),
            (255, 255, 255, 255),
            (255, 255, 255, 255),
        ]
    )

    sample = sample_grid(image, width=2, height=1, coverage_threshold=0.5)

    assert sample.coverage_ppm == (500_000, 1_000_000)
    assert sample.display_srgb == ("#FF0000", "#FFFFFF")
    stricter = sample_grid(image, width=2, height=1, coverage_threshold=0.51)
    assert stricter.display_srgb == (None, "#FFFFFF")


def test_grid_sampling_averages_in_linear_rgb() -> None:
    image = Image.new("RGB", (2, 1))
    image.putdata([(0, 0, 0), (255, 255, 255)])

    sample = sample_grid(image, width=1, height=1)

    assert sample.display_srgb == ("#BCBCBC",)


@pytest.mark.parametrize(
    ("width", "height", "threshold", "message"),
    [
        (0, 1, 0.5, "网格"),
        (601, 1, 0.5, "网格"),
        (301, 300, 0.5, "网格"),
        (1, 1, -0.1, "coverage_threshold"),
        (1, 1, 1.1, "coverage_threshold"),
    ],
)
def test_grid_sampling_rejects_invalid_parameters(width, height, threshold, message) -> None:
    with pytest.raises(ValueError, match=message):
        sample_grid(
            Image.new("RGB", (2, 2)),
            width=width,
            height=height,
            coverage_threshold=threshold,
        )


def test_grid_sampling_rejects_upsampling() -> None:
    with pytest.raises(ValueError, match="不能小于目标网格"):
        sample_grid(Image.new("RGB", (1, 1)), width=2, height=1)


def test_sample_normalized_image_requires_png(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    Image.new("RGB", (2, 2), "red").save(source)
    with pytest.raises(ImageInputError, match="有效 PNG"):
        sample_normalized_image(source, width=1, height=1)


def test_sample_normalized_image_rejects_unmarked_png(tmp_path: Path) -> None:
    source = tmp_path / "plain.png"
    Image.new("RGB", (2, 2), "red").save(source)
    with pytest.raises(ImageInputError, match="规范化标记"):
        sample_normalized_image(source, width=1, height=1)


def test_crop_uses_half_open_bounds_and_preserves_work_marker(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    cropped = tmp_path / "cropped.png"
    Image.new("RGB", (6, 4), "red").save(source)
    normalize_image(source, normalized)

    report = crop_normalized_image(normalized, cropped, box=(1, 1, 5, 3))

    assert (report.output_width, report.output_height) == (4, 2)
    assert report.details["box"] == [1, 1, 5, 3]
    with Image.open(cropped) as image:
        assert image.size == (4, 2)
        assert image.info["stitchforge_normalized"] == "srgb-v1"
        assert image.info["stitchforge_crop_box"] == "1,1,5,3"


@pytest.mark.parametrize("box", [(-1, 0, 1, 1), (0, 0, 0, 1), (0, 0, 3, 1)])
def test_crop_rejects_out_of_bounds_or_empty_box(tmp_path: Path, box) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    Image.new("RGB", (2, 2), "red").save(source)
    normalize_image(source, normalized)
    with pytest.raises(ValueError, match="裁切框"):
        crop_normalized_image(normalized, tmp_path / "cropped.png", box=box)


def test_alpha_mask_multiplies_existing_alpha(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    masked = tmp_path / "masked.png"
    mask = tmp_path / "mask.png"
    Image.new("RGBA", (3, 1), (255, 0, 0, 128)).save(source)
    Image.fromarray(__import__("numpy").array([[0, 128, 255]], dtype="uint8")).save(mask)
    normalize_image(source, normalized)

    report = apply_alpha_mask(normalized, mask, masked)

    assert report.operation == "alpha_mask"
    with Image.open(masked) as image:
        assert [image.getpixel((x, 0))[3] for x in range(3)] == [0, 64, 128]


def test_alpha_mask_requires_matching_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    normalized = tmp_path / "normalized.png"
    mask = tmp_path / "mask.png"
    Image.new("RGB", (2, 2), "red").save(source)
    Image.new("L", (1, 1), 255).save(mask)
    normalize_image(source, normalized)
    with pytest.raises(ValueError, match="蒙版尺寸"):
        apply_alpha_mask(normalized, mask, tmp_path / "masked.png")


def test_border_background_removal_preserves_enclosed_similar_color() -> None:
    image = Image.new("RGB", (5, 5), "#F1F0ED")
    pixels = image.load()
    for x, y in ((1, 1), (2, 1), (3, 1), (1, 2), (3, 2), (1, 3), (2, 3), (3, 3)):
        pixels[x, y] = (25, 80, 140)
    pixels[2, 2] = (241, 240, 237)
    sample = sample_grid(image, width=5, height=5)

    cleaned, report = remove_connected_border_background(sample, delta_e00_threshold=2)

    assert report.background_display_srgb == "#F1F0ED"
    assert report.removed_cells == 16
    assert report.retained_cells == 9
    assert cleaned.display_srgb[0] is None
    assert cleaned.display_srgb[2 * 5 + 2] == "#F1F0ED"
    assert cleaned.coverage_ppm[0] == 0


def test_border_background_removal_keeps_different_subject_touching_border() -> None:
    image = Image.new("RGB", (4, 3), "#EEEEEE")
    image.putpixel((0, 1), (200, 20, 20))
    sample = sample_grid(image, width=4, height=3)

    cleaned, report = remove_connected_border_background(sample, delta_e00_threshold=3)

    assert report.removed_cells == 11
    assert cleaned.display_srgb[4] == "#C81414"


@pytest.mark.parametrize("threshold", [-1, float("nan"), 51])
def test_border_background_removal_rejects_invalid_threshold(threshold: float) -> None:
    sample = sample_grid(Image.new("RGB", (2, 2), "white"), width=2, height=2)
    with pytest.raises(ValueError, match="delta_e00_threshold"):
        remove_connected_border_background(sample, delta_e00_threshold=threshold)


def test_rendered_stitch_recovery_only_lifts_light_neutral_colors() -> None:
    sample = sample_grid(
        Image.new("RGB", (4, 1)),
        width=4,
        height=1,
    )
    sample = type(sample)(
        width=4,
        height=1,
        coverage_threshold_ppm=500_000,
        coverage_ppm=(1_000_000, 1_000_000, 1_000_000, 0),
        display_srgb=("#E0D8CC", "#FF0000", "#202020", None),
    )

    recovered, report = recover_rendered_stitch_colors(sample, lightness_lift=5)

    assert recovered.display_srgb[0] != "#E0D8CC"
    assert recovered.display_srgb[1:] == ("#FF0000", "#202020", None)
    assert report.adjusted_colors == 1
    assert report.adjusted_cells == 1
    assert 0 < report.maximum_applied_lift <= 5


def test_rendered_stitch_recovery_accepts_an_empty_grid() -> None:
    sample = type(sample_grid(Image.new("RGB", (1, 1)), width=1, height=1))(
        width=1,
        height=1,
        coverage_threshold_ppm=500_000,
        coverage_ppm=(0,),
        display_srgb=(None,),
    )

    recovered, report = recover_rendered_stitch_colors(sample)

    assert recovered is sample
    assert report.adjusted_colors == report.adjusted_cells == 0
    assert report.maximum_applied_lift == 0


@pytest.mark.parametrize("lightness_lift", [-1, float("nan"), 13])
def test_rendered_stitch_recovery_rejects_invalid_lift(lightness_lift: float) -> None:
    sample = sample_grid(Image.new("RGB", (1, 1), "white"), width=1, height=1)
    with pytest.raises(ValueError, match="lightness_lift"):
        recover_rendered_stitch_colors(sample, lightness_lift=lightness_lift)
