"""Tests for src/gui.py — focused on settings persistence behaviour."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path


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

    import sys  # noqa: PLC0415
    with (
        patch.dict(sys.modules, {
            "tkinter": tk_mock,
            "tkinter.scrolledtext": MagicMock(),
            "tkinter.ttk": MagicMock(),
        }),
        # Keep the real config module but redirect settings.json to tmp_path
        patch("src.paths.get_install_dir", return_value=tmp_path),
        # Prevent auto-start side effects
        patch("src.gui.RCFlowGUI._setup_tray", return_value=False),
        patch("src.gui.RCFlowGUI._start_server"),
        patch("src.gui.RCFlowGUI._set_window_icon"),
    ):
        from src.gui import RCFlowGUI  # noqa: PLC0415

        gui = RCFlowGUI.__new__(RCFlowGUI)
        # Manually initialise only the attributes touched by _start_server
        gui._ip_var = _Var("0.0.0.0")
        gui._port_var = _Var("53890")
        gui._wss_var = _Var(True)
        gui._server_lock = __import__("threading").Lock()
        gui._server_proc = None
        gui._start_time = None
        gui._quitting = False
        gui._log_queue = __import__("queue").Queue()

        # Stub UI-mutating calls so they don't raise
        for attr in (
            "_ip_entry", "_port_entry", "_wss_check", "_toggle_btn",
            "_status_label", "_uptime_var", "_bound_addr_var",
            "_sessions_var", "_backend_id_var",
        ):
            setattr(gui, attr, MagicMock())
        gui._set_status = MagicMock()
        gui._log_append = MagicMock()

    return gui  # type: ignore[return-value]


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

    # Patch socket.socket so the port-availability check always passes
    with (
        patch("socket.socket") as mock_sock,
        patch("subprocess.Popen") as mock_popen,
        patch("src.paths.get_install_dir", return_value=tmp_path),
    ):
        mock_sock.return_value.__enter__ = MagicMock(return_value=mock_sock.return_value)
        mock_sock.return_value.__exit__ = MagicMock(return_value=False)
        mock_sock.return_value.bind = MagicMock()
        mock_sock.return_value.close = MagicMock()
        mock_popen.return_value.pid = 9999
        mock_popen.return_value.poll.return_value = None

        # Import after patching install dir so _get_settings_path resolves correctly
        with patch("src.config._get_settings_path", return_value=settings_path):
            # Temporarily restore the real _start_server (it was stubbed during __new__)
            import src.gui as gui_mod  # noqa: PLC0415
            gui._start_server = gui_mod.RCFlowGUI._start_server.__get__(gui)
            gui._start_server()

    assert settings_path.exists(), "settings.json was not created"
    data = json.loads(settings_path.read_text())
    assert data.get("RCFLOW_HOST") == "192.168.50.10", (
        f"RCFLOW_HOST not persisted; got: {data.get('RCFLOW_HOST')!r}"
    )
    assert data.get("RCFLOW_PORT") == "54321", (
        f"RCFLOW_PORT not persisted; got: {data.get('RCFLOW_PORT')!r}"
    )


def test_start_server_persists_default_host_and_port(tmp_path: Path) -> None:
    """Even the default values must be written on Start so subsequent reads are consistent."""
    settings_path = tmp_path / "settings.json"

    gui = _make_gui(tmp_path)
    # Leave defaults: 0.0.0.0 / 53890

    with (
        patch("socket.socket") as mock_sock,
        patch("subprocess.Popen") as mock_popen,
        patch("src.config._get_settings_path", return_value=settings_path),
    ):
        mock_sock.return_value.bind = MagicMock()
        mock_sock.return_value.close = MagicMock()
        mock_popen.return_value.pid = 1234
        mock_popen.return_value.poll.return_value = None

        import src.gui as gui_mod  # noqa: PLC0415
        gui._start_server = gui_mod.RCFlowGUI._start_server.__get__(gui)
        gui._start_server()

    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert data.get("RCFLOW_HOST") == "0.0.0.0"
    assert data.get("RCFLOW_PORT") == "53890"
