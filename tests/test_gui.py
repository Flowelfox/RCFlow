"""Tests for src/gui — settings persistence and process lifecycle."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

# Pre-install mocks for GUI-only dependencies that are not available in the
# test environment (customtkinter, tkinter, etc.).  These must be in
# sys.modules *before* ``src.gui`` is first imported so that module-level
# ``import customtkinter`` and ``ctk.set_appearance_mode(...)`` succeed.
_ctk_mock = MagicMock()
sys.modules.setdefault("customtkinter", _ctk_mock)


def _make_gui(tmp_path: Path) -> RCFlowGUI:  # type: ignore[name-defined]  # noqa: F821
    """Construct an RCFlowGUI with all tkinter/pystray/subprocess pieces mocked out."""
    # Patch tkinter so no real window is created
    tk_mock = MagicMock()

    # StringVar / BooleanVar need to behave like value containers
    class _Var:
        def __init__(self, value=None):
            self._v = value

        def get(self):
            return self._v

        def set(self, v):
            self._v = v

    tk_mock.Tk.return_value = MagicMock()
    tk_mock.StringVar.side_effect = _Var
    tk_mock.BooleanVar.side_effect = _Var

    with (
        patch.dict(
            sys.modules,
            {
                "tkinter": tk_mock,
                "tkinter.scrolledtext": MagicMock(),
                "tkinter.ttk": MagicMock(),
            },
        ),
        # Keep the real config module but redirect settings.json to tmp_path
        patch("src.paths.get_install_dir", return_value=tmp_path),
        # Prevent auto-start side effects
        patch("src.gui.windows.RCFlowGUI._setup_tray", return_value=False),
        patch("src.gui.windows.RCFlowGUI._start_server"),
        patch("src.gui.windows.RCFlowGUI._set_window_icon"),
    ):
        from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415
        from src.gui.windows import RCFlowGUI  # noqa: PLC0415

        gui = RCFlowGUI.__new__(RCFlowGUI)
        # Manually initialise only the attributes touched by _start_server
        gui._ip_var = _Var("0.0.0.0")
        gui._port_var = _Var("53890")
        gui._wss_var = _Var(True)
        gui._quitting = False

        # Use a real ServerManager so that persistence logic is exercised
        gui._log_buffer = LogBuffer()
        gui._server = ServerManager(gui._log_buffer)

        # Stub UI-mutating calls so they don't raise
        for attr in (
            "_ip_entry",
            "_port_entry",
            "_wss_check",
            "_toggle_btn",
            "_status_label",
            "_uptime_var",
            "_bound_addr_var",
            "_sessions_var",
            "_backend_id_var",
            "_version_var",
        ):
            setattr(gui, attr, MagicMock())
        gui._set_status = MagicMock()
        gui._log_append = MagicMock()

    return gui


def test_start_server_persists_host_and_port(tmp_path: Path) -> None:
    """Changing IP/port in the GUI and clicking Start must write them to settings.json."""
    settings_path = tmp_path / "settings.json"

    # Ensure _load_settings_into_env re-runs against our tmp dir
    with patch("src.paths.get_install_dir", return_value=tmp_path):
        import importlib  # noqa: PLC0415

        from src import config as cfg_mod  # noqa: PLC0415

        importlib.reload(cfg_mod)

    gui = _make_gui(tmp_path)

    # Simulate user changing host and port
    gui._ip_var.set("192.168.50.10")
    gui._port_var.set("54321")

    # Patch socket.socket so the port-availability check always passes,
    # and subprocess so no real server is spawned.
    with (
        patch("src.gui.core.socket.socket") as mock_sock,
        patch("src.gui.core.subprocess.Popen") as mock_popen,
        patch("src.gui.core.subprocess.run") as mock_run,
        patch("src.paths.get_install_dir", return_value=tmp_path),
        patch("src.config._get_settings_path", return_value=settings_path),
    ):
        mock_sock.return_value.bind = MagicMock()
        mock_sock.return_value.close = MagicMock()
        mock_popen.return_value.pid = 9999
        mock_popen.return_value.poll.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        # Temporarily restore the real _start_server (it was stubbed during __new__)
        import src.gui.windows as gui_mod  # noqa: PLC0415

        gui._start_server = gui_mod.RCFlowGUI._start_server.__get__(gui)
        gui._start_server()

    assert settings_path.exists(), "settings.json was not created"
    data = json.loads(settings_path.read_text())
    assert data.get("RCFLOW_HOST") == "192.168.50.10", f"RCFLOW_HOST not persisted; got: {data.get('RCFLOW_HOST')!r}"
    assert data.get("RCFLOW_PORT") == "54321", f"RCFLOW_PORT not persisted; got: {data.get('RCFLOW_PORT')!r}"


class _Var:
    """Minimal StringVar/BooleanVar replacement usable outside _make_gui."""

    def __init__(self, value: object = None) -> None:
        self._v = value

    def get(self) -> object:
        return self._v

    def set(self, v: object) -> None:
        self._v = v


def test_on_status_result_marshals_via_after(tmp_path: Path) -> None:
    """_on_status_result must schedule StringVar updates via root.after(0, …).

    poll_server_status invokes the callback from a daemon thread.  On macOS,
    calling StringVar.set() from a background thread while NSMenu is in its
    modal tracking loop accesses AppKit off the main thread and produces a
    deadlock / spinning-beachball cursor.  The fix routes all mutations through
    self._root.after(0, apply_fn) so they always execute on the Tk main thread.
    """
    gui = _make_gui(tmp_path)
    gui._sessions_var = _Var()
    gui._backend_id_var = _Var()
    gui._version_var = _Var()

    after_calls: list[tuple] = []
    gui._root = MagicMock()
    gui._root.after.side_effect = lambda delay, fn: after_calls.append((delay, fn))

    gui._on_status_result(42, "abc123", "1.0.0")

    # after() must have been called exactly once with delay=0
    assert len(after_calls) == 1, f"expected 1 after() call, got {len(after_calls)}"
    delay, apply_fn = after_calls[0]
    assert delay == 0, f"expected after(0, …), got after({delay}, …)"

    # The deferred function must actually apply the values
    apply_fn()
    assert gui._sessions_var.get() == "42"
    assert gui._backend_id_var.get() == "abc123"
    assert gui._version_var.get() == "1.0.0"


def test_on_status_result_none_values_not_set(tmp_path: Path) -> None:
    """None values must not overwrite existing StringVar contents."""
    gui = _make_gui(tmp_path)
    gui._sessions_var = _Var("5")
    gui._backend_id_var = _Var("oldid")
    gui._version_var = _Var("0.9")

    after_calls: list[tuple] = []
    gui._root = MagicMock()
    gui._root.after.side_effect = lambda delay, fn: after_calls.append((delay, fn))

    gui._on_status_result(None, None, None)
    assert len(after_calls) == 1
    after_calls[0][1]()  # execute deferred function

    # None inputs must not overwrite existing values
    assert gui._sessions_var.get() == "5"
    assert gui._backend_id_var.get() == "oldid"
    assert gui._version_var.get() == "0.9"


def test_copy_token_reads_from_file_not_env(tmp_path: Path) -> None:
    """_on_copy_token must read from settings.json, not os.environ.

    On a clean install the GUI process starts before the server subprocess
    generates the token.  os.environ is therefore stale — the token only
    exists in settings.json after the server writes it.  Copying the token
    must succeed regardless.
    """
    import json  # noqa: PLC0415

    settings_path = tmp_path / "settings.json"
    token = "test-token-abc123"
    settings_path.write_text(json.dumps({"RCFLOW_API_KEY": token}), encoding="utf-8")

    gui = _make_gui(tmp_path)

    clipboard_contents: list[str] = []
    gui._root = MagicMock()
    gui._root.clipboard_append.side_effect = clipboard_contents.append

    with patch("src.config._get_settings_path", return_value=settings_path):
        import src.gui.windows as gui_mod  # noqa: PLC0415

        gui._on_copy_token = gui_mod.RCFlowGUI._on_copy_token.__get__(gui)
        gui._on_copy_token()

    gui._set_status.assert_called_with("Token copied to clipboard", sticky=True)
    assert clipboard_contents == [token], f"clipboard got {clipboard_contents!r}"


def test_copy_token_error_when_file_missing(tmp_path: Path) -> None:
    """_on_copy_token shows error when settings.json has no token (file absent)."""
    gui = _make_gui(tmp_path)

    nonexistent = tmp_path / "settings.json"
    assert not nonexistent.exists()

    with patch("src.config._get_settings_path", return_value=nonexistent):
        import src.gui.windows as gui_mod  # noqa: PLC0415

        gui._on_copy_token = gui_mod.RCFlowGUI._on_copy_token.__get__(gui)
        gui._on_copy_token()

    gui._set_status.assert_called_with("No API token configured", error=True, sticky=True)


def test_start_server_persists_default_host_and_port(tmp_path: Path) -> None:
    """Even the default values must be written on Start so subsequent reads are consistent."""
    settings_path = tmp_path / "settings.json"

    gui = _make_gui(tmp_path)
    # Leave defaults: 0.0.0.0 / 53890

    with (
        patch("src.gui.core.socket.socket") as mock_sock,
        patch("src.gui.core.subprocess.Popen") as mock_popen,
        patch("src.gui.core.subprocess.run") as mock_run,
        patch("src.config._get_settings_path", return_value=settings_path),
    ):
        mock_sock.return_value.bind = MagicMock()
        mock_sock.return_value.close = MagicMock()
        mock_popen.return_value.pid = 1234
        mock_popen.return_value.poll.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        import src.gui.windows as gui_mod  # noqa: PLC0415

        gui._start_server = gui_mod.RCFlowGUI._start_server.__get__(gui)
        gui._start_server()

    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert data.get("RCFLOW_HOST") == "0.0.0.0"
    assert data.get("RCFLOW_PORT") == "53890"


# ── ServerManager.stop_sync tests ────────────────────────────────────────────


def test_stop_sync_terminates_subprocess() -> None:
    """stop_sync must block until the subprocess is dead."""
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    mgr = ServerManager(LogBuffer())

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.poll.return_value = None  # "still running" on first check
    mock_proc.wait.return_value = 0

    with mgr._lock:
        mgr._proc = mock_proc
    mgr._start_time = 100.0

    mgr.stop_sync(timeout=5)

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called_once_with(timeout=5)
    assert mgr._proc is None
    assert mgr._start_time is None


def test_stop_sync_escalates_to_kill() -> None:
    """stop_sync must SIGKILL when SIGTERM times out."""
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    mgr = ServerManager(LogBuffer())

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.poll.return_value = None
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired("rcflow", 5), 0]

    with mgr._lock:
        mgr._proc = mock_proc
    mgr._start_time = 100.0

    mgr.stop_sync(timeout=5)

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_stop_sync_noop_when_not_running() -> None:
    """stop_sync on a stopped manager must be a safe no-op."""
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    mgr = ServerManager(LogBuffer())
    mgr.stop_sync()  # should not raise


# ── Singleton file lock tests ────────────────────────────────────────────────


def test_singleton_lock_prevents_second_acquire(tmp_path: Path) -> None:
    """Second call to _acquire_singleton_lock must return False."""
    import fcntl  # noqa: PLC0415

    import src.gui.macos as macos_mod  # noqa: PLC0415

    lock_file = tmp_path / ".worker.lock"

    with patch.object(macos_mod, "_LOCK_PATH", lock_file):
        assert macos_mod._acquire_singleton_lock() is True

        # Simulate a second process trying the same lock from another thread
        result: list[bool] = []

        def _try_lock() -> None:
            try:
                fd = open(lock_file, "w")  # noqa: SIM115
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result.append(True)
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
            except OSError:
                result.append(False)

        t = threading.Thread(target=_try_lock)
        t.start()
        t.join(timeout=2)

        assert result == [False], "second lock attempt should fail"

        # Clean up the lock fd
        if macos_mod._lock_fd is not None:
            macos_mod._lock_fd.close()
            macos_mod._lock_fd = None


# ── Worker pidfile / adoption tests ──────────────────────────────────────────
#
# These cover the orphan-recovery path: when a previous GUI crashed (e.g.
# a Cocoa re-entrancy after macOS auto-lock), the server subprocess it
# spawned keeps running but is no longer tracked by any GUI.  A relaunched
# GUI must adopt the orphan via the pidfile and allow the user to stop it.


def test_start_writes_pidfile(tmp_path: Path) -> None:
    """ServerManager.start must persist the child PID to the pidfile."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    mgr = ServerManager(LogBuffer())

    with (
        patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile),
        patch("src.gui.core.socket.socket") as mock_sock,
        patch("src.gui.core.subprocess.Popen") as mock_popen,
        patch("src.gui.core.subprocess.run") as mock_run,
        patch("src.config._get_settings_path", return_value=tmp_path / "settings.json"),
    ):
        mock_sock.return_value.bind = MagicMock()
        mock_sock.return_value.close = MagicMock()
        mock_popen.return_value.pid = 54321
        mock_popen.return_value.poll.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        err = mgr.start("127.0.0.1", 53890, False)

    assert err is None, f"start failed: {err}"
    assert pidfile.exists(), "pidfile was not written"
    assert pidfile.read_text().strip() == "54321"


