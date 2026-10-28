from __future__ import annotations

import io
from copy import deepcopy
from urllib.parse import unquote

import pytest
from conftest import AccountClient
from PIL import Image, ImageChops, ImageFont
from stitchforge.chart_image import MAX_CHART_PIXELS, _symbol_font, render_download_chart
from stitchforge.oxs import SUPPORTED_COORDINATE_PROFILE, import_oxs
from stitchforge.xsd import import_xsd
from stitchforge_api import create_app
from stitchforge_api.app import CHART_EXPORT_SLOT


@pytest.mark.parametrize("symbol", ["AA", "WW", "!?", "AM"])
def test_extended_symbol_labels_fit_chart_cells(symbol):
    for cell in (12, 28):
        font = _symbol_font(symbol, max(10, round(cell * 0.66)), cell - 4)
        assert font.getlength(symbol) <= cell - 4


def test_complete_png_has_all_cells_symbols_and_legend(tmp_path, minimal_document, monkeypatch):
    # Use a deterministic portable font so pixel assertions also work on Linux.
    monkeypatch.setattr(
        "stitchforge.chart_image._text_font",
        lambda size: (ImageFont.load_default(size=size), False),
    )
    minimal_document["metadata"]["title"] = "Asymmetric test"
    path = render_download_chart(minimal_document, tmp_path / "chart.png")
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.info["Title"] == "Asymmetric test"
        left, top, cell = 64, 160, 28
        assert image.width >= left + 4 * cell
        assert image.height > top + 3 * cell + 150
        # Every blank stays blank; each occupied tile has its correct background and glyph.
        for offset, index in enumerate(minimal_document["full_stitches"]["cells"]):
            x, y = left + offset % 4 * cell, top + offset // 4 * cell
            tile = image.crop((x + 3, y + 3, x + cell - 2, y + cell - 2))
            background = (0, 0, 0) if index == 1 else (255, 255, 255)
            assert tile.getpixel((0, 0)) == background
            diff = ImageChops.difference(tile, Image.new("RGB", tile.size, background))
            assert (diff.getbbox() is not None) == (index != 0)
        legend = image.crop((32, top + 3 * cell + 64, image.width - 32, image.height - 70))
        assert ImageChops.difference(legend, Image.new("RGB", legend.size, "white")).getbbox()


def test_png_wraps_long_thread_codes_and_omits_unused_recipes(
    tmp_path, minimal_document, monkeypatch
):
    monkeypatch.setattr(
        "stitchforge.chart_image._text_font",
        lambda size: (ImageFont.load_default(size=size), False),
    )
    minimal_document["metadata"]["title"] = "Chart"
    before = render_download_chart(minimal_document, tmp_path / "before.png")
    with Image.open(before) as image:
        initial_height = image.height
    minimal_document["materials"][0]["brand"] = "LongBrand" * 14
    minimal_document["materials"][0]["code"] = "W" * 64
    path = render_download_chart(minimal_document, tmp_path / "wrapped.png")
    with Image.open(path) as image:
        assert image.height > initial_height
    minimal_document["full_stitches"]["cells"] = [0] * 12
    path = render_download_chart(minimal_document, tmp_path / "empty.png")
    with Image.open(path) as image:
        assert image.height < initial_height


def test_png_largest_supported_grid_fits_bound(tmp_path, minimal_document, monkeypatch):
    monkeypatch.setattr(
        "stitchforge.chart_image._text_font",
        lambda size: (ImageFont.load_default(size=size), False),
    )
    minimal_document["metadata"]["title"] = "Large chart"
    minimal_document["grid"].update(width=600, height=600)
    minimal_document["full_stitches"]["cells"] = [1] * 360_000
    path = render_download_chart(minimal_document, tmp_path / "large.png")
    with Image.open(path) as image:
        assert image.width >= 600 * 12
        assert image.width * image.height <= MAX_CHART_PIXELS
        image.verify()


@pytest.mark.parametrize("format", ["oxs", "xsd", "png"])
def test_download_is_private_free_and_revision_specific(tmp_path, minimal_document, format):
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "Download"}).json()
        project_id = created["project"]["project_id"]
        document = deepcopy(minimal_document)
        document["metadata"]["title"] = "Chart / test"
        saved = client.post(
            f"/api/v1/projects/{project_id}/revisions",
            json={
                "expected_head_revision_id": created["initial_revision"]["revision_id"],
                "document": document,
            },
        ).json()
        url = f"/api/v1/projects/{project_id}/revisions/{saved['revision_id']}/download/{format}"
        balance = client.get("/api/v1/billing").json()["credits"]
        response = client.get(url)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert f"Chart _ test.{format}" in unquote(response.headers["content-disposition"])
        assert response.headers["content-disposition"].startswith("attachment;")
        assert client.get("/api/v1/billing").json()["credits"] == balance
        if format in {"oxs", "xsd"}:
            path = tmp_path / f"download.{format}"
            path.write_bytes(response.content)
            loaded = (
                import_oxs(path, coordinate_profile=SUPPORTED_COORDINATE_PROFILE).document
                if format == "oxs"
                else import_xsd(path).document
            )
            assert loaded["full_stitches"]["cells"] == document["full_stitches"]["cells"]
            assert loaded["grid"] == document["grid"]
            assert [m["code"] for m in loaded["materials"]] == [
                m["code"] for m in document["materials"]
            ]
        else:
            with Image.open(io.BytesIO(response.content)) as image:
                assert image.info["Title"] == document["metadata"]["title"]
                image.verify()
        other = client.post("/api/v1/projects", json={"name": "Other"}).json()["project"][
            "project_id"
        ]
        assert client.get(url.replace(project_id, other)).status_code == 404
        client.delete(f"/api/v1/projects/{project_id}")
        assert client.get(url).status_code == 404
        client.post("/api/v1/auth/logout")
        assert client.get(url).status_code == 401
        client.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": "other-password-123"},
        )
        assert client.get(url).status_code == 404


def test_busy_export_and_render_failure_recover(tmp_path, monkeypatch):
    with AccountClient(create_app(tmp_path / "data")) as client:
        created = client.post("/api/v1/projects", json={"name": "Chart"}).json()
        project = created["project"]
        url = (
            f"/api/v1/projects/{project['project_id']}/revisions/"
            f"{project['head_revision_id']}/download/png"
        )
        with CHART_EXPORT_SLOT:
            assert client.get(url).status_code == 503
        monkeypatch.setenv("STITCHFORGE_CHART_FONT", str(tmp_path / "missing-font.ttf"))
        response = client.get(url)
        assert response.status_code == 422
        assert response.json()["code"] == "EXPORT_FAILED"
        monkeypatch.delenv("STITCHFORGE_CHART_FONT")
        assert client.get(url).status_code == 200
