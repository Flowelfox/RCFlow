"""pytest fixtures for backend WebSocket e2e tests.

The ``make_e2e_client`` factory creates a minimal FastAPI test app — **without
the production lifespan** — wired with a ``MockLLMClient``.  This avoids DB
connections, env-var settings, and tool-manager startup.  WS authentication
is bypassed via an autouse fixture so every test runs without API keys.

Tests drive the real WebSocket protocol, real session management, and real
tool execution (ShellExecutor etc.).  Only the LLM step is scripted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.ws.input_text import router as input_text_router
from src.api.ws.output_text import router as output_text_router
from src.config import Settings
from src.core.attachment_store import AttachmentStore
from src.core.llm import LLMStreamEvent, StreamDone, TextChunk
from src.core.prompt_router import PromptRouter
from src.core.session import SessionManager
from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.registry import ToolRegistry
from tests.e2e.mock_llm import MockLLMClient

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.tools.loader import ToolDefinition


class _InProcessShellExecutor(BaseExecutor):
    """Stand-in for ``ShellExecutor`` that never spawns a subprocess.

    The real ShellExecutor spawns processes via ``asyncio.create_subprocess_shell``.
    In e2e tests each ``TestClient`` gets its own anyio portal + event loop, which
    is then torn down between tests. Subprocess transports retain a reference to
    the dead loop and leave global asyncio child-watcher state in a half-broken
    state for subsequent tests — producing a 60 s hang inside ``process.wait()``
    on whichever test happens to land on the poisoned watcher. Avoiding real
    subprocesses entirely sidesteps the whole interaction; the tests exercise the
    WebSocket protocol, not shell semantics.
    """

    def __init__(self) -> None:
        self._cancelled = False

    async def execute(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> ExecutionResult:
        command = str(parameters.get("command", ""))
        return ExecutionResult(output=command, exit_code=0)

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        command = str(parameters.get("command", ""))
        # Strip a leading "echo " so the mock behaves like `echo X → X` and the
        # protocol tests see the same content they asked for without caring
        # about the wrapper.
        output = command[5:] if command.startswith("echo ") else command
        yield ExecutionChunk(stream="stdout", content=f"{output}\n")

    async def send_input(self, data: str) -> None:
        return

    async def cancel(self) -> None:
        self._cancelled = True


# ---------------------------------------------------------------------------
# Autouse: bypass WS authentication for all e2e tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bypass_ws_auth():
    """Patch WS auth functions to no-ops so tests don't need real API keys."""

    async def _noop(websocket: Any = None, api_key: str = "") -> str:
        return api_key

    with (
        patch("src.api.ws.input_text.verify_ws_api_key", new=_noop),
        patch("src.api.ws.input_text.handle_ws_first_message_auth", new=_noop),
        patch("src.api.ws.output_text.verify_ws_api_key", new=_noop),
        patch("src.api.ws.output_text.handle_ws_first_message_auth", new=_noop),
    ):
        yield


# ---------------------------------------------------------------------------
# Shared settings / registry  (module-scoped for speed)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e2e_settings() -> Settings:
    return Settings(
        RCFLOW_HOST="127.0.0.1",
        RCFLOW_PORT=8765,
        RCFLOW_API_KEY="test-api-key",
        DATABASE_URL="sqlite+aiosqlite:///./data/e2e_pytest.db",
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="mock-key",
        ANTHROPIC_MODEL="claude-sonnet-4-6",
        TOOLS_DIR=Path(__file__).parent.parent.parent / "tools",
    )


@pytest.fixture(scope="module")
def e2e_tool_registry(e2e_settings: Settings) -> ToolRegistry:
    registry = ToolRegistry()
    registry.load_from_directory(e2e_settings.TOOLS_DIR)
    return registry


# ---------------------------------------------------------------------------
# App builder (no lifespan → no DB, no env-var settings)
# ---------------------------------------------------------------------------


def _build_e2e_app(
    settings: Settings,
    tool_registry: ToolRegistry,
    turns: list[list[LLMStreamEvent]],
) -> FastAPI:
    """Build a minimal FastAPI app with MockLLMClient, bypassing the lifespan."""
    session_manager = SessionManager("e2e-backend")
    mock_llm = MockLLMClient(turns)

    # Create a plain FastAPI app — no lifespan, no DB, no env-var startup.
    app = FastAPI(title="RCFlow E2E Test App")
    app.include_router(input_text_router)
    app.include_router(output_text_router)

    # Wire up all the state the WS handlers expect
    app.state.settings = settings
    app.state.tool_registry = tool_registry
    app.state.session_manager = session_manager
    app.state.db_session_factory = None
    app.state.attachment_store = AttachmentStore()
    app.state.terminal_manager = None
    prompt_router = PromptRouter(
        mock_llm,
        session_manager,
        tool_registry,
        db_session_factory=None,
        settings=settings,
    )
    # Pre-populate the router's executor cache with a subprocess-free stand-in
    # so _get_executor("shell") never constructs the real ShellExecutor. See
    # _InProcessShellExecutor for the asyncio lifecycle reason.
    prompt_router._executors["shell"] = _InProcessShellExecutor()
    app.state.prompt_router = prompt_router

    return app


# ---------------------------------------------------------------------------
# Client factory fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def make_e2e_client(e2e_settings: Settings, e2e_tool_registry: ToolRegistry):
    """Factory fixture — call with a list of scripted LLM turns.

    Example::

        def test_something(make_e2e_client):
            client = make_e2e_client([
                [TextChunk("Hi!"), StreamDone("end_turn")],
            ])
    """

    def _factory(turns: list[list[LLMStreamEvent]] | None = None) -> TestClient:
        _turns: list[list[LLMStreamEvent]] = turns or [
            [TextChunk(content="Hello!"), StreamDone(stop_reason="end_turn")]
        ]
        app = _build_e2e_app(e2e_settings, e2e_tool_registry, _turns)
        return TestClient(app, raise_server_exceptions=True)

    return _factory