def test_start_propagates_parent_pid_env(tmp_path: Path) -> None:
    """ServerManager.start must pass RCFLOW_PARENT_PID so the child can watchdog us."""
    import os  # noqa: PLC0415

    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    mgr = ServerManager(LogBuffer())

    with (
        patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile),
        patch("src.gui.core.socket.socket") as mock_sock,
        patch("src.gui.core.subprocess.Popen") as mock_popen,
        patch("src.gui.core.subprocess.run") as mock_run,
        patch("src.config._get_settings_path", return_value=tmp_path / "settings.json"),
    ):
        mock_sock.return_value.bind = MagicMock()
        mock_sock.return_value.close = MagicMock()
        mock_popen.return_value.pid = 1234
        mock_popen.return_value.poll.return_value = None
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        mgr.start("127.0.0.1", 53890, False)

    assert mock_popen.called
    spawn_kwargs = mock_popen.call_args.kwargs
    env = spawn_kwargs["env"]
    assert env.get("RCFLOW_PARENT_PID") == str(os.getpid())


def test_adopt_if_running_with_alive_pid(tmp_path: Path) -> None:
    """When the pidfile references a live pid, adopt it and report running."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    pidfile.write_text("99999")

    mgr = ServerManager(LogBuffer())

    with (
        patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile),
        patch.object(core_mod, "_is_pid_alive", return_value=True),
    ):
        adopted = mgr.adopt_if_running()

    assert adopted == 99999
    assert mgr.is_adopted is True
    with patch.object(core_mod, "_is_pid_alive", return_value=True):
        assert mgr.is_running() is True


def test_adopt_if_running_removes_stale_pidfile(tmp_path: Path) -> None:
    """When the pidfile references a dead pid, clean it up and don't adopt."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    pidfile.write_text("99999")

    mgr = ServerManager(LogBuffer())

    with (
        patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile),
        patch.object(core_mod, "_is_pid_alive", return_value=False),
    ):
        adopted = mgr.adopt_if_running()

    assert adopted is None
    assert mgr.is_adopted is False
    assert not pidfile.exists(), "stale pidfile should be removed"


