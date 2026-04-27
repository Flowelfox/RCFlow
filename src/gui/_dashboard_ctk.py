"""Cross-platform CustomTkinter dashboard for the RCFlow worker.

Owns the widget tree, server lifecycle, log viewer, updater banner, and
singleton/autostart helpers shared by Windows and Linux.  Tray integration
and platform-native window-icon installation are abstract hooks subclasses
override (``_setup_tray`` / ``_update_tray_status`` / ``_install_platform_icon``)
so the same dashboard runs against pystray on Windows and AppIndicator
on Linux without duplicating UI code.

macOS has its own NSStatusBar implementation in ``src/gui/macos.py`` and
does not use this module.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import tkinter as tk
from pathlib import Path
from typing import Protocol

import customtkinter as ctk

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
    start_ipc_server,
)
from src.gui.updater import (
    UpdateService,
    cleanup_partial_downloads,
    resolve_current_version,
)

# Apply appearance settings before any CTk window is created
ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

logger = logging.getLogger(__name__)


class TrayIconProtocol(Protocol):
    """Structural type for the platform tray icon (optional)."""

    def update_menu(self) -> None: ...
    def stop(self) -> None: ...


class RCFlowDashboard:
    """CTk window + shared business logic for the RCFlow worker GUI.

    Subclasses provide platform-specific tray integration by overriding
    ``_setup_tray``, ``_update_tray_status``, and ``_install_platform_icon``.
    The default implementations are no-ops so a headless / window-only
    fallback is always available.
    """

    def __init__(self) -> None:
        self._log_buffer = LogBuffer()
        self._server = ServerManager(self._log_buffer)
        self._quitting = False
        self._tray_icon: TrayIconProtocol | None = None
        # Loopback IPC listener so a second `rcflow gui` launch can reveal
        # this instance's dashboard instead of starting a broken second GUI.
        self._ipc_server: object | None = None
        # monotonic expiry for transient status messages (copy-token feedback).
        self._status_sticky_until: float = 0.0
        # Thread-safe mirrors of Tk vars consulted from background tray
        # threads.  Reading ``tk.BooleanVar.get()`` / ``StringVar.get()`` off
        # the Tk main thread raises ``RuntimeError: main thread is not in
        # main loop``, so menu visibility / title lambdas use these instead.
        self._upnp_enabled_mirror: bool = False
        self._natpmp_enabled_mirror: bool = False
        self._external_addr_mirror: str = "—"
        self._update_available_mirror: bool = False
        self._update_latest_mirror: str = ""

        cleanup_partial_downloads()
        self._updater = UpdateService(current_version=resolve_current_version())
        self._updater.restore_cached_state()
        self._updater.add_listener(self._on_updater_change)

        # On X11 the window's WM_CLASS is fixed at construction time and
        # determines which ``.desktop`` entry GNOME / KDE associate with
        # the window in the Activities overview / dock.  Match the
        # ``StartupWMClass=rcflow`` we ship in ``rcflow-worker.desktop``
        # so the dock shows the RCFlow icon instead of a generic one.
        if sys.platform.startswith("linux"):
            self._root = ctk.CTk(className="rcflow")
        else:
            self._root = ctk.CTk()
        self._root.title("RCFlow Worker")
        self._root.geometry("900x720")
        # Settings card uses 2 rows (inputs + checkboxes) and Instance
        # Details wraps to 2 rows of 3 fields, so the dashboard fits on
        # narrower screens (1024-px laptops, half-screen splits).
        self._root.minsize(720, 520)
        self._root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._install_platform_icon()
        self._build_ui()
        self._load_settings()

    # ── Platform hooks (override in subclass) ─────────────────────────────

    def _install_platform_icon(self) -> None:
        """Install the platform-native window icon. Default: no-op."""

    def _setup_tray(self) -> bool:
        """Set up the system tray icon.  Default: no-op, returns False."""
        return False

    def _update_tray_status(self) -> None:
        """Refresh tray menu state.  Default: no-op."""

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from types import ModuleType  # noqa: F401, PLC0415

        p = theme.PAD_OUTER
        g = theme.PAD_GROUP
        s = theme.PAD_SMALL

        self._root.grid_columnconfigure(0, weight=1)
        self._root.grid_rowconfigure(5, weight=1)  # log row expands

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

        # ── Update banner (hidden unless update available) ───────────
        self._build_update_banner(row=1)

        # ── Status pill ──────────────────────────────────────────────
        self._status_label = ctk.CTkLabel(
            self._root,
            text="  Stopped  ",
            fg_color=theme.STATUS_STOPPED,
            text_color=("#ffffff", "#e5e7eb"),
            corner_radius=6,
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        )
        self._status_label.grid(row=2, column=0, sticky="w", padx=p, pady=(s + 2, 0))

        # ── Instance details card ────────────────────────────────────
        dc = ctk.CTkFrame(self._root, corner_radius=8)
        dc.grid(row=3, column=0, sticky="ew", padx=p, pady=(s, 0))

        ctk.CTkLabel(
            dc,
            text="Instance Details",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=0, column=0, columnspan=6, sticky="w", padx=g, pady=(g, s))

        detail_info = [
            ("Bound Address", "_bound_addr_var", 130),
            ("Uptime", "_uptime_var", 80),
            ("Active Sessions", "_sessions_var", 50),
            ("Backend ID", "_backend_id_var", 100),
            ("Version", "_version_var", 70),
            ("External Address", "_external_addr_var", 170),
        ]
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
            var = tk.StringVar(value="—")
            setattr(self, var_name, var)
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

        # ── Updates card ─────────────────────────────────────────────
        self._build_updates_card(row=4)

        # ── Log viewer card ──────────────────────────────────────────
        lc = ctk.CTkFrame(self._root, corner_radius=8)
        lc.grid(row=5, column=0, sticky="nsew", padx=p, pady=(s, p))
        lc.grid_rowconfigure(1, weight=1)
        lc.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            lc,
            text="Server Log",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=g, pady=(g, s))

        self._log_box = ctk.CTkTextbox(lc, wrap="word", font=(theme.mono_font(), theme.FONT_SIZE_LOG))
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=s, pady=(0, s))

        _dark = ctk.get_appearance_mode().lower() == "dark"
        self._log_widget = self._log_box._textbox
        self._log_widget.tag_configure("error", foreground=theme.LOG_DARK_ERROR if _dark else theme.LOG_LIGHT_ERROR)
        self._log_widget.tag_configure("warning", foreground=theme.LOG_DARK_WARN if _dark else theme.LOG_LIGHT_WARN)
        make_text_readonly(self._log_widget)
        attach_copy_context_menu(self._log_widget)

    # ── Update banner / card ─────────────────────────────────────────────

    def _build_update_banner(self, *, row: int) -> None:
        p = theme.PAD_OUTER
        g = theme.PAD_GROUP
        s = theme.PAD_SMALL

        self._update_banner = ctk.CTkFrame(self._root, fg_color=("#fde68a", "#854d0e"), corner_radius=8)
        self._update_banner.grid(row=row, column=0, sticky="ew", padx=p, pady=(s, 0))
        self._update_banner.grid_columnconfigure(1, weight=1)
        self._update_banner.grid_remove()  # hidden until first update detected

        self._update_banner_label = ctk.CTkLabel(
            self._update_banner,
            text="",
            text_color=("#1f2937", "#fef3c7"),
            anchor="w",
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY, weight="bold"),
        )
        self._update_banner_label.grid(row=0, column=1, sticky="ew", padx=(g, s), pady=s)

        self._update_banner_install_btn = ctk.CTkButton(
            self._update_banner,
            text="Download & Install",
            width=140,
            command=self._on_update_install,
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        )
        self._update_banner_install_btn.grid(row=0, column=2, padx=(s, s), pady=s)

        self._update_banner_notes_btn = ctk.CTkButton(
            self._update_banner,
            text="Release Notes",
            width=110,
            fg_color="transparent",
            border_width=1,
            text_color=("#1f2937", "#fef3c7"),
            command=self._on_update_open_notes,
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
        )
        self._update_banner_notes_btn.grid(row=0, column=3, padx=(s, s), pady=s)

        self._update_banner_dismiss_btn = ctk.CTkButton(
            self._update_banner,
            text="✕",
            width=28,
            fg_color="transparent",
            text_color=("#1f2937", "#fef3c7"),
            command=self._on_update_dismiss,
            font=ctk.CTkFont(size=theme.FONT_SIZE_BODY, weight="bold"),
        )
        self._update_banner_dismiss_btn.grid(row=0, column=4, padx=(0, s), pady=s)

    def _build_updates_card(self, *, row: int) -> None:
        p = theme.PAD_OUTER
        g = theme.PAD_GROUP
        s = theme.PAD_SMALL

        uc = ctk.CTkFrame(self._root, corner_radius=8)
        uc.grid(row=row, column=0, sticky="ew", padx=p, pady=(s, 0))
        uc.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            uc,
            text="Updates",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=g, pady=(g, s))

        ctk.CTkLabel(
            uc,
            text="Status",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
            text_color=("gray40", "gray60"),
        ).grid(row=1, column=0, sticky="w", padx=(g, s), pady=(0, g))

        self._update_status_var = tk.StringVar(value="—")
        ctk.CTkEntry(
            uc,
            textvariable=self._update_status_var,
            state="readonly",
            border_width=0,
            corner_radius=0,
            fg_color="transparent",
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL, weight="bold"),
        ).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, s), pady=(0, g))

        self._update_check_btn = ctk.CTkButton(
            uc,
            text="Check for Updates",
            width=140,
            command=self._on_update_check_now,
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
        )
        self._update_check_btn.grid(row=1, column=4, padx=(s, g), pady=(0, g))

        from src.config import Settings  # noqa: PLC0415

        try:
            auto = bool(Settings().RCFLOW_UPDATE_AUTO_CHECK)
        except Exception:
            auto = True
        self._update_auto_var = tk.BooleanVar(value=auto)
        ctk.CTkCheckBox(
            uc,
            text="Check for updates automatically",
            variable=self._update_auto_var,
            command=self._on_update_auto_toggle,
            font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
        ).grid(row=2, column=0, columnspan=5, sticky="w", padx=g, pady=(0, g))

    # ── Update event handlers ────────────────────────────────────────────

    def _on_updater_change(self) -> None:
        """Listener invoked from the updater worker thread."""
        self._root.after(0, self._refresh_update_ui)

    def _refresh_update_ui(self) -> None:
        if self._quitting:
            return
        latest = self._updater.latest
        current = self._updater.current_version or "—"

        if self._updater.show_banner and latest is not None:
            text = f"Update available: v{latest.version} (you have v{current})"
            self._update_banner_label.configure(text=text)
            install_state = "normal" if latest.download_url else "disabled"
            self._update_banner_install_btn.configure(state=install_state)
            self._update_banner.grid()
        else:
            self._update_banner.grid_remove()

        self._update_available_mirror = self._updater.show_banner
        self._update_latest_mirror = latest.version if latest is not None else ""
        self._update_status_var.set(self._format_update_status())

        if self._updater.is_checking or self._updater.is_downloading:
            self._update_check_btn.configure(state="disabled")
        else:
            self._update_check_btn.configure(state="normal")

        self._update_tray_status()

    def _format_update_status(self) -> str:
        if self._updater.is_downloading:
            return "Downloading update…"
        if self._updater.is_checking:
            return "Checking…"
        err = self._updater.last_error
        if err:
            return f"Error: {err}"
        latest = self._updater.latest
        current = self._updater.current_version or "unknown"
        if latest is None:
            return f"Installed v{current} — no check yet"
        if self._updater.update_available:
            return f"Latest v{latest.version} available — installed v{current}"
        return f"Up to date (v{current})"

    def _on_update_check_now(self) -> None:
        self._updater.check_now()

    def _on_update_install(self) -> None:
        self._set_status("Downloading update…", sticky=True)

        def _on_progress(received: int, total: int) -> None:
            pct = int(received * 100 / total) if total else 0
            self._root.after(0, lambda: self._set_status(f"Downloading update… {pct}%", sticky=True))

        def _on_done(path: Path) -> None:
            def _ui() -> None:
                self._set_status(f"Downloaded to {path.name}", sticky=True)
                self._prompt_launch_installer(path)

            self._root.after(0, _ui)

        def _on_error(msg: str) -> None:
            self._root.after(0, lambda: self._set_status(f"Update download failed: {msg}", error=True, sticky=True))

        self._updater.download(on_progress=_on_progress, on_done=_on_done, on_error=_on_error)

    def _prompt_launch_installer(self, path: Path) -> None:
        """Modal: ask the user whether to launch the installer now."""
        from tkinter import messagebox  # noqa: PLC0415

        choice = messagebox.askyesnocancel(
            "Update downloaded",
            f"The installer was saved to:\n{path}\n\nLaunch it now?\n\n"
            "Yes — open the installer\nNo — show the file in your file manager\nCancel — keep the download for later",
        )
        if choice is True:
            try:
                self._updater.launch_installer(path)
                self._set_status("Installer launched", sticky=True)
            except Exception as exc:
                self._set_status(f"Failed to launch installer: {exc}", error=True, sticky=True)
        elif choice is False:
            self._reveal_in_explorer(path)

    @staticmethod
    def _reveal_in_explorer(path: Path) -> None:
        """Open the OS file manager focused on *path* (best-effort)."""
        import subprocess  # noqa: PLC0415

        with contextlib.suppress(Exception):
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                # Most Linux file managers don't support file selection;
                # opening the parent directory is the broadly-compatible choice.
                subprocess.Popen(["xdg-open", str(path.parent)])

    def _on_update_open_notes(self) -> None:
        self._updater.open_release_page()

    def _on_update_dismiss(self) -> None:
        self._updater.dismiss_current()

    def _on_update_auto_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        update_settings_file({"RCFLOW_UPDATE_AUTO_CHECK": "true" if self._update_auto_var.get() else "false"})

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
        if enabled and self._upnp_var.get():
            self._upnp_var.set(False)
            self._upnp_enabled_mirror = False
            updates["UPNP_ENABLED"] = "false"
        update_settings_file(updates)
        self._apply_forwarding_mutex()

    def _apply_forwarding_mutex(self) -> None:
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
        import time  # noqa: PLC0415

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
        import time  # noqa: PLC0415

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
                self._uptime_var.set("—")  # ty:ignore[unresolved-attribute]
                self._bound_addr_var.set("—")  # ty:ignore[unresolved-attribute]
                self._sessions_var.set("—")  # ty:ignore[unresolved-attribute]
                self._backend_id_var.set("—")  # ty:ignore[unresolved-attribute]
                self._version_var.set("—")  # ty:ignore[unresolved-attribute]
                self._external_addr_var.set("—")  # ty:ignore[unresolved-attribute]
                self._external_addr_mirror = "—"
                self._update_tray_status()

        self._root.after(POLL_MS, self._update_ui)

    # ── Tray callback shims (used by subclass tray menus) ─────────────────

    def _on_tray_open(self, *_args: object) -> None:
        self._root.after(0, self._show_window)

    def _show_window(self) -> None:
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _on_tray_toggle_server(self, *_args: object) -> None:
        def _apply() -> None:
            self._on_toggle()
            self._update_tray_status()

        self._root.after(0, _apply)

    def _on_tray_copy_token(self, *_args: object) -> None:
        self._root.after(0, self._on_copy_token)

    def _on_tray_add_to_client(self, *_args: object) -> None:
        self._root.after(0, self._on_add_to_client)

    def _on_tray_install_update(self, *_args: object) -> None:
        self._root.after(0, self._show_window)
        self._root.after(0, self._on_update_install)

    def _on_tray_check_updates(self, *_args: object) -> None:
        self._root.after(0, self._on_update_check_now)

    def _on_toggle_autostart(self, *_args: object) -> None:
        set_autostart(not is_autostart_enabled())
        self._update_tray_status()

    def _on_tray_quit(self, *_args: object) -> None:
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
        """Start the GUI event loop."""
        self._setup_tray()
        if minimized and self._tray_icon is not None:
            self._root.withdraw()

        # Upgrade path: rewrite the autostart entry to include --minimized
        # for installs that predate that flag.  Idempotent for new entries.
        if is_autostart_enabled():
            with contextlib.suppress(Exception):
                set_autostart(True)

        def _on_ipc_show() -> None:
            self._root.after(0, self._show_window)

        self._ipc_server = start_ipc_server(_on_ipc_show)

        adopted_pid = self._server.adopt_if_running()
        if adopted_pid is None:
            self._start_server()
        else:
            self._on_adopted_server()

        self._refresh_update_ui()
        if self._update_auto_var.get() and self._updater.current_version:
            self._updater.maybe_check()

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
            self._update_tray_status()

        self._root.after(0, _apply)


# ── Autostart helpers (Windows + Linux) ───────────────────────────────────────

_AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_VALUE_NAME = "RCFlow"

# Linux: XDG Autostart spec — files in ~/.config/autostart/<name>.desktop are
# launched by the user's session at login.
_LINUX_AUTOSTART_FILENAME = "rcflow-worker.desktop"


def autostart_menu_label() -> str:
    """Platform-appropriate label for the tray "Start at login" toggle."""
    if sys.platform == "win32":
        return "Start with Windows"
    if sys.platform.startswith("linux"):
        return "Start with Linux"
    return "Start at login"


def _linux_autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / _LINUX_AUTOSTART_FILENAME


def _linux_autostart_command() -> str:
    """Resolve the command line that the autostart .desktop should Exec."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" gui --minimized'
    return f'"{sys.executable}" -m src gui --minimized'


