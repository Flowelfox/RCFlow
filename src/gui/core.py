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
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Shared constants consumed by both GUI modules
POLL_MS = 300
MAX_LOG_LINES = 5000
MAX_LOG_BUFFER = 10000

# ── Worker pidfile ──────────────────────────────────────────────────────────
#
# The GUI spawns the RCFlow server as a subprocess via Popen.  If the GUI
# crashes (e.g. a Cocoa re-entrancy after macOS auto-lock / sleep-wake), the
# child is reparented to launchd and keeps serving clients — the user has no
# UI to stop it.  To recover: the GUI writes the subprocess PID to this file
# on start and deletes it on graceful stop.  A relaunched GUI checks the file
# via ServerManager.adopt_if_running() and adopts the orphan so the user can
# stop it from the new GUI.  The server itself also installs a parent-death
# watchdog (see src/__main__._install_parent_death_watchdog) so new orphans
# are prevented at the source.
_PIDFILE_NAME = ".worker.pid"

# Env var propagated to the server subprocess; the child's watchdog polls
# whether this pid is still alive and exits the server if it is not.
_PARENT_PID_ENV = "RCFLOW_PARENT_PID"


def _worker_pidfile_path() -> Path:
    """Return the path of the worker pidfile.

    Prefers the shared data directory (same directory used for settings.json).
    On macOS frozen builds this resolves to
    ``~/Library/Application Support/rcflow/.worker.pid``.
    """
    try:
        from src.paths import get_data_dir  # noqa: PLC0415

        base = get_data_dir()
    except Exception:
        base = Path.home()
    return base / _PIDFILE_NAME


