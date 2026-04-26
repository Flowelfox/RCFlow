"""Tests for ``GET /api/models`` (dynamic model catalog endpoint)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from src.services.model_catalog import CatalogResult, Credentials, ModelEntry

if TYPE_CHECKING:
    from fastapi import FastAPI


class _StubCatalog:
    """In-memory stand-in for :class:`ModelCatalog` used by the route tests."""

    def __init__(
        self,
        result: CatalogResult | None = None,
        capture: list[tuple[str, str, Credentials, bool]] | None = None,
    ) -> None:
        self.result = result or CatalogResult(
            options=[ModelEntry(value="claude-opus-test", label="Opus")],
            source="live",
            fetched_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
            error=None,
            ttl_seconds=600,
        )
        self.calls: list[tuple[str, str, Credentials, bool]] = capture if capture is not None else []

    async def get(
        self,
        provider: str,
        scope: str,
        credentials: Credentials,
        *,
        force_refresh: bool = False,
    ) -> CatalogResult:
        self.calls.append((provider, scope, credentials, force_refresh))
        return self.result


def _stub_tool_settings(per_tool: dict[str, dict[str, Any]] | None = None):
    """Build a fake ``ToolSettingsManager`` with controllable ``get_settings``."""

    class _Stub:
        def get_settings(self, tool_name: str) -> dict[str, Any]:
            return (per_tool or {}).get(tool_name, {})

    return _Stub()


def _attach_catalog(test_app: FastAPI, catalog: _StubCatalog) -> None:
    test_app.state.model_catalog = catalog


def _client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def test_list_models_returns_live_payload(test_app: FastAPI) -> None:
    catalog = _StubCatalog()
    _attach_catalog(test_app, catalog)
    test_app.state.tool_settings = _stub_tool_settings()

    response = _client(test_app).get(
        "/api/models",
        params={"provider": "anthropic", "scope": "global"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "anthropic"
    assert body["scope"] == "global"
    assert body["source"] == "live"
    assert body["allow_custom"] is True
    assert body["options"] == [{"value": "claude-opus-test", "label": "Opus"}]
    assert body["fetched_at"] == "2026-04-26T12:00:00+00:00"
    assert body["ttl_seconds"] == 600
    # The catalog received the global Anthropic API key from settings.
    assert len(catalog.calls) == 1
    _, _, creds, refresh = catalog.calls[0]
    assert creds.api_key == "test-anthropic-key"
    assert refresh is False


def test_list_models_force_refresh_passes_through(test_app: FastAPI) -> None:
    catalog = _StubCatalog()
    _attach_catalog(test_app, catalog)
    test_app.state.tool_settings = _stub_tool_settings()

    _client(test_app).get(
        "/api/models",
        params={"provider": "anthropic", "scope": "global", "refresh": "true"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert catalog.calls[0][3] is True


def test_list_models_fallback_returns_200_with_error(test_app: FastAPI) -> None:
    failing = _StubCatalog(
        result=CatalogResult(
            options=[ModelEntry(value="claude-fallback", label="Fallback")],
            source="fallback",
            fetched_at=None,
            error="upstream 500",
            ttl_seconds=600,
        )
    )
    _attach_catalog(test_app, failing)
    test_app.state.tool_settings = _stub_tool_settings()

    response = _client(test_app).get(
        "/api/models",
        params={"provider": "anthropic", "scope": "global"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "fallback"
    assert body["error"] == "upstream 500"
    assert body["fetched_at"] is None


def test_list_models_unknown_provider_returns_422(test_app: FastAPI) -> None:
    _attach_catalog(test_app, _StubCatalog())
    test_app.state.tool_settings = _stub_tool_settings()
    response = _client(test_app).get(
        "/api/models",
        params={"provider": "not-a-provider", "scope": "global"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 422


def test_list_models_unknown_scope_returns_422(test_app: FastAPI) -> None:
    _attach_catalog(test_app, _StubCatalog())
    test_app.state.tool_settings = _stub_tool_settings()
    response = _client(test_app).get(
        "/api/models",
        params={"provider": "anthropic", "scope": "garbage"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 422


def test_list_models_requires_api_key(test_app: FastAPI) -> None:
    _attach_catalog(test_app, _StubCatalog())
    test_app.state.tool_settings = _stub_tool_settings()
    response = _client(test_app).get(
        "/api/models",
        params={"provider": "anthropic", "scope": "global"},
    )
    # Missing X-API-Key header → FastAPI's APIKeyHeader returns 403 by default.
    assert response.status_code in (401, 403)


def test_list_models_claude_code_scope_reads_tool_settings(test_app: FastAPI) -> None:
    catalog = _StubCatalog()
    _attach_catalog(test_app, catalog)
    test_app.state.tool_settings = _stub_tool_settings({"claude_code": {"anthropic_api_key": "tool-only-key"}})
    response = _client(test_app).get(
        "/api/models",
        params={"provider": "anthropic", "scope": "claude_code"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 200
    _, scope, creds, _ = catalog.calls[0]
    assert scope == "claude_code"
    assert creds.api_key == "tool-only-key"


def test_list_models_opencode_scope_uses_openrouter_unauthenticated(test_app: FastAPI) -> None:
    catalog = _StubCatalog()
    _attach_catalog(test_app, catalog)
    test_app.state.tool_settings = _stub_tool_settings({"opencode": {"opencode_api_key": "anth-key"}})
    response = _client(test_app).get(
        "/api/models",
        params={"provider": "openrouter", "scope": "opencode"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 200
    provider, scope, _, _ = catalog.calls[0]
    assert (provider, scope) == ("openrouter", "opencode")


def test_list_models_codex_scope_uses_codex_api_key(test_app: FastAPI) -> None:
    catalog = _StubCatalog()
    _attach_catalog(test_app, catalog)
    test_app.state.tool_settings = _stub_tool_settings({"codex": {"codex_api_key": "codex-tool-key"}})
    response = _client(test_app).get(
        "/api/models",
        params={"provider": "openai", "scope": "codex"},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 200
    _, _, creds, _ = catalog.calls[0]
    assert creds.api_key == "codex-tool-key"


@pytest.mark.parametrize(
    ("provider", "scope"),
    [
        ("openai", "claude_code"),  # claude_code does not own an OpenAI key
        ("bedrock", "codex"),  # codex never uses Bedrock
    ],
)
def test_list_models_invalid_provider_for_scope_returns_422(test_app: FastAPI, provider: str, scope: str) -> None:
    _attach_catalog(test_app, _StubCatalog())
    test_app.state.tool_settings = _stub_tool_settings()
    response = _client(test_app).get(
        "/api/models",
        params={"provider": provider, "scope": scope},
        headers={"X-API-Key": "test-api-key"},
    )
    assert response.status_code == 422
