"""Tests for API key verification dependencies.

Covers:
- ``verify_http_api_key`` — header-based auth (HTTPException on failure)
- ``verify_ws_api_key`` — WebSocket auth (WebSocketException on failure)
- ``handle_ws_first_message_auth`` — first-message auth (close on failure)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, WebSocketException, status

from src.api.deps import handle_ws_first_message_auth, verify_http_api_key, verify_ws_api_key

_VALID_KEY = "super-secret-test-key"


def _mock_settings(key: str = _VALID_KEY) -> MagicMock:
    s = MagicMock()
    s.RCFLOW_API_KEY = key
    s.WS_ALLOWED_ORIGINS = ""
    return s


def _mock_websocket() -> MagicMock:
    ws = MagicMock()
    ws.headers = {}
    return ws


# ---------------------------------------------------------------------------
# verify_http_api_key
# ---------------------------------------------------------------------------


class TestVerifyHttpApiKey:
    async def test_valid_key_returns_key(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await verify_http_api_key(api_key=_VALID_KEY)
        assert result == _VALID_KEY

    async def test_wrong_key_raises_401(self) -> None:
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await verify_http_api_key(api_key="wrong-key")
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_empty_key_raises_401(self) -> None:
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await verify_http_api_key(api_key="")
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_partial_key_raises_401(self) -> None:
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException),
        ):
            await verify_http_api_key(api_key=_VALID_KEY[:-1])

    async def test_detail_message_is_set(self) -> None:
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(HTTPException) as exc_info,
        ):
            await verify_http_api_key(api_key="bad")
        assert exc_info.value.detail is not None


# ---------------------------------------------------------------------------
# verify_ws_api_key
# ---------------------------------------------------------------------------


class TestVerifyWsApiKey:
    async def test_valid_key_returns_key(self) -> None:
        ws = _mock_websocket()
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await verify_ws_api_key(websocket=ws, api_key=_VALID_KEY)
        assert result == _VALID_KEY

    async def test_wrong_key_raises_ws_policy_violation(self) -> None:
        ws = _mock_websocket()
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(WebSocketException) as exc_info,
        ):
            await verify_ws_api_key(websocket=ws, api_key="wrong-key")
        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION

    async def test_empty_key_raises_ws_exception(self) -> None:
        ws = _mock_websocket()
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(WebSocketException) as exc_info,
        ):
            await verify_ws_api_key(websocket=ws, api_key="")
        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION

    async def test_partial_key_raises_ws_exception(self) -> None:
        ws = _mock_websocket()
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(WebSocketException),
        ):
            await verify_ws_api_key(websocket=ws, api_key=_VALID_KEY[:5])

    async def test_reason_is_set(self) -> None:
        ws = _mock_websocket()
        with (
            patch("src.api.deps.get_settings", return_value=_mock_settings()),
            pytest.raises(WebSocketException) as exc_info,
        ):
            await verify_ws_api_key(websocket=ws, api_key="bad")
        assert exc_info.value.reason is not None

    async def test_blocked_origin_raises_ws_exception(self) -> None:
        ws = _mock_websocket()
        ws.headers = {"origin": "https://evil.example.com"}
        settings = _mock_settings()
        settings.WS_ALLOWED_ORIGINS = "https://app.example.com"
        with (
            patch("src.api.deps.get_settings", return_value=settings),
            pytest.raises(WebSocketException) as exc_info,
        ):
            await verify_ws_api_key(websocket=ws, api_key=_VALID_KEY)
        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION

    async def test_allowed_origin_passes(self) -> None:
        ws = _mock_websocket()
        ws.headers = {"origin": "https://app.example.com"}
        settings = _mock_settings()
        settings.WS_ALLOWED_ORIGINS = "https://app.example.com"
        with patch("src.api.deps.get_settings", return_value=settings):
            result = await verify_ws_api_key(websocket=ws, api_key=_VALID_KEY)
        assert result == _VALID_KEY


# ---------------------------------------------------------------------------
# handle_ws_first_message_auth
# ---------------------------------------------------------------------------


def _mock_async_websocket() -> MagicMock:
    """Create a mock WebSocket with async close and receive_text methods."""
    ws = MagicMock()
    ws.headers = {}
    ws.close = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


class TestHandleWsFirstMessageAuth:
    async def test_valid_auth_returns_true(self) -> None:
        ws = _mock_async_websocket()
        ws.receive_text.return_value = json.dumps({"type": "auth", "api_key": _VALID_KEY})
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await handle_ws_first_message_auth(ws)
        assert result is True
        ws.close.assert_not_called()

    async def test_wrong_key_closes_socket(self) -> None:
        ws = _mock_async_websocket()
        ws.receive_text.return_value = json.dumps({"type": "auth", "api_key": "wrong-key"})
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await handle_ws_first_message_auth(ws)
        assert result is False
        ws.close.assert_called_once()

    async def test_timeout_closes_socket(self) -> None:
        ws = _mock_async_websocket()
        ws.receive_text.side_effect = TimeoutError()
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await handle_ws_first_message_auth(ws)
        assert result is False
        ws.close.assert_called_once()

    async def test_invalid_json_closes_socket(self) -> None:
        ws = _mock_async_websocket()
        ws.receive_text.return_value = "not json"
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await handle_ws_first_message_auth(ws)
        assert result is False
        ws.close.assert_called_once()

    async def test_wrong_message_type_closes_socket(self) -> None:
        ws = _mock_async_websocket()
        ws.receive_text.return_value = json.dumps({"type": "prompt", "text": "hi"})
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await handle_ws_first_message_auth(ws)
        assert result is False
        ws.close.assert_called_once()

    async def test_blocked_origin_closes_socket(self) -> None:
        ws = _mock_async_websocket()
        ws.headers = {"origin": "https://evil.example.com"}
        settings = _mock_settings()
        settings.WS_ALLOWED_ORIGINS = "https://app.example.com"
        with patch("src.api.deps.get_settings", return_value=settings):
            result = await handle_ws_first_message_auth(ws)
        assert result is False
        ws.close.assert_called_once()
        # Should not even try to receive a message
        ws.receive_text.assert_not_called()

    async def test_allowed_origin_with_valid_key_passes(self) -> None:
        ws = _mock_async_websocket()
        ws.headers = {"origin": "https://app.example.com"}
        ws.receive_text.return_value = json.dumps({"type": "auth", "api_key": _VALID_KEY})
        settings = _mock_settings()
        settings.WS_ALLOWED_ORIGINS = "https://app.example.com"
        with patch("src.api.deps.get_settings", return_value=settings):
            result = await handle_ws_first_message_auth(ws)
        assert result is True

    async def test_no_origin_header_bypasses_origin_check(self) -> None:
        ws = _mock_async_websocket()
        ws.headers = {}
        ws.receive_text.return_value = json.dumps({"type": "auth", "api_key": _VALID_KEY})
        settings = _mock_settings()
        settings.WS_ALLOWED_ORIGINS = "https://app.example.com"
        with patch("src.api.deps.get_settings", return_value=settings):
            result = await handle_ws_first_message_auth(ws)
        assert result is True
