"""Tests for API key verification dependencies.

Covers:
- ``hash_api_key`` — SHA-256 helper
- ``verify_http_api_key`` — header-based auth (HTTPException on failure)
- ``verify_ws_api_key`` — query-param auth (WebSocketException on failure)
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, WebSocketException, status

from src.api.deps import hash_api_key, verify_http_api_key, verify_ws_api_key

_VALID_KEY = "super-secret-test-key"


def _mock_settings(key: str = _VALID_KEY) -> MagicMock:
    s = MagicMock()
    s.RCFLOW_API_KEY = key
    return s


# ---------------------------------------------------------------------------
# hash_api_key
# ---------------------------------------------------------------------------


class TestHashApiKey:
    def test_returns_sha256_hex_digest(self) -> None:
        import hashlib

        expected = hashlib.sha256(_VALID_KEY.encode()).hexdigest()
        assert hash_api_key(_VALID_KEY) == expected

    def test_different_inputs_produce_different_hashes(self) -> None:
        assert hash_api_key("key-a") != hash_api_key("key-b")

    def test_same_input_is_deterministic(self) -> None:
        assert hash_api_key(_VALID_KEY) == hash_api_key(_VALID_KEY)


# ---------------------------------------------------------------------------
# verify_http_api_key
# ---------------------------------------------------------------------------


class TestVerifyHttpApiKey:
    async def test_valid_key_returns_key(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await verify_http_api_key(api_key=_VALID_KEY)
        assert result == _VALID_KEY

    async def test_wrong_key_raises_401(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(HTTPException) as exc_info:
                await verify_http_api_key(api_key="wrong-key")
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_empty_key_raises_401(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(HTTPException) as exc_info:
                await verify_http_api_key(api_key="")
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_partial_key_raises_401(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(HTTPException):
                await verify_http_api_key(api_key=_VALID_KEY[:-1])

    async def test_detail_message_is_set(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(HTTPException) as exc_info:
                await verify_http_api_key(api_key="bad")
        assert exc_info.value.detail is not None


# ---------------------------------------------------------------------------
# verify_ws_api_key
# ---------------------------------------------------------------------------


class TestVerifyWsApiKey:
    async def test_valid_key_returns_key(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            result = await verify_ws_api_key(api_key=_VALID_KEY)
        assert result == _VALID_KEY

    async def test_wrong_key_raises_ws_policy_violation(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(WebSocketException) as exc_info:
                await verify_ws_api_key(api_key="wrong-key")
        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION

    async def test_empty_key_raises_ws_exception(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(WebSocketException) as exc_info:
                await verify_ws_api_key(api_key="")
        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION

    async def test_partial_key_raises_ws_exception(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(WebSocketException):
                await verify_ws_api_key(api_key=_VALID_KEY[:5])

    async def test_reason_is_set(self) -> None:
        with patch("src.api.deps.get_settings", return_value=_mock_settings()):
            with pytest.raises(WebSocketException) as exc_info:
                await verify_ws_api_key(api_key="bad")
        assert exc_info.value.reason is not None
