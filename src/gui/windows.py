"""Windows GUI + system tray application for the RCFlow backend server.

Launches a CustomTkinter window with server controls, a status badge,
instance details, and a live log viewer.  The server runs as a managed
subprocess via ServerManager.  Closing the window minimizes to the system
tray; double-clicking the tray icon restores the window.  "Quit" from the
tray stops the server and exits the application entirely.

This is the default mode for frozen Windows builds.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Protocol

import customtkinter as ctk  # ty:ignore[unresolved-import]

from src.gui import theme
from src.gui.core import (
    MAX_LOG_LINES,
    POLL_MS,
    LogBuffer,
    ServerManager,
    attach_copy_context_menu,
    make_text_readonly,
    poll_server_status,
    remove_ipc_file,
    send_show_to_existing,
    start_ipc_server,
)

# Apply appearance settings before any CTk window is created
ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

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


class _TrayIconProtocol(Protocol):
    """Structural type for pystray.Icon (optional dependency)."""

    def update_menu(self) -> None: ...
    def stop(self) -> None: ...


class RCFlowGUI:
    """CTk window + system tray for the RCFlow server."""

    def __init__(self) -> None:
        self._log_buffer = LogBuffer()
        self._server = ServerManager(self._log_buffer)
        self._quitting = False
        self._tray_icon: _TrayIconProtocol | None = None
        # Loopback IPC listener so a second `rcflow gui` launch can reveal
        # this instance's dashboard instead of starting a broken second GUI.
        self._ipc_server: object | None = None
        # monotonic expiry for transient status messages (copy-token feedback).
        # _update_ui will not overwrite the status pill while this is in the
        # future, so the message is visible for ~3 seconds regardless of the
        # 300 ms _update_ui polling rate.
        self._status_sticky_until: float = 0.0
        # Thread-safe mirrors of Tk vars consulted from the pystray daemon
        # thread.  Reading ``tk.BooleanVar.get()`` / ``StringVar.get()`` off
        # the Tk main thread raises ``RuntimeError: main thread is not in
        # main loop``, so menu visibility / title lambdas use these instead.
        self._upnp_enabled_mirror: bool = False
        self._natpmp_enabled_mirror: bool = False
        self._external_addr_mirror: str = "—"

        self._root = ctk.CTk()
        self._root.title("RCFlow Worker")
        self._root.geometry("900x720")
        # Settings card uses 2 rows (inputs + checkboxes) and Instance
        # Details wraps to 2 rows of 3 fields, so the dashboard fits on
        # narrower screens (1024-px laptops, half-screen splits).
        self._root.minsize(720, 520)
        self._root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._set_window_icon()
        self._build_ui()
        self._load_settings()

    # ── UI construction ───────────────────────────────────────────────────

    def _set_window_icon(self) -> None:
        """Install the RCFlow icon as the window's title-bar / taskbar / Alt-Tab icon.

        Tk's ``iconbitmap`` and ``iconphoto`` on Windows both end up
        sending a single rasterised size via ``WM_SETICON`` (typically
        the 32x32 bitmap from the .ico); on HiDPI displays Windows then
        stretches that 32-px bitmap up to 40/48/64 px for the taskbar,
        which is what the user saw as "low-res, stretched".

        We bypass Tk's abstractions here and call ``LoadImageW`` directly
        via ctypes to pull the correctly-sized bitmaps from the multi-
        resolution .ico (``LoadImage`` picks the best embedded match
        rather than scaling). The small icon (title bar) is loaded at
        the system's ``SM_CXSMICON`` size and the large icon (taskbar /
        Alt-Tab) at a DPI-scaled ``SM_CXICON``. Both are installed on
        the HWND via ``WM_SETICON`` *and* the window class via
        ``SetClassLongPtrW`` so anywhere Windows queries the icon (the
        Alt-Tab switcher uses class icons, WM_SETICON targets per-
        window state) picks up a native-sized bitmap instead of a
        stretched one.

        ``iconbitmap(default=...)`` is still called as a backstop so Tk-
        created child toplevels (dialogs) inherit the icon.
        """
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
        # SetClassLongPtrW lives under SetClassLongW on 32-bit builds and
        # SetClassLongPtrW on 64-bit; ctypes maps both via getattr fallback.
        set_class_long = getattr(user32, "SetClassLongPtrW", getattr(user32, "SetClassLongW", None))
        if set_class_long is None:
            return
        set_class_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_class_long.restype = ctypes.c_void_p

        # Ask Windows to make us DPI-aware so GetSystemMetrics returns
        # the scaled size instead of the 96-dpi baseline. Idempotent.
        with contextlib.suppress(OSError, AttributeError):
            # SetProcessDpiAwarenessContext is only on Windows 10 1703+;
            # fall back to SetProcessDPIAware for older builds.
            dpi_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
            if dpi_context is not None:
                # -4 = PER_MONITOR_AWARE_V2, the modern behaviour.
                dpi_context(ctypes.c_void_p(-4))
            else:
                user32.SetProcessDPIAware()

        # Resolve the toplevel HWND. Tk gives the frame window via
        # wm_frame() as a hex string; that's the window Explorer sees
        # for WM_SETICON / the window class.
        try:
            hwnd_str = self._root.wm_frame()
            hwnd = int(hwnd_str, 16) if hwnd_str else self._root.winfo_id()
        except (tk.TclError, ValueError):
            return

        cx_small = user32.GetSystemMetrics(_SM_CXSMICON) or 16
        cx_big = user32.GetSystemMetrics(_SM_CXICON) or 32

        ico = str(icon_path)
        flags = _LR_LOADFROMFILE | _LR_SHARED
        # LoadImageW with explicit cx/cy picks the closest embedded
        # resolution — no stretching from 32->48.
        h_small = user32.LoadImageW(None, ico, _IMAGE_ICON, cx_small, cx_small, flags)
        h_big = user32.LoadImageW(None, ico, _IMAGE_ICON, cx_big, cx_big, flags)
        if not h_small and not h_big:
            return
        # If only one load succeeded, reuse it for both slots so the
        # other slot does not fall back to Tk's stretched bitmap.
        h_small = h_small or h_big
        h_big = h_big or h_small

        user32.SendMessageW(hwnd, _WM_SETICON, _ICON_SMALL, h_small)
        user32.SendMessageW(hwnd, _WM_SETICON, _ICON_BIG, h_big)
        set_class_long(hwnd, _GCLP_HICONSM, h_small)
        set_class_long(hwnd, _GCLP_HICON, h_big)
        # Keep the HICON handles pinned on the instance so Windows does
        # not free them out from under us before the window is destroyed.
        self._win32_icon_handles = (h_small, h_big)

    def _build_ui(self) -> None:
        p = theme.PAD_OUTER
        g = theme.PAD_GROUP
        s = theme.PAD_SMALL

        self._root.grid_columnconfigure(0, weight=1)
        self._root.grid_rowconfigure(3, weight=1)  # log row expands

        # ── Settings + controls ──────────────────────────────────────
        top = ctk.CTkFrame(self._root, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=p, pady=(p, 0))
        top.grid_columnconfigure(0, weight=1)

        sc = ctk.CTkFrame(top, corner_radius=8)
        sc.grid(row=0, column=0, sticky="ew")

        # Two-row settings layout — bind inputs (IP + port) on row 1,
        # protocol toggles on row 2 — so the dashboard stays usable on
        # narrower screens (1024-px laptops, half-screen splits, etc.).
        ctk.CTkLabel(
            sc,
            text="Server Settings",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=g, pady=(g, s))

        ctk.CTkLabel(sc, text="IP Address", font=ctk.CTkFont(size=theme.FONT_SIZE_BODY)).grid(
            row=1, column=0, sticky="w", padx=(g, s), pady=(0, g)
        )
        self._ip_var = tk.StringVar(value="0.0.0.0")
        self._ip_entry = ctk.CTkEntry(
            sc, textvariable=self._ip_var, width=155, font=ctk.CTkFont(size=theme.FONT_SIZE_BODY)
        )
        self._ip_entry.grid(row=1, column=1, sticky="w", padx=(0, g), pady=(0, g))

        ctk.CTkLabel(sc, text="Port", font=ctk.CTkFont(size=theme.FONT_SIZE_BODY)).grid(
            row=1, column=2, sticky="w", padx=(0, s), pady=(0, g)
        )
        from src.config import _DEFAULT_PORT  # noqa: PLC0415

        self._port_var = tk.StringVar(value=str(_DEFAULT_PORT))
        self._port_entry = ctk.CTkEntry(
            sc, textvariable=self._port_var, width=76, font=ctk.CTkFont(size=theme.FONT_SIZE_BODY)
        )
        self._port_entry.grid(row=1, column=3, sticky="w", padx=(0, g), pady=(0, g))

        self._wss_var = tk.BooleanVar(value=True)
        self._wss_check = ctk.CTkCheckBox(
            sc,
            text="WSS Enabled",
            variable=self._wss_var,
            command=self._on_wss_toggle,
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY),
        )
        self._wss_check.grid(row=2, column=0, sticky="w", padx=(g, g), pady=(0, g))

        self._upnp_var = tk.BooleanVar(value=False)
        self._upnp_check = ctk.CTkCheckBox(
            sc,
            text="UPnP Port Forwarding",
            variable=self._upnp_var,
            command=self._on_upnp_toggle,
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY),
        )
        self._upnp_check.grid(row=2, column=1, columnspan=2, sticky="w", padx=(0, g), pady=(0, g))

        self._natpmp_var = tk.BooleanVar(value=False)
        self._natpmp_check = ctk.CTkCheckBox(
            sc,
            text="VPN Port Forwarding (NAT-PMP)",
            variable=self._natpmp_var,
            command=self._on_natpmp_toggle,
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY),
        )
        self._natpmp_check.grid(row=2, column=3, sticky="w", padx=(0, g), pady=(0, g))

        # Action buttons (right of settings card)
        btns = ctk.CTkFrame(top, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="ne", padx=(g, 0))

        self._toggle_btn = ctk.CTkButton(
            btns,
            text="Start",
            width=104,
            fg_color=theme.BTN_START_FG,
            hover_color=theme.BTN_START_HOVER,
            text_color=theme.BTN_START_TEXT,
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY, weight="bold"),
            command=self._on_toggle,
        )
        self._toggle_btn.pack(pady=(0, s))

        self._copy_token_btn = ctk.CTkButton(
            btns,
            text="Copy Token",
            width=104,
            fg_color=theme.BTN_COPY_FG,
            hover_color=theme.BTN_COPY_HOVER,
            text_color=theme.BTN_COPY_TEXT,
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY),
            command=self._on_copy_token,
        )
        self._copy_token_btn.pack(pady=(0, s))

        self._add_client_btn = ctk.CTkButton(
            btns,
            text="Add to Client",
            width=104,
            fg_color=theme.BTN_COPY_FG,
            hover_color=theme.BTN_COPY_HOVER,
            text_color=theme.BTN_COPY_TEXT,
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY),
            command=self._on_add_to_client,
        )
        self._add_client_btn.pack()

        # ── Status pill ──────────────────────────────────────────────
        self._status_label = ctk.CTkLabel(
            self._root,
            text="  Stopped  ",
            fg_color=theme.STATUS_STOPPED,
            text_color=("#ffffff", "#e5e7eb"),
            corner_radius=6,
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        )
        self._status_label.grid(row=1, column=0, sticky="w", padx=p, pady=(s + 2, 0))

        # ── Instance details card ────────────────────────────────────
        dc = ctk.CTkFrame(self._root, corner_radius=8)
        dc.grid(row=2, column=0, sticky="ew", padx=p, pady=(s, 0))

        ctk.CTkLabel(
            dc,
            text="Instance Details",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=0, column=0, columnspan=6, sticky="w", padx=g, pady=(g, s))

        # 2-row \u00d7 3-field grid keeps the card width manageable on small
        # screens.  Per-field widths target typical contents; users can
        # scroll within the read-only entry to see / select longer values.
        detail_info = [
            ("Bound Address", "_bound_addr_var", 130),
            ("Uptime", "_uptime_var", 80),
            ("Active Sessions", "_sessions_var", 50),
            ("Backend ID", "_backend_id_var", 100),
            ("Version", "_version_var", 70),
            ("External Address", "_external_addr_var", 170),
        ]
        # Tight 2-row layout: zero vertical pad between rows so the two
        # stacked detail rows read as a single block.  Row 2 still gets
        # ``g`` pad below for normal card-bottom breathing room.  Entry
        # height is also pulled in to match the label baseline since
        # CTkEntry defaults to 28 px which inflates the row.
        for index, (label, var_name, value_width) in enumerate(detail_info):
            row = 1 + index // 3
            col_pair = index % 3
            label_col = col_pair * 2
            value_col = col_pair * 2 + 1
            row_pady = (0, 0) if row == 1 else (0, g)
            ctk.CTkLabel(
                dc,
                text=label,
                font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
                text_color=("gray40", "gray60"),
            ).grid(
                row=row,
                column=label_col,
                sticky="w",
                padx=(g if col_pair == 0 else g // 2, s),
                pady=row_pady,
            )
            var = tk.StringVar(value="\u2014")
            setattr(self, var_name, var)
            # Read-only Entry instead of Label so users can select + copy
            # values (Ctrl+C / Cmd+C).  Borderless transparent styling keeps
            # the visual treatment close to the original Label.
            ctk.CTkEntry(
                dc,
                textvariable=var,
                state="readonly",
                width=value_width,
                height=20,
                border_width=0,
                corner_radius=0,
                fg_color="transparent",
                font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
            ).grid(row=row, column=value_col, sticky="w", padx=(0, s), pady=row_pady)

        # ── Log viewer card ──────────────────────────────────────────
        lc = ctk.CTkFrame(self._root, corner_radius=8)
        lc.grid(row=3, column=0, sticky="nsew", padx=p, pady=(s, p))
        lc.grid_rowconfigure(1, weight=1)
        lc.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            lc,
            text="Server Log",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=g, pady=(g, s))

        self._log_box = ctk.CTkTextbox(lc, wrap="word", font=(theme.mono_font(), theme.FONT_SIZE_LOG))
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=s, pady=(0, s))

        # Apply syntax-highlight tags on the underlying tk.Text widget
        _dark = ctk.get_appearance_mode().lower() == "dark"
        self._log_widget = self._log_box._textbox
        self._log_widget.tag_configure("error", foreground=theme.LOG_DARK_ERROR if _dark else theme.LOG_LIGHT_ERROR)
        self._log_widget.tag_configure("warning", foreground=theme.LOG_DARK_WARN if _dark else theme.LOG_LIGHT_WARN)
        # Keep the log viewer read-only while still allowing text selection and
        # Ctrl/Cmd+C — a plain ``state='disabled'`` blocks selection on X11.
        make_text_readonly(self._log_widget)
        attach_copy_context_menu(self._log_widget)

    # ── Settings I/O ─────────────────────────────────────────────────────

    def _load_settings(self) -> None:
        try:
            from src.config import Settings  # noqa: PLC0415

            s = Settings()
            self._ip_var.set(s.RCFLOW_HOST)
            self._port_var.set(str(s.RCFLOW_PORT))
            self._wss_var.set(s.WSS_ENABLED)
            self._upnp_var.set(s.UPNP_ENABLED)
            self._natpmp_var.set(s.NATPMP_ENABLED)
            self._upnp_enabled_mirror = bool(s.UPNP_ENABLED)
            self._natpmp_enabled_mirror = bool(s.NATPMP_ENABLED)
        except Exception:
            pass
        self._apply_forwarding_mutex()

    def _on_wss_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        update_settings_file({"WSS_ENABLED": str(self._wss_var.get())})

    def _on_upnp_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        enabled = bool(self._upnp_var.get())
        self._upnp_enabled_mirror = enabled
        updates: dict[str, str] = {"UPNP_ENABLED": "true" if enabled else "false"}
        # Mutex with NAT-PMP: enabling UPnP turns NAT-PMP off.  Both routes
        # cannot coexist usefully — VPN captures the default route and
        # routing asymmetry breaks the UPnP path while VPN is active.
        if enabled and self._natpmp_var.get():
            self._natpmp_var.set(False)
            self._natpmp_enabled_mirror = False
            updates["NATPMP_ENABLED"] = "false"
        update_settings_file(updates)
        self._apply_forwarding_mutex()

    def _on_natpmp_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        enabled = bool(self._natpmp_var.get())
        self._natpmp_enabled_mirror = enabled
        updates: dict[str, str] = {"NATPMP_ENABLED": "true" if enabled else "false"}
        # Mutex with UPnP — see ``_on_upnp_toggle`` for the rationale.
        if enabled and self._upnp_var.get():
            self._upnp_var.set(False)
            self._upnp_enabled_mirror = False
            updates["UPNP_ENABLED"] = "false"
        update_settings_file(updates)
        self._apply_forwarding_mutex()

    def _apply_forwarding_mutex(self) -> None:
        """Grey out the inactive port-forwarding checkbox to make the mutex visible.

        Disables whichever of UPnP / NAT-PMP is currently unchecked while the
        other is on, so users see at a glance that only one can run at a
        time.  No-op while the server is running because both checkboxes are
        already disabled by the start path.
        """
        if self._server.is_running():
            return
        upnp_on = bool(self._upnp_var.get())
        natpmp_on = bool(self._natpmp_var.get())
        self._upnp_check.configure(state="disabled" if natpmp_on else "normal")
        self._natpmp_check.configure(state="disabled" if upnp_on else "normal")

    def _on_copy_token(self) -> None:
        try:
            from src.config import read_token_from_file  # noqa: PLC0415

            api_key = read_token_from_file()
            if not api_key:
                self._set_status("No API token configured", error=True, sticky=True)
                return
            self._root.clipboard_clear()
            self._root.clipboard_append(api_key)
            self._root.update()  # Required on Windows for clipboard to persist
            self._set_status("Token copied to clipboard", sticky=True)
        except Exception as exc:
            self._set_status(f"Failed to copy token: {exc}", error=True, sticky=True)

    def _on_add_to_client(self) -> None:
        try:
            import webbrowser  # noqa: PLC0415

            from src.config import read_token_from_file  # noqa: PLC0415
            from src.gui.deeplink import build_add_worker_url  # noqa: PLC0415

            host = self._ip_var.get().strip()
            port_str = self._port_var.get().strip()
            if not host or not port_str:
                self._set_status("Set IP and port first", error=True, sticky=True)
                return
            try:
                port = int(port_str)
            except ValueError:
                self._set_status("Invalid port number", error=True, sticky=True)
                return
            api_key = read_token_from_file()
            if not api_key:
                self._set_status("No API token configured", error=True, sticky=True)
                return
            url = build_add_worker_url(host, port, api_key, wss=bool(self._wss_var.get()))
            webbrowser.open(url)
            self._set_status("Opening in client...", sticky=True)
        except Exception as exc:
            self._set_status(f"Failed to launch client: {exc}", error=True, sticky=True)

    # ── Server controls ───────────────────────────────────────────────────

    def _on_toggle(self) -> None:
        if self._server.is_running():
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self) -> None:
        host = self._ip_var.get().strip()
        port_str = self._port_var.get().strip()

        if not host:
            self._set_status("Error: IP address is empty", error=True)
            return
        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self._set_status("Error: Invalid port number", error=True)
            return

        err = self._server.start(host, port, self._wss_var.get())
        if err:
            self._set_status(err, error=True)
            return

        self._ip_entry.configure(state="disabled")
        self._port_entry.configure(state="disabled")
        self._wss_check.configure(state="disabled")
        self._upnp_check.configure(state="disabled")
        self._natpmp_check.configure(state="disabled")
        self._toggle_btn.configure(
            text="Stop", fg_color=theme.BTN_STOP_FG, hover_color=theme.BTN_STOP_HOVER, text_color=theme.BTN_STOP_TEXT
        )
        protocol = "WSS" if self._wss_var.get() else "WS"
        self._set_status(f"Starting ({protocol})...")

    def _stop_server(self) -> None:
        self._set_status("Stopping...")
        self._server.stop()

    def _on_adopted_server(self) -> None:
        """Reflect an adopted running server in the UI.

        Mirrors the state transitions ``_start_server`` makes after a
        successful launch (disable settings, flip toggle to Stop, update
        status pill and tray), but without spawning a new subprocess.
        """
        self._ip_entry.configure(state="disabled")
        self._port_entry.configure(state="disabled")
        self._wss_check.configure(state="disabled")
        self._upnp_check.configure(state="disabled")
        self._natpmp_check.configure(state="disabled")
        self._toggle_btn.configure(
            text="Stop", fg_color=theme.BTN_STOP_FG, hover_color=theme.BTN_STOP_HOVER, text_color=theme.BTN_STOP_TEXT
        )
        protocol = "WSS" if self._wss_var.get() else "WS"
        self._set_status(f"Running ({protocol}) — recovered", sticky=True)
        self._update_tray_status()

    # ── Log display ───────────────────────────────────────────────────────

    def _drain_log_queue(self) -> None:
        lines = self._log_buffer.drain()
        if not lines:
            return

        at_bottom = self._log_widget.yview()[1] >= 0.95

        for line in lines:
            upper = line.upper()
            tag: tuple[str, ...] = ()
            if "ERROR" in upper or "CRITICAL" in upper:
                tag = ("error",)
            elif "WARNING" in upper:
                tag = ("warning",)
            self._log_widget.insert(tk.END, line + "\n", tag)

        total = int(self._log_widget.index("end-1c").split(".")[0])
        if total > MAX_LOG_LINES:
            self._log_widget.delete("1.0", f"{total - MAX_LOG_LINES}.0")

        if at_bottom:
            self._log_widget.see(tk.END)

    # ── Status & details ──────────────────────────────────────────────────

    def _set_status(self, text: str, *, error: bool = False, sticky: bool = False) -> None:
        tl = text.lower()
        if error:
            color = theme.STATUS_ERROR
        elif "stop" in tl or text == "Stopped":
            color = theme.STATUS_STOPPED
        elif "start" in tl:
            color = theme.STATUS_STARTING
        else:
            color = theme.STATUS_RUNNING
        if sticky:
            self._status_sticky_until = time.monotonic() + 3.0
        self._status_label.configure(text=f"  {text}  ", fg_color=color)

    def _update_ui(self) -> None:
        """Periodic UI refresh (300 ms)."""
        if self._quitting:
            return

        self._drain_log_queue()
        running = self._server.is_running()

        if running:
            protocol = "WSS" if self._wss_var.get() else "WS"
            if time.monotonic() >= self._status_sticky_until:
                self._set_status(f"Running ({protocol})")
            t = self._server.start_time
            if t is not None:
                h, rem = divmod(int(time.monotonic() - t), 3600)
                m, s = divmod(rem, 60)
                self._uptime_var.set(f"{h:02d}:{m:02d}:{s:02d}")  # ty:ignore[unresolved-attribute]
            self._bound_addr_var.set(  # ty:ignore[unresolved-attribute]
                f"{self._ip_var.get()}:{self._port_var.get()}"
            )
        else:
            if self._toggle_btn.cget("text") == "Stop":
                rc = self._server.exit_code
                self._server.clear()
                self._ip_entry.configure(state="normal")
                self._port_entry.configure(state="normal")
                self._wss_check.configure(state="normal")
                self._upnp_check.configure(state="normal")
                self._natpmp_check.configure(state="normal")
                # Re-apply mutex once the running-state lock is gone so the
                # inactive forwarding checkbox returns to disabled if the
                # other was the active choice.
                self._apply_forwarding_mutex()
                self._toggle_btn.configure(
                    text="Start",
                    fg_color=theme.BTN_START_FG,
                    hover_color=theme.BTN_START_HOVER,
                    text_color=theme.BTN_START_TEXT,
                )
                if rc and rc != 0:
                    self._set_status(f"Stopped (exit code {rc})", error=True)
                    self._log_buffer.append(f"Server exited with code {rc}")
                else:
                    self._set_status("Stopped")
                self._uptime_var.set("\u2014")  # ty:ignore[unresolved-attribute]
                self._bound_addr_var.set("\u2014")  # ty:ignore[unresolved-attribute]
                self._sessions_var.set("\u2014")  # ty:ignore[unresolved-attribute]
                self._backend_id_var.set("\u2014")  # ty:ignore[unresolved-attribute]
                self._version_var.set("\u2014")  # ty:ignore[unresolved-attribute]
                self._external_addr_var.set("\u2014")  # ty:ignore[unresolved-attribute]
                self._external_addr_mirror = "\u2014"
                self._update_tray_status()

        self._root.after(POLL_MS, self._update_ui)

    # ── System tray ───────────────────────────────────────────────────────

    def _setup_tray(self) -> bool:
        """Set up the system tray icon. Returns True on success."""
        try:
            import pystray  # noqa: PLC0415  # ty:ignore[unresolved-import]
            from PIL import Image  # noqa: PLC0415  # ty:ignore[unresolved-import]
        except ImportError:
            logger.debug("pystray/Pillow not available — running without tray icon")
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
                "Start with Windows",
                self._on_toggle_autostart,
                checked=lambda item: _is_autostart_enabled(),
                visible=sys.platform == "win32",
            ),
            pystray.MenuItem("Quit", self._on_tray_quit),
        )
        icon = pystray.Icon("rcflow", icon_image, "RCFlow Worker", menu)
        self._tray_icon = icon
        threading.Thread(target=icon.run, daemon=True).start()
        return True

    @staticmethod
    def _load_tray_icon(Image: type) -> object:  # noqa: N803
        from src.paths import get_install_dir, is_frozen  # noqa: PLC0415

        icon_path = (
            get_install_dir() / "tray_icon.ico"
            if is_frozen()
            else Path(__file__).resolve().parent / "assets" / "tray_icon.ico"
        )
        if icon_path.exists():
            return Image.open(str(icon_path))  # ty:ignore[unresolved-attribute]

        from PIL import ImageDraw  # noqa: PLC0415  # ty:ignore[unresolved-import]

        img = Image.new("RGBA", (64, 64), (15, 23, 42, 255))  # ty:ignore[unresolved-attribute]
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, 59, 59], radius=8, fill=(56, 189, 248, 255))
        draw.text((14, 16), "RC", fill=(15, 23, 42, 255))
        return img

    def _update_tray_status(self) -> None:
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.update_menu()

    def _on_tray_open(self, icon: object = None, item: object = None) -> None:
        self._root.after(0, self._show_window)

    def _show_window(self) -> None:
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _on_tray_toggle_server(self, icon: object = None, item: object = None) -> None:
        # pystray callbacks run on a daemon thread — hop back to the Tk
        # main loop before touching widgets.
        def _apply() -> None:
            self._on_toggle()
            self._update_tray_status()

        self._root.after(0, _apply)

    def _on_tray_copy_token(self, icon: object = None, item: object = None) -> None:
        self._root.after(0, self._on_copy_token)

    def _on_tray_add_to_client(self, icon: object = None, item: object = None) -> None:
        self._root.after(0, self._on_add_to_client)

    def _on_toggle_autostart(self, icon: object, item: object) -> None:
        _set_autostart(not _is_autostart_enabled())
        self._update_tray_status()

    def _on_tray_quit(self, icon: object = None, item: object = None) -> None:
        self._quitting = True
        self._server.stop()
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.stop()
        if self._ipc_server is not None:
            with contextlib.suppress(Exception):
                self._ipc_server.close()  # ty:ignore[unresolved-attribute]
            self._ipc_server = None
        remove_ipc_file()
        self._root.after(0, self._root.destroy)

    # ── Window lifecycle ──────────────────────────────────────────────────

    def _on_window_close(self) -> None:
        if self._tray_icon is not None:
            self._root.withdraw()
        else:
            self._on_tray_quit()

    def run(self, *, minimized: bool = False) -> None:
        """Start the GUI event loop.

        When *minimized* is True (login autostart), the dashboard is hidden
        at launch — tray icon only, no window popup on boot.
        """
        self._setup_tray()
        if minimized and self._tray_icon is not None:
            self._root.withdraw()

        # Upgrade path: older builds wrote the autostart registry value
        # without ``--minimized``, so reboots popped up a dashboard.  If
        # autostart is enabled, rewrite the value to include the flag.
        # Idempotent for already-new values.
        if _is_autostart_enabled():
            with contextlib.suppress(Exception):
                _set_autostart(True)

        # Start the singleton IPC listener so a second launch can ask us to
        # reveal the dashboard.  The accept thread schedules the window
        # reveal onto the Tk main thread (tk.deiconify must never run from
        # a worker thread).
        def _on_ipc_show() -> None:
            self._root.after(0, self._show_window)

        self._ipc_server = start_ipc_server(_on_ipc_show)

        # If a previous GUI crashed, the server subprocess it spawned may
        # still be running (reparented to the init process).  Adopt it so
        # the user can stop it from this new GUI instead of leaving it
        # orphaned with the port bound.
        adopted_pid = self._server.adopt_if_running()
        if adopted_pid is None:
            self._start_server()
        else:
            self._on_adopted_server()
        self._root.after(POLL_MS, self._update_ui)

        def _status_loop() -> None:
            if not self._quitting:
                poll_server_status(
                    self._ip_var.get().strip(),
                    self._port_var.get().strip(),
                    self._wss_var.get(),
                    self._on_status_result,
                )
                self._root.after(5000, _status_loop)

        self._root.after(3000, _status_loop)  # First check after 3 s to let server start
        self._root.mainloop()

    def _on_status_result(
        self,
        sessions: int | None,
        backend_id: str | None,
        version: str | None,
        external_display: str | None,
    ) -> None:
        # poll_server_status calls this from a daemon thread.  All StringVar
        # mutations must happen on the Tk main thread to avoid races between
        # the background poller and the Tk event loop.
        def _apply() -> None:
            if sessions is not None:
                self._sessions_var.set(str(sessions))  # ty:ignore[unresolved-attribute]
            if backend_id:
                self._backend_id_var.set(backend_id)  # ty:ignore[unresolved-attribute]
            if version:
                self._version_var.set(version)  # ty:ignore[unresolved-attribute]
            ext_text = external_display or "—"
            self._external_addr_var.set(ext_text)  # ty:ignore[unresolved-attribute]
            self._external_addr_mirror = ext_text
            if self._tray_icon is not None:
                with contextlib.suppress(Exception):
                    self._tray_icon.update_menu()

        self._root.after(0, _apply)


# ── Windows autostart helpers ─────────────────────────────────────────────────

_AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_VALUE_NAME = "RCFlow"


def _is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _AUTOSTART_VALUE_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False


def _set_autostart(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                # --minimized keeps the dashboard hidden on login so we
                # don't pop a window every time the user boots; the tray
                # icon is still available to open the dashboard.
                winreg.SetValueEx(key, _AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" gui --minimized')
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
    except OSError as exc:
        logger.error("Failed to update autostart registry: %s", exc)


# ── Singleton process lock ───────────────────────────────────────────────────
#
# A second `rcflow gui` launch should reveal the running instance's dashboard
# rather than start a second half-broken GUI.  We acquire an exclusive lock on
# a sentinel file under the data dir; the lock is released automatically when
# the process exits.  If the lock is already held, run_gui() asks the running
# instance via the IPC channel (core._start_ipc_server) to raise its window,
# then exits 0.

_LOCK_FILENAME = ".worker.lock"
_lock_fd: object | None = None


def _lock_file_path() -> Path:
    from src.paths import get_data_dir  # noqa: PLC0415

    return get_data_dir() / _LOCK_FILENAME


def _acquire_singleton_lock() -> bool:
    """Acquire an exclusive lock on the singleton sentinel file.

    On Windows we use ``msvcrt.locking()`` with ``LK_NBLCK``; on POSIX
    (when this module is somehow loaded elsewhere for tests) we fall back
    to ``fcntl.flock`` so the helper stays usable in a testing context.
    Returns True when this process now holds the lock.
    """
    global _lock_fd
    path = _lock_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = open(path, "a+b")  # noqa: SIM115 — held for process lifetime
    except OSError as exc:
        logger.warning("Singleton lock open failed (%s): %s", path, exc)
        return False
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415

            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        with contextlib.suppress(OSError):
            fd.close()
        return False
    _lock_fd = fd
    return True


def _enable_dpi_awareness() -> None:
    """Mark the process DPI-aware before any window is created.

    Without this, Windows lies to us in ``GetSystemMetrics`` (returning
    96-dpi baseline sizes) and the compositor DPI-virtualises our
    windows, blurring the entire UI on HiDPI displays. Must run before
    the first Tk window is created, otherwise that window is stuck in
    DPI-unaware mode for its lifetime.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        # PER_MONITOR_AWARE_V2 (-4) — Windows 10 1703+.
        ctx = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if ctx is not None:
            ctx(ctypes.c_void_p(-4))
        else:
            # Fallback for older Windows.
            user32.SetProcessDPIAware()
    except (OSError, AttributeError) as exc:
        logger.debug("Failed to set DPI awareness: %s", exc)


