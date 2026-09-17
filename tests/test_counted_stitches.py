from collections import Counter
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from conftest import AccountClient
from PIL import Image, ImageDraw
from stitchforge.counted_stitches import inspect_stitch_evidence, recover_counted_stitches
from stitchforge_api import create_app

MOTIF = ["...R...", "..RRR..", ".RRPRR.", "RRRRRRR", ".RRRRR.", "..RRR..", "...R..."]


def stitched_photo(path: Path, *, pitch=20, accent="#EDADA8") -> Path:
    image = Image.new("RGB", (pitch * 20, pitch * 25), "#E4E0D6")
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(MOTIF):
        for col, cell in enumerate(line):
            if cell == ".":
                continue
            x, y = pitch * (7 + col), pitch * (9 + row)
            # Vary thread brightness across the motif without adding new floss.
            shade = (row + col) % 4
            colour = (204 - shade * 9, 32 - shade, 40 - shade) if cell == "R" else accent
            radius, thickness = round(pitch * 0.35), round(pitch * 0.45)
            draw.line(
                (x - radius, y - radius, x + radius, y + radius), fill=colour, width=thickness
            )
            draw.line(
                (x - radius, y + radius, x + radius, y - radius), fill=colour, width=thickness
            )
    image.save(path)
    return path


def aida_scene(path: Path, *, pitch=16, columns=48, rows=72) -> Path:
    """Finished embroidery photo: Aida holes plus a large dark scene."""
    width, height = pitch * columns, pitch * rows
    cloth = np.full((height, width, 3), [228, 224, 214], dtype=np.uint8)
    rows_i, cols_i = np.mgrid[:height, :width]
    radius = max(1, round(pitch * 0.14))
    holes = (cols_i % pitch) ** 2 + (rows_i % pitch) ** 2 <= radius * radius
    cloth[holes] = [198, 192, 180]
    image = Image.fromarray(cloth)
    draw = ImageDraw.Draw(image)

    def cross(column, row, colour):
        x, y = column * pitch + pitch // 2, row * pitch + pitch // 2
        radius_x, thickness = round(pitch * 0.32), max(2, round(pitch * 0.38))
        draw.line(
            (x - radius_x, y - radius_x, x + radius_x, y + radius_x),
            fill=colour,
            width=thickness,
        )
        draw.line(
            (x - radius_x, y + radius_x, x + radius_x, y - radius_x),
            fill=colour,
            width=thickness,
        )

    for row in range(8, 40):
        for column in range(10, 38):
            cross(column, row, (40, 55, 70))
    for row in range(40, 64):
        for column in range(10, 38):
            cross(column, row, (30, 80, 90) if row % 3 else (40, 55, 70))
    for row in range(18, 28):
        for column in range(18, 28):
            cross(column, row, (220, 170, 40))
    image.save(path)
    return path


@pytest.mark.parametrize("pitch", [16, 20, 28])
def test_recovers_one_cell_per_cross_and_merges_shadows(tmp_path, pitch):
    source = stitched_photo(tmp_path / "stitches.png", pitch=pitch)
    result = recover_counted_stitches(source)
    assert result is not None
    assert (result.sample.width, result.sample.height) == (11, 11)
    counts = Counter(c for c in result.sample.display_srgb if c)
    assert sorted(counts.values()) == [1, 24]
    assert result.report["stitch_count"] == 25
    assert result.report["thread_colours"] == 2
    assert result.report["period_x_pixels"] == pitch
    assert result.report["period_y_pixels"] == pitch
    for row, line in enumerate(MOTIF):
        for col, cell in enumerate(line):
            assert (result.sample.display_srgb[(row + 2) * 11 + col + 2] is None) == (cell == ".")


def test_ambiguous_white_details_defer_instead_of_disappearing(tmp_path):
    source = stitched_photo(tmp_path / "neutral.png", accent="#FFFFFF")
    assert recover_counted_stitches(source) is None


@pytest.mark.parametrize("accent", ["#202020", "#000000", "#512812"])
def test_dark_eye_is_kept_as_one_stitch(tmp_path, accent):
    source = stitched_photo(tmp_path / "eye.png", accent=accent)
    result = recover_counted_stitches(source)
    assert result is not None
    assert sorted(Counter(c for c in result.sample.display_srgb if c).values()) == [1, 24]
    assert result.sample.display_srgb[4 * 11 + 5] == accent


def test_luminance_period_recovers_filled_yellow_floss_and_keeps_brown_separate(tmp_path):
    import numpy as np

    motif = ["...Y...", "..YYY..", ".YBYYY.", "YYYYYYY", ".YOYYY.", "..YYY..", "...Y..."]
    pixels = np.full((500, 400, 3), [228, 224, 214], dtype=np.uint8)
    y, x = np.mgrid[:20, :20]
    shade = 0.78 + 0.11 * (np.cos(x * 2 * np.pi / 20) + np.cos(y * 2 * np.pi / 20))
    # Brown and orange deliberately share channel proportions, but differ in
    # brightness. They are separate materials, not shadows of one colour.
    colours = {"Y": [240, 190, 40], "B": [80, 40, 10], "O": [240, 120, 30]}
    for row, line in enumerate(motif):
        for col, cell in enumerate(line):
            if cell != ".":
                pixels[170 + row * 20 : 190 + row * 20, 130 + col * 20 : 150 + col * 20] = (
                    np.array(colours[cell]) * shade[:, :, None]
                )
    source = tmp_path / "yellow.png"
    Image.fromarray(pixels).save(source)
    result = recover_counted_stitches(source)
    assert result is not None
    assert result.report["period_source"] == "thread_luminance"
    assert result.report["stitch_count"] == 25
    assert result.report["thread_colours"] == 3
    assert sorted(Counter(c for c in result.sample.display_srgb if c).values()) == [1, 1, 23]
    assert result.sample.display_srgb[4 * 11 + 4] != result.sample.display_srgb[6 * 11 + 4]


