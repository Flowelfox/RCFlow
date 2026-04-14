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
    poll_server_status,
)

# Apply appearance settings before any CTk window is created
ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

logger = logging.getLogger(__name__)


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

        self._root = ctk.CTk()
        self._root.title("RCFlow Worker")
        self._root.geometry("860x700")
        self._root.minsize(640, 500)
        self._root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._set_window_icon()
        self._build_ui()
        self._load_settings()

    # ── UI construction ───────────────────────────────────────────────────

    def _set_window_icon(self) -> None:
        from src.paths import get_install_dir, is_frozen  # noqa: PLC0415

        icon_path = (
            get_install_dir() / "tray_icon.ico"
            if is_frozen()
            else Path(__file__).resolve().parent / "assets" / "tray_icon.ico"
        )
        if icon_path.exists():
            with contextlib.suppress(tk.TclError):
                self._root.iconbitmap(str(icon_path))

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

        ctk.CTkLabel(
            sc,
            text="Server Settings",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=0, column=0, columnspan=6, sticky="w", padx=g, pady=(g, s))

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
        self._wss_check.grid(row=1, column=4, sticky="w", padx=(0, g), pady=(0, g))

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
        self._copy_token_btn.pack()

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
        ).grid(row=0, column=0, columnspan=8, sticky="w", padx=g, pady=(g, s))

        detail_info = [
            ("Bound Address", "_bound_addr_var"),
            ("Uptime", "_uptime_var"),
            ("Active Sessions", "_sessions_var"),
            ("Backend ID", "_backend_id_var"),
        ]
        for col, (label, var_name) in enumerate(detail_info):
            ctk.CTkLabel(
                dc,
                text=label,
                font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
                text_color=("gray40", "gray60"),
            ).grid(row=1, column=col * 2, sticky="w", padx=(g if col == 0 else g // 2, s), pady=(0, g))
            var = tk.StringVar(value="\u2014")
            setattr(self, var_name, var)
            ctk.CTkLabel(
                dc,
                textvariable=var,
                font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
            ).grid(row=1, column=col * 2 + 1, sticky="w", padx=(0, g), pady=(0, g))

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

        self._log_box = ctk.CTkTextbox(lc, state="disabled", wrap="word", font=(theme.mono_font(), theme.FONT_SIZE_LOG))
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=s, pady=(0, s))

        # Apply syntax-highlight tags on the underlying tk.Text widget
        _dark = ctk.get_appearance_mode().lower() == "dark"
        self._log_widget = self._log_box._textbox
        self._log_widget.tag_configure("error", foreground=theme.LOG_DARK_ERROR if _dark else theme.LOG_LIGHT_ERROR)
        self._log_widget.tag_configure("warning", foreground=theme.LOG_DARK_WARN if _dark else theme.LOG_LIGHT_WARN)

    # ── Settings I/O ─────────────────────────────────────────────────────

    def _load_settings(self) -> None:
        try:
            from src.config import Settings  # noqa: PLC0415

            s = Settings()
            self._ip_var.set(s.RCFLOW_HOST)
            self._port_var.set(str(s.RCFLOW_PORT))
            self._wss_var.set(s.WSS_ENABLED)
        except Exception:
            pass

    def _on_wss_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        update_settings_file({"WSS_ENABLED": str(self._wss_var.get())})

    def _on_copy_token(self) -> None:
        try:
            from src.config import Settings  # noqa: PLC0415

            api_key = Settings().RCFLOW_API_KEY
            if not api_key:
                self._set_status("No API token configured", error=True)
                return
            self._root.clipboard_clear()
            self._root.clipboard_append(api_key)
            self._root.update()  # Required on Windows for clipboard to persist
            self._set_status("Token copied to clipboard")
        except Exception as exc:
            self._set_status(f"Failed to copy token: {exc}", error=True)

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
        self._toggle_btn.configure(
            text="Stop", fg_color=theme.BTN_STOP_FG, hover_color=theme.BTN_STOP_HOVER, text_color=theme.BTN_STOP_TEXT
        )
        protocol = "WSS" if self._wss_var.get() else "WS"
        self._set_status(f"Starting ({protocol})...")

    def _stop_server(self) -> None:
        self._set_status("Stopping...")
        self._server.stop()

    # ── Log display ───────────────────────────────────────────────────────

    def _drain_log_queue(self) -> None:
        lines = self._log_buffer.drain()
        if not lines:
            return

        self._log_box.configure(state="normal")
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
        self._log_box.configure(state="disabled")

    # ── Status & details ──────────────────────────────────────────────────

    def _set_status(self, text: str, *, error: bool = False) -> None:
        tl = text.lower()
        if error:
            color = theme.STATUS_ERROR
        elif "stop" in tl or text == "Stopped":
            color = theme.STATUS_STOPPED
        elif "start" in tl:
            color = theme.STATUS_STARTING
        else:
            color = theme.STATUS_RUNNING
        self._status_label.configure(text=f"  {text}  ", fg_color=color)

    def _update_ui(self) -> None:
        """Periodic UI refresh (300 ms)."""
        if self._quitting:
            return

        self._drain_log_queue()
        running = self._server.is_running()

        if running:
            protocol = "WSS" if self._wss_var.get() else "WS"
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
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open", self._on_tray_open, default=True),
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

    def _on_toggle_autostart(self, icon: object, item: object) -> None:
        _set_autostart(not _is_autostart_enabled())
        self._update_tray_status()

    def _on_tray_quit(self, icon: object = None, item: object = None) -> None:
        self._quitting = True
        self._server.stop()
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.stop()
        self._root.after(0, self._root.destroy)

    # ── Window lifecycle ──────────────────────────────────────────────────

    def _on_window_close(self) -> None:
        if self._tray_icon is not None:
            self._root.withdraw()
        else:
            self._on_tray_quit()

    def run(self) -> None:
        """Start the GUI event loop."""
        self._setup_tray()
        self._start_server()
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

    def _on_status_result(self, sessions: int | None, backend_id: str | None) -> None:
        if sessions is not None:
            self._sessions_var.set(str(sessions))  # ty:ignore[unresolved-attribute]
        if backend_id:
            self._backend_id_var.set(backend_id)  # ty:ignore[unresolved-attribute]


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
                winreg.SetValueEx(key, _AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" gui')
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
    except OSError as exc:
        logger.error("Failed to update autostart registry: %s", exc)


def run_gui() -> None:
    """Entry point for the GUI + tray application."""
    gui = RCFlowGUI()
    gui.run()
