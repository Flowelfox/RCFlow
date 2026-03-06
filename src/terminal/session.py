"""PTY session management for interactive terminal sessions.

Cross-platform: uses native pty/fork on Unix, pywinpty (ConPTY) on Windows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import struct
import sys
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# --- Platform-specific imports (deferred to avoid ImportError on wrong OS) ---

if _IS_WINDOWS:
    import winpty as _winpty  # pywinpty
else:
    import fcntl
    import pty
    import signal
    import termios

# ---------------------------------------------------------------------------
# Shell validation
# ---------------------------------------------------------------------------

_FALLBACK_SHELLS = frozenset({
    "/bin/bash",
    "/bin/sh",
    "/bin/zsh",
    "/bin/fish",
    "/usr/bin/bash",
    "/usr/bin/zsh",
    "/usr/bin/fish",
    "/usr/local/bin/bash",
    "/usr/local/bin/zsh",
    "/usr/local/bin/fish",
})


def _read_etc_shells() -> set[str]:
    """Read valid shells from /etc/shells."""
    try:
        with open("/etc/shells") as f:
            return {
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            }
    except OSError:
        return set()


def validate_shell(shell: str) -> str:
    """Validate and resolve a shell path. Returns the resolved path."""
    if _IS_WINDOWS:
        resolved = shutil.which(shell) or shell
        if not resolved or not os.path.isfile(resolved):
            raise ValueError(f"Shell not found: {shell}")
        return resolved

    resolved = shutil.which(shell) or shell
    valid = _read_etc_shells() | _FALLBACK_SHELLS
    if resolved not in valid:
        raise ValueError(f"Shell not allowed: {shell}")
    return resolved


def _default_shell() -> str:
    """Return the default shell for the current platform."""
    if _IS_WINDOWS:
        # Prefer PowerShell, fall back to cmd.exe
        ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if ps:
            return ps
        return os.environ.get("COMSPEC", "cmd.exe")
    return os.environ.get("SHELL", "/bin/bash")


# ---------------------------------------------------------------------------
# PTYSession — cross-platform
# ---------------------------------------------------------------------------


class PTYSession:
    """Manages a single PTY process for an interactive terminal session."""

    def __init__(
        self,
        terminal_id: str,
        cols: int = 80,
        rows: int = 24,
        shell: str | None = None,
        cwd: str | None = None,
        on_output: Callable[[bytes], Awaitable[None]] | None = None,
        on_exit: Callable[[int | None], Awaitable[None]] | None = None,
    ) -> None:
        self.terminal_id = terminal_id
        self.cols = cols
        self.rows = rows
        self.cwd = cwd or os.path.expanduser("~")
        self._on_output = on_output
        self._on_exit = on_exit
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

        # Validate shell
        self.shell = validate_shell(shell or _default_shell())

        # Validate cwd
        if not os.path.isdir(self.cwd):
            raise ValueError(f"Working directory does not exist: {self.cwd}")

        # Platform-specific handles
        if _IS_WINDOWS:
            self._pty_process: _winpty.PtyProcess | None = None
        else:
            self._master_fd: int | None = None
            self._pid: int | None = None

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the PTY and shell process."""
        if _IS_WINDOWS:
            await self._start_windows()
        else:
            await self._start_unix()

    async def _start_windows(self) -> None:
        """Spawn a ConPTY process via pywinpty."""
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        self._pty_process = _winpty.PtyProcess.spawn(
            self.shell,
            cwd=self.cwd,
            dimensions=(self.rows, self.cols),
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_output_windows())
        logger.info(
            "Started PTY session %s (pid=%d, shell=%s)",
            self.terminal_id,
            self._pty_process.pid,
            self.shell,
        )

    async def _start_unix(self) -> None:
        """Spawn a PTY process using fork/exec."""
        master_fd, slave_fd = pty.openpty()
        self._set_winsize(master_fd, self.rows, self.cols)

        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)

            os.environ["TERM"] = "xterm-256color"
            os.environ["COLORTERM"] = "truecolor"

            try:
                os.chdir(self.cwd)
            except OSError:
                pass

            os.execvp(self.shell, [self.shell])
        else:
            # Parent process
            os.close(slave_fd)
            self._master_fd = master_fd
            self._pid = pid
            os.set_blocking(master_fd, False)
            self._reader_task = asyncio.create_task(self._read_output_unix())
            logger.info(
                "Started PTY session %s (pid=%d, shell=%s)",
                self.terminal_id,
                pid,
                self.shell,
            )

    # ------------------------------------------------------------------
    # Output readers
    # ------------------------------------------------------------------

    async def _read_output_unix(self) -> None:
        """Read PTY output on Unix and forward to callback."""
        loop = asyncio.get_event_loop()
        try:
            while not self._closed and self._master_fd is not None:
                try:
                    data = await loop.run_in_executor(None, self._blocking_read_unix)
                    if data is None:
                        break
                    if not data:
                        continue
                    if self._on_output:
                        await self._on_output(data)
                except OSError:
                    break
        finally:
            exit_code = await self._wait_for_exit_unix()
            logger.info(
                "PTY session %s exited (code=%s)", self.terminal_id, exit_code
            )
            if self._on_exit and not self._closed:
                await self._on_exit(exit_code)

    def _blocking_read_unix(self) -> bytes | None:
        """Read from PTY master fd (called in executor thread)."""
        if self._master_fd is None or self._closed:
            return None
        try:
            import select as sel

            readable, _, _ = sel.select([self._master_fd], [], [], 0.5)
            if not readable:
                return b"" if not self._closed else None
            return os.read(self._master_fd, 65536)
        except OSError:
            return None

    async def _wait_for_exit_unix(self) -> int | None:
        """Wait for the child process to exit (Unix)."""
        if self._pid is None:
            return None
        try:
            _, status = await asyncio.get_event_loop().run_in_executor(
                None, os.waitpid, self._pid, 0
            )
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            return -1
        except ChildProcessError:
            return None

    async def _read_output_windows(self) -> None:
        """Read ConPTY output on Windows and forward to callback."""
        loop = asyncio.get_event_loop()
        try:
            while not self._closed and self._pty_process is not None:
                try:
                    data = await loop.run_in_executor(None, self._blocking_read_windows)
                    if data is None:
                        break
                    if not data:
                        continue
                    if self._on_output:
                        await self._on_output(data)
                except (OSError, EOFError):
                    break
        finally:
            exit_code = self._get_exitstatus_windows()
            logger.info(
                "PTY session %s exited (code=%s)", self.terminal_id, exit_code
            )
            if self._on_exit and not self._closed:
                await self._on_exit(exit_code)

    def _blocking_read_windows(self) -> bytes | None:
        """Read from ConPTY (called in executor thread)."""
        if self._pty_process is None or self._closed:
            return None
        try:
            if not self._pty_process.isalive():
                return None
            data = self._pty_process.read(65536)
            if not data:
                return b"" if self._pty_process.isalive() else None
            return data.encode("utf-8") if isinstance(data, str) else data
        except (EOFError, OSError):
            return None

    def _get_exitstatus_windows(self) -> int | None:
        """Get exit status of the ConPTY process."""
        if self._pty_process is None:
            return None
        try:
            if hasattr(self._pty_process, "exitstatus"):
                return self._pty_process.exitstatus
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write(self, data: bytes) -> None:
        """Write input data to the PTY."""
        if self._closed:
            return
        if _IS_WINDOWS:
            if self._pty_process is not None:
                try:
                    text = data.decode("utf-8", errors="replace")
                    self._pty_process.write(text)
                except OSError as e:
                    logger.warning("Failed to write to PTY %s: %s", self.terminal_id, e)
        else:
            if self._master_fd is not None:
                try:
                    os.write(self._master_fd, data)
                except OSError as e:
                    logger.warning("Failed to write to PTY %s: %s", self.terminal_id, e)

    # ------------------------------------------------------------------
    # Resize
    # ------------------------------------------------------------------

    def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY terminal."""
        self.cols = cols
        self.rows = rows
        if _IS_WINDOWS:
            if self._pty_process is not None:
                try:
                    self._pty_process.setwinsize(rows, cols)
                except Exception:
                    pass
        else:
            if self._master_fd is not None:
                self._set_winsize(self._master_fd, rows, cols)
                if self._pid is not None:
                    try:
                        os.killpg(os.getpgid(self._pid), signal.SIGWINCH)
                    except (ProcessLookupError, PermissionError):
                        pass

    @staticmethod
    def _set_winsize(fd: int, rows: int, cols: int) -> None:
        """Set the terminal window size on a file descriptor (Unix only)."""
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the PTY session and kill the shell."""
        if self._closed:
            return
        self._closed = True

        if _IS_WINDOWS:
            await self._close_windows()
        else:
            await self._close_unix()

        logger.info("Closed PTY session %s", self.terminal_id)

    async def _close_unix(self) -> None:
        if self._pid is not None:
            try:
                os.killpg(os.getpgid(self._pid), signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def _close_windows(self) -> None:
        if self._pty_process is not None:
            try:
                if self._pty_process.isalive():
                    self._pty_process.terminate()
            except Exception:
                pass
            self._pty_process = None

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
