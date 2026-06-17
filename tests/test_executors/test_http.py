"""Tests for HttpExecutor (network mocked)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.executors.http import (
    HttpExecutor,
    _extract_json_path,
    _is_blocked_ip,
    _substitute_env_vars,
    _validate_url_no_ssrf,
)
from src.tools.loader import ToolDefinition

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _http_tool(**http_config: Any) -> ToolDefinition:
    config: dict[str, Any] = {
        "method": "GET",
        "url_template": "https://example.com/api",
    }
    config.update(http_config)
    return ToolDefinition(
        name="http_tool",
        description="An http tool",
        version="1.0.0",
        session_type="one-shot",
        llm_context="stateless",
        executor="http",
        parameters={"type": "object", "properties": {}},
        executor_config={"http": config},
    )


@pytest.fixture
def executor() -> HttpExecutor:
    return HttpExecutor()


@pytest.fixture(autouse=True)
def _no_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real DNS/SSRF check in most tests so we don't hit the network."""

    async def _noop(url: str) -> None:
        return None

    monkeypatch.setattr("src.executors.http._validate_url_no_ssrf", _noop)


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


class _FakeClient:
    """Stand-in for httpx.AsyncClient used as an async context manager."""

    def __init__(self, response: _FakeResponse | Exception, *, timeout: int | None = None) -> None:
        self._response = response
        self.timeout = timeout
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def request(self, *, method: str, url: str, headers: dict[str, str], content: Any) -> _FakeResponse:
        self.requests.append({"method": method, "url": url, "headers": headers, "content": content})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def aclose(self) -> None:
        self.closed = True


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    def factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client.timeout = kwargs.get("timeout")
        return client

    monkeypatch.setattr("src.executors.http.httpx.AsyncClient", factory)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_substitute_env_var_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN", "secret")
        assert _substitute_env_vars("Bearer ${MY_TOKEN}") == "Bearer secret"

    def test_substitute_env_var_missing_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOPE_VAR", raising=False)
        assert _substitute_env_vars("x=${NOPE_VAR}") == "x="

    def test_substitute_no_placeholder(self) -> None:
        assert _substitute_env_vars("plain text") == "plain text"

    def test_extract_json_path_nested(self) -> None:
        data = {"data": {"summary": "hi"}}
        assert _extract_json_path(data, "$.data.summary") == "hi"

    def test_extract_json_path_list_index(self) -> None:
        data = {"items": [{"v": 1}, {"v": 2}]}
        assert _extract_json_path(data, "$.items.1.v") == 2

    def test_extract_json_path_into_scalar_raises(self) -> None:
        with pytest.raises(KeyError):
            _extract_json_path({"a": 5}, "$.a.b")

    def test_is_blocked_ip_private(self) -> None:
        assert _is_blocked_ip("10.0.0.1") is True
        assert _is_blocked_ip("127.0.0.1") is True
        assert _is_blocked_ip("169.254.169.254") is True

    def test_is_blocked_ip_public(self) -> None:
        assert _is_blocked_ip("8.8.8.8") is False

    def test_is_blocked_ip_unparseable_fails_closed(self) -> None:
        assert _is_blocked_ip("not-an-ip") is True


# ---------------------------------------------------------------------------
# SSRF validation (DNS mocked)
# ---------------------------------------------------------------------------