def _set_app_user_model_id() -> None:
    """Tell Windows this process is its own application.

    Without an explicit AppUserModelID, Windows groups the running
    ``python.exe`` (dev) or the PyInstaller bootloader under a generic
    identity and the taskbar falls back to the host exe's icon — the
    user sees the Python interpreter icon instead of the RCFlow icon.
    Setting a stable ID here makes the taskbar (and Alt-Tab, Start
    pins) honour ``iconbitmap`` below and the icon embedded in the
    frozen exe.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes  # noqa: PLC0415

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.rcflow.worker")
    except (OSError, AttributeError) as exc:
        logger.debug("Failed to set AppUserModelID: %s", exc)


def run_gui(*, minimized: bool = False) -> None:
    """Entry point for the GUI + tray application.

    *minimized* hides the dashboard at launch (tray-only).  The registry
    autostart entry passes it so login boot does not pop a window.
    """
    _enable_dpi_awareness()
    _set_app_user_model_id()
    if not _acquire_singleton_lock():
        # Running instance present — ask it to show its dashboard and exit.
        # Minimized autostart still takes this path, but since the already-
        # running instance decides whether to reveal the window, sending
        # SHOW here would be wrong — skip the ask in that case.
        if not minimized:
            delivered = send_show_to_existing()
            if not delivered:
                print(
                    "RCFlow Worker is already running. Look for its icon in the system tray.",
                    file=sys.stderr,
                )
        sys.exit(0)

    gui = RCFlowGUI()
    gui.run(minimized=minimized)