def is_autostart_enabled() -> bool:
    if sys.platform == "win32":
        try:
            import winreg  # noqa: PLC0415

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, _AUTOSTART_VALUE_NAME)
                return True
        except (FileNotFoundError, OSError):
            return False
    if sys.platform.startswith("linux"):
        return _linux_autostart_path().exists()
    return False


def set_autostart(enabled: bool) -> None:
    if sys.platform == "win32":
        _set_autostart_win32(enabled)
        return
    if sys.platform.startswith("linux"):
        _set_autostart_linux(enabled)
        return


def _set_autostart_linux(enabled: bool) -> None:
    """Write or remove ~/.config/autostart/rcflow-worker.desktop.

    XDG-spec keys: Type, Exec, Hidden=false, X-GNOME-Autostart-enabled=true
    so GNOME Tweaks surfaces the entry.  ``--minimized`` keeps the dashboard
    hidden on login (tray-only) — same convention as Windows and the macOS
    LaunchAgent.
    """
    path = _linux_autostart_path()
    if not enabled:
        with contextlib.suppress(FileNotFoundError, OSError):
            path.unlink()
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=RCFlow Worker\n"
            "Comment=RCFlow background worker\n"
            f"Exec={_linux_autostart_command()}\n"
            "Icon=rcflow-worker\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Hidden=false\n"
        )
    except OSError as exc:
        logger.error("Failed to write Linux autostart entry %s: %s", path, exc)


def _set_autostart_win32(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" gui --minimized')
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
    except OSError as exc:
        logger.error("Failed to update autostart registry: %s", exc)


# ── Singleton process lock ───────────────────────────────────────────────────

_LOCK_FILENAME = ".worker.lock"
_lock_fd: object | None = None


def _lock_file_path() -> Path:
    from src.paths import get_data_dir  # noqa: PLC0415

    return get_data_dir() / _LOCK_FILENAME


def acquire_singleton_lock() -> bool:
    """Acquire an exclusive lock on the singleton sentinel file.

    On Windows we use ``msvcrt.locking()`` with ``LK_NBLCK``; on POSIX
    we fall back to ``fcntl.flock``.  Returns True when this process now
    holds the lock.
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
