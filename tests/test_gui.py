"""Tests for src/gui.py — focused on settings persistence behaviour."""

from __future__ import annotations

import json
import sys
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
