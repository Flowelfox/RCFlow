"""Utilities for PTY-backed subprocess I/O.

Unix-only. Provides:
- ``configure_raw(fd)``  — set a PTY slave fd to raw mode (no echo, no line
  processing) so JSON written to the master fd is not echoed back and output
  arrives with clean ``\\n`` line endings.
- ``set_winsize(fd, rows, cols)`` — configure the terminal window size.
- ``PtyLineReader``  — asyncio-native line reader over a PTY master fd; uses
  the event loop I/O-reader callback rather than threads or polling.
- ``strip_ansi(text)`` — remove ANSI escape sequences from a decoded string.

This module is intentionally *not* imported at the top level so that Windows
environments (where the ``pty``, ``termios``, and ``fcntl`` stdlib modules do
not exist) never trigger an ``ImportError``.  Import it only after checking
``sys.platform != "win32"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno as _errno
import logging
import os
import re
import struct
import sys

if sys.platform == "win32":
    raise ImportError("pty_utils is not available on Windows")  # guard only; never imported on Windows

import fcntl
import termios

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------

# Matches CSI sequences (ESC[..m etc.), OSC sequences (ESC]...BEL/ST),
# DCS/APC/PM string sequences (ESC P/_ /^ ... ST), and two-char ESC sequences.
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"              # CSI: ESC [ ... final_byte
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC: ESC ] ... (BEL | ST)
    r"|[P_^][^\x1b]*\x1b\\"             # DCS / APC / PM: ESC P/_ /^ ... ST
    r"|[@-Z\\-_]"                        # Two-char ESC sequences (must be last)
    r")"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI/VT100 escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Terminal configuration
# ---------------------------------------------------------------------------


def configure_raw(fd: int) -> None:
    """Set a PTY slave file descriptor to raw mode.

    Raw mode disables:

    * **Echo** — writes to the PTY master fd are not reflected back as output.
    * **Canonical mode** — no line buffering; data passes through immediately.
    * **Signal generation** — Ctrl+C does not send SIGINT to the child.
    * **Output post-processing** — ``OPOST`` off, so ``\\n`` is never
      translated to ``\\r\\n``; JSON line endings arrive clean.
    * **Input translations** — ``ICRNL`` etc. off; no CR/NL mapping.

    Called *before* spawning the subprocess so the slave inherits the raw
    discipline from the moment the child opens it.
    """
    attrs = termios.tcgetattr(fd)

    # iflag — disable all input transformations
    attrs[0] &= ~(
        termios.IGNBRK
        | termios.BRKINT
        | termios.PARMRK
        | termios.ISTRIP
        | termios.INLCR
        | termios.IGNCR
        | termios.ICRNL
        | termios.IXON
    )
    # oflag — disable output post-processing (prevents NL → CRNL translation)
    attrs[1] &= ~termios.OPOST
    # lflag — disable echo, canonical mode, signal generation, extended processing
    attrs[3] &= ~(
        termios.ECHO
        | termios.ECHONL
        | termios.ICANON
        | termios.ISIG
        | termios.IEXTEN
    )
    # cflag — 8-bit characters, no parity
    attrs[2] &= ~(termios.CSIZE | termios.PARENB)
    attrs[2] |= termios.CS8
    # cc — minimum 1 char per read, no timeout
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0

    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def set_winsize(fd: int, rows: int = 24, cols: int = 220) -> None:
    """Set terminal window dimensions on a PTY file descriptor (TIOCSWINSZ).

    *cols* defaults to 220 to give Claude Code a wide viewport for tool output
    formatting, which avoids word-wrap artifacts in the JSON stream.
    """
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


# ---------------------------------------------------------------------------
# PtyLineReader
# ---------------------------------------------------------------------------


class PtyLineReader:
    """Asynchronous line reader backed by a PTY master file descriptor.

    Uses ``asyncio.get_event_loop().add_reader()`` to receive data-ready
    notifications from the OS.  When bytes arrive, they are appended to an
    internal buffer.  Callers await :meth:`readline` which returns the next
    ``\\n``-terminated line; if no newline is available yet the coroutine
    suspends without blocking the event loop.

    The master fd must be **blocking** (default after ``pty.openpty()``).
    ``add_reader`` guarantees the read callback is only invoked when data is
    available, so ``os.read`` in the callback never blocks.

    Parameters
    ----------
    master_fd:
        The PTY master file descriptor returned by ``pty.openpty()``.
    limit:
        Maximum bytes to retain in the internal buffer before dropping old
        data (safety guard against runaway output; 10 MB default).
    """

    def __init__(self, master_fd: int, limit: int = 10 * 1024 * 1024) -> None:
        self._fd = master_fd
        self._limit = limit
        self._buf = bytearray()
        self._waiters: list[asyncio.Future[None]] = []
        self._closed = False
        self._exception: BaseException | None = None
        self._loop = asyncio.get_event_loop()
        self._loop.add_reader(master_fd, self._on_readable)

    # ------------------------------------------------------------------
    # Internal event-loop callback

    def _on_readable(self) -> None:
        """Invoked by the event loop when *master_fd* has data available."""
        try:
            data = os.read(self._fd, 65536)
        except OSError as exc:
            if exc.errno in (_errno.EAGAIN, _errno.EWOULDBLOCK, _errno.EIO):
                # EIO typically means the slave side was closed (process exited)
                self._set_closed()
                return
            self._set_exception(exc)
            return

        if not data:
            self._set_closed()
            return

        self._buf.extend(data)
        if len(self._buf) > self._limit:
            # Trim oldest data to stay within the limit
            self._buf = self._buf[-self._limit :]
        # Wake waiters whenever a newline arrives or on close
        if b"\n" in data:
            self._notify_waiters()

    # ------------------------------------------------------------------
    # State transitions

    def _set_closed(self) -> None:
        self._closed = True
        with contextlib.suppress(Exception):
            self._loop.remove_reader(self._fd)
        self._notify_waiters()

    def _set_exception(self, exc: BaseException) -> None:
        self._exception = exc
        self._set_closed()

    def _notify_waiters(self) -> None:
        waiters, self._waiters = self._waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)

    # ------------------------------------------------------------------
    # Public API

    async def readline(self) -> bytes:
        """Return the next ``\\n``-terminated line from the PTY.

        Blocks asynchronously (suspends the coroutine) until a complete line
        is available.  Returns ``b""`` on EOF.  Raises the underlying
        ``OSError`` if an I/O error occurred on the master fd.
        """
        while True:
            nl = self._buf.find(b"\n")
            if nl != -1:
                line = bytes(self._buf[: nl + 1])
                del self._buf[: nl + 1]
                return line
            if self._closed:
                if self._exception is not None:
                    raise self._exception
                # Flush any remaining buffered bytes as the final "line"
                if self._buf:
                    line = bytes(self._buf)
                    self._buf.clear()
                    return line
                return b""
            # No newline yet — suspend until more data arrives
            fut: asyncio.Future[None] = self._loop.create_future()
            self._waiters.append(fut)
            await fut

    def close(self) -> None:
        """Stop listening on *master_fd* and wake any pending readers."""
        if not self._closed:
            self._set_closed()
