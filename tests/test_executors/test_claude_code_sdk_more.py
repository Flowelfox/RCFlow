"""Lifecycle / streaming coverage for ClaudeCodeSdkExecutor with a mocked SDK client."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, UserMessage

from src.executors.claude_code_sdk import _STREAM_END, ClaudeCodeSdkExecutor
from src.tools.loader import ToolDefinition


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model="claude-opus-4-8")


def _result(**kw: Any) -> ResultMessage:
    base = dict(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="done",
    )
    base.update(kw)
    return ResultMessage(**base)  # type: ignore[arg-type]


class FakeSDKClient:
    """Minimal stand-in for ClaudeSDKClient.

    Each ``query`` enqueues a scripted list of messages onto an internal asyncio
    queue; ``receive_messages`` is an infinite async generator draining it (the
    real client's stream never ends on its own).
    """

    def __init__(self, options: Any = None) -> None:
        self.options = options
        self.connected = False
        self.interrupted = False
        self.disconnected = False
        self.queries: list[str] = []
        self._mq: asyncio.Queue[Any] = asyncio.Queue()
        # Scripted responses keyed by query order.
        self.scripts: list[list[Any]] = []

    async def connect(self) -> None:
        self.connected = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        script = self.scripts.pop(0) if self.scripts else [_result()]
        for msg in script:
            await self._mq.put(msg)

    async def receive_messages(self):
        while True:
            msg = await self._mq.get()
            yield msg

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch) -> FakeSDKClient:
    holder: dict[str, FakeSDKClient] = {}

    def factory(options: Any = None) -> FakeSDKClient:
        client = FakeSDKClient(options)
        holder["client"] = client
        return client

    monkeypatch.setattr("src.executors.claude_code_sdk.ClaudeSDKClient", factory)
    # Created lazily on first connect; expose via attribute access in tests.
    return holder  # type: ignore[return-value]


@pytest.fixture
def tool() -> ToolDefinition:
    return ToolDefinition(
        name="claude_code",
        description="Claude Code",
        version="1.0.0",
        session_type="long-running",
        llm_context="session-scoped",
        executor="claude_code",
        parameters={"type": "object", "properties": {}},
        executor_config={"claude_code": {"binary_path": "claude"}},
    )


# ---------------------------------------------------------------------------
# Options mapping (no client)
# ---------------------------------------------------------------------------


class TestOptionsMapping:
    def test_resolve_cwd_from_params(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        assert ex._resolve_cwd({"working_directory": "/work"}) == "/work"

    def test_resolve_cwd_falls_back_to_stored(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        ex._cwd = "/stored"
        assert ex._resolve_cwd({}) == "/stored"

    def test_build_options_basic(self) -> None:
        ex = ClaudeCodeSdkExecutor(binary_path="/bin/claude", config_overrides={"model": "m1", "max_turns": 7})
        opts = ex._build_options({"working_directory": "/w"}, resume=None)
        assert opts.cli_path == "/bin/claude"
        assert opts.model == "m1"
        assert opts.max_turns == 7
        assert opts.cwd == "/w"
        assert opts.permission_mode == "default"
        # Fresh session pins the session id via extra_args.
        assert opts.extra_args == {"session-id": ex.session_id}

    def test_build_options_resume_omits_session_id(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        opts = ex._build_options({}, resume="prev-session")
        assert opts.resume == "prev-session"
        assert opts.extra_args == {}

    def test_param_model_overrides_config(self) -> None:
        ex = ClaudeCodeSdkExecutor(config_overrides={"model": "cfg"})
        opts = ex._build_options({"model": "param"}, resume=None)
        assert opts.model == "param"

    def test_allowed_tools_csv_parsed(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        opts = ex._build_options({"allowed_tools": "Bash, Read ,"}, resume=None)
        assert opts.allowed_tools == ["Bash", "Read"]

    def test_allowed_tools_list_passthrough(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        opts = ex._build_options({"allowed_tools": ["Edit"]}, resume=None)
        assert opts.allowed_tools == ["Edit"]

    def test_allowed_tools_default_empty(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        opts = ex._build_options({}, resume=None)
        assert opts.allowed_tools == []


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_session_id_generated(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        assert isinstance(ex.session_id, str) and ex.session_id

    def test_session_id_explicit(self) -> None:
        ex = ClaudeCodeSdkExecutor(session_id="abc")
        assert ex.session_id == "abc"

    def test_is_running_false_initially(self) -> None:
        assert ClaudeCodeSdkExecutor().is_running is False

    def test_got_result_and_exit_code_initial(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        assert ex.got_result is False
        assert ex.exit_code is None

    def test_set_can_use_tool(self) -> None:
        ex = ClaudeCodeSdkExecutor()

        async def cb(*a: Any) -> Any:  # pragma: no cover - never invoked
            return None

        ex.set_can_use_tool(cb)
        assert ex._can_use_tool is cb


# ---------------------------------------------------------------------------
# Streaming lifecycle
# ---------------------------------------------------------------------------


class TestStreaming:
    async def test_execute_streaming_full_turn(
        self, patched_client: dict[str, FakeSDKClient], tool: ToolDefinition
    ) -> None:
        ex = ClaudeCodeSdkExecutor()
        chunks = [c async for c in ex.execute_streaming(tool, {"prompt": "hi"})]
        # Default script is a single result message.
        events = [json.loads(c.content) for c in chunks]
        assert events[-1]["type"] == "result"
        assert ex.got_result is True
        assert ex.exit_code == 0
        assert ex.is_running is True
        await ex.cancel()

    async def test_streaming_assistant_then_result(
        self, patched_client: dict[str, FakeSDKClient], tool: ToolDefinition, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ex = ClaudeCodeSdkExecutor()

        def factory(options: Any = None) -> FakeSDKClient:
            client = FakeSDKClient(options)
            client.scripts = [[_assistant("working"), _result(result="ok")]]
            patched_client["client"] = client
            return client

        monkeypatch.setattr("src.executors.claude_code_sdk.ClaudeSDKClient", factory)
        events = [json.loads(c.content) async for c in ex.execute_streaming(tool, {"prompt": "hi"})]
        types = [e["type"] for e in events]
        assert types == ["assistant", "result"]
        await ex.cancel()

    async def test_execute_collects_output(
        self, patched_client: dict[str, FakeSDKClient], tool: ToolDefinition, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ex = ClaudeCodeSdkExecutor()

        def factory(options: Any = None) -> FakeSDKClient:
            client = FakeSDKClient(options)
            client.scripts = [[_assistant("a"), _result(result="r")]]
            patched_client["client"] = client
            return client

        monkeypatch.setattr("src.executors.claude_code_sdk.ClaudeSDKClient", factory)
        result = await ex.execute(tool, {"prompt": "go"})
        assert result.exit_code == 0
        assert "assistant" in result.output
        assert "result" in result.output
        await ex.cancel()

    async def test_restart_reuses_live_client(
        self, patched_client: dict[str, FakeSDKClient], tool: ToolDefinition
    ) -> None:
        ex = ClaudeCodeSdkExecutor()
        async for _ in ex.execute_streaming(tool, {"prompt": "first"}):
            pass
        client = patched_client["client"]
        # Live client -> restart should not create a new one (resume=None path).
        async for _ in ex.restart_with_prompt("second"):
            pass
        assert patched_client["client"] is client
        assert client.queries == ["first", "second"]
        await ex.cancel()

    async def test_stream_end_marks_disconnected(self, tool: ToolDefinition, monkeypatch: pytest.MonkeyPatch) -> None:
        ex = ClaudeCodeSdkExecutor()

        class EndingClient(FakeSDKClient):
            async def receive_messages(self):
                # End immediately without producing a result.
                if False:
                    yield None
                return

        monkeypatch.setattr("src.executors.claude_code_sdk.ClaudeSDKClient", lambda options=None: EndingClient(options))
        chunks = [c async for c in ex.execute_streaming(tool, {"prompt": "x"})]
        assert chunks == []
        assert ex.is_running is False
        await ex.cancel()


# ---------------------------------------------------------------------------
# read_more_events
# ---------------------------------------------------------------------------


class TestReadMoreEvents:
    async def test_no_queue_returns_immediately(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        assert [c async for c in ex.read_more_events()] == []

    async def test_yields_only_user_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.executors.claude_code_sdk._DRAIN_IDLE_TIMEOUT", 0.05)
        ex = ClaudeCodeSdkExecutor()
        ex._queue = asyncio.Queue()
        await ex._queue.put(_assistant("ignored"))
        await ex._queue.put(UserMessage(content=[ToolResultBlock(tool_use_id="t", content="ok", is_error=False)]))
        events = [json.loads(c.content) async for c in ex.read_more_events()]
        assert [e["type"] for e in events] == ["user"]

    async def test_include_assistant_yields_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.executors.claude_code_sdk._DRAIN_IDLE_TIMEOUT", 0.05)
        ex = ClaudeCodeSdkExecutor()
        ex._queue = asyncio.Queue()
        await ex._queue.put(_assistant("seen"))
        events = [json.loads(c.content) async for c in ex.read_more_events(include_assistant=True)]
        assert events[0]["type"] == "assistant"

    async def test_stream_end_sentinel_stops(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        ex._queue = asyncio.Queue()
        ex._connected = True
        await ex._queue.put(_STREAM_END)
        events = [c async for c in ex.read_more_events()]
        assert events == []
        assert ex._connected is False


# ---------------------------------------------------------------------------
# send_input
# ---------------------------------------------------------------------------


class TestSendInput:
    async def test_send_input_no_session_raises(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        with pytest.raises(RuntimeError, match="No live"):
            await ex.send_input("hello")

    async def test_send_input_queries_live_client(
        self, patched_client: dict[str, FakeSDKClient], tool: ToolDefinition
    ) -> None:
        ex = ClaudeCodeSdkExecutor()
        async for _ in ex.execute_streaming(tool, {"prompt": "first"}):
            pass
        await ex.send_input("follow up")
        assert "follow up" in patched_client["client"].queries
        await ex.cancel()


# ---------------------------------------------------------------------------
# Lifecycle / disconnect
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_disconnect_with_no_client_is_safe(self) -> None:
        ex = ClaudeCodeSdkExecutor()
        await ex.stop_process()  # no client; must not raise
        assert ex.is_running is False

    async def test_cancel_interrupts_and_disconnects(
        self, patched_client: dict[str, FakeSDKClient], tool: ToolDefinition
    ) -> None:
        ex = ClaudeCodeSdkExecutor()
        async for _ in ex.execute_streaming(tool, {"prompt": "x"}):
            pass
        client = patched_client["client"]
        await ex.cancel()
        assert client.interrupted is True
        assert client.disconnected is True
        assert ex.is_running is False
        assert ex._client is None

    async def test_disconnect_suppresses_interrupt_error(
        self, tool: ToolDefinition, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ex = ClaudeCodeSdkExecutor()

        class BadClient(FakeSDKClient):
            async def interrupt(self) -> None:
                raise RuntimeError("interrupt failed")

            async def disconnect(self) -> None:
                raise RuntimeError("disconnect failed")

        monkeypatch.setattr("src.executors.claude_code_sdk.ClaudeSDKClient", lambda options=None: BadClient(options))
        async for _ in ex.execute_streaming(tool, {"prompt": "x"}):
            pass
        # Both errors are swallowed.
        await ex.cancel()
        assert ex._client is None
        assert ex.is_running is False