def _is_pid_alive(pid: int) -> bool:
    """Return True if *pid* is alive and owned by the current user.

    On Unix this is ``os.kill(pid, 0)``.  On Windows we use
    ``OpenProcess / GetExitCodeProcess`` via ctypes to avoid a dependency on
    psutil.  Any error is treated as "not alive" so adoption fails safely.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes  # noqa: PLC0415

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # noqa: N806
            STILL_ACTIVE = 259  # noqa: N806
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by a different user — not our orphan.
        return False
    except OSError:
        return False
    return True


def _kill_pid(pid: int, *, force: bool = False) -> None:
    """Send SIGTERM (default) or SIGKILL (force=True) to *pid*.

    On Windows we fall back to ``TerminateProcess`` because Windows has no
    SIGTERM; ``force`` is ignored there (all termination is ungraceful).
    Errors are swallowed — the caller polls :func:`_is_pid_alive` afterwards.
    """
    if pid <= 0:
        return
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes  # noqa: PLC0415

            PROCESS_TERMINATE = 0x0001  # noqa: N806
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                try:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        return
    import signal as _signal  # noqa: PLC0415

    sig = _signal.SIGKILL if force else _signal.SIGTERM
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, sig)


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

    Two tracking modes:

    - **Owned** (``_proc`` set) — the normal case; the GUI spawned the server
      via :meth:`start` and holds a ``Popen`` handle.
    - **Adopted** (``_adopted_pid`` set) — :meth:`adopt_if_running` found an
      orphan left behind by a crashed previous GUI, recorded via the worker
      pidfile.  The server is alive but this process is not its parent, so
      we can only track it by pid and terminate it with a raw signal.
    """

    def __init__(self, log_buffer: LogBuffer) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._adopted_pid: int | None = None
        self._lock = threading.Lock()
        self._start_time: float | None = None
        self._log = log_buffer

    # ── State ───────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Return True if the server subprocess is alive.

        Handles both owned (Popen) and adopted (pidfile) processes.
        """
        with self._lock:
            if self._proc is not None:
                return self._proc.poll() is None
            if self._adopted_pid is not None:
                if _is_pid_alive(self._adopted_pid):
                    return True
                # Adopted pid is gone — drop the reference so subsequent
                # calls report accurately.
                self._adopted_pid = None
                self._start_time = None
            return False

    @property
    def start_time(self) -> float | None:
        """``time.monotonic()`` timestamp of the last successful start, or None."""
        return self._start_time

    @property
    def exit_code(self) -> int | None:
        """Exit code of a finished-but-not-yet-cleared process, else None.

        Adopted processes have no exit code (we are not their parent, so
        ``waitpid`` is not available) — returns None in that case.
        """
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None:
                return self._proc.returncode
            return None

    @property
    def is_adopted(self) -> bool:
        """True if the currently-tracked process was adopted from a pidfile."""
        with self._lock:
            return self._adopted_pid is not None and self._proc is None

    def clear(self) -> None:
        """Remove the reference to a stopped process and reset start time.

        Safe to call even if the process is already cleared or still running
        (in the latter case it is a no-op).  Also removes the pidfile when
        the server has exited so a subsequent launch doesn't try to adopt a
        dead pid.
        """
        cleared = False
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None:
                self._proc = None
                cleared = True
            if self._adopted_pid is not None and not _is_pid_alive(self._adopted_pid):
                self._adopted_pid = None
                cleared = True
        if cleared:
            self._delete_pidfile()
        self._start_time = None

    # ── Pidfile ──────────────────────────────────────────────────────────

    def _write_pidfile(self, pid: int) -> None:
        """Persist the child PID so a relaunched GUI can adopt an orphan.

        Best-effort — any I/O error is logged and ignored (pidfile is purely
        a recovery aid; absence only prevents adoption on the next launch).
        """
        path = _worker_pidfile_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(pid), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write worker pidfile %s: %s", path, exc)

    def _delete_pidfile(self) -> None:
        """Remove the pidfile (called on graceful stop)."""
        path = _worker_pidfile_path()
        with contextlib.suppress(OSError):
            path.unlink()

    def adopt_if_running(self) -> int | None:
        """Re-attach to an orphaned server left behind by a crashed GUI.

        Reads the worker pidfile; if the pid is alive, records it as an
        adopted process and returns the pid.  Returns None when there is no
        pidfile or the pid is dead (pidfile is cleaned up in that case).

        Calling this on a manager that already has a running owned process
        is a no-op and returns None.
        """
        with self._lock:
            if self._proc is not None or self._adopted_pid is not None:
                return None

        path = _worker_pidfile_path()
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            pid = int(raw)
        except ValueError:
            with contextlib.suppress(OSError):
                path.unlink()
            return None

        if not _is_pid_alive(pid):
            with contextlib.suppress(OSError):
                path.unlink()
            return None

        with self._lock:
            self._adopted_pid = pid
        self._start_time = time.monotonic()
        self._log.append(f"Adopted running server (PID {pid}) — recovered from previous session.")
        logger.info("Adopted orphan worker pid=%d from %s", pid, path)
        return pid

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

        # Persist user settings and ensure the API token exists before launching.
        # get_settings() generates RCFLOW_API_KEY (and RCFLOW_BACKEND_ID) if
        # absent, then writes them to settings.json *and* updates os.environ in
        # this GUI process.  Doing this here — before the subprocess spawns —
        # eliminates the race where the GUI shows "Running" (process alive) but
        # the server hasn't yet initialised and written the token, causing
        # "Copy Token" to fail during the startup window.
        from src.config import get_settings, update_settings_file  # noqa: PLC0415

        get_settings()
        update_settings_file({"RCFLOW_HOST": host, "RCFLOW_PORT": str(port)})

        env = os.environ.copy()
        env["RCFLOW_HOST"] = host
        env["RCFLOW_PORT"] = str(port)
        env["WSS_ENABLED"] = str(wss)
        # The server's parent-death watchdog exits the process when this
        # pid is gone, preventing orphaned backends after GUI crashes.
        env[_PARENT_PID_ENV] = str(os.getpid())

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
            # Owned start supersedes any stale adoption reference.
            self._adopted_pid = None
        self._start_time = time.monotonic()
        self._write_pidfile(proc.pid)

        # Background thread streams subprocess stdout into the log buffer
        threading.Thread(target=self._read_output, args=(proc,), daemon=True).start()

        protocol = "wss" if wss else "ws"
        self._log.append(f"Server starting on {protocol}://{host}:{port} (PID {proc.pid})...")
        return None

    # ── Stop ─────────────────────────────────────────────────────────────

    def stop(self, on_stopped: Callable[[], None] | None = None) -> None:
        """Terminate the server subprocess in a background thread.

        Handles both owned (``Popen``) and adopted (pidfile-only) processes.
        Calls ``on_stopped()`` (if provided) once the process has exited.
        """
        with self._lock:
            proc = self._proc
            adopted_pid = self._adopted_pid if proc is None else None
        if proc is None and adopted_pid is None:
            return

        self._log.append("Stopping server...")

        def _do() -> None:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            else:
                assert adopted_pid is not None
                _kill_pid(adopted_pid)
                self._wait_for_pid_exit(adopted_pid, timeout=10)
                if _is_pid_alive(adopted_pid):
                    _kill_pid(adopted_pid, force=True)
                    self._wait_for_pid_exit(adopted_pid, timeout=5)
            with self._lock:
                if proc is not None and self._proc is proc:
                    self._proc = None
                if adopted_pid is not None and self._adopted_pid == adopted_pid:
                    self._adopted_pid = None
            self._start_time = None
            self._delete_pidfile()
            if on_stopped is not None:
                on_stopped()

        threading.Thread(target=_do, daemon=True).start()

    def stop_sync(self, timeout: float = 12) -> None:
        """Terminate the server subprocess synchronously (blocks until dead).

        Use this during app shutdown where we must guarantee the child process
        is gone before the parent exits.  Falls back to SIGKILL if SIGTERM is
        not honoured within *timeout* seconds.  Handles both owned and
        adopted processes.
        """
        with self._lock:
            proc = self._proc
            adopted_pid = self._adopted_pid if proc is None else None

        if proc is not None:
            if proc.poll() is not None:
                with self._lock:
                    if self._proc is proc:
                        self._proc = None
                self._start_time = None
                self._delete_pidfile()
                return
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=3)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
        elif adopted_pid is not None:
            if not _is_pid_alive(adopted_pid):
                with self._lock:
                    if self._adopted_pid == adopted_pid:
                        self._adopted_pid = None
                self._start_time = None
                self._delete_pidfile()
                return
            _kill_pid(adopted_pid)
            self._wait_for_pid_exit(adopted_pid, timeout=timeout)
            if _is_pid_alive(adopted_pid):
                _kill_pid(adopted_pid, force=True)
                self._wait_for_pid_exit(adopted_pid, timeout=3)
            with self._lock:
                if self._adopted_pid == adopted_pid:
                    self._adopted_pid = None
        else:
            return

        self._start_time = None
        self._delete_pidfile()

    @staticmethod
    def _wait_for_pid_exit(pid: int, timeout: float) -> None:
        """Poll until *pid* exits or *timeout* elapses.

        Used for adopted processes where we cannot use ``Popen.wait`` because
        we are not the parent.  Returns without raising on timeout so the
        caller can escalate to SIGKILL.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _is_pid_alive(pid):
                return
            time.sleep(0.1)

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


