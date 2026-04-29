"""macOS menu bar + settings panel for the RCFlow backend server.

Launches a CustomTkinter window (native Aqua theme, hidden on launch) with
server controls, status display, and an inline live log viewer.  The backend
server runs as a managed subprocess via ServerManager.  The window is revealed
only by clicking "Open Settings…" in the macOS menu bar icon; closing the window
hides it back to the menu bar.  "Quit" from the menu stops the server and exits.

This is the default mode for frozen macOS builds (``LSUIElement`` app — no
Dock icon, lives entirely in the menu bar).

Menu bar icon is implemented via PyObjC's ``NSStatusBar`` / ``NSStatusItem``
API, running entirely on the main thread (driven by tkinter's ``after()`` loop).
This avoids the ``NSUpdateCycleInitialize() is called off the main thread``
crash that occurs when pystray tries to call ``NSApplication.run()`` from a
background thread after tkinter has already claimed NSApp on the main thread.
"""

from __future__ import annotations

import atexit
import contextlib
import fcntl
import logging
import plistlib
import queue
import signal
import sys
import time
import tkinter as tk
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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
    send_show_to_existing,
    start_ipc_server,
)
from src.gui.updater import (
    UpdateService,
    cleanup_partial_downloads,
    resolve_current_version,
)

logger = logging.getLogger(__name__)

# LaunchAgent plist for "Start with macOS" autostart
_LAUNCHAGENT_LABEL = "com.rcflow.worker"
_LAUNCHAGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHAGENT_LABEL}.plist"

# ── Singleton process lock ───────────────────────────────────────────────────
#
# Uses an fcntl.flock() exclusive lock on a well-known file.  The lock is
# released automatically when this process exits (even on crash / SIGKILL),
# so there is no stale-PID-file problem.

_LOCK_PATH = Path.home() / "Library" / "Application Support" / "RCFlow" / ".worker.lock"
_lock_fd: IO[str] | None = None


def _acquire_singleton_lock() -> bool:
    """Try to acquire the singleton file lock.

    Returns True if this is the only running instance.  Returns False if
    another instance already holds the lock (caller should exit).
    """
    global _lock_fd
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _lock_fd = open(_LOCK_PATH, "w")  # noqa: SIM115
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        if _lock_fd is not None:
            with contextlib.suppress(OSError):
                _lock_fd.close()
            _lock_fd = None
        return False


def _is_autostart_enabled() -> bool:
    """Return True if the RCFlow LaunchAgent plist exists."""
    return _LAUNCHAGENT_PLIST.exists()


def _set_autostart(enabled: bool) -> None:
    """Write or remove the LaunchAgent plist for login autostart."""
    if enabled:
        _LAUNCHAGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
        # "--minimized" keeps the dashboard window hidden on login so the
        # user doesn't get a popup every time they boot; the tray icon is
        # still visible and they can open the dashboard from there.
        prog_args = (
            [str(Path(sys.executable).resolve()), "gui", "--minimized"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "src", "gui", "--minimized"]
        )
        plist: dict[str, object] = {
            "Label": _LAUNCHAGENT_LABEL,
            "ProgramArguments": prog_args,
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Interactive",
        }
        with _LAUNCHAGENT_PLIST.open("wb") as fh:
            plistlib.dump(plist, fh)
        # Do NOT call `launchctl load` here — that would immediately spawn a
        # second instance while the app is already running.  Placing the plist
        # in ~/Library/LaunchAgents/ is sufficient; launchd picks it up
        # automatically on the next user login.
    else:
        # Remove the plist so launchd won't load it on the next login.
        # Do NOT call `launchctl unload` — that sends SIGTERM to the process
        # launchd owns under this label, which would kill the running app if
        # it was started by this LaunchAgent (e.g. after a previous reboot).
        with contextlib.suppress(OSError):
            _LAUNCHAGENT_PLIST.unlink()


