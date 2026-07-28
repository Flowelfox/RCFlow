"""Tests for the shared one-shot CLI executor base.

``OneShotCliExecutor`` owns the subprocess plumbing that the Codex and OpenCode
executors used to duplicate byte-for-byte (stderr drain, exit logging, teardown).
These tests exercise the base directly so a regression shows up here rather than
twice over in the two subclasses.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.executors.oneshot_cli import OneShotCliExecutor


class _StubExecutor(OneShotCliExecutor):
    """Minimal concrete subclass — the base is abstract on ``_session_ref``."""

    _AGENT_NAME = "stub"
    _SESSION_REF_LABEL = "thread_id"

    def __init__(self) -> None:
        self._process = None
        self._done = False
        self._stderr_output = ""
        self._stderr_task = None
        self._ref = "abc123"

    @property
    def _session_ref(self) -> str | None:
        return self._ref

    async def execute_streaming(self, *args, **kwargs):  # pragma: no cover - unused here
        yield  # type: ignore[misc]


def _process_with_stderr(lines: list[bytes]) -> MagicMock:
    """Build a fake process whose stderr yields *lines* then EOF."""
    process = MagicMock()
    process.returncode = None
    stderr = MagicMock()
    stderr.readline = AsyncMock(side_effect=[*lines, b""])
    process.stderr = stderr
    return process


class TestSessionRefContract:
    def test_base_requires_session_ref(self):
        """The base intentionally refuses to guess a subclass's resume identifier."""
        with pytest.raises(NotImplementedError):
            _ = OneShotCliExecutor._session_ref.fget(_StubExecutor())  # type: ignore[attr-defined]


class TestIsRunning:
    def test_false_without_process(self):
        assert _StubExecutor().is_running is False

    def test_true_while_returncode_is_none(self):
        ex = _StubExecutor()
        ex._process = MagicMock(returncode=None)
        assert ex.is_running is True

    def test_false_after_exit(self):
        ex = _StubExecutor()
        ex._process = MagicMock(returncode=0)
        assert ex.is_running is False


class TestDrainStderr:
    async def test_no_process_is_a_noop(self):
        ex = _StubExecutor()
        await ex._drain_stderr()
        assert ex._stderr_output == ""

    async def test_captures_lines_until_eof(self):
        ex = _StubExecutor()
        ex._process = _process_with_stderr([b"first\n", b"second\n"])

        await ex._drain_stderr()

        assert ex._stderr_output == "first\nsecond\n"

    async def test_output_is_bounded(self):
        """Stderr is drained to avoid a pipe deadlock, so it must not grow without bound.

        A chatty agent can emit megabytes; only the tail is kept for diagnostics.
        """
        ex = _StubExecutor()
        chunk = b"x" * 4096
        ex._process = _process_with_stderr([chunk + b"\n"] * 200)

        await ex._drain_stderr()

        assert len(ex._stderr_output) <= ex._STDERR_MAX_BYTES

    async def test_connection_reset_is_tolerated(self):
        """A killed subprocess can reset the pipe; that is a normal teardown race."""
        ex = _StubExecutor()
        process = MagicMock()
        process.stderr = MagicMock()
        process.stderr.readline = AsyncMock(side_effect=ConnectionResetError)
        ex._process = process

        await ex._drain_stderr()  # must not raise

    async def test_cancellation_propagates(self):
        """Swallowing CancelledError would defeat the lifecycle's task.cancel()."""
        ex = _StubExecutor()
        started = asyncio.Event()

        async def _block() -> bytes:
            started.set()
            await asyncio.sleep(3600)
            return b""

        process = MagicMock()
        process.stderr = MagicMock()
        process.stderr.readline = _block
        ex._process = process

        task = asyncio.create_task(ex._drain_stderr())
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


class TestWaitAndLogExit:
    async def test_no_process_is_a_noop(self):
        await _StubExecutor()._wait_and_log_exit()

    async def test_returns_when_process_already_exited(self):
        ex = _StubExecutor()
        ex._process = MagicMock(returncode=0)
        await ex._wait_and_log_exit()

    async def test_gives_up_when_process_does_not_exit(self):
        """A hung process must not block teardown forever."""
        ex = _StubExecutor()
        process = MagicMock()
        process.returncode = None

        async def _never() -> int:
            await asyncio.sleep(3600)
            return 0

        process.wait = _never
        ex._process = process

        # Patch the module's timeout indirectly by racing it — the call must
        # return rather than hang, so bound the test itself.
        await asyncio.wait_for(ex._wait_and_log_exit(), timeout=5)