def test_adopt_if_running_missing_pidfile_is_noop(tmp_path: Path) -> None:
    """No pidfile means no orphan to adopt — return None, don't raise."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    assert not pidfile.exists()

    mgr = ServerManager(LogBuffer())

    with patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile):
        assert mgr.adopt_if_running() is None
    assert mgr.is_adopted is False


def test_adopt_if_running_skips_when_already_owned(tmp_path: Path) -> None:
    """When the manager already owns a Popen, adoption must be a no-op."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    pidfile.write_text("99999")

    mgr = ServerManager(LogBuffer())
    with mgr._lock:
        mgr._proc = MagicMock(spec=subprocess.Popen)
        mgr._proc.poll.return_value = None

    with patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile):
        assert mgr.adopt_if_running() is None
    assert mgr.is_adopted is False


def test_stop_sync_terminates_adopted_pid(tmp_path: Path) -> None:
    """stop_sync on an adopted pid must kill it via raw signal + clean pidfile."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    pidfile.write_text("77777")

    mgr = ServerManager(LogBuffer())
    with mgr._lock:
        mgr._adopted_pid = 77777
    mgr._start_time = 0.0

    killed_pids: list[tuple[int, bool]] = []

    def _fake_kill(pid: int, *, force: bool = False) -> None:
        killed_pids.append((pid, force))

    # First alive check returns True (to trigger the kill path); subsequent
    # calls return False so the poll loop exits quickly.
    alive_calls = iter([True, False, False, False])

    with (
        patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile),
        patch.object(core_mod, "_kill_pid", side_effect=_fake_kill),
        patch.object(core_mod, "_is_pid_alive", side_effect=lambda _pid: next(alive_calls, False)),
    ):
        mgr.stop_sync(timeout=1)

    assert killed_pids, "expected _kill_pid to be called on adopted pid"
    assert killed_pids[0] == (77777, False)
    assert mgr._adopted_pid is None
    assert not pidfile.exists(), "pidfile should be removed after stop"


def test_stop_sync_escalates_to_force_kill_for_adopted_pid(tmp_path: Path) -> None:
    """When SIGTERM doesn't bring down the adopted process, stop_sync must SIGKILL."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    pidfile.write_text("88888")

    mgr = ServerManager(LogBuffer())
    with mgr._lock:
        mgr._adopted_pid = 88888
    mgr._start_time = 0.0

    kill_calls: list[tuple[int, bool]] = []

    def _fake_kill(pid: int, *, force: bool = False) -> None:
        kill_calls.append((pid, force))

    # Pretend the process survives SIGTERM but dies after SIGKILL.
    # The calls to _is_pid_alive happen:
    #   1. start of stop_sync (alive → True)
    #   2-N. polling after SIGTERM (all True — never exits during timeout)
    #   after timeout: one check before force kill (True)
    #   subsequent checks after SIGKILL (False).
    alive_states = [True] + [True] * 50 + [False] * 50

    def _alive(_pid: int) -> bool:
        return alive_states.pop(0) if alive_states else False

    with (
        patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile),
        patch.object(core_mod, "_kill_pid", side_effect=_fake_kill),
        patch.object(core_mod, "_is_pid_alive", side_effect=_alive),
    ):
        mgr.stop_sync(timeout=0.3)  # short timeout so the test doesn't drag

    force_kills = [c for c in kill_calls if c[1] is True]
    assert force_kills, f"expected an escalation to force=True kill, got {kill_calls!r}"
    assert mgr._adopted_pid is None
    assert not pidfile.exists()


