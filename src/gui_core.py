"""Shared server-management primitives for the RCFlow GUI.

Both gui.py (Windows) and gui_macos.py (macOS) import ServerManager,
LogBuffer, and poll_server_status from here.  Per-platform files contain
only window construction and tray/menu-bar integration.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Shared constants consumed by both GUI modules
POLL_MS = 300
MAX_LOG_LINES = 5000
MAX_LOG_BUFFER = 10000


class LogBuffer:
    """Thread-safe FIFO buffer for subprocess log lines."""

    def __init__(self, maxsize: int = MAX_LOG_BUFFER) -> None:
        self._queue: queue.Queue[str] = queue.Queue(maxsize=maxsize)

    def append(self, text: str) -> None:
        """Enqueue a line; silently drop if the buffer is full."""
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(text)

    def drain(self) -> list[str]:
        """Dequeue and return all pending lines without blocking."""
        lines: list[str] = []
        try:
            while True:
                lines.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        return lines


class ServerManager:
    """Manages the RCFlow server subprocess lifecycle.

    Thread-safe: start/stop may be called from any thread; the internal
    lock guards all ``_proc`` mutations.
    """

    def __init__(self, log_buffer: LogBuffer) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._start_time: float | None = None
        self._log = log_buffer

    # ── State ───────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Return True if the server subprocess is alive."""
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def start_time(self) -> float | None:
        """``time.monotonic()`` timestamp of the last successful start, or None."""
        return self._start_time

    @property
    def exit_code(self) -> int | None:
        """Exit code of a finished-but-not-yet-cleared process, else None."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None:
                return self._proc.returncode
            return None

    def clear(self) -> None:
        """Remove the reference to a stopped process and reset start time.

        Safe to call even if the process is already cleared or still running
        (in the latter case it is a no-op).
        """
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None
        self._start_time = None

    # ── Start ────────────────────────────────────────────────────────────

    def start(self, host: str, port: int, wss: bool) -> str | None:
        """Launch the server subprocess.

        Returns None on success, or a human-readable error string on failure.
        The caller is responsible for updating any UI state after a successful
        return.
        """
        # Pre-flight: verify the port is available before spawning
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((host, port))
            s.close()
        except OSError as exc:
            return f"Error: Cannot bind {host}:{port} \u2014 {exc}"

        # Persist user settings before launching
        from src.config import update_settings_file  # noqa: PLC0415

        update_settings_file({"RCFLOW_HOST": host, "RCFLOW_PORT": str(port)})

        env = os.environ.copy()
        env["RCFLOW_HOST"] = host
        env["RCFLOW_PORT"] = str(port)
        env["WSS_ENABLED"] = str(wss)

        if getattr(sys, "frozen", False):
            from src.paths import get_data_dir  # noqa: PLC0415

            data_dir = get_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            cwd: str | None = str(data_dir)

            # Run migrations before starting the server so the database
            # schema is always up-to-date (handles first launch, version
            # upgrades, and data-dir relocations).
            migrate_cmd: list[str] = [sys.executable, "migrate"]
            try:
                result = subprocess.run(
                    migrate_cmd,
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    logger.error("Migration failed (exit %d): %s", result.returncode, result.stderr)
                    self._log.append(f"Migration failed (exit {result.returncode})")
                    if result.stderr:
                        self._log.append(result.stderr.strip())
                else:
                    logger.info("Migrations completed successfully")
            except Exception as exc:
                logger.error("Migration error: %s", exc)
                self._log.append(f"Migration error: {exc}")

            cmd: list[str] = [sys.executable, "run"]
        else:
            # Run migrations in dev mode too
            migrate_cmd = [sys.executable, "-m", "src", "migrate"]
            try:
                result = subprocess.run(
                    migrate_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    logger.error("Migration failed (exit %d): %s", result.returncode, result.stderr)
                    self._log.append(f"Migration failed (exit {result.returncode})")
                else:
                    logger.info("Migrations completed successfully")
            except Exception as exc:
                logger.error("Migration error: %s", exc)
                self._log.append(f"Migration error: {exc}")

            cmd = [sys.executable, "-m", "src", "run"]
            cwd = None

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                creationflags=creation_flags,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            return f"Error: Failed to start server \u2014 {exc}"

        with self._lock:
            self._proc = proc
        self._start_time = time.monotonic()

        # Background thread streams subprocess stdout into the log buffer
        threading.Thread(target=self._read_output, args=(proc,), daemon=True).start()

        protocol = "wss" if wss else "ws"
        self._log.append(f"Server starting on {protocol}://{host}:{port} (PID {proc.pid})...")
        return None

    # ── Stop ─────────────────────────────────────────────────────────────

    def stop(self, on_stopped: Callable[[], None] | None = None) -> None:
        """Terminate the server subprocess in a background thread.

        Calls ``on_stopped()`` (if provided) once the process has exited.
        """
        with self._lock:
            proc = self._proc
        if proc is None:
            return

        self._log.append("Stopping server...")

        def _do() -> None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            self._start_time = None
            if on_stopped is not None:
                on_stopped()

        threading.Thread(target=_do, daemon=True).start()

    # ── Output reader ─────────────────────────────────────────────────────

    def _read_output(self, proc: subprocess.Popen[str]) -> None:
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n\r")
                if line:
                    self._log.append(line)
        except Exception:
            pass


def poll_server_status(
    host: str,
    port: str,
    wss_enabled: bool,
    on_result: Callable[[int | None, str | None], None],
) -> None:
    """Fetch session count and backend ID from the server HTTP API (non-blocking).

    Spawns a daemon thread.  ``on_result(sessions, backend_id)`` is invoked
    with None values when the request fails or the server is unreachable.
    """

    def _fetch() -> None:
        import json  # noqa: PLC0415
        import ssl  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        scheme = "https" if wss_enabled else "http"
        effective_host = "127.0.0.1" if host == "0.0.0.0" else host
        base = f"{scheme}://{effective_host}:{port}"

        ctx: ssl.SSLContext | None = None
        if wss_enabled:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2, context=ctx) as resp:
                if resp.status != 200:
                    on_result(None, None)
                    return
        except Exception:
            on_result(None, None)
            return

        try:
            from src.config import Settings  # noqa: PLC0415

            api_key = Settings().RCFLOW_API_KEY
            req = urllib.request.Request(f"{base}/api/info", headers={"X-API-Key": api_key})
            with urllib.request.urlopen(req, timeout=2, context=ctx) as resp:
                data = json.loads(resp.read())
                sessions: int | None = data.get("active_sessions")
                raw_id: str = data.get("backend_id", "") or ""
                backend_id: str | None = (raw_id[:8] + "...") if len(raw_id) > 12 else (raw_id or None)
                on_result(sessions, backend_id)
        except Exception:
            on_result(None, None)

    threading.Thread(target=_fetch, daemon=True).start()
