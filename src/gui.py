"""Windows GUI + system tray application for the RCFlow backend server.

Launches a tkinter window with server controls, status, and live log output.
The server runs as a subprocess. Closing the window minimizes to the system tray;
double-clicking the tray icon restores the window. "Quit" from the tray stops
the server and exits the application entirely.

This is the default mode for frozen Windows builds.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# How often the UI polls for updates (ms)
_POLL_MS = 300
# Max log lines kept in the text widget
_MAX_LOG_LINES = 5000
# Max log lines buffered from subprocess output
_MAX_LOG_BUFFER = 10000


class RCFlowGUI:
    """Combined GUI window + system tray for the RCFlow server."""

    def __init__(self) -> None:
        self._server_proc: subprocess.Popen[str] | None = None
        self._server_lock = threading.Lock()
        self._start_time: float | None = None
        self._log_lines: collections.deque[str] = collections.deque(maxlen=_MAX_LOG_BUFFER)
        self._log_queue: queue.Queue[str] = queue.Queue(maxsize=_MAX_LOG_BUFFER)
        self._quitting = False

        # Tray icon (optional — may not have pystray)
        self._tray_icon: object | None = None

        self._root = tk.Tk()
        self._root.title("RCFlow Worker")
        self._root.geometry("820x660")
        self._root.minsize(620, 480)
        self._root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._set_window_icon()

        self._setup_styles()
        self._build_ui()
        self._load_settings()

    # ── UI construction ──────────────────────────────────────────────

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam" if "clam" in style.theme_names() else "default")

    def _set_window_icon(self) -> None:
        """Set the title bar icon to the RCFlow icon."""
        from src.paths import get_install_dir, is_frozen  # noqa: PLC0415

        if is_frozen():
            icon_path = get_install_dir() / "tray_icon.ico"
        else:
            icon_path = Path(__file__).resolve().parent.parent / "assets" / "tray_icon.ico"

        if icon_path.exists():
            try:
                self._root.iconbitmap(str(icon_path))
            except tk.TclError:
                logger.debug("Failed to set window icon from %s", icon_path)

    def _build_ui(self) -> None:
        root = self._root

        # Top frame: settings + controls
        top = ttk.Frame(root, padding=10)
        top.pack(fill=tk.X)

        settings_frame = ttk.LabelFrame(top, text="Server Settings", padding=8)
        settings_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(settings_frame, text="IP Address:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self._ip_var = tk.StringVar(value="0.0.0.0")
        self._ip_entry = ttk.Entry(settings_frame, textvariable=self._ip_var, width=18)
        self._ip_entry.grid(row=0, column=1, sticky=tk.W, padx=(0, 16))

        ttk.Label(settings_frame, text="Port:").grid(row=0, column=2, sticky=tk.W, padx=(0, 6))
        from src.config import _DEFAULT_PORT  # noqa: PLC0415

        self._port_var = tk.StringVar(value=str(_DEFAULT_PORT))
        self._port_entry = ttk.Entry(settings_frame, textvariable=self._port_var, width=8)
        self._port_entry.grid(row=0, column=3, sticky=tk.W)

        self._wss_var = tk.BooleanVar(value=True)
        self._wss_check = ttk.Checkbutton(
            settings_frame, text="WSS Enabled", variable=self._wss_var, command=self._on_wss_toggle,
        )
        self._wss_check.grid(row=0, column=4, sticky=tk.W, padx=(16, 0))

        btn_frame = ttk.Frame(top, padding=(16, 0, 0, 0))
        btn_frame.pack(side=tk.RIGHT)

        self._toggle_btn = ttk.Button(btn_frame, text="Start", width=10, command=self._on_toggle)
        self._toggle_btn.pack()

        self._copy_token_btn = ttk.Button(btn_frame, text="Copy Token", width=12, command=self._on_copy_token)
        self._copy_token_btn.pack(pady=(4, 0))

        # Status bar
        status_frame = ttk.Frame(root, padding=(10, 0, 10, 0))
        status_frame.pack(fill=tk.X)

        self._status_label = ttk.Label(status_frame, text="Stopped", foreground="gray")
        self._status_label.pack(side=tk.LEFT)

        # Instance details
        details_frame = ttk.LabelFrame(root, text="Instance Details", padding=8)
        details_frame.pack(fill=tk.X, padx=10, pady=(6, 0))

        detail_grid = ttk.Frame(details_frame)
        detail_grid.pack(fill=tk.X)

        labels = [
            ("Bound Address:", "_bound_addr_var"),
            ("Uptime:", "_uptime_var"),
            ("Active Sessions:", "_sessions_var"),
            ("Backend ID:", "_backend_id_var"),
        ]
        for i, (label_text, var_name) in enumerate(labels):
            ttk.Label(detail_grid, text=label_text).grid(row=i, column=0, sticky=tk.W, padx=(0, 8), pady=1)
            var = tk.StringVar(value="\u2014")
            setattr(self, var_name, var)
            ttk.Label(detail_grid, textvariable=var).grid(row=i, column=1, sticky=tk.W, pady=1)

        # Log output
        log_frame = ttk.LabelFrame(root, text="Server Log", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 10))

        self._log_text = scrolledtext.ScrolledText(
            log_frame,
            state=tk.DISABLED,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#1e1e2e",
            fg="#cdd6f4",
            insertbackground="#cdd6f4",
            selectbackground="#45475a",
        )
        self._log_text.pack(fill=tk.BOTH, expand=True)

        self._log_text.tag_configure("error", foreground="#f38ba8")
        self._log_text.tag_configure("warning", foreground="#fab387")

    # ── Settings I/O ─────────────────────────────────────────────────

    def _load_settings(self) -> None:
        """Load current config values into the input fields."""
        try:
            from src.config import Settings  # noqa: PLC0415

            settings = Settings()  # type: ignore[call-arg]
            self._ip_var.set(settings.RCFLOW_HOST)
            self._port_var.set(str(settings.RCFLOW_PORT))
            self._wss_var.set(settings.WSS_ENABLED)
        except Exception:
            pass

    def _on_wss_toggle(self) -> None:
        """Persist the WSS Enabled checkbox state to settings.json."""
        from src.config import update_settings_file  # noqa: PLC0415

        update_settings_file({"WSS_ENABLED": str(self._wss_var.get())})

    def _on_copy_token(self) -> None:
        """Copy the API key (token) to the system clipboard."""
        try:
            from src.config import Settings  # noqa: PLC0415

            api_key = Settings().RCFLOW_API_KEY  # type: ignore[call-arg]
            if not api_key:
                self._set_status("No API token configured", error=True)
                return
            self._root.clipboard_clear()
            self._root.clipboard_append(api_key)
            self._root.update()  # Required for clipboard to persist on Windows
            self._set_status("Token copied to clipboard")
        except Exception as exc:
            self._set_status(f"Failed to copy token: {exc}", error=True)

    # ── Server subprocess management ─────────────────────────────────

    def _is_server_running(self) -> bool:
        with self._server_lock:
            return self._server_proc is not None and self._server_proc.poll() is None

    def _on_toggle(self) -> None:
        if self._is_server_running():
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

        # Check port availability
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((host, port))
            sock.close()
        except OSError as exc:
            self._set_status(f"Error: Cannot bind {host}:{port} \u2014 {exc}", error=True)
            return

        # Persist user-entered host/port to settings.json so they survive restarts.
        # WSS_ENABLED is already persisted by _on_wss_toggle, but host/port have no
        # equivalent event handler, so we save them here at the moment of commitment.
        from src.config import update_settings_file  # noqa: PLC0415

        update_settings_file({"RCFLOW_HOST": host, "RCFLOW_PORT": str(port)})

        # Build environment with overridden host/port and WSS setting
        env = os.environ.copy()
        env["RCFLOW_HOST"] = host
        env["RCFLOW_PORT"] = str(port)
        env["WSS_ENABLED"] = str(self._wss_var.get())

        exe = self._get_executable_path()
        if getattr(sys, "frozen", False):
            cmd = [exe, "run"]
            cwd = str(Path(exe).parent)
        else:
            cmd = [sys.executable, "-m", "src", "run"]
            cwd = None

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                creationflags=creation_flags,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self._set_status(f"Error: Failed to start server \u2014 {exc}", error=True)
            return

        with self._server_lock:
            self._server_proc = proc

        self._start_time = time.monotonic()
        self._ip_entry.configure(state=tk.DISABLED)
        self._port_entry.configure(state=tk.DISABLED)
        self._wss_check.configure(state=tk.DISABLED)
        self._toggle_btn.configure(text="Stop")
        protocol = "WSS" if self._wss_var.get() else "WS"
        self._set_status(f"Starting ({protocol})...")

        # Start log reader thread
        reader = threading.Thread(target=self._read_server_output, args=(proc,), daemon=True)
        reader.start()

        protocol = "wss" if self._wss_var.get() else "ws"
        self._log_append(f"Server starting on {protocol}://{host}:{port} (PID {proc.pid})...")

    def _stop_server(self) -> None:
        with self._server_lock:
            proc = self._server_proc
        if proc is None:
            return

        self._set_status("Stopping...")
        self._log_append("Stopping server...")

        def _do_stop() -> None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            with self._server_lock:
                if self._server_proc is proc:
                    self._server_proc = None
            self._start_time = None

        threading.Thread(target=_do_stop, daemon=True).start()

    def _read_server_output(self, proc: subprocess.Popen[str]) -> None:
        """Read stdout from the server subprocess and push lines to the log queue."""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n\r")
                if line:
                    self._log_queue.put_nowait(line)
        except Exception:
            pass

    @staticmethod
    def _get_executable_path() -> str:
        if getattr(sys, "frozen", False):
            return sys.executable
        return sys.executable

    # ── Log display ──────────────────────────────────────────────────

    def _log_append(self, text: str) -> None:
        """Append a local message to the log queue (not from subprocess)."""
        try:
            self._log_queue.put_nowait(text)
        except queue.Full:
            pass

    def _drain_log_queue(self) -> None:
        lines: list[str] = []
        try:
            while True:
                lines.append(self._log_queue.get_nowait())
        except queue.Empty:
            pass

        if not lines:
            return

        self._log_text.configure(state=tk.NORMAL)
        at_bottom = self._log_text.yview()[1] >= 0.95

        for line in lines:
            tag = ()
            upper = line.upper()
            if "ERROR" in upper or "CRITICAL" in upper:
                tag = ("error",)
            elif "WARNING" in upper:
                tag = ("warning",)
            self._log_text.insert(tk.END, line + "\n", tag)

        total = int(self._log_text.index("end-1c").split(".")[0])
        if total > _MAX_LOG_LINES:
            self._log_text.delete("1.0", f"{total - _MAX_LOG_LINES}.0")

        if at_bottom:
            self._log_text.see(tk.END)
        self._log_text.configure(state=tk.DISABLED)

    # ── Status & details ─────────────────────────────────────────────

    def _set_status(self, text: str, *, error: bool = False) -> None:
        if error:
            self._status_label.configure(text=text, foreground="#e64553")
        elif "stop" in text.lower() or text == "Stopped":
            self._status_label.configure(text=text, foreground="gray")
        elif "start" in text.lower():
            self._status_label.configure(text=text, foreground="#df8e1d")
        else:
            self._status_label.configure(text=text, foreground="#40a02b")

    def _poll_server_status(self) -> None:
        """Fetch active session count and backend ID from the server's HTTP API."""
        if not self._is_server_running():
            return

        host = self._ip_var.get().strip()
        port = self._port_var.get().strip()

        wss_enabled = self._wss_var.get()

        # Use a thread so the HTTP request doesn't block the UI
        def _fetch() -> None:
            import json  # noqa: PLC0415
            import ssl  # noqa: PLC0415
            import urllib.request  # noqa: PLC0415

            scheme = "https" if wss_enabled else "http"
            base = f"{scheme}://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

            # When using WSS with self-signed certs, skip verification for local health checks
            ctx = None
            if wss_enabled:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=2, context=ctx) as resp:
                    if resp.status != 200:
                        return
            except Exception:
                return

            # Try to get info (requires API key)
            try:
                from src.config import Settings  # noqa: PLC0415

                api_key = Settings().RCFLOW_API_KEY  # type: ignore[call-arg]
                req = urllib.request.Request(
                    f"{base}/api/info",
                    headers={"X-API-Key": api_key},
                )
                with urllib.request.urlopen(req, timeout=2, context=ctx) as resp:
                    data = json.loads(resp.read())
                    sessions = data.get("active_sessions")
                    backend_id = data.get("backend_id", "")
                    if sessions is not None:
                        self._sessions_var.set(str(sessions))
                    if backend_id:
                        display = backend_id[:8] + "..." if len(backend_id) > 12 else backend_id
                        self._backend_id_var.set(display)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_ui(self) -> None:
        """Periodic UI refresh."""
        if self._quitting:
            return

        self._drain_log_queue()

        running = self._is_server_running()

        if running:
            protocol = "WSS" if self._wss_var.get() else "WS"
            self._set_status(f"Running ({protocol})")
            if self._start_time:
                elapsed = time.monotonic() - self._start_time
                h, rem = divmod(int(elapsed), 3600)
                m, s = divmod(rem, 60)
                self._uptime_var.set(f"{h:02d}:{m:02d}:{s:02d}")
            self._bound_addr_var.set(f"{self._ip_var.get()}:{self._port_var.get()}")
        else:
            if self._toggle_btn.cget("text") == "Stop":
                # Server exited unexpectedly or was stopped
                with self._server_lock:
                    rc = self._server_proc.returncode if self._server_proc else None
                    self._server_proc = None
                self._start_time = None
                self._ip_entry.configure(state=tk.NORMAL)
                self._port_entry.configure(state=tk.NORMAL)
                self._wss_check.configure(state=tk.NORMAL)
                self._toggle_btn.configure(text="Start")
                if rc and rc != 0:
                    self._set_status(f"Stopped (exit code {rc})", error=True)
                    self._log_append(f"Server exited with code {rc}")
                else:
                    self._set_status("Stopped")
                self._uptime_var.set("\u2014")
                self._bound_addr_var.set("\u2014")
                self._sessions_var.set("\u2014")
                self._backend_id_var.set("\u2014")
                self._update_tray_status()

        self._root.after(_POLL_MS, self._update_ui)

    # ── System tray integration ──────────────────────────────────────

    def _setup_tray(self) -> bool:
        """Set up the system tray icon. Returns True on success."""
        try:
            import pystray  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415
        except ImportError:
            logger.debug("pystray/Pillow not available — running without tray icon")
            return False

        icon_image = self._load_tray_icon(Image)

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: "RCFlow Worker: Running" if self._is_server_running() else "RCFlow Worker: Stopped",
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

        # Run pystray in a background thread
        tray_thread = threading.Thread(target=icon.run, daemon=True)
        tray_thread.start()
        return True

    @staticmethod
    def _load_tray_icon(Image: type) -> object:  # noqa: N803
        """Load or generate the tray icon."""
        from src.paths import get_install_dir, is_frozen  # noqa: PLC0415

        if is_frozen():
            icon_path = get_install_dir() / "tray_icon.ico"
        else:
            icon_path = Path(__file__).resolve().parent.parent / "assets" / "tray_icon.ico"

        if icon_path.exists():
            return Image.open(str(icon_path))

        # Fallback: generate a simple icon
        from PIL import ImageDraw  # noqa: PLC0415

        img = Image.new("RGBA", (64, 64), (15, 23, 42, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, 59, 59], radius=8, fill=(56, 189, 248, 255))
        draw.text((14, 16), "RC", fill=(15, 23, 42, 255))
        return img

    def _update_tray_status(self) -> None:
        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.update_menu()  # type: ignore[attr-defined]

    def _on_tray_open(self, icon: object = None, item: object = None) -> None:
        """Restore the GUI window from the tray."""
        self._root.after(0, self._show_window)

    def _show_window(self) -> None:
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()

    def _on_toggle_autostart(self, icon: object, item: object) -> None:
        current = _is_autostart_enabled()
        _set_autostart(not current)
        self._update_tray_status()

    def _on_tray_quit(self, icon: object = None, item: object = None) -> None:
        """Quit the entire application: stop server, close tray, destroy window."""
        self._quitting = True
        self._stop_server()

        if self._tray_icon is not None:
            with contextlib.suppress(Exception):
                self._tray_icon.stop()  # type: ignore[attr-defined]

        # Destroy the tkinter window from the main thread
        self._root.after(0, self._root.destroy)

    # ── Window lifecycle ─────────────────────────────────────────────

    def _on_window_close(self) -> None:
        """Handle the X button: minimize to tray if available, otherwise quit."""
        if self._tray_icon is not None:
            self._root.withdraw()
        else:
            # No tray support — actually quit
            self._on_tray_quit()

    def run(self) -> None:
        """Start the GUI event loop."""
        has_tray = self._setup_tray()

        # Auto-start the server on launch
        self._start_server()

        # Start periodic UI updates
        self._root.after(_POLL_MS, self._update_ui)

        # Poll server status via HTTP every 5 seconds
        def _status_loop() -> None:
            if not self._quitting:
                self._poll_server_status()
                self._root.after(5000, _status_loop)

        self._root.after(3000, _status_loop)  # First check after 3s to let server start

        self._root.mainloop()


# ── Windows autostart helpers ────────────────────────────────────────

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
                exe = sys.executable if getattr(sys, "frozen", False) else sys.executable
                winreg.SetValueEx(key, _AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, f'"{exe}" gui')
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
    except OSError as e:
        logger.error("Failed to update autostart registry: %s", e)


def run_gui() -> None:
    """Entry point for the GUI + tray application."""
    gui = RCFlowGUI()
    gui.run()
