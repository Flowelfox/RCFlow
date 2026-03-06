"""Terminal session manager — tracks all active PTY sessions."""

import logging
from collections.abc import Awaitable, Callable

from src.terminal.session import PTYSession

logger = logging.getLogger(__name__)

MAX_TERMINALS_PER_CONNECTION = 10


class TerminalSessionManager:
    """Manages all active PTY terminal sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, PTYSession] = {}

    async def create_session(
        self,
        terminal_id: str,
        cols: int = 80,
        rows: int = 24,
        shell: str | None = None,
        cwd: str | None = None,
        on_output: Callable[[bytes], Awaitable[None]] | None = None,
        on_exit: Callable[[int | None], Awaitable[None]] | None = None,
    ) -> PTYSession:
        """Create and start a new PTY session."""
        if terminal_id in self._sessions:
            raise ValueError(f"Terminal {terminal_id} already exists")

        session = PTYSession(
            terminal_id=terminal_id,
            cols=cols,
            rows=rows,
            shell=shell,
            cwd=cwd,
            on_output=on_output,
            on_exit=on_exit,
        )
        await session.start()
        self._sessions[terminal_id] = session
        return session

    def get_session(self, terminal_id: str) -> PTYSession | None:
        return self._sessions.get(terminal_id)

    async def close_session(self, terminal_id: str) -> None:
        session = self._sessions.pop(terminal_id, None)
        if session:
            await session.close()

    async def close_all(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()

    @property
    def active_count(self) -> int:
        return len(self._sessions)
