"""Tests for the Claude subscription usage client in src/services/usage_service.py."""

from __future__ import annotations

import json

import httpx
import pytest

from src.services import usage_service
from src.services.usage_service import (
    USAGE_API_URL,
    UsageService,
    UsageServiceError,
    parse_usage,
    read_oauth_token,
)

# A representative endpoint body (mirrors the live probe): headline windows
# present, one per-model window null, the other a zero-utilization window.
SAMPLE_BODY = {
    "five_hour": {"utilization": 6.0, "resets_at": "2026-06-08T21:50:00+00:00"},
    "seven_day": {"utilization": 45.0, "resets_at": "2026-06-10T07:59:59+00:00"},
    "seven_day_opus": None,
    "seven_day_sonnet": {"utilization": 0.0, "resets_at": None},
    "extra_usage": {"is_enabled": False},
}


def _mock_service(token: str, handler) -> UsageService:
    """Build a UsageService whose transport is mocked but auth headers kept."""
    svc = UsageService(token)
    svc._client._transport = httpx.MockTransport(handler)
    return svc


def test_parse_usage_full_body():
    parsed = parse_usage(SAMPLE_BODY)
    assert parsed["five_hour"] == {"utilization": 6.0, "resets_at": "2026-06-08T21:50:00+00:00"}
    assert parsed["seven_day"]["utilization"] == 45.0
    # Null per-model window stays None; zero-utilization window is kept.
    assert parsed["seven_day_opus"] is None
    assert parsed["seven_day_sonnet"] == {"utilization": 0.0, "resets_at": None}


def test_parse_usage_handles_missing_and_malformed():
    parsed = parse_usage({"five_hour": {"resets_at": "x"}, "seven_day": "nope"})
    # No utilization → None; wrong type → None; absent windows → None.
    assert parsed["five_hour"] is None
    assert parsed["seven_day"] is None
    assert parsed["seven_day_opus"] is None


def test_parse_usage_non_dict():
    assert parse_usage(None) == {
        "five_hour": None,
        "seven_day": None,
        "seven_day_opus": None,
        "seven_day_sonnet": None,
    }


def test_read_oauth_token_present(tmp_path, monkeypatch):
    config = tmp_path / "cfg"
    config.mkdir()
    (config / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok-123"}}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    assert read_oauth_token() == "tok-123"


def test_read_oauth_token_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    assert read_oauth_token() is None


def test_read_oauth_token_from_managed_dir(tmp_path, monkeypatch):
    # Token lives only in RCFlow's managed config dir (not env, not ~/.claude).
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "managed-tok"}}), encoding="utf-8"
    )
    assert read_oauth_token([managed]) == "managed-tok"


def test_read_oauth_token_managed_takes_precedence(tmp_path, monkeypatch):
    # Both managed dir and ~/.claude have a token; managed wins (searched first).
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "home-tok"}}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "managed-tok"}}), encoding="utf-8"
    )
    assert read_oauth_token([managed]) == "managed-tok"
    # Falls back to ~/.claude when no managed dir is given.
    assert read_oauth_token() == "home-tok"


def test_read_oauth_token_api_key_worker(tmp_path, monkeypatch):
    # Credentials file exists but has no claudeAiOauth (API-key auth).
    config = tmp_path / "cfg"
    config.mkdir()
    (config / ".credentials.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))  # no ~/.claude fallback
    assert read_oauth_token() is None


def test_read_oauth_token_malformed_json(tmp_path, monkeypatch):
    config = tmp_path / "cfg"
    config.mkdir()
    (config / ".credentials.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))  # no ~/.claude fallback
    assert read_oauth_token() is None


@pytest.mark.asyncio
async def test_fetch_usage_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == USAGE_API_URL
        assert request.headers["Authorization"] == "Bearer tok-123"
        assert request.headers["anthropic-beta"] == usage_service.OAUTH_BETA
        return httpx.Response(200, json=SAMPLE_BODY)

    svc = _mock_service("tok-123", handler)
    try:
        parsed = await svc.fetch_usage()
    finally:
        await svc.aclose()
    assert parsed["five_hour"]["utilization"] == 6.0
    assert parsed["seven_day"]["utilization"] == 45.0


@pytest.mark.asyncio
async def test_fetch_usage_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    svc = _mock_service("bad", handler)
    try:
        with pytest.raises(UsageServiceError) as exc:
            await svc.fetch_usage()
    finally:
        await svc.aclose()
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_fetch_usage_rate_limited_carries_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"}, json={})

    svc = _mock_service("tok", handler)
    try:
        with pytest.raises(UsageServiceError) as exc:
            await svc.fetch_usage()
    finally:
        await svc.aclose()
    assert exc.value.status_code == 429
    assert exc.value.retry_after == 120.0


def test_parse_retry_after():
    assert usage_service._parse_retry_after("30") == 30.0
    assert usage_service._parse_retry_after("  45 ") == 45.0
    # HTTP-date form and junk are ignored (caller falls back to its own backoff).
    assert usage_service._parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert usage_service._parse_retry_after("") is None
    assert usage_service._parse_retry_after(None) is None


@pytest.mark.asyncio
async def test_fetch_usage_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    svc = _mock_service("tok", handler)
    try:
        with pytest.raises(UsageServiceError, match="failed"):
            await svc.fetch_usage()
    finally:
        await svc.aclose()
