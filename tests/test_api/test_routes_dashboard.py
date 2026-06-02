"""Tests for src/api/routes/dashboard.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


class TestDashboard:
    def test_returns_html(self, client: TestClient) -> None:
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        # The dashboard is the worker browser GUI; check a couple of stable
        # markers from the page chrome.
        assert "<html" in body.lower()
        assert "<body" in body.lower()

    def test_dashboard_is_public(self, client: TestClient) -> None:
        # The endpoint is intentionally unauthenticated; the page itself
        # accepts the API key via the URL fragment.
        resp = client.get("/dashboard")
        assert resp.status_code == 200
