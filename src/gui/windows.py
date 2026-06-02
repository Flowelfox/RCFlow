"""Windows GUI + system tray application for the RCFlow worker.

Launches a CustomTkinter window with server controls, a status badge,
instance details, and a live log viewer.  The server runs as a managed
subprocess via ServerManager.  Closing the window minimizes to the system
tray; double-clicking the tray icon restores the window.  "Quit" from the
tray stops the server and exits the application entirely.

The dashboard widgets, server lifecycle, autostart helpers, and singleton
lock all live in :mod:`src.gui._dashboard_ctk`; this module only adds the
Windows-specific tray (pystray) and HICON / DPI / AppUserModelID glue.

macOS has its own NSStatusBar implementation in :mod:`src.gui.macos`.
The Linux path lives in :mod:`src.gui.linux_app` and runs under the host's
system Python interpreter.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

from src.gui._dashboard_ctk import (
    RCFlowDashboard,
    acquire_singleton_lock,
    autostart_menu_label,
    is_autostart_enabled,
)
from src.gui.core import send_show_to_existing

if TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)

# Win32 constants used by _install_win32_icon. Kept at module scope so ruff
# N806 doesn't trip on PEP8 uppercase naming inside the method body.
_LR_LOADFROMFILE = 0x00000010
_LR_SHARED = 0x00008000
_IMAGE_ICON = 1
_WM_SETICON = 0x0080
_ICON_SMALL = 0
_ICON_BIG = 1
_SM_CXICON = 11  # GetSystemMetrics — large icon width (Alt-Tab / taskbar)
_SM_CXSMICON = 49  # GetSystemMetrics — small icon width (title bar)
_GCLP_HICON = -14
_GCLP_HICONSM = -34


class WindowsGUI(RCFlowDashboard):
    """Windows-specific subclass: pystray tray + Win32 HICON window icon."""

    # ── Window icon ───────────────────────────────────────────────────────

    def _install_platform_icon(self) -> None:
        """Install the RCFlow icon as the window's title-bar / taskbar / Alt-Tab icon."""
        from src.paths import get_install_dir, is_frozen  # noqa: PLC0415

        icon_path = (
            get_install_dir() / "tray_icon.ico"
            if is_frozen()
            else Path(__file__).resolve().parent / "assets" / "tray_icon.ico"
        )
        if not icon_path.exists():
            return

        # Set the .ico as the default for any future Tk toplevels (dialogs).
        with contextlib.suppress(tk.TclError):
            self._root.iconbitmap(default=str(icon_path))

        if sys.platform == "win32":
            self._install_win32_icon(icon_path)

    def _install_win32_icon(self, icon_path: Path) -> None:
        """Load ico + hand native-sized HICONs to WM_SETICON + the window class."""
        try:
            import ctypes  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415
        except ImportError:
            return

        user32 = ctypes.windll.user32  # ty:ignore[unresolved-attribute]

        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = wintypes.LPARAM
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        set_class_long = getattr(user32, "SetClassLongPtrW", getattr(user32, "SetClassLongW", None))
        if set_class_long is None:
            return
        set_class_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_class_long.restype = ctypes.c_void_p

        with contextlib.suppress(OSError, AttributeError):
            dpi_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
            if dpi_context is not None:
                dpi_context(ctypes.c_void_p(-4))
            else:
                user32.SetProcessDPIAware()

        try:
            hwnd_str = self._root.wm_frame()
            hwnd = int(hwnd_str, 16) if hwnd_str else self._root.winfo_id()
        except (tk.TclError, ValueError):
            return

        cx_small = user32.GetSystemMetrics(_SM_CXSMICON) or 16
        cx_big = user32.GetSystemMetrics(_SM_CXICON) or 32

        ico = str(icon_path)
        flags = _LR_LOADFROMFILE | _LR_SHARED
        h_small = user32.LoadImageW(None, ico, _IMAGE_ICON, cx_small, cx_small, flags)
        h_big = user32.LoadImageW(None, ico, _IMAGE_ICON, cx_big, cx_big, flags)
        if not h_small and not h_big:
            return
        h_small = h_small or h_big
        h_big = h_big or h_small

        user32.SendMessageW(hwnd, _WM_SETICON, _ICON_SMALL, h_small)
        user32.SendMessageW(hwnd, _WM_SETICON, _ICON_BIG, h_big)
        set_class_long(hwnd, _GCLP_HICONSM, h_small)
        set_class_long(hwnd, _GCLP_HICON, h_big)
        # Pin handles for the window's lifetime.
        self._win32_icon_handles = (h_small, h_big)

    # ── System tray (pystray) ─────────────────────────────────────────────

    def _setup_tray(self) -> bool:
        """Set up the system tray icon. Returns True on success."""
        import os  # noqa: PLC0415

        # Operator escape hatch — set ``RCFLOW_DISABLE_TRAY=1`` to force
        # window-only mode (useful when testing without a tray host).
        if os.environ.get("RCFLOW_DISABLE_TRAY", "").lower() in {"1", "true", "yes"}:
            logger.info("RCFLOW_DISABLE_TRAY set — running RCFlow Worker without a tray icon.")
            return False

        try:
            import pystray  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415
        except ImportError:
            logger.info("pystray/Pillow not available — running without tray icon")
            return False

        icon_image = self._load_tray_icon(Image)
        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: "RCFlow Worker: Running" if self._server.is_running() else "RCFlow Worker: Stopped",
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda item: f"External: {self._external_addr_mirror or '—'}",
                None,
                enabled=False,
                visible=lambda item: self._upnp_enabled_mirror or self._natpmp_enabled_mirror,
            ),
            pystray.MenuItem(
                lambda item: f"Update available — install v{self._update_latest_mirror}",
                self._on_tray_install_update,
                visible=lambda item: self._update_available_mirror,
            ),
            pystray.MenuItem("Dashboard", self._on_tray_open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Stop Server" if self._server.is_running() else "Start Server",
                self._on_tray_toggle_server,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Copy Token", self._on_tray_copy_token),
            pystray.MenuItem("Add to Client…", self._on_tray_add_to_client),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                autostart_menu_label(),
                self._on_toggle_autostart,
                checked=lambda item: is_autostart_enabled(),
                visible=sys.platform == "win32",
            ),
            pystray.MenuItem("Check for Updates", self._on_tray_check_updates),
            pystray.MenuItem("Quit", self._on_tray_quit),
        )
        icon = pystray.Icon("rcflow", icon_image, "RCFlow Worker", menu)
        self._tray_icon = icon
        threading.Thread(target=icon.run, daemon=True).start()
        return True

    def _update_tray_status(self) -> None:
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.update_menu()

    @staticmethod
    def _load_tray_icon(image_module: ModuleType) -> object:
        from src.paths import get_install_dir, is_frozen  # noqa: PLC0415

        icon_path = (
            get_install_dir() / "tray_icon.ico"
            if is_frozen()
            else Path(__file__).resolve().parent / "assets" / "tray_icon.ico"
        )
        if icon_path.exists():
            return image_module.open(str(icon_path))

        from PIL import ImageDraw  # noqa: PLC0415

        img = image_module.new("RGBA", (64, 64), (15, 23, 42, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, 59, 59], radius=8, fill=(56, 189, 248, 255))
        draw.text((14, 16), "RC", fill=(15, 23, 42, 255))
        return img


# ── Process bootstrap (Windows-only) ──────────────────────────────────────────


def _enable_dpi_awareness() -> None:
    """Mark the process DPI-aware before any window is created."""
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        ctx = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if ctx is not None:
            ctx(ctypes.c_void_p(-4))
        else:
            user32.SetProcessDPIAware()
    except (OSError, AttributeError) as exc:
        logger.debug("Failed to set DPI awareness: %s", exc)


def _set_app_user_model_id() -> None:
    """Tell Windows this process is its own application."""
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.rcflow.worker")
    except (OSError, AttributeError) as exc:
        logger.debug("Failed to set AppUserModelID: %s", exc)


def run_gui(*, minimized: bool = False) -> None:
    """Entry point for the Windows GUI + tray application."""
    _enable_dpi_awareness()
    _set_app_user_model_id()
    if not acquire_singleton_lock():
        if not minimized:
            delivered = send_show_to_existing()
            if not delivered:
                print(
                    "RCFlow Worker is already running. Look for its icon in the system tray.",
                    file=sys.stderr,
                )
        sys.exit(0)

    gui = WindowsGUI()
    gui.run(minimized=minimized)
