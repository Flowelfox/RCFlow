"""Tests for the singleton IPC helpers in src.gui.core."""

from __future__ import annotations

import socket
import sys
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

# src.gui.__init__ eagerly imports src.gui.windows, which imports
# customtkinter at module scope.  Provide a stub so the IPC test does not
# require the real GUI toolkit in CI / dev environments.
sys.modules.setdefault("customtkinter", MagicMock())

from src.gui.core import (  # noqa: E402
    _IPC_SHOW_CMD,
    _ipc_file_path,
    remove_ipc_file,
    send_show_to_existing,
    start_ipc_server,
)


def _wait_for(pred, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_show_roundtrip_invokes_callback(tmp_path: Path) -> None:
    """Running instance receives SHOW and fires the on_show callback."""
    fired = threading.Event()

    with patch("src.paths.get_data_dir", return_value=tmp_path):
        srv = start_ipc_server(fired.set)
        assert srv is not None, "IPC server must bind on loopback"
        try:
            delivered = send_show_to_existing()
            assert delivered is True
            assert _wait_for(fired.is_set)
        finally:
            srv.close()
            remove_ipc_file()


def test_send_returns_false_when_no_ipc_file(tmp_path: Path) -> None:
    """With no running singleton there is nothing to connect to."""
    with patch("src.paths.get_data_dir", return_value=tmp_path):
        # Make sure no stale file from an earlier test leaks in.
        path = _ipc_file_path()
        if path.exists():
            path.unlink()
        assert send_show_to_existing() is False


def test_send_returns_false_when_port_is_stale(tmp_path: Path) -> None:
    """Dead singleton leaves a stale port file; callers must fail gracefully."""
    with patch("src.paths.get_data_dir", return_value=tmp_path):
        path = _ipc_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Pick an ephemeral port and close immediately so no one listens there.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        stale_port = sock.getsockname()[1]
        sock.close()
        path.write_text(str(stale_port), encoding="utf-8")

        assert send_show_to_existing() is False


def test_non_show_payload_does_not_fire_callback(tmp_path: Path) -> None:
    """Garbage bytes on the socket must not trigger the window reveal."""
    fired = threading.Event()

    with patch("src.paths.get_data_dir", return_value=tmp_path):
        srv = start_ipc_server(fired.set)
        assert srv is not None
        try:
            port = srv.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=1.0) as s:
                s.sendall(b"PING\n")
            # Give the accept loop a moment; the callback must not fire.
            time.sleep(0.15)
            assert not fired.is_set()
        finally:
            srv.close()
            remove_ipc_file()


def test_show_cmd_constant_is_expected_bytes() -> None:
    """Guard against accidental protocol changes."""
    assert _IPC_SHOW_CMD == b"SHOW\n"