class TestSsrf:
    # The autouse ``_no_ssrf`` fixture patches the module attribute, but these
    # tests import and call ``_validate_url_no_ssrf`` directly (a separate name
    # binding in this module), so the real implementation runs here.

    async def test_no_hostname_raises(self) -> None:
        with pytest.raises(ValueError, match="no hostname"):
            await _validate_url_no_ssrf("not a url")

    async def test_public_address_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_getaddrinfo(host: str, port: int, *a: Any) -> list[Any]:
            return [(None, None, None, "", ("93.184.216.34", port))]

        monkeypatch.setattr("src.executors.http.socket.getaddrinfo", fake_getaddrinfo)
        await _validate_url_no_ssrf("https://example.com/api")

    async def test_private_address_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_getaddrinfo(host: str, port: int, *a: Any) -> list[Any]:
            return [(None, None, None, "", ("127.0.0.1", port))]

        monkeypatch.setattr("src.executors.http.socket.getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="blocked"):
            await _validate_url_no_ssrf("http://localhost/api")

    async def test_dns_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_getaddrinfo(host: str, port: int, *a: Any) -> list[Any]:
            raise OSError("nope")

        monkeypatch.setattr("src.executors.http.socket.getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="DNS resolution failed"):
            await _validate_url_no_ssrf("https://example.com/api")


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------


class TestExecute:
    async def test_success_returns_text(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse("hello world", 200))
        _patch_client(monkeypatch, client)
        result = await executor.execute(_http_tool(), {})
        assert result.exit_code == 0
        assert result.output == "hello world"
        assert result.error is None
        assert result.metadata["status_code"] == 200
        assert client.timeout == 30

    async def test_url_and_body_templating(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse("ok"))
        _patch_client(monkeypatch, client)
        tool = _http_tool(
            method="POST",
            url_template="https://example.com/items/{item_id}",
            body_template='{{"q": "{query}"}}',
            headers={"X-Key": "static"},
        )
        await executor.execute(tool, {"item_id": "42", "query": "frob"})
        req = client.requests[0]
        assert req["method"] == "POST"
        assert req["url"] == "https://example.com/items/42"
        assert req["content"] == '{"q": "frob"}'
        assert req["headers"]["X-Key"] == "static"

    async def test_header_env_substitution(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_TOKEN", "abc123")
        client = _FakeClient(_FakeResponse("ok"))
        _patch_client(monkeypatch, client)
        tool = _http_tool(headers={"Authorization": "Bearer ${API_TOKEN}"})
        await executor.execute(tool, {})
        assert client.requests[0]["headers"]["Authorization"] == "Bearer abc123"

    async def test_response_path_extraction(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse('{"data": {"summary": "extracted"}}'))
        _patch_client(monkeypatch, client)
        tool = _http_tool(response_path="$.data.summary")
        result = await executor.execute(tool, {})
        assert result.output == "extracted"

    async def test_response_path_failure_returns_full_body(
        self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(_FakeResponse("not json"))
        _patch_client(monkeypatch, client)
        tool = _http_tool(response_path="$.data.summary")
        result = await executor.execute(tool, {})
        assert result.output == "not json"

    async def test_non_2xx_sets_error(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse("server error", 500))
        _patch_client(monkeypatch, client)
        result = await executor.execute(_http_tool(), {})
        assert result.exit_code == 500
        assert result.error == "HTTP 500"

    async def test_timeout_returns_minus_one(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(httpx.TimeoutException("slow"))
        _patch_client(monkeypatch, client)
        result = await executor.execute(_http_tool(timeout=5), {})
        assert result.exit_code == -1
        assert "timed out" in (result.error or "")

    async def test_generic_exception_returns_minus_one(
        self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(RuntimeError("boom"))
        _patch_client(monkeypatch, client)
        result = await executor.execute(_http_tool(), {})
        assert result.exit_code == -1
        assert result.error == "boom"

    async def test_client_cleared_after_execute(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(_FakeResponse("ok"))
        _patch_client(monkeypatch, client)
        await executor.execute(_http_tool(), {})
        assert executor._client is None


# ---------------------------------------------------------------------------
# execute_streaming()
# ---------------------------------------------------------------------------


class _StreamResponse:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _StreamResponse:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def aiter_text(self):
        for c in self._chunks:
            yield c


class _StreamClient:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.stream_args: tuple[Any, ...] | None = None
        self.stream_kwargs: dict[str, Any] | None = None

    async def __aenter__(self) -> _StreamClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def stream(self, method: str, url: str, **kwargs: Any) -> _StreamResponse:
        self.stream_args = (method, url)
        self.stream_kwargs = kwargs
        return _StreamResponse(self._chunks)


class TestStreaming:
    async def test_streaming_yields_chunks(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _StreamClient(["part1", "part2"])
        monkeypatch.setattr("src.executors.http.httpx.AsyncClient", lambda *a, **k: client)
        chunks = [c async for c in executor.execute_streaming(_http_tool(), {})]
        assert [c.content for c in chunks] == ["part1", "part2"]
        assert all(c.stream == "response" for c in chunks)
        assert client.stream_args == ("GET", "https://example.com/api")

    async def test_streaming_with_body(self, executor: HttpExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _StreamClient(["x"])
        monkeypatch.setattr("src.executors.http.httpx.AsyncClient", lambda *a, **k: client)
        tool = _http_tool(method="POST", body_template='{{"a": {n}}}')
        _ = [c async for c in executor.execute_streaming(tool, {"n": "1"})]
        assert client.stream_kwargs is not None
        assert client.stream_kwargs["content"] == '{"a": 1}'


# ---------------------------------------------------------------------------
# send_input / cancel
# ---------------------------------------------------------------------------


class TestInputCancel:
    async def test_send_input_raises(self, executor: HttpExecutor) -> None:
        with pytest.raises(RuntimeError, match="does not support"):
            await executor.send_input("data")

    async def test_cancel_with_no_client_is_noop(self, executor: HttpExecutor) -> None:
        await executor.cancel()
        assert executor._client is None

    async def test_cancel_closes_client(self, executor: HttpExecutor) -> None:
        fake = _FakeClient(_FakeResponse("ok"))
        executor._client = fake  # type: ignore[assignment]
        await executor.cancel()
        assert fake.closed is True
        assert executor._client is None