def test_clear_removes_pidfile_when_process_exited(tmp_path: Path) -> None:
    """clear() after subprocess exit must remove the pidfile so next launch doesn't adopt a ghost."""
    from src.gui import core as core_mod  # noqa: PLC0415
    from src.gui.core import LogBuffer, ServerManager  # noqa: PLC0415

    pidfile = tmp_path / ".worker.pid"
    pidfile.write_text("11111")

    mgr = ServerManager(LogBuffer())

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.poll.return_value = 0  # exited
    with mgr._lock:
        mgr._proc = mock_proc
    mgr._start_time = 0.0

    with patch.object(core_mod, "_worker_pidfile_path", return_value=pidfile):
        mgr.clear()

    assert mgr._proc is None
    assert not pidfile.exists()


# ── Parent-death watchdog tests ──────────────────────────────────────────────


def test_parent_death_watchdog_noop_without_env_var(monkeypatch) -> None:
    """Absent RCFLOW_PARENT_PID → no watchdog thread (daemon/systemd installs)."""
    monkeypatch.delenv("RCFLOW_PARENT_PID", raising=False)

    from src.__main__ import _install_parent_death_watchdog  # noqa: PLC0415

    thread_count_before = threading.active_count()
    _install_parent_death_watchdog()
    # Give any spawned thread a moment to register
    import time as _time  # noqa: PLC0415

    _time.sleep(0.05)
    thread_count_after = threading.active_count()
    # Must not have started a watchdog thread
    assert thread_count_after <= thread_count_before


def test_parent_death_watchdog_ignores_invalid_env_var(monkeypatch) -> None:
    """Non-integer RCFLOW_PARENT_PID must be ignored (don't crash)."""
    monkeypatch.setenv("RCFLOW_PARENT_PID", "not-a-pid")

    from src.__main__ import _install_parent_death_watchdog  # noqa: PLC0415

    thread_count_before = threading.active_count()
    _install_parent_death_watchdog()
    import time as _time  # noqa: PLC0415

    _time.sleep(0.05)
    thread_count_after = threading.active_count()
    assert thread_count_after <= thread_count_before
