from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def minimal_document() -> dict[str, Any]:
    with (ROOT / "examples/minimal-pattern.json").open("r", encoding="utf-8") as stream:
        return json.load(stream)


@pytest.fixture
def many_color_document(minimal_document) -> dict[str, Any]:
    """77 distinct test threads, all used, without bundling a user's design."""
    document = minimal_document
    document["metadata"]["title"] = "77 colour regression"
    document["grid"].update(width=11, height=7)
    document["regions"] = []
    document["materials"] = [
        {
            "id": f"m_{i}",
            "brand": "TEST",
            "series": "Synthetic",
            "code": f"T{i:03d}",
            "name": f"Thread {i}",
            "color": {
                "display_srgb": f"#{i * 2000:06X}",
                "status": "synthetic",
                "source_ref": "src_fixture",
            },
        }
        for i in range(1, 78)
    ]
    document["recipes"] = [
        {
            "id": f"r_{i}",
            "index": i,
            "components": [{"material_id": f"m_{i}", "strands": 2}],
        }
        for i in range(1, 78)
    ]
    document["legend"] = [{"recipe_id": f"r_{i}", "symbol_id": f"s{i:03d}"} for i in range(1, 78)]
    document["full_stitches"]["cells"] = list(range(1, 78))
    document["constraints"]["color_budget"] = 77
    return document


class AccountClient:
    """Existing API contracts exercised inside a real authenticated account."""

    def __init__(self, app):
        from fastapi.testclient import TestClient

        self.client = TestClient(app, headers={"X-StitchForge-Request": "1"})

    def __enter__(self):
        client = self.client.__enter__()
        credentials = {"email": "test@example.com", "password": "test-password-123"}
        response = client.post("/api/v1/auth/register", json=credentials)
        if response.status_code == 409:
            response = client.post("/api/v1/auth/login", json=credentials)
        assert response.status_code in {200, 201}, response.text
        accounts = client.app.state.accounts
        order = accounts.create_order(
            response.json()["user_id"],
            {
                "id": "test",
                "credits": 100,
                "amount_minor": 100,
                "currency": "CNY",
            },
            "test-credit-seed",
        )
        accounts.settle_order(order["order_id"], "test-credit-payment", 100, "CNY")
        return client

    def __exit__(self, *args):
        return self.client.__exit__(*args)