def test_flat_artwork_and_random_texture_are_not_counted_stitches(tmp_path):
    import numpy as np

    flat = Image.new("RGB", (400, 500), "#E4E0D6")
    ImageDraw.Draw(flat).ellipse((120, 150, 280, 310), fill="#CC2028")
    source = tmp_path / "flat.png"
    flat.save(source)
    assert recover_counted_stitches(source) is None
    pixels = np.array(flat)
    noise = np.random.default_rng(7).integers(0, 40, pixels.shape, dtype=np.uint8)
    Image.fromarray(pixels - np.minimum(pixels, noise)).save(source)
    assert recover_counted_stitches(source) is None


def test_complex_reference_images_keep_normal_conversion():
    root = Path(__file__).resolve().parents[1] / "fixtures/fidelity/amazing-images-creator"
    for sample in ("lucky-rabbit", "folk-owl", "parakeets"):
        assert recover_counted_stitches(root / sample / "source.png") is None


def test_counted_conversion_bypasses_ai_preserves_accent_and_charges_once(tmp_path, monkeypatch):
    from stitchforge_api import conversion as app_module

    source = stitched_photo(tmp_path / "source.png")

    def unexpected_ai(*_):
        pytest.fail("A confident stitch lattice must not be enlarged by an AI proposal")

    monkeypatch.setattr(app_module, "plan_import", unexpected_ai)
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "数针验证"}).json()
        project_id = created["project"]["project_id"]
        response = client.post(
            f"/api/v1/projects/{project_id}/convert-image",
            params={"expected_head_revision_id": created["initial_revision"]["revision_id"]},
            content=source.read_bytes(),
        )
        assert response.status_code == 201, response.text
        result = response.json()
        document = result["revision"]["document"]
        assert result["planning_report"]["method"] == "counted_stitches"
        assert result["planning_report"]["plan"]["grid_width"] == 11
        assert result["statistics"]["full_stitch_count"] == 25
        assert sorted(result["statistics"]["recipe_stitch_counts"].values()) == [1, 24]
        assert result["optimization_report"] is None
        assert result["rare_color_report"] is None
        assert result["motif_resolution"]["passes_minimum"]
        assert document["extensions"]["stitchforge.counted_stitches"]["stitch_count"] == 25
        crop_url = result["normalized_asset"]["url"]
        image_source = next(s for s in document["provenance"]["sources"] if s["id"] == "src_image")
        assert image_source["locator"] == crop_url
        with Image.open(BytesIO(client.get(crop_url).content)) as cropped:
            assert cropped.width == cropped.height
        assert client.get("/api/v1/auth/me").json()["credits"] == 99
        client.post("/api/v1/auth/logout")
        assert client.get(crop_url).status_code == 401


def test_explicit_manual_grid_is_not_overridden(tmp_path):
    source = stitched_photo(tmp_path / "source.png")
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "手动尺寸"}).json()
        response = client.post(
            f"/api/v1/projects/{created['project']['project_id']}/convert-image",
            params={
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
                "grid_width": 40,
                "grid_height": 50,
                "color_budget": 3,
            },
            content=source.read_bytes(),
        )
        assert response.status_code == 201, response.text
        document = response.json()["revision"]["document"]
        assert (document["grid"]["width"], document["grid"]["height"]) == (40, 50)
        assert "stitchforge.counted_stitches" not in document.get("extensions", {})


def test_aida_cloth_reports_an_agreed_lattice_period(tmp_path):
    source = aida_scene(tmp_path / "aida.png")
    evidence = inspect_stitch_evidence(source)
    assert evidence["lattice_period"] == 16
    assert recover_counted_stitches(source) is None


def test_printed_artwork_does_not_invent_a_cloth_lattice():
    root = Path(__file__).resolve().parents[1] / "fixtures/fidelity/amazing-images-creator"
    for sample in ("lucky-rabbit", "folk-owl", "parakeets"):
        evidence = inspect_stitch_evidence(root / sample / "source.png")
        assert evidence.get("lattice_period") is None


def test_large_dark_embroidered_scene_stays_a_small_chart(tmp_path, monkeypatch):
    from stitchforge_api import conversion as app_module

    source = aida_scene(tmp_path / "scene.png")

    def unexpected_ai(*_):
        pytest.fail("A measured cloth lattice must not be enlarged by an AI proposal")

    monkeypatch.setattr(app_module, "plan_import", unexpected_ai)
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "绣片小图"}).json()
        response = client.post(
            f"/api/v1/projects/{created['project']['project_id']}/convert-image",
            params={"expected_head_revision_id": created["initial_revision"]["revision_id"]},
            content=source.read_bytes(),
        )
        assert response.status_code == 201, response.text
        result = response.json()
        plan = result["planning_report"]["plan"]
        document = result["revision"]["document"]
        assert result["planning_report"]["method"] == "stitch_pitch"
        assert (plan["grid_width"], plan["grid_height"]) == (96, 144)
        assert plan["source_profile"] == "rendered_stitch"
        assert 90 <= max(document["grid"]["width"], document["grid"]["height"]) <= 144
        assert result["statistics"]["full_stitch_count"] > 200
        assert result["optimization_report"] is None
        assert result["rare_color_report"] is None
        assert client.get("/api/v1/auth/me").json()["credits"] == 99