# ── Singleton IPC ────────────────────────────────────────────────────────────
#
# The first GUI process binds a loopback-only TCP listener on an ephemeral
# port and writes the chosen port to ``.worker.ipc`` next to the pidfile.  A
# second launch reads the port, connects, and sends ``SHOW\n`` — the running
# instance responds by revealing its dashboard window — then exits 0.  This
# makes "open again" reliably re-raise the existing window instead of
# silently failing (macOS AppleScript fallback only worked for registered
# LaunchServices bundles).

_IPC_FILENAME = ".worker.ipc"
_IPC_SHOW_CMD = b"SHOW\n"


def _ipc_file_path() -> Path:
    """Return the path of the singleton IPC discovery file."""
    try:
        from src.paths import get_data_dir  # noqa: PLC0415

        base = get_data_dir()
    except Exception:
        base = Path.home()
    return base / _IPC_FILENAME


def remove_ipc_file() -> None:
    """Delete the IPC discovery file. Safe to call at shutdown."""
    with contextlib.suppress(OSError):
        _ipc_file_path().unlink()


def start_ipc_server(on_show: Callable[[], None]) -> socket.socket | None:
    """Bind a loopback TCP listener and record the port in ``.worker.ipc``.

    Spawns a daemon thread that accepts connections and invokes ``on_show()``
    when a client sends ``SHOW\\n``.  The callback is invoked from the daemon
    thread — it MUST be safe to call from a non-UI thread (typically a
    flag-set, not a direct Tk / AppKit call).

    Returns the server socket on success so the caller can close it on
    shutdown, or None if binding failed (IPC disabled in that case).
    """
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
    except OSError as exc:
        logger.warning("IPC server bind failed: %s", exc)
        return None

    port = srv.getsockname()[1]
    path = _ipc_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(port), encoding="utf-8")
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    except OSError as exc:
        logger.warning("IPC file write failed (%s): %s", path, exc)
        with contextlib.suppress(OSError):
            srv.close()
        return None

    def _accept_loop() -> None:
        while True:
            try:
                conn, _addr = srv.accept()
            except OSError:
                return
            try:
                conn.settimeout(1.0)
                data = b""
                while b"\n" not in data and len(data) < 32:
                    chunk = conn.recv(32 - len(data))
                    if not chunk:
                        break
                    data += chunk
                line, _, _ = data.partition(b"\n")
                if line.strip() == _IPC_SHOW_CMD.strip():
                    try:
                        on_show()
                    except Exception:
                        logger.exception("IPC on_show callback failed")
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    conn.close()

    threading.Thread(target=_accept_loop, daemon=True).start()
    return srv


def send_show_to_existing() -> bool:
    """Try to hand a ``SHOW`` command to the running singleton.

    Reads the port from ``.worker.ipc``, connects, writes ``SHOW\\n``, and
    returns True when the write succeeds.  Returns False when the IPC file
    is missing, unreadable, stale (port not listening), or the write fails —
    the caller can then decide whether to exit or fall back to another
    activation mechanism.
    """
    path = _ipc_file_path()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    try:
        port = int(raw)
    except ValueError:
        return False
    if not (1 <= port <= 65535):
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as sock:
            sock.sendall(_IPC_SHOW_CMD)
    except OSError:
        return False
    return True


def poll_server_status(
    host: str,
    port: str,
    wss_enabled: bool,
    on_result: Callable[[int | None, str | None, str | None], None],
) -> None:
    """Fetch session count, backend ID, and version from the server HTTP API (non-blocking).

    Spawns a daemon thread.  ``on_result(sessions, backend_id, version)`` is
    invoked with None values when the request fails or the server is unreachable.
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
                    on_result(None, None, None)
                    return
        except Exception:
            on_result(None, None, None)
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
                version: str | None = data.get("version") or None
                on_result(sessions, backend_id, version)
        except Exception:
            on_result(None, None, None)

    threading.Thread(target=_fetch, daemon=True).start()
