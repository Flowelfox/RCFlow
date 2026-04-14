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

import contextlib
import logging
import plistlib
import sys
import time
import tkinter as tk
from pathlib import Path

import customtkinter as ctk  # ty:ignore[unresolved-import]

from src.gui import theme
from src.gui.core import (
    MAX_LOG_LINES,
    POLL_MS,
    LogBuffer,
    ServerManager,
    poll_server_status,
)

logger = logging.getLogger(__name__)

# LaunchAgent plist for "Start with macOS" autostart
_LAUNCHAGENT_LABEL = "com.rcflow.worker"
_LAUNCHAGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHAGENT_LABEL}.plist"


def _is_autostart_enabled() -> bool:
    """Return True if the RCFlow LaunchAgent plist exists."""
    return _LAUNCHAGENT_PLIST.exists()


def _set_autostart(enabled: bool) -> None:
    """Write or remove the LaunchAgent plist for login autostart."""
    if enabled:
        _LAUNCHAGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
        prog_args = (
            [str(Path(sys.executable).resolve()), "gui"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "src", "gui"]
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

        # NSStatusItem and related ObjC objects (populated by _init_status_item)
        self._status_item: object | None = None
        self._delegate: object | None = None
        self._ns_status_text: object | None = None
        self._ns_toggle_item: object | None = None
        self._ns_copy_token_item: object | None = None
        self._ns_autostart_item: object | None = None

        self._root = ctk.CTk()
        self._root.title("RCFlow Worker")
        self._root.geometry("860x700")
        self._root.minsize(640, 500)
        self._root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._build_ui()
        self._load_settings()

    # ── UI construction ───────────────────────────────────────────────────

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
            self._root.update()
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

        # Refresh the tray menu text after server starts
        self._root.after(1500, self._update_tray_status)

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
        return self._status_item is not None

    def _init_status_item(self) -> None:
        """Create the NSStatusItem and its dropdown menu (main thread only).

        On success, withdraws the settings window (the menu bar icon becomes
        the only visible UI).  On failure, the window stays visible as a
        fallback so the app is not completely invisible.
        """
        try:
            from AppKit import (  # noqa: PLC0415  # ty:ignore[unresolved-import]
                NSImage,
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

            img: object | None = None

            # Prefer an SF Symbol — guaranteed visible in both light and dark mode.
            # imageWithSystemSymbolName:accessibilityDescription: requires macOS 11+;
            # guard with getattr so the code runs on older systems without crashing.
            _sym_init = getattr(
                NSImage,
                "imageWithSystemSymbolName_accessibilityDescription_",
                None,
            )
            if _sym_init is not None:
                img = _sym_init("bolt.fill", "RCFlow Worker")

            # Fall back to the bundled .icns file.
            if img is None:
                icon_path = self._get_icon_path()
                if icon_path.exists():
                    img = NSImage.imageWithContentsOfFile_(str(icon_path))

            # Explicitly size the image to the standard 18x18 pt menu-bar height
            # so it renders at the correct scale on both standard and Retina displays.
            if img is not None:
                img.setSize_(NSMakeSize(18, 18))
                img.setTemplate_(True)
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

            self._ns_toggle_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Start Server", "toggleServer:", ""
            )
            self._ns_toggle_item.setTarget_(self._delegate)
            menu.addItem_(self._ns_toggle_item)

            menu.addItem_(NSMenuItem.separatorItem())

            open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Open Settings\u2026", "openSettings:", ""
            )
            open_item.setTarget_(self._delegate)
            menu.addItem_(open_item)

            self._ns_copy_token_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Copy Token", "copyToken:", ""
            )
            self._ns_copy_token_item.setTarget_(self._delegate)
            menu.addItem_(self._ns_copy_token_item)

            menu.addItem_(NSMenuItem.separatorItem())

            self._ns_autostart_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Start with macOS", "toggleAutostart:", ""
            )
            self._ns_autostart_item.setTarget_(self._delegate)
            self._ns_autostart_item.setState_(1 if _is_autostart_enabled() else 0)
            menu.addItem_(self._ns_autostart_item)

            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", "quitApp:", "")
            quit_item.setTarget_(self._delegate)
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

    def _update_tray_status(self) -> None:
        running = self._server.is_running()
        if self._ns_status_text is not None:
            with contextlib.suppress(Exception):
                text = "RCFlow Worker: Running" if running else "RCFlow Worker: Stopped"
                self._ns_status_text.setTitle_(text)  # ty:ignore[unresolved-attribute]
        if self._ns_toggle_item is not None:
            with contextlib.suppress(Exception):
                label = "Stop Server" if running else "Start Server"
                self._ns_toggle_item.setTitle_(label)  # ty:ignore[unresolved-attribute]

    def _refresh_autostart_item(self) -> None:
        if self._ns_autostart_item is None:
            return
        with contextlib.suppress(Exception):
            self._ns_autostart_item.setState_(1 if _is_autostart_enabled() else 0)  # ty:ignore[unresolved-attribute]

    def _show_window(self) -> None:
        """Request that the settings panel be shown.

        Sets a flag consumed by _update_ui on the Tk event-loop tick.
        Must NOT call any Tk or AppKit APIs directly — this may be called from
        ObjC action callbacks which run inside Cocoa's event-dispatch context.
        """
        self._show_window_requested = True

    def _do_show_window(self) -> None:
        """Reveal and raise the settings panel.  Called only from _update_ui."""
        self._root.deiconify()
        self._root.lift()
        # Float above all windows briefly so it is visible even when another
        # app is in front; normal layering is restored after 300 ms.
        self._root.attributes("-topmost", True)
        self._root.after(300, lambda: self._root.attributes("-topmost", False))

    def _on_tray_quit(self, icon: object = None, item: object = None) -> None:
        self._quitting = True
        self._server.stop()
        if self._status_item is not None:
            with contextlib.suppress(Exception):
                from AppKit import NSStatusBar  # noqa: PLC0415  # ty:ignore[unresolved-import]

                NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            self._status_item = None
        self._root.after(0, self._root.destroy)

    # ── Window lifecycle ──────────────────────────────────────────────────

    def _on_window_close(self) -> None:
        """Close button hides the window to the menu bar (not a full quit).

        Only falls back to a full quit if the tray icon failed to initialise.
        """
        if self._status_item is not None:
            self._root.withdraw()
        else:
            self._on_tray_quit()

    def run(self) -> None:
        """Start the menu bar icon and CTk event loop."""
        import datetime  # noqa: PLC0415

        _trace_path = Path.home() / "Library" / "Logs" / "rcflow-worker-trace.log"

        def _t(msg: str) -> None:
            with contextlib.suppress(OSError), _trace_path.open("a", encoding="utf-8") as _f:
                _f.write(f"  {datetime.datetime.now().isoformat()} {msg}\n")

        _t("run() entered")
        tray_ok = self._setup_tray()
        _t(f"_setup_tray() → tray_ok={tray_ok}")

        if tray_ok:
            if not getattr(sys, "frozen", False):
                with contextlib.suppress(Exception):
                    from AppKit import NSApplication  # noqa: PLC0415  # ty:ignore[unresolved-import]

                    NSApplication.sharedApplication().setActivationPolicy_(1)
            self._root.withdraw()
            _t("window withdrawn")
        else:
            logger.warning("Menu bar icon unavailable — keeping settings window visible")
            _t("tray unavailable — window visible")

        self._root.after(0, self._start_server)
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

    def _on_status_result(self, sessions: int | None, backend_id: str | None) -> None:
        if sessions is not None:
            self._sessions_var.set(str(sessions))  # ty:ignore[unresolved-attribute]
        if backend_id:
            self._backend_id_var.set(backend_id)  # ty:ignore[unresolved-attribute]


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
        def toggleAutostart_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                _set_autostart(not _is_autostart_enabled())
                self._gui._refresh_autostart_item()

        @_objc.IBAction
        def quitApp_(self, sender: object) -> None:  # noqa: N802
            if self._gui is not None:
                self._gui._on_tray_quit()

    _PYOBJC_AVAILABLE = True

except ImportError:
    pass


def _get_crash_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "rcflow-worker-crash.log"


def run_gui_macos() -> None:
    """Entry point for the macOS menu bar + settings panel application."""
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

    try:
        _trace("creating RCFlowMacOSGUI()")
        gui = RCFlowMacOSGUI()
        _trace("calling gui.run()")
        gui.run()
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
