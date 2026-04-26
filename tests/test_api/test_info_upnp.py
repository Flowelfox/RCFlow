"""Tests for the ``upnp`` block in the ``GET /api/info`` response."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from src.core.llm import LLMClient
from src.services.natpmp_service import NatPmpState, NatPmpStatus
from src.services.upnp_service import UpnpState, UpnpStatus

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.config import Settings
    from src.tools.registry import ToolRegistry

API_KEY = "test-api-key"


@pytest.fixture
def client(test_app: FastAPI, test_settings: Settings, tool_registry: ToolRegistry) -> TestClient:
    # /api/info reads app.state.llm_client; the base test_app fixture does not
    # set it because most tests don't need it.  Installing a real LLMClient here
    # keeps the response shape identical to production.
    test_app.state.llm_client = LLMClient(test_settings, tool_registry)
    return TestClient(test_app)


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def test_info_upnp_disabled_by_default(client: TestClient, test_app: FastAPI) -> None:
    test_app.state.upnp_service = None
    resp = client.get("/api/info", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert "upnp" in body
    assert body["upnp"]["enabled"] is False
    assert body["upnp"]["status"] == "disabled"
    assert body["upnp"]["external_ip"] is None
    assert body["upnp"]["external_port"] is None
    assert body["upnp"]["error"] is None


def test_info_upnp_includes_snapshot_when_service_present(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    snapshot_state = UpnpState(
        status=UpnpStatus.MAPPED,
        external_ip="203.0.113.5",
        external_port=53890,
        internal_port=53890,
        error=None,
    )
    test_app.state.upnp_service = SimpleNamespace(snapshot=lambda: snapshot_state)

    resp = client.get("/api/info", headers=_auth_headers())
    assert resp.status_code == 200
    upnp = resp.json()["upnp"]
    assert upnp["enabled"] is True
    assert upnp["status"] == "mapped"
    assert upnp["external_ip"] == "203.0.113.5"
    assert upnp["external_port"] == 53890
    assert upnp["error"] is None


def test_info_ok_when_llm_client_is_none(test_app: FastAPI) -> None:
    """Direct-tool mode (``LLM_PROVIDER=none``) leaves ``llm_client`` as None.

    The endpoint must still serve ``/api/info`` successfully with an empty
    attachment-capability map instead of crashing with ``AttributeError``.
    """
    test_app.state.llm_client = None
    test_app.state.upnp_service = None
    local_client = TestClient(test_app)
    resp = local_client.get("/api/info", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["supports_attachments"] is True
    assert body["attachment_capabilities"] == {}


def test_info_upnp_reports_failure(client: TestClient, test_app: FastAPI) -> None:
    snapshot_state = UpnpState(
        status=UpnpStatus.FAILED,
        error="No UPnP IGD found on LAN",
    )
    test_app.state.upnp_service = SimpleNamespace(snapshot=lambda: snapshot_state)

    resp = client.get("/api/info", headers=_auth_headers())
    assert resp.status_code == 200
    upnp = resp.json()["upnp"]
    assert upnp["enabled"] is True
    assert upnp["status"] == "failed"
    assert upnp["error"] == "No UPnP IGD found on LAN"


def test_info_natpmp_disabled_by_default(client: TestClient, test_app: FastAPI) -> None:
    test_app.state.upnp_service = None
    test_app.state.natpmp_service = None
    resp = client.get("/api/info", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert "natpmp" in body
    assert body["natpmp"]["enabled"] is False
    assert body["natpmp"]["status"] == "disabled"
    assert body["natpmp"]["public_ip"] is None
    assert body["natpmp"]["external_port"] is None
    assert body["natpmp"]["gateway"] is None
    assert body["natpmp"]["error"] is None


def test_info_natpmp_includes_snapshot_when_mapped(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    snapshot_state = NatPmpState(
        status=NatPmpStatus.MAPPED,
        gateway="10.2.0.1",
        public_ip="89.45.224.13",
        external_port=51234,
        internal_port=53890,
        error=None,
    )
    test_app.state.upnp_service = None
    test_app.state.natpmp_service = SimpleNamespace(snapshot=lambda: snapshot_state)

    resp = client.get("/api/info", headers=_auth_headers())
    assert resp.status_code == 200
    natpmp = resp.json()["natpmp"]
    assert natpmp["enabled"] is True
    assert natpmp["status"] == "mapped"
    assert natpmp["gateway"] == "10.2.0.1"
    assert natpmp["public_ip"] == "89.45.224.13"
    assert natpmp["external_port"] == 51234
    assert natpmp["internal_port"] == 53890
    assert natpmp["error"] is None
