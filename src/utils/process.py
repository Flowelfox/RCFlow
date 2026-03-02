"""Cross-platform process creation and tree-kill helpers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Windows constant — only used when sys.platform == "win32"
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def new_session_kwargs() -> dict[str, Any]:
    """Return kwargs for ``asyncio.create_subprocess_exec`` to isolate the child
    process tree from the parent.

    On POSIX: ``start_new_session=True`` (creates a new process group).
    On Windows: ``creationflags=CREATE_NEW_PROCESS_GROUP``.
    """
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill a process and all its children, cross-platform.

    On POSIX: sends SIGKILL to the entire process group via ``os.killpg()``.
    On Windows: uses ``taskkill /T /F /PID`` to kill the process tree.

    Falls back to ``proc.kill()`` if the tree-kill fails.
    """
    pid = proc.pid
    if pid is None:
        return

    if sys.platform == "win32":
        await _kill_tree_windows(pid)
    else:
        _kill_tree_posix(pid)

    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()

    with contextlib.suppress(ProcessLookupError, OSError):
        await proc.wait()


def _kill_tree_posix(pid: int) -> None:
    """Kill the entire process group on POSIX."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pid, signal.SIGKILL)


async def _kill_tree_windows(pid: int) -> None:
    """Kill the process tree on Windows using ``taskkill``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "taskkill",
            "/T",
            "/F",
            "/PID",
            str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:
        logger.debug("taskkill failed for PID %d", pid, exc_info=True)
