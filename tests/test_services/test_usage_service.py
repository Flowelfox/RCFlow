"""Tests for the Claude subscription usage client in src/services/usage_service.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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


def _no_keychain(monkeypatch):
    """Force the macOS Keychain path to be a no-op (tests run on Linux/CI)."""
    monkeypatch.setattr(usage_service.sys, "platform", "linux")


def test_read_oauth_token_from_managed_file(tmp_path, monkeypatch):
    _no_keychain(monkeypatch)
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "managed-tok"}}), encoding="utf-8"
    )
    assert read_oauth_token(managed) == "managed-tok"


def test_read_oauth_token_none_config_dir(monkeypatch):
    _no_keychain(monkeypatch)
    assert read_oauth_token(None) is None


def test_read_oauth_token_missing_file(tmp_path, monkeypatch):
    _no_keychain(monkeypatch)
    assert read_oauth_token(tmp_path / "absent") is None


def test_read_oauth_token_api_key_worker(tmp_path, monkeypatch):
    # File exists but has no claudeAiOauth (API-key auth) → no subscription token.
    _no_keychain(monkeypatch)
    managed = tmp_path / "cfg"
    managed.mkdir()
    (managed / ".credentials.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert read_oauth_token(managed) is None


def test_read_oauth_token_malformed_json(tmp_path, monkeypatch):
    _no_keychain(monkeypatch)
    managed = tmp_path / "cfg"
    managed.mkdir()
    (managed / ".credentials.json").write_text("{not json", encoding="utf-8")
    assert read_oauth_token(managed) is None


def test_keychain_service_name_is_sha256_prefixed():
    config = Path("/Users/me/.local/share/rcflow/tools/claude-code/config")
    digest = hashlib.sha256(str(config).encode()).hexdigest()[:8]
    assert usage_service._keychain_service(config) == f"Claude Code-credentials-{digest}"


def test_read_oauth_token_from_keychain(tmp_path, monkeypatch):
    # macOS: no file, token comes from `security find-generic-password -w`.
    monkeypatch.setattr(usage_service.sys, "platform", "darwin")
    managed = tmp_path / "managed"
    managed.mkdir()  # no .credentials.json

    class _Proc:
        returncode = 0
        stdout = json.dumps({"claudeAiOauth": {"accessToken": "keychain-tok"}})

    def fake_run(argv, **kwargs):
        assert argv[0] == "/usr/bin/security"
        assert usage_service._keychain_service(managed) in argv
        return _Proc()

    monkeypatch.setattr(usage_service.subprocess, "run", fake_run)
    assert read_oauth_token(managed) == "keychain-tok"


def test_read_oauth_token_keychain_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_service.sys, "platform", "darwin")
    managed = tmp_path / "managed"
    managed.mkdir()

    class _Proc:
        returncode = 44  # e.g. item not found / interaction not allowed
        stdout = ""

    monkeypatch.setattr(usage_service.subprocess, "run", lambda *a, **k: _Proc())
    assert read_oauth_token(managed) is None


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
