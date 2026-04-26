"""Tests for the hot-reload path on PATCH /api/config.

Guards the regression where switching ``LLM_PROVIDER`` to ``"none"`` crashed
the worker because ``_reload_components`` unconditionally constructed
``LLMClient``, which rejects ``"none"`` with ``ValueError``.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.config import _reload_components


def _make_request(provider: str, old_llm_client: object | None = None) -> SimpleNamespace:
    """Build a minimal Request-shaped object with the attributes the fn touches."""
    settings = MagicMock()
    settings.LLM_PROVIDER = provider
    prompt_router = SimpleNamespace(_llm=old_llm_client, _settings=None)
    app = SimpleNamespace(
        state=SimpleNamespace(
            tool_registry=MagicMock(),
            llm_client=old_llm_client,
            prompt_router=prompt_router,
        ),
    )
    return SimpleNamespace(app=app), settings


def test_reload_skips_llm_construction_when_provider_is_none() -> None:
    """Switching to LLM_PROVIDER=none must null the llm_client, not raise."""
    old = MagicMock()
    old.close = AsyncMock()
    request, settings = _make_request("none", old_llm_client=old)
    with patch("src.api.routes.config.LLMClient") as mock_llm:
        _reload_components(request, settings)  # ty:ignore[invalid-argument-type]
    mock_llm.assert_not_called()
    assert request.app.state.llm_client is None
    assert request.app.state.prompt_router._llm is None
    assert request.app.state.prompt_router._settings is settings


def test_reload_constructs_llm_when_provider_is_anthropic() -> None:
    request, settings = _make_request("anthropic", old_llm_client=None)
    with patch("src.api.routes.config.LLMClient") as mock_llm:
        mock_llm.return_value = "fake_llm_client"
        _reload_components(request, settings)  # ty:ignore[invalid-argument-type]
    mock_llm.assert_called_once()
    assert request.app.state.llm_client == "fake_llm_client"
    assert request.app.state.prompt_router._llm == "fake_llm_client"


@pytest.mark.asyncio
async def test_reload_closes_old_llm_client_on_swap() -> None:
    """The previous client's ``close`` task must be scheduled on swap."""
    close_called = asyncio.Event()

    class _FakeOldLLM:
        async def close(self) -> None:
            close_called.set()

    request, settings = _make_request("none", old_llm_client=_FakeOldLLM())
    with patch("src.api.routes.config.LLMClient"):
        _reload_components(request, settings)  # ty:ignore[invalid-argument-type]
    await asyncio.wait_for(close_called.wait(), timeout=1.0)