class RCFlowMacOSGUI:
    """CTk settings panel + macOS menu bar icon for the RCFlow server."""

    def __init__(self) -> None:
        self._log_buffer = LogBuffer()
        self._server = ServerManager(self._log_buffer)
        self._quitting = False
        # Thread-safe queue for UI callbacks posted from background threads.
        # Background threads must NEVER call self._root.after() directly —
        # doing so calls into the Tcl interpreter from a non-Tcl thread, which
        # races with the main thread's Tcl event loop and corrupts interpreter
        # state (TEOV_SwitchVarFrame null-deref, Tkapp_Call PC=0x1 crashes).
        # Instead they put a callable here; _update_ui drains it on the main
        # thread once per 300 ms tick.
        self._pending_ui: queue.Queue[Callable[[], None]] = queue.Queue()

        # CTk global settings — deferred here (not module-level) so an error
        # finding the bundled theme JSON in a frozen build doesn't prevent the
        # module from importing and the trace log from being written.
        with contextlib.suppress(Exception):
            ctk.set_appearance_mode("system")
            ctk.set_default_color_theme("blue")

        # Flags set by ObjC callbacks; consumed by _update_ui on the Tk event
        # loop.  ObjC action handlers must NEVER call Tk or AppKit window APIs
        # directly — doing so from inside a Cocoa event-dispatch context causes
        # re-entrancy deadlocks.  Setting a plain bool is the only safe operation.
        self._show_window_requested: bool = False
        self._toggle_server_requested: bool = False
        self._copy_token_requested: bool = False
        self._add_client_requested: bool = False
        # Quit is driven through the same flag pattern so the NSMenu modal
        # tracking loop returns before stop_sync() starts blocking — otherwise
        # the menu bar freezes (stuck cursor) while the server child reaps.
        self._quit_requested: bool = False
        # Update flow flags — set by ObjC menu actions and by the updater
        # listener thread; consumed on the Tk event loop tick.
        self._install_update_requested: bool = False
        self._check_updates_requested: bool = False
        self._update_ui_dirty: bool = False

        # Loopback IPC listener so a second `rcflow gui` launch reveals this
        # instance's dashboard instead of silently failing the singleton check.
        self._ipc_server: object | None = None

        # monotonic expiry for transient status messages (copy-token feedback).
        # _update_ui will not overwrite the status pill while this is in the
        # future, so the message is visible for ~3 seconds regardless of the
        # 300 ms _update_ui polling rate.
        self._status_sticky_until: float = 0.0

        # NSStatusItem and related ObjC objects (populated by _init_status_item)
        self._status_item: object | None = None
        self._delegate: object | None = None
        self._ns_status_text: object | None = None
        self._ns_toggle_item: object | None = None
        self._ns_copy_token_item: object | None = None
        self._ns_add_client_item: object | None = None
        self._ns_autostart_item: object | None = None
        self._ns_external_item: object | None = None
        self._ns_update_item: object | None = None
        self._ns_check_updates_item: object | None = None

        cleanup_partial_downloads()
        self._updater = UpdateService(current_version=resolve_current_version())
        self._updater.restore_cached_state()
        self._updater.add_listener(self._on_updater_change)

        self._root = ctk.CTk()
        self._root.title("RCFlow Worker")
        self._root.geometry("900x720")
        # Settings card uses 2 rows (inputs + checkboxes) and Instance
        # Details wraps to 2 rows of 3 fields, so the dashboard fits on
        # narrower screens (1024-px laptops, half-screen splits).
        self._root.minsize(720, 520)
        self._root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._build_ui()
        self._load_settings()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
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
            # values (Cmd+C).  Borderless transparent styling keeps the
            # visual treatment close to the original Label.
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

        # Apply syntax-highlight tags on the underlying tk.Text widget
        _dark = ctk.get_appearance_mode().lower() == "dark"
        self._log_widget = self._log_box._textbox
        self._log_widget.tag_configure("error", foreground=theme.LOG_DARK_ERROR if _dark else theme.LOG_LIGHT_ERROR)
        self._log_widget.tag_configure("warning", foreground=theme.LOG_DARK_WARN if _dark else theme.LOG_LIGHT_WARN)
        # Keep the log viewer read-only while still allowing text selection and
        # Cmd+C — ``state='disabled'`` blocks selection entirely on X11-style Tk.
        make_text_readonly(self._log_widget)
        attach_copy_context_menu(self._log_widget)

    # ── Update banner / card ─────────────────────────────────────────────

    def _build_update_banner(self, *, row: int) -> None:
        """Construct the amber banner shown when a newer release is available."""
        p = theme.PAD_OUTER
        g = theme.PAD_GROUP
        s = theme.PAD_SMALL

        self._update_banner = ctk.CTkFrame(self._root, fg_color=("#fde68a", "#854d0e"), corner_radius=8)
        self._update_banner.grid(row=row, column=0, sticky="ew", padx=p, pady=(s, 0))
        self._update_banner.grid_columnconfigure(1, weight=1)
        self._update_banner.grid_remove()

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
        """Construct the persistent 'Updates' card with manual controls."""
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
        """Listener invoked from the updater worker thread.

        Sets a flag consumed on the next ``_update_ui`` tick — the only safe
        place to mutate Tk widgets and AppKit objects on macOS.
        """
        self._update_ui_dirty = True

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

        self._update_status_var.set(self._format_update_status())

        if self._updater.is_checking or self._updater.is_downloading:
            self._update_check_btn.configure(state="disabled")
        else:
            self._update_check_btn.configure(state="normal")

        self._refresh_update_menu_item()

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

    def _refresh_update_menu_item(self) -> None:
        if self._ns_update_item is None:
            return
        latest = self._updater.latest
        with contextlib.suppress(Exception):
            if self._updater.show_banner and latest is not None:
                self._ns_update_item.setTitle_(f"Update available — install v{latest.version}")  # ty:ignore[unresolved-attribute]
                self._ns_update_item.setHidden_(False)  # ty:ignore[unresolved-attribute]
            else:
                self._ns_update_item.setHidden_(True)  # ty:ignore[unresolved-attribute]

    def _on_update_check_now(self) -> None:
        self._updater.check_now()

    def _on_update_install(self) -> None:
        self._set_status("Downloading update…", sticky=True)

        def _on_progress(received: int, total: int) -> None:
            pct = int(received * 100 / total) if total else 0
            self._pending_ui.put_nowait(lambda p=pct: self._set_status(f"Downloading update… {p}%", sticky=True))

        def _on_done(path: Path) -> None:
            def _ui() -> None:
                self._set_status(f"Downloaded to {path.name}", sticky=True)
                self._prompt_launch_installer(path)

            self._pending_ui.put_nowait(_ui)

        def _on_error(msg: str) -> None:
            self._pending_ui.put_nowait(
                lambda m=msg: self._set_status(f"Update download failed: {m}", error=True, sticky=True)
            )

        self._updater.download(on_progress=_on_progress, on_done=_on_done, on_error=_on_error)

    def _prompt_launch_installer(self, path: Path) -> None:
        from tkinter import messagebox  # noqa: PLC0415

        choice = messagebox.askyesnocancel(
            "Update downloaded",
            f"The installer was saved to:\n{path}\n\nLaunch it now?\n\n"
            "Yes — open the .dmg in Finder\nNo — reveal the file in Finder\nCancel — keep the download for later",
        )
        if choice is True:
            try:
                self._updater.launch_installer(path)
                self._set_status("Installer launched", sticky=True)
            except Exception as exc:
                self._set_status(f"Failed to launch installer: {exc}", error=True, sticky=True)
        elif choice is False:
            self._reveal_in_finder(path)

    @staticmethod
    def _reveal_in_finder(path: Path) -> None:
        import subprocess  # noqa: PLC0415

        with contextlib.suppress(Exception):
            subprocess.Popen(["open", "-R", str(path)])

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
        except Exception:
            pass
        self._apply_forwarding_mutex()

    def _on_wss_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        update_settings_file({"WSS_ENABLED": str(self._wss_var.get())})

    def _on_upnp_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        enabled = bool(self._upnp_var.get())
        updates: dict[str, str] = {"UPNP_ENABLED": "true" if enabled else "false"}
        # Mutex with NAT-PMP: enabling UPnP turns NAT-PMP off.  Both routes
        # cannot coexist usefully — VPN captures the default route and
        # routing asymmetry breaks the UPnP path while VPN is active.
        if enabled and self._natpmp_var.get():
            self._natpmp_var.set(False)
            updates["NATPMP_ENABLED"] = "false"
        update_settings_file(updates)
        self._apply_forwarding_mutex()

    def _on_natpmp_toggle(self) -> None:
        from src.config import update_settings_file  # noqa: PLC0415

        enabled = bool(self._natpmp_var.get())
        updates: dict[str, str] = {"NATPMP_ENABLED": "true" if enabled else "false"}
        # Mutex with UPnP — see ``_on_upnp_toggle`` for the rationale.
        if enabled and self._upnp_var.get():
            self._upnp_var.set(False)
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
            self._root.update()
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

        # Refresh the tray menu text after server starts
        self._root.after(1500, self._update_tray_status)

    def _stop_server(self) -> None:
        self._set_status("Stopping...")
        self._server.stop()

    def _on_adopted_server(self) -> None:
        """Reflect an adopted running server in the UI.

        Mirrors the state transitions ``_start_server`` makes after a
        successful launch (disable settings, flip toggle to Stop, update
        status pill and tray), but without spawning a new subprocess.
        The user can then click Stop/Quit to terminate the orphan.
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
        """Periodic UI refresh (300 ms).

        Also drains ObjC-callback flags — the only safe place to call Tk or
        AppKit window APIs is from within the Tk event loop, never from inside
        a Cocoa event-dispatch callback.
        """
        if self._quitting:
            return

        if self._show_window_requested:
            self._show_window_requested = False
            self._do_show_window()
        if self._toggle_server_requested:
            self._toggle_server_requested = False
            self._on_toggle()
            self._update_tray_status()
        if self._copy_token_requested:
            self._copy_token_requested = False
            self._on_copy_token()
        if self._add_client_requested:
            self._add_client_requested = False
            self._on_add_to_client()
        if self._install_update_requested:
            self._install_update_requested = False
            self._on_update_install()
        if self._check_updates_requested:
            self._check_updates_requested = False
            self._on_update_check_now()
        if self._update_ui_dirty:
            self._update_ui_dirty = False
            self._refresh_update_ui()
        if self._quit_requested:
            self._quit_requested = False
            self._on_tray_quit()
            return

        try:
            while True:
                cb = self._pending_ui.get_nowait()
                cb()
        except queue.Empty:
            pass

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
                self._update_tray_status()

        self._root.after(POLL_MS, self._update_ui)

    # ── Menu bar (NSStatusItem via PyObjC) ────────────────────────────────

    def _setup_tray(self) -> bool:
        """Set up the macOS menu bar icon using NSStatusItem.

        Creates the ObjC action delegate and the NSStatusItem synchronously.
        CTk initialises NSApp when the CTk() window is created, so the
        status bar is available immediately — no need to defer via after().
        Returns True when the menu bar icon was successfully created.
        """
        if not _PYOBJC_AVAILABLE:
            print(
                "RCFlow: PyObjC not available — menu bar icon disabled.\n"
                "Install the tray extras:  uv sync --extra tray",
                file=sys.stderr,
            )
            logger.warning("PyObjC not available — running without menu bar icon")
            return False

        self._delegate = _TrayDelegate.new()
        self._delegate._gui = self
        self._init_status_item()
        self._install_reopen_handler()
        return self._status_item is not None

    def _install_reopen_handler(self) -> None:
        """Install the ``kAEReopenApplication`` AppleEvent handler.

        macOS fires this event on the running process when the user launches
        the .app again via Finder / Dock / Launchpad / ``open``.  Without a
        handler, the event is dropped and the dashboard never re-opens.
        """
        try:
            from Foundation import NSAppleEventManager  # noqa: PLC0415  # ty:ignore[unresolved-import]
        except ImportError:
            return

        # FourCharCodes:  'aevt' -> kCoreEventClass,  'rapp' -> kAEReopenApplication
        core_event_class = int.from_bytes(b"aevt", "big")
        reopen_event_id = int.from_bytes(b"rapp", "big")

        with contextlib.suppress(Exception):
            mgr = NSAppleEventManager.sharedAppleEventManager()
            mgr.setEventHandler_andSelector_forEventClass_andEventID_(
                self._delegate,
                b"handleReopen:withReplyEvent:",
                core_event_class,
                reopen_event_id,
            )

    def _init_status_item(self) -> None:
        """Create the NSStatusItem and its dropdown menu (main thread only).

        On success, withdraws the settings window (the menu bar icon becomes
        the only visible UI).  On failure, the window stays visible as a
        fallback so the app is not completely invisible.
        """
        try:
            from AppKit import (  # noqa: PLC0415  # ty:ignore[unresolved-import]
                NSImage,
                NSImageRep,
                NSMenu,
                NSMenuItem,
                NSStatusBar,
                NSVariableStatusItemLength,
            )
            from Foundation import NSMakeSize  # noqa: PLC0415  # ty:ignore[unresolved-import]
        except ImportError:
            logger.warning("AppKit import failed in _init_status_item — keeping window visible")
            return

        try:
            status_bar = NSStatusBar.systemStatusBar()
            self._status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)

            img: object | None = self._load_tray_template_image(NSImage, NSImageRep, NSMakeSize)

            # Fall back to the bundled .icns app icon (full-color, non-template).
            if img is None:
                icon_path = self._get_icon_path()
                if icon_path.exists():
                    img = NSImage.imageWithContentsOfFile_(str(icon_path))
                    if img is not None:
                        img.setSize_(NSMakeSize(18, 18))

            if img is not None:
                self._status_item.button().setImage_(img)

            if self._status_item.button().image() is None:
                self._status_item.button().setTitle_("RC")
            menu = NSMenu.new()

            self._ns_status_text = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "RCFlow Worker: Stopped", None, ""
            )
            self._ns_status_text.setEnabled_(False)
            menu.addItem_(self._ns_status_text)

            menu.addItem_(NSMenuItem.separatorItem())

            self._ns_external_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("External: —", None, "")
            self._ns_external_item.setEnabled_(False)
            self._ns_external_item.setHidden_(True)
            menu.addItem_(self._ns_external_item)

            self._ns_update_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Update available", "installUpdate:", ""
            )
            self._ns_update_item.setTarget_(self._delegate)
            self._ns_update_item.setHidden_(True)
            _img = self._sf_symbol_image("arrow.down.circle.fill", "Install update")
            if _img is not None:
                self._ns_update_item.setImage_(_img)
            menu.addItem_(self._ns_update_item)

            dashboard_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Dashboard", "openSettings:", "")
            dashboard_item.setTarget_(self._delegate)
            _img = self._sf_symbol_image("macwindow", "Open RCFlow dashboard")
            if _img is not None:
                dashboard_item.setImage_(_img)
            menu.addItem_(dashboard_item)

            menu.addItem_(NSMenuItem.separatorItem())

            self._ns_toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Start Server", "toggleServer:", ""
            )
            self._ns_toggle_item.setTarget_(self._delegate)
            self._apply_toggle_icon(running=False)
            menu.addItem_(self._ns_toggle_item)

            menu.addItem_(NSMenuItem.separatorItem())

            self._ns_copy_token_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Copy Token", "copyToken:", ""
            )
            self._ns_copy_token_item.setTarget_(self._delegate)
            _img = self._sf_symbol_image("key.fill", "Copy API token")
            if _img is not None:
                self._ns_copy_token_item.setImage_(_img)
            menu.addItem_(self._ns_copy_token_item)

            self._ns_add_client_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Add to Client…", "addToClient:", ""
            )
            self._ns_add_client_item.setTarget_(self._delegate)
            _img = self._sf_symbol_image("plus.app", "Add to client")
            if _img is not None:
                self._ns_add_client_item.setImage_(_img)
            menu.addItem_(self._ns_add_client_item)

            menu.addItem_(NSMenuItem.separatorItem())

            self._ns_autostart_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Start with macOS", "toggleAutostart:", ""
            )
            self._ns_autostart_item.setTarget_(self._delegate)
            self._apply_autostart_icon(_is_autostart_enabled())
            menu.addItem_(self._ns_autostart_item)

            self._ns_check_updates_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Check for Updates", "checkUpdates:", ""
            )
            self._ns_check_updates_item.setTarget_(self._delegate)
            _img = self._sf_symbol_image("arrow.triangle.2.circlepath", "Check for updates")
            if _img is not None:
                self._ns_check_updates_item.setImage_(_img)
            menu.addItem_(self._ns_check_updates_item)

            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", "quitApp:", "")
            quit_item.setTarget_(self._delegate)
            _img = self._sf_symbol_image("power", "Quit RCFlow Worker")
            if _img is not None:
                quit_item.setImage_(_img)
            menu.addItem_(quit_item)

            self._status_item.setMenu_(menu)
        except Exception as _exc:
            import traceback  # noqa: PLC0415

            msg = traceback.format_exc()
            print(f"RCFlow: NSStatusItem setup failed — {_exc}\n{msg}", file=sys.stderr)
            logger.exception("Failed to create NSStatusItem — keeping window visible")
            self._status_item = None

    @staticmethod
    def _get_icon_path() -> Path:
        from src.paths import is_frozen  # noqa: PLC0415

        if is_frozen():
            # PyInstaller --icon places the .icns at Contents/Resources/
            return Path(sys.executable).resolve().parent.parent / "Resources" / "tray_icon.icns"
        return Path(__file__).resolve().parent / "assets" / "tray_icon.icns"

    @staticmethod
    def _tray_template_dir() -> Path:
        """Directory holding the monochrome menu-bar template PNGs."""
        from src.paths import is_frozen  # noqa: PLC0415

        if is_frozen():
            return Path(sys.executable).resolve().parent.parent / "Resources"
        return Path(__file__).resolve().parent / "assets"

    def _load_tray_template_image(
        self,
        ns_image_cls: object,
        ns_image_rep_cls: object,
        ns_make_size: object,
    ) -> object | None:
        """Build a multi-rep NSImage from the 1x/2x/3x template PNGs.

        Returns a template-flagged NSImage sized to 18x18 pt with full-resolution
        backing reps for retina, or None if no rep file could be loaded.
        """
        base_dir = self._tray_template_dir()
        rep_files = [
            base_dir / "tray_icon_template.png",
            base_dir / "tray_icon_template@2x.png",
            base_dir / "tray_icon_template@3x.png",
        ]

        # Wide 2:1 menu-bar template (36x18 pt). NSStatusItem with
        # NSVariableStatusItemLength expands the slot to fit non-square images.
        img = ns_image_cls.alloc().initWithSize_(ns_make_size(36, 18))  # ty:ignore[unresolved-attribute]
        added = 0
        for rep_path in rep_files:
            if not rep_path.exists():
                continue
            with contextlib.suppress(Exception):
                rep = ns_image_rep_cls.imageRepWithContentsOfFile_(str(rep_path))  # ty:ignore[unresolved-attribute]
                if rep is not None:
                    img.addRepresentation_(rep)
                    added += 1

        if added == 0:
            return None
        img.setTemplate_(True)
        return img

    @staticmethod
    def _make_status_dot(rgb: tuple[float, float, float]) -> object | None:
        """Return a small filled circle NSImage in the given color.

        Used as the leading icon on the "RCFlow Worker: Running/Stopped" menu
        item to surface daemon state at a glance.
        """
        try:
            from AppKit import (  # noqa: PLC0415  # ty:ignore[unresolved-import]
                NSBezierPath,
                NSColor,
                NSImage,
            )
            from Foundation import NSMakeRect, NSMakeSize  # noqa: PLC0415  # ty:ignore[unresolved-import]
        except ImportError:
            return None

        size = 12.0
        img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
        img.lockFocus()
        try:
            r, g, b = rgb
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).setFill()
            inset = 1.0
            path = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
            )
            path.fill()
        finally:
            img.unlockFocus()
        # Don't flag as template — we want the actual color rendered, not auto-tinted.
        return img

    def _update_tray_status(self) -> None:
        running = self._server.is_running()
        if self._ns_status_text is not None:
            with contextlib.suppress(Exception):
                text = "RCFlow Worker: Running" if running else "RCFlow Worker: Stopped"
                self._ns_status_text.setTitle_(text)  # ty:ignore[unresolved-attribute]
                # Green dot when running, neutral grey when stopped.
                dot_rgb: tuple[float, float, float] = (
                    (0.30, 0.78, 0.40) if running else (0.55, 0.55, 0.55)
                )
                dot_img = self._make_status_dot(dot_rgb)
                if dot_img is not None:
                    self._ns_status_text.setImage_(dot_img)  # ty:ignore[unresolved-attribute]
        if self._ns_toggle_item is not None:
            with contextlib.suppress(Exception):
                label = "Stop Server" if running else "Start Server"
                self._ns_toggle_item.setTitle_(label)  # ty:ignore[unresolved-attribute]
                self._apply_toggle_icon(running=running)
        if self._ns_external_item is not None:
            with contextlib.suppress(Exception):
                forwarding_on = bool(self._upnp_var.get()) or bool(self._natpmp_var.get())
                self._ns_external_item.setHidden_(not forwarding_on)  # ty:ignore[unresolved-attribute]
                display = self._external_addr_var.get() or "—"  # ty:ignore[unresolved-attribute]
                self._ns_external_item.setTitle_(f"External: {display}")  # ty:ignore[unresolved-attribute]

    def _refresh_autostart_item(self) -> None:
        if self._ns_autostart_item is None:
            return
        with contextlib.suppress(Exception):
            self._apply_autostart_icon(_is_autostart_enabled())

    @staticmethod
    def _sf_symbol_image(name: str, accessibility: str, *, template: bool = True) -> object | None:
        """Load an SF Symbol as an NSImage. Returns None on macOS < 11 or symbol miss."""
        try:
            from AppKit import NSImage  # noqa: PLC0415  # ty:ignore[unresolved-import]
        except ImportError:
            return None
        with contextlib.suppress(Exception):
            init = getattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
            if init is None:
                return None
            img = init(name, accessibility)
            if img is not None and template:
                img.setTemplate_(True)
            return img
        return None

    def _apply_toggle_icon(self, *, running: bool) -> None:
        """Set play/stop SF Symbol on the start/stop menu item."""
        if self._ns_toggle_item is None:
            return
        with contextlib.suppress(Exception):
            sym = "stop.fill" if running else "play.fill"
            label = "Stop server" if running else "Start server"
            img = self._sf_symbol_image(sym, label)
            if img is not None:
                self._ns_toggle_item.setImage_(img)  # ty:ignore[unresolved-attribute]

    def _apply_autostart_icon(self, enabled: bool) -> None:
        """Show a checkmark / empty-circle leading icon on the autostart item.

        Replaces NSMenuItem.setState_ — the built-in state column reserves a
        shared indent across the whole menu, which shifts every other row a
        few pixels right whenever any item is checked. Using a per-item image
        keeps the rest of the menu aligned with the dashboard / start-stop
        icons next to them.
        """
        if self._ns_autostart_item is None:
            return
        with contextlib.suppress(Exception):
            self._ns_autostart_item.setState_(0)  # ty:ignore[unresolved-attribute]
            sym = "checkmark.circle.fill" if enabled else "circle"
            img = self._sf_symbol_image(sym, "Start with macOS")
            if img is not None:
                self._ns_autostart_item.setImage_(img)  # ty:ignore[unresolved-attribute]

    def _show_window(self) -> None:
        """Request that the settings panel be shown.

        Sets a flag consumed by _update_ui on the Tk event-loop tick.
        Must NOT call any Tk or AppKit APIs directly — this may be called from
        ObjC action callbacks which run inside Cocoa's event-dispatch context.
        """
        self._show_window_requested = True

    def _do_show_window(self) -> None:
        """Reveal and raise the settings panel.  Called only from _update_ui."""
        # Promote to a regular foreground app while the window is visible:
        # this gives the process a proper Dock icon + Cmd-Tab entry and lets
        # Cocoa activate it correctly.  When the window closes we drop back
        # to accessory so the Dock tile disappears (see _on_window_close).
        with contextlib.suppress(Exception):
            from AppKit import NSApplication  # noqa: PLC0415  # ty:ignore[unresolved-import]

            ns_app = NSApplication.sharedApplication()
            ns_app.setActivationPolicy_(0)  # NSApplicationActivationPolicyRegular
            ns_app.activateIgnoringOtherApps_(True)

        self._root.deiconify()
        self._root.lift()
        # Float above all windows briefly so it is visible even when another
        # app is in front; normal layering is restored after 300 ms.
        self._root.attributes("-topmost", True)
        self._root.after(300, lambda: self._root.attributes("-topmost", False))

    def _on_tray_quit(self, icon: object = None, item: object = None) -> None:
        if self._quitting:
            return
        self._quitting = True

        # Remove the menu bar icon immediately so it disappears while we wait
        # for the server subprocess to shut down.
        if self._status_item is not None:
            with contextlib.suppress(Exception):
                from AppKit import NSStatusBar  # noqa: PLC0415  # ty:ignore[unresolved-import]

                NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            self._status_item = None

        # Tear down the IPC listener so the discovery file does not outlive us.
        if self._ipc_server is not None:
            with contextlib.suppress(Exception):
                self._ipc_server.close()  # ty:ignore[unresolved-attribute]
            self._ipc_server = None
        remove_ipc_file()

        # Stop the server synchronously — blocks until the child process is
        # dead so it cannot be orphaned when we destroy the Tk root next.
        self._server.stop_sync()

        self._root.after(0, self._root.destroy)

    # ── Window lifecycle ──────────────────────────────────────────────────

    def _on_window_close(self) -> None:
        """Close button hides the window to the menu bar (not a full quit).

        Only falls back to a full quit if the tray icon failed to initialise.
        """
        if self._status_item is not None:
            self._root.withdraw()
            # Drop back to accessory policy so the Dock tile that appeared
            # while the window was visible disappears — menu-bar-only state
            # is the expected "server running in the background" look.
            with contextlib.suppress(Exception):
                from AppKit import NSApplication  # noqa: PLC0415  # ty:ignore[unresolved-import]

                NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
        else:
            self._on_tray_quit()

    def _cleanup(self) -> None:
        """Safety-net cleanup: kill the server subprocess if still alive.

        Registered via ``atexit`` and as a SIGTERM handler so the child
        process is always reaped — even on abnormal exits or external kills.
        """
        if self._quitting:
            return  # _on_tray_quit already handled cleanup
        self._quitting = True
        remove_ipc_file()
        self._server.stop_sync(timeout=5)

    def run(self, *, minimized: bool = False) -> None:
        """Start the menu bar icon and CTk event loop.

        When *minimized* is True (login autostart), the dashboard is kept
        hidden and the process stays in accessory policy — tray icon only.
        """
        import datetime  # noqa: PLC0415

        _trace_path = Path.home() / "Library" / "Logs" / "rcflow-worker-trace.log"

        def _t(msg: str) -> None:
            with contextlib.suppress(OSError), _trace_path.open("a", encoding="utf-8") as _f:
                _f.write(f"  {datetime.datetime.now().isoformat()} {msg}\n")

        _t("run() entered")

        # Register cleanup handlers so the server subprocess is always reaped.
        atexit.register(self._cleanup)

        def _sigterm_handler(signum: int, frame: object) -> None:
            # Flag-based quit so the Tk event loop drains it safely.
            self._quit_requested = True

        signal.signal(signal.SIGTERM, _sigterm_handler)

        # Upgrade path: older builds installed the LaunchAgent plist without
        # the ``--minimized`` flag, so login-time launches popped up the
        # dashboard.  If autostart is currently enabled, rewrite the plist
        # so the next login uses the new flag.  Idempotent for new plists.
        if _is_autostart_enabled():
            with contextlib.suppress(Exception):
                _set_autostart(True)

        # Start the singleton IPC listener; a second launch will connect here
        # to ask us to reveal the dashboard window.  The accept thread sets a
        # flag — Tk/AppKit APIs are never touched from the socket thread.
        def _on_ipc_show() -> None:
            self._show_window_requested = True

        self._ipc_server = start_ipc_server(_on_ipc_show)
        _t(f"IPC listener started: {self._ipc_server is not None}")

        tray_ok = self._setup_tray()
        _t(f"_setup_tray() → tray_ok={tray_ok}")

        if tray_ok:
            if not getattr(sys, "frozen", False):
                with contextlib.suppress(Exception):
                    from AppKit import NSApplication  # noqa: PLC0415  # ty:ignore[unresolved-import]

                    NSApplication.sharedApplication().setActivationPolicy_(1)

            if minimized:
                # Login autostart path: keep the dashboard hidden so we don't
                # steal focus on boot.  Tray icon remains the entry point.
                self._root.withdraw()
                _t("window withdrawn (minimized autostart)")
            else:
                # Reveal the dashboard on launch.  Scheduled on the Tk event
                # loop so AppKit is fully initialised; _do_show_window already
                # activates the process (LSUIElement apps are not auto-frontmost).
                self._root.after(0, self._do_show_window)
                _t("window scheduled to show on launch")
        else:
            logger.warning("Menu bar icon unavailable — keeping settings window visible")
            _t("tray unavailable — window visible")

        # If a previous GUI crashed, the server subprocess it spawned may
        # still be running (reparented to launchd).  Adopt it so the user
        # can stop it from this new GUI instead of leaving it orphaned.
        adopted_pid = self._server.adopt_if_running()
        if adopted_pid is None:
            self._root.after(0, self._start_server)
        else:
            self._root.after(0, self._on_adopted_server)

        self._root.after(0, self._refresh_update_ui)
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

        self._root.after(3000, _status_loop)
        _t("entering mainloop()")
        self._root.mainloop()
        _t("mainloop() returned")

    def _on_status_result(
        self,
        sessions: int | None,
        backend_id: str | None,
        version: str | None,
        external_display: str | None,
    ) -> None:
        # poll_server_status calls this from a daemon thread.  All StringVar
        # mutations must happen on the Tk main thread — on macOS, calling them
        # from a background thread while NSMenu is in its modal tracking loop
        # causes AppKit to be accessed off the main thread, producing a deadlock
        # that manifests as a spinning-beachball cursor over the menu bar area.
        def _apply() -> None:
            if sessions is not None:
                self._sessions_var.set(str(sessions))  # ty:ignore[unresolved-attribute]
            if backend_id:
                self._backend_id_var.set(backend_id)  # ty:ignore[unresolved-attribute]
            if version:
                self._version_var.set(version)  # ty:ignore[unresolved-attribute]
            self._external_addr_var.set(external_display or "—")  # ty:ignore[unresolved-attribute]
            self._update_tray_status()

        self._pending_ui.put_nowait(_apply)


# ── NSStatusItem action delegate (PyObjC) ────────────────────────────────────
#
# Defined at module level so PyObjC registers the class with the ObjC runtime
# exactly once.  Guarded by try/except so the module loads cleanly on platforms
# where PyObjC is not installed.

_PYOBJC_AVAILABLE = False

try:
    import objc as _objc  # type: ignore[import-untyped]
    from AppKit import NSObject  # type: ignore[assignment]

    class _TrayDelegate(NSObject):
        """Objective-C action target for NSMenuItem callbacks."""

        _gui: RCFlowMacOSGUI | None

        def init(self) -> _TrayDelegate:
            self = _objc.super(_TrayDelegate, self).init()
            if self is not None:
                self._gui = None
            return self

        @_objc.IBAction
        def toggleServer_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                self._gui._toggle_server_requested = True

        @_objc.IBAction
        def openSettings_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                self._gui._show_window_requested = True

        @_objc.IBAction
        def copyToken_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                self._gui._copy_token_requested = True

        @_objc.IBAction
        def addToClient_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                self._gui._add_client_requested = True

        @_objc.IBAction
        def toggleAutostart_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                _set_autostart(not _is_autostart_enabled())
                self._gui._refresh_autostart_item()

        @_objc.IBAction
        def installUpdate_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                self._gui._install_update_requested = True
                self._gui._show_window_requested = True

        @_objc.IBAction
        def checkUpdates_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                self._gui._check_updates_requested = True

        @_objc.IBAction
        def quitApp_(self, sender: object) -> None:  # noqa: N802
            # Defer the actual shutdown to the Tk event loop — running
            # _on_tray_quit inside the NSMenu modal tracking loop blocks
            # the AppKit run loop for the full duration of stop_sync(),
            # which manifests as a stuck beachball cursor over the menu bar.
            if self._gui is not None:
                self._gui._quit_requested = True

        def handleReopen_withReplyEvent_(self, event: object, reply: object) -> None:  # noqa: N802
            """AppleEvent 'rapp' callback.

            macOS LaunchServices routes a double-click on an already-running
            .app (from Finder / Launchpad / Dock) to the existing process as
            a ``kAEReopenApplication`` AppleEvent rather than spawning a new
            process, so our singleton/IPC path never runs in that case.
            Hooking the reopen event here makes the second launch reveal the
            dashboard just like the IPC ``SHOW`` from a command-line launch.
            """
            if self._gui is not None:
                self._gui._show_window_requested = True

    _PYOBJC_AVAILABLE = True

except ImportError:
    pass


def _get_crash_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "rcflow-worker-crash.log"


def run_gui_macos(*, minimized: bool = False) -> None:
    """Entry point for the macOS menu bar + settings panel application.

    *minimized*: start with the dashboard hidden (tray-only).  The login
    autostart LaunchAgent passes this flag so rebooting does not pop the
    window; user-initiated launches leave it False and the dashboard shows.
    """
    import datetime  # noqa: PLC0415

    _trace_path = Path.home() / "Library" / "Logs" / "rcflow-worker-trace.log"

    def _trace(msg: str) -> None:
        try:
            _trace_path.parent.mkdir(parents=True, exist_ok=True)
            with _trace_path.open("a", encoding="utf-8") as _fh:
                _fh.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
        except OSError:
            pass

    _trace(
        f"run_gui_macos() entered — "
        f"frozen={getattr(sys, 'frozen', False)} "
        f"pyobjc={_PYOBJC_AVAILABLE} "
        f"platform={sys.platform}"
    )

    # ── Singleton check ──────────────────────────────────────────────────
    if not _acquire_singleton_lock():
        _trace("another instance already running — asking it to show dashboard")
        # Primary path: use the loopback IPC channel the running instance
        # exposes.  Works regardless of LaunchServices registration and
        # without spawning osascript.
        delivered = send_show_to_existing()
        _trace(f"send_show_to_existing() -> {delivered}")
        if not delivered:
            print(
                "RCFlow Worker is already running. Look for the bolt icon in the macOS menu bar.",
                file=sys.stderr,
            )
            # Fallback: AppleScript activate.  Works only for a registered
            # .app bundle, so it is best-effort.
            with contextlib.suppress(Exception):
                import subprocess as _sp  # noqa: PLC0415

                _sp.Popen(
                    [
                        "osascript",
                        "-e",
                        'tell application "RCFlow Worker" to activate',
                    ],
                    stdout=_sp.DEVNULL,
                    stderr=_sp.DEVNULL,
                )
        sys.exit(0)

    try:
        _trace("creating RCFlowMacOSGUI()")
        gui = RCFlowMacOSGUI()
        _trace(f"calling gui.run(minimized={minimized})")
        gui.run(minimized=minimized)
        _trace("gui.run() returned — mainloop exited cleanly")
    except Exception:
        import traceback  # noqa: PLC0415

        crash_msg = traceback.format_exc()
        _trace(f"EXCEPTION:\n{crash_msg}")

        try:
            crash_log = _get_crash_log_path()
            crash_log.parent.mkdir(parents=True, exist_ok=True)
            with crash_log.open("a", encoding="utf-8") as fh:
                fh.write(f"\n--- Crash at {datetime.datetime.now().isoformat()} ---\n")
                fh.write(crash_msg)
        except OSError:
            pass

        try:
            import tkinter as _tk  # noqa: PLC0415
            from tkinter import messagebox as _mb  # noqa: PLC0415

            _hidden = _tk.Tk()
            _hidden.withdraw()
            _mb.showerror(
                "RCFlow Worker \u2014 Startup Error",
                f"RCFlow failed to start.\n\n{crash_msg[:500]}\n\nFull log: {_get_crash_log_path()}",
            )
            _hidden.destroy()
        except Exception:
            pass

        raise
