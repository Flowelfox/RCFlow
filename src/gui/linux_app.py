"""Linux launcher for the RCFlow worker GUI.

Runs under the host's system Python interpreter (``/usr/bin/python3``) so
it can use the distro-shipped Tcl/Tk and PyGObject bindings rather than
the PyInstaller-bundled ones — the bundled stack aborts on Ubuntu 25.04
with a libxcb 1.17 sequence-number assertion that distro Tk does not.

The frozen ``rcflow`` binary spawns this script via
``src.__main__._run_linux_native_dashboard`` after starting the FastAPI
worker subprocess.  This launcher then:

* Adopts the running worker via the pidfile (``ServerManager.adopt_if_running``).
* Shows the shared :class:`RCFlowDashboard` window (CustomTkinter).
* Hosts an AyatanaAppIndicator3 tray on a daemon GLib thread —
  callbacks marshal back onto the Tk thread via ``root.after(0, …)``.
* Sends desktop notifications via ``org.freedesktop.Notifications`` and
  reads the active light/dark theme from ``xdg-desktop-portal`` —
  both via :mod:`jeepney`.

Tray and portal helpers fall back to no-ops when their system bindings
are missing so a stripped-down install still gets a working window.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

if TYPE_CHECKING:
    from collections.abc import Callable

from src.gui._dashboard_ctk import (
    RCFlowDashboard,
    acquire_singleton_lock,
    autostart_menu_label,
    is_autostart_enabled,
    set_autostart,
)
from src.gui.core import send_show_to_existing

logger = logging.getLogger(__name__)


# ── Icon resolution ──────────────────────────────────────────────────────────


def _resolve_icon_path() -> str | None:
    """Locate the worker tray icon shipped alongside the launcher.

    Order: the .deb-installed location, the hicolor system icon, the dev
    repo asset.  Returns the first match or ``None`` so callers can fall
    back to a generic indicator name.
    """
    candidates = (
        Path("/opt/rcflow/tray_icon.png"),
        Path("/usr/share/icons/hicolor/256x256/apps/rcflow-worker.png"),
        Path(__file__).resolve().parent / "assets" / "tray_icon.png",
    )
    for path in candidates:
        if path.exists():
            return str(path)
    return None


# ── jeepney portal helpers (notifications + theme) ───────────────────────────


def _try_notify(summary: str, body: str = "") -> None:
    """Send a desktop notification via ``org.freedesktop.Notifications``.

    Best-effort: any D-Bus error (no notification daemon, jeepney missing,
    bus not reachable) is swallowed — notifications are convenience UX,
    not a guarantee.
    """
    try:
        import jeepney  # noqa: PLC0415  # ty:ignore[unresolved-import]
        from jeepney.io.blocking import open_dbus_connection  # noqa: PLC0415  # ty:ignore[unresolved-import]
    except ImportError:
        return
    try:
        addr = jeepney.DBusAddress(
            "/org/freedesktop/Notifications",
            bus_name="org.freedesktop.Notifications",
            interface="org.freedesktop.Notifications",
        )
        msg = jeepney.new_method_call(
            addr,
            "Notify",
            "susssasa{sv}i",
            (
                "RCFlow Worker",
                0,
                "rcflow-worker",
                summary,
                body,
                [],
                {},
                5000,
            ),
        )
        with open_dbus_connection(bus="SESSION") as conn:
            conn.send_and_get_reply(msg, timeout=2)
    except Exception as exc:
        logger.debug("Notification failed: %s", exc)


def _read_color_scheme() -> int | None:
    """Return the portal ``color-scheme`` value or ``None`` if unreachable.

    Mapping per the spec: ``0`` = no preference, ``1`` = dark, ``2`` = light.
    """
    try:
        import jeepney  # noqa: PLC0415  # ty:ignore[unresolved-import]
        from jeepney.io.blocking import open_dbus_connection  # noqa: PLC0415  # ty:ignore[unresolved-import]
    except ImportError:
        return None
    try:
        addr = jeepney.DBusAddress(
            "/org/freedesktop/portal/desktop",
            bus_name="org.freedesktop.portal.Desktop",
            interface="org.freedesktop.portal.Settings",
        )
        msg = jeepney.new_method_call(
            addr,
            "Read",
            "ss",
            ("org.freedesktop.appearance", "color-scheme"),
        )
        with open_dbus_connection(bus="SESSION") as conn:
            reply = conn.send_and_get_reply(msg, timeout=2)
        # Reply body shape: (variant<variant<uint32>>,)
        outer = reply.body[0]
        inner = outer[1] if isinstance(outer, tuple) and len(outer) >= 2 else outer
        value = inner[1] if isinstance(inner, tuple) and len(inner) >= 2 else inner
        return int(value)
    except Exception as exc:
        logger.debug("Portal color-scheme read failed: %s", exc)
        return None


def _color_scheme_to_ctk(value: int | None) -> str:
    """Map the portal ``color-scheme`` enum to a CTk appearance string."""
    if value == 1:
        return "dark"
    if value == 2:
        return "light"
    return "system"


def _start_color_scheme_subscriber(on_change: Callable[[int], None]) -> None:
    """Run a daemon thread that calls *on_change* whenever the portal value flips.

    Uses jeepney's blocking router with a ``MatchRule`` for the
    ``SettingChanged`` signal restricted to the appearance namespace.
    Silently exits when jeepney is missing or the bus is unreachable.
    """
    try:
        import jeepney  # noqa: PLC0415  # ty:ignore[unresolved-import]
        from jeepney.bus_messages import MatchRule  # noqa: PLC0415  # ty:ignore[unresolved-import]
        from jeepney.io.blocking import open_dbus_connection  # noqa: PLC0415  # ty:ignore[unresolved-import]
    except ImportError:
        return

    def _run() -> None:
        try:
            with open_dbus_connection(bus="SESSION") as conn:
                rule = MatchRule(
                    type="signal",
                    interface="org.freedesktop.portal.Settings",
                    member="SettingChanged",
                    path="/org/freedesktop/portal/desktop",
                )
                addr = jeepney.DBusAddress(
                    "/org/freedesktop/DBus",
                    bus_name="org.freedesktop.DBus",
                    interface="org.freedesktop.DBus",
                )
                conn.send_and_get_reply(
                    jeepney.new_method_call(addr, "AddMatch", "s", (rule.serialise(),)),
                    timeout=2,
                )
                while True:
                    msg = conn.receive(timeout=None)
                    if msg.header.fields.get(jeepney.HeaderFields.member) != "SettingChanged":
                        continue
                    body = msg.body
                    if len(body) < 3:
                        continue
                    namespace, key, variant = body[0], body[1], body[2]
                    if namespace != "org.freedesktop.appearance" or key != "color-scheme":
                        continue
                    value = variant[1] if isinstance(variant, tuple) else variant
                    try:
                        on_change(int(value))
                    except (TypeError, ValueError):
                        continue
        except Exception as exc:
            logger.debug("color-scheme subscriber exited: %s", exc)

    threading.Thread(target=_run, name="rcflow-portal-watch", daemon=True).start()


def _systemd_manage_unit(method: str, unit: str) -> str | None:
    """Call a Manager method (``StartUnit`` / ``StopUnit``) over the system bus.

    Returns ``None`` on success, otherwise the error name / message
    surfaced by D-Bus (``AccessDenied`` + polkit details, etc.).
    """
    try:
        import jeepney  # noqa: PLC0415  # ty:ignore[unresolved-import]
        from jeepney.io.blocking import open_dbus_connection  # noqa: PLC0415  # ty:ignore[unresolved-import]
    except ImportError:
        return "jeepney not available"
    try:
        addr = jeepney.DBusAddress(
            "/org/freedesktop/systemd1",
            bus_name="org.freedesktop.systemd1",
            interface="org.freedesktop.systemd1.Manager",
        )
        msg = jeepney.new_method_call(addr, method, "ss", (unit, "replace"))
        with open_dbus_connection(bus="SYSTEM") as conn:
            reply = conn.send_and_get_reply(msg, timeout=10)
    except Exception as exc:
        return str(exc)
    if reply.header.message_type == jeepney.MessageType.error:
        err = reply.header.fields.get(jeepney.HeaderFields.error_name, "?")
        msg_body = reply.body[0] if reply.body else ""
        return f"{err}: {msg_body}"
    return None


def _systemd_stop_unit(unit: str) -> str | None:
    return _systemd_manage_unit("StopUnit", unit)


def _systemd_start_unit(unit: str) -> str | None:
    return _systemd_manage_unit("StartUnit", unit)


def _read_install_version() -> str | None:
    """Read the worker version string from the .deb-installed ``VERSION`` file."""
    for candidate in (Path("/opt/rcflow/VERSION"), Path("/usr/local/rcflow/VERSION")):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return None


def _read_install_api_key(default: str = "") -> str:
    """Resolve the systemd worker's API key from ``/opt/rcflow/settings.json``.

    The launcher runs as the desktop user; its per-user settings.json may
    hold a different ``RCFLOW_API_KEY`` than the systemd unit's
    ``/opt/rcflow/settings.json`` (owned by ``rcflow:OWNER_USER`` 0640 by
    the deb postinst).  Prefer the install-side key so dashboard HTTP
    polling matches the running worker's key and ``/api/info`` returns
    200 instead of 401.
    """
    import json as _json  # noqa: PLC0415

    try:
        data = _json.loads(Path("/opt/rcflow/settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    key = data.get("RCFLOW_API_KEY")
    return key if isinstance(key, str) and key else default


def _find_listening_rcflow_pid(port: int) -> int | None:
    """Best-effort lookup of the rcflow worker pid bound to *port* on loopback.

    Walks ``/proc/net/tcp`` and ``/proc/<pid>/cmdline`` so we don't depend
    on ``lsof`` / ``ss`` / ``psutil`` being on PATH inside the launcher's
    runtime.  Returns ``None`` when no rcflow process owns the port (the
    caller falls back to a sentinel pid so adoption still succeeds).
    """
    import os as _os  # noqa: PLC0415

    try:
        with open("/proc/net/tcp") as fh:
            tcp_lines = fh.readlines()[1:]
    except OSError:
        return None
    target_local = f":{port:04X}"
    listening_inodes: set[int] = set()
    for line in tcp_lines:
        parts = line.split()
        if len(parts) < 10:
            continue
        local, state = parts[1], parts[3]
        if state != "0A":  # TCP_LISTEN
            continue
        if not local.upper().endswith(target_local):
            continue
        try:
            listening_inodes.add(int(parts[9]))
        except ValueError:
            continue
    if not listening_inodes:
        return None
    try:
        pids = _os.listdir("/proc")
    except OSError:
        return None
    for pid_entry in pids:
        if not pid_entry.isdigit():
            continue
        pid = int(pid_entry)
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = _os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = _os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if not target.startswith("socket:["):
                continue
            inode_str = target[len("socket:[") : -1]
            try:
                inode = int(inode_str)
            except ValueError:
                continue
            if inode not in listening_inodes:
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmdline = fh.read().decode("utf-8", "replace")
            except OSError:
                continue
            if "rcflow" in cmdline:
                return pid
    return None


# ── Linux GUI subclass ───────────────────────────────────────────────────────


class LinuxGUI(RCFlowDashboard):
    """Linux subclass: AyatanaAppIndicator3 tray + tk.PhotoImage window icon."""

    def __init__(self) -> None:
        # Apply the portal-reported theme before any CTk widget is built so
        # the dashboard starts with the correct appearance.  Falls back to
        # ``"system"`` (Tk's auto-detection) when the portal is unavailable.
        with contextlib.suppress(Exception):
            ctk.set_appearance_mode(_color_scheme_to_ctk(_read_color_scheme()))
        super().__init__()
        self._gtk_module: Any | None = None
        self._gtk_indicator: Any | None = None
        self._gtk_loop: Any | None = None
        self._linux_icon_photo: tk.PhotoImage | None = None

        # The launcher runs under the desktop user and ``UpdateService``'s
        # ``resolve_current_version`` only knows how to find the VERSION
        # file from inside the frozen rcflow binary's bundle.  For the
        # systemd-installed case the version file sits at
        # ``/opt/rcflow/VERSION`` — use that when the lookup came back
        # empty so the Updates card reports a real version instead of
        # "vunknown".
        version_override = _read_install_version()
        if version_override and not self._updater.current_version:
            self._updater._current_version = version_override

        # Always wrap ``is_running`` with a loopback probe fallback so
        # the dashboard reflects the live worker state regardless of
        # adoption status.  Cross-uid ``os.kill(pid, 0)`` returns False
        # for the systemd worker (PermissionError), and the launcher may
        # not yet have an adopted_pid — without this fallback
        # ``_update_ui`` flips the toggle to "Start" even while the
        # service is happily serving requests.
        original_is_running = self._server.is_running

        def _is_running_with_probe() -> bool:
            return original_is_running() or self._port_already_in_use()

        self._server.is_running = _is_running_with_probe  # ty:ignore[invalid-assignment]

    # ── Window icon ───────────────────────────────────────────────────────

    # ── External-worker adoption ──────────────────────────────────────────

    _adopted_external_worker: bool = False

    def run(self, *, minimized: bool = False) -> None:
        """Adopt an externally-managed worker (systemd) before delegating."""
        self._maybe_adopt_external_worker()
        super().run(minimized=minimized)

    def _start_server(self) -> None:
        """Skip the spawn path when an external worker has already been adopted.

        For systemd-installed deployments (``/opt/rcflow/rcflow`` exists)
        the dashboard's Start button asks systemd to start the unit so
        the worker keeps running as the ``rcflow`` user — spawning the
        binary directly here would inherit the desktop user's UID and
        the FastAPI process would fail to write the rcflow-owned SQLite
        database under ``/opt/rcflow/data``.
        """
        # Re-run adoption every time we'd otherwise spawn so a worker that
        # came up after the initial probe still gets picked up — without
        # this the ``_update_ui`` poll later flips the toggle back to
        # "Start" because ``ServerManager.is_running`` returns False when
        # the wrapper hasn't been installed yet.
        if not self._adopted_external_worker:
            self._maybe_adopt_external_worker()
        if self._adopted_external_worker:
            self._on_adopted_server()
            return
        if Path("/opt/rcflow/rcflow").exists() and self._start_via_systemd():
            return
        super()._start_server()

    def _start_via_systemd(self) -> bool:
        """Ask systemd to start ``rcflow.service`` via D-Bus.  Best-effort."""
        import threading as _th  # noqa: PLC0415

        self._set_status("Starting (systemd)…", sticky=True)
        self._log_buffer.append("[systemd] StartUnit rcflow.service via system D-Bus")

        def _run() -> None:
            error = _systemd_start_unit("rcflow.service")
            if error is not None:
                self._log_buffer.append(f"[systemd] StartUnit failed: {error}")
                self._root.after(
                    0,
                    lambda: self._set_status(f"StartUnit failed: {error}", error=True, sticky=True),
                )
                return
            self._log_buffer.append("[systemd] start request accepted, probing port…")
            # Poll for the port to come up so adoption can pick up the new pid.
            import time as _time  # noqa: PLC0415

            deadline = _time.monotonic() + 15.0
            while _time.monotonic() < deadline:
                if self._port_already_in_use():
                    self._root.after(0, self._maybe_adopt_external_worker)
                    self._root.after(0, self._on_adopted_server)
                    return
                _time.sleep(0.3)
            self._log_buffer.append("[systemd] worker did not bind port within 15 s")
            self._root.after(
                0,
                lambda: self._set_status("Worker did not start", error=True, sticky=True),
            )

        _th.Thread(target=_run, name="rcflow-start-unit", daemon=True).start()
        return True

    def _port_already_in_use(self) -> bool:
        """Loopback probe: ``True`` when the configured port already answers.

        Used as a defence-in-depth check because ``ServerManager.is_running``
        relies on ``os.kill(pid, 0)`` which returns ``False`` on
        ``PermissionError`` — the systemd worker runs as the ``rcflow``
        user, so the launcher (running as the desktop user) cannot signal
        it and ``is_running`` reports a false negative.
        """
        import socket as _socket  # noqa: PLC0415

        port_str = self._port_var.get().strip()
        try:
            port = int(port_str)
        except ValueError:
            return False
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            try:
                probe.connect(("127.0.0.1", port))
            except OSError:
                return False
        return True

    def _maybe_adopt_external_worker(self) -> None:
        """Detect a systemd-managed worker on the configured port and adopt it.

        The systemd unit runs as user ``rcflow`` with ``HOME=/opt/rcflow``,
        so its pidfile (when present) lives outside the launcher user's
        XDG data dir.  Probe loopback for an open port instead and look up
        the listening process so :class:`ServerManager` reports it as
        running and the dashboard skips the spawn path.
        """
        import socket as _socket  # noqa: PLC0415
        import time as _time  # noqa: PLC0415

        port_str = self._port_var.get().strip()
        try:
            port = int(port_str)
        except ValueError:
            return

        # Brief retry: the wrapper invokes ``systemctl start rcflow.service``
        # right before launching us, but the unit's ``ExecStart`` may still
        # be coming up when we get here.  Poll for a few seconds so the
        # adoption flag is set deterministically even when the dashboard
        # races the worker's first port-bind.
        deadline = _time.monotonic() + 5.0
        connected = False
        while _time.monotonic() < deadline:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                try:
                    probe.connect(("127.0.0.1", port))
                    connected = True
                    break
                except OSError:
                    pass
            _time.sleep(0.2)
        if not connected:
            return

        pid = _find_listening_rcflow_pid(port) or 0
        # ``ServerManager`` is otherwise package-private about adoption,
        # but the launcher needs to register an externally-running worker
        # without a pidfile.  Mutate the manager's state directly under
        # its lock — and remember the adoption on the GUI so the start
        # path can short-circuit even when ``is_running`` reports a false
        # negative because ``os.kill(pid, 0)`` raises PermissionError for
        # the cross-uid systemd worker.
        with self._server._lock:
            self._server._adopted_pid = pid or None
        self._server._start_time = _time.monotonic()
        self._adopted_external_worker = True

        # ``ServerManager.is_running`` calls ``_is_pid_alive`` which uses
        # ``os.kill(pid, 0)``.  PermissionError (cross-uid signal denied)
        # is treated as "not alive", so the systemd worker — running as
        # the ``rcflow`` user while the launcher runs as the desktop
        # user — is reported as stopped and ``_update_ui`` flips the
        # toggle back to "Start".  Wrap the method to fall back to a
        # loopback probe so the dashboard reflects the real state.
        original_is_running = self._server.is_running

        def _adopted_is_running() -> bool:
            return original_is_running() or self._port_already_in_use()

        self._server.is_running = _adopted_is_running  # ty:ignore[invalid-assignment]

        # Stream the systemd journal of the worker into the log viewer.
        # ``_proc`` is None for adopted servers, so the manager's normal
        # stdout-reader thread never starts; without this the dashboard
        # would show an empty Server Log.
        self._spawn_journal_tail()

        logger.info("Adopted external worker (pid=%s) on port %d", pid, port)

    def _spawn_journal_tail(self, *, unit: str = "rcflow.service") -> None:
        """Tail ``journalctl -u <unit> -f`` into the dashboard log buffer.

        Best-effort: ``journalctl`` may not be on PATH inside the launcher
        environment, the user may lack journal-read access, or the unit
        may be running under ``--user``.  Falls back to a single warning
        line in the log viewer when the tail process exits.
        """
        import shutil as _shutil  # noqa: PLC0415
        import subprocess as _sp  # noqa: PLC0415
        import threading as _th  # noqa: PLC0415

        journalctl = _shutil.which("journalctl")
        if journalctl is None:
            self._log_buffer.append("[journal] journalctl not on PATH — log streaming disabled.")
            return

        try:
            proc = _sp.Popen(
                [journalctl, "-u", unit, "-f", "-n", "200", "--no-pager", "-o", "cat"],
                stdout=_sp.PIPE,
                stderr=_sp.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._log_buffer.append(f"[journal] failed to start journalctl: {exc}")
            return

        self._journal_proc = proc

        def _pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._log_buffer.append(line.rstrip("\n"))
            self._log_buffer.append(f"[journal] journalctl exited with code {proc.poll()}")

        _th.Thread(target=_pump, name="rcflow-journal-tail", daemon=True).start()

    def _stop_server(self) -> None:
        """For adopted systemd workers, ask systemd to stop the unit via D-Bus.

        ``ServerManager.stop`` would call ``os.kill`` on the adopted pid,
        which raises ``PermissionError`` for the cross-uid systemd worker
        and silently no-ops.  ``pkexec`` requires a graphical polkit
        authentication agent which a stripped-down GNOME session may not
        be running.  Skip both layers and call
        ``org.freedesktop.systemd1.Manager.StopUnit`` directly via jeepney
        — the bundled ``50-rcflow.rules`` polkit rule grants local active
        sessions YES on the ``manage-units`` action for ``rcflow.service``,
        so no auth prompt is needed.  Falls back to ``pkexec`` only if
        the D-Bus call returns ``AccessDenied``.
        """
        if not self._adopted_external_worker:
            super()._stop_server()
            return

        import threading as _th  # noqa: PLC0415

        self._set_status("Stopping (systemd)…", sticky=True)
        self._log_buffer.append("[systemd] StopUnit rcflow.service via system D-Bus")

        def _run() -> None:
            error = _systemd_stop_unit("rcflow.service")
            if error is None:
                self._log_buffer.append("[systemd] stop request accepted.")
                self._root.after(0, self._on_external_stop_succeeded)
                return
            self._log_buffer.append(f"[systemd] StopUnit failed: {error}")
            if "AccessDenied" not in error and "auth_admin" not in error:
                self._root.after(
                    0,
                    lambda: self._set_status(f"StopUnit failed: {error}", error=True, sticky=True),
                )
                return
            # Polkit denied without an auth agent — try pkexec as a last resort.
            self._stop_via_pkexec()

        _th.Thread(target=_run, name="rcflow-stop-unit", daemon=True).start()

    def _stop_via_pkexec(self) -> None:
        """Last-ditch ``pkexec systemctl stop`` when D-Bus is denied."""
        import shutil as _shutil  # noqa: PLC0415
        import subprocess as _sp  # noqa: PLC0415

        pkexec = _shutil.which("pkexec") or "/usr/bin/pkexec"
        argv = [pkexec, "systemctl", "stop", "rcflow.service"]
        try:
            result = _sp.run(argv, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, _sp.SubprocessError) as exc:
            self._log_buffer.append(f"[systemctl] pkexec failed: {exc}")
            return
        if result.returncode == 0:
            self._log_buffer.append("[systemctl] stop via pkexec completed.")
            self._root.after(0, self._on_external_stop_succeeded)
        else:
            msg = (result.stderr or result.stdout or "").strip()
            self._log_buffer.append(f"[systemctl] pkexec exit {result.returncode}: {msg}")
            rc = result.returncode
            self._root.after(
                0,
                lambda: self._set_status(f"pkexec exit {rc}", error=True, sticky=True),
            )

    def _update_ui(self) -> None:
        """Auto-attach to a worker that came up after the initial probe.

        On a fresh dashboard launch the worker may take a beat to bind
        its port — adoption then races with mainloop startup.  Whenever
        the periodic poll sees the worker live but the toggle still
        reads "Start", flip it to the adopted-running state so the
        dashboard reflects reality without forcing the user to click
        Start (which would only respawn a duplicate worker).
        """
        if self._server.is_running() and self._toggle_btn.cget("text") == "Start":
            if not self._adopted_external_worker:
                self._maybe_adopt_external_worker()
            self._on_adopted_server()
        super()._update_ui()

    def _on_adopted_server(self) -> None:
        """Linux adoption path runs every launch, not just after a crash.

        The base implementation labels the status pill ``Running (WSS) —
        recovered`` and pins it sticky for 3 s — wording that fits an
        orphan recovery, but on systemd-managed installs there is
        nothing to "recover": the service is always running and the
        dashboard is just attaching to it.  Render a plain
        ``Running (PROTO)`` so opening the tray dashboard feels like
        reconnecting to a live state instead of recovering from a crash.
        """
        from src.gui import theme  # noqa: PLC0415

        self._ip_entry.configure(state="disabled")
        self._port_entry.configure(state="disabled")
        self._wss_check.configure(state="disabled")
        self._upnp_check.configure(state="disabled")
        self._natpmp_check.configure(state="disabled")
        self._toggle_btn.configure(
            text="Stop",
            fg_color=theme.BTN_STOP_FG,
            hover_color=theme.BTN_STOP_HOVER,
            text_color=theme.BTN_STOP_TEXT,
        )
        protocol = "WSS" if self._wss_var.get() else "WS"
        # Non-sticky: ``_update_ui`` immediately repaints with the live
        # uptime / bound address as soon as ``_on_status_result`` lands.
        self._set_status(f"Running ({protocol})")
        self._update_tray_status()

    def _on_external_stop_succeeded(self) -> None:
        """UI flip after systemctl stop returned 0."""
        with self._server._lock:
            self._server._adopted_pid = None
        self._server._start_time = None
        self._adopted_external_worker = False
        # Drop the wrapper so future starts use the real is_running.
        with contextlib.suppress(AttributeError):
            del self._server.is_running  # type: ignore[attr-defined]
        self._set_status("Stopped (systemctl)", sticky=True)

    # ── Token resolution (override) ──────────────────────────────────────

    def _on_copy_token(self) -> None:
        """Copy the systemd worker's API key (if installed) instead of the user one."""
        api_key = _read_install_api_key()
        if not api_key:
            super()._on_copy_token()
            return
        try:
            self._root.clipboard_clear()
            self._root.clipboard_append(api_key)
            self._root.update()
            self._set_status("Token copied to clipboard", sticky=True)
        except Exception as exc:
            self._set_status(f"Failed to copy token: {exc}", error=True, sticky=True)

    def _on_add_to_client(self) -> None:
        """Build the rcflow:// deep-link with the worker's actual API key.

        The base implementation uses :func:`read_token_from_file` which
        reads the launcher user's ``settings.json`` — but the systemd
        worker validates against its own ``/opt/rcflow/settings.json``,
        so the dashboard must hand the client *that* key, not the local
        one.  Falls back to the base flow when no install settings file
        is readable.
        """
        api_key = _read_install_api_key()
        if not api_key:
            super()._on_add_to_client()
            return
        try:
            import webbrowser  # noqa: PLC0415

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
            url = build_add_worker_url(host, port, api_key, wss=bool(self._wss_var.get()))
            webbrowser.open(url)
            self._set_status("Opening in client...", sticky=True)
        except Exception as exc:
            self._set_status(f"Failed to launch client: {exc}", error=True, sticky=True)

    # ── Window icon ───────────────────────────────────────────────────────

    def _install_platform_icon(self) -> None:
        """Set the title-bar / Alt-Tab icon on X11 / Wayland (XWayland)."""
        icon_path = _resolve_icon_path()
        if icon_path is None:
            return
        try:
            photo = tk.PhotoImage(file=icon_path)
        except tk.TclError:
            return
        with contextlib.suppress(tk.TclError):
            self._root.iconphoto(True, photo)
        # Hold a reference so Tk does not garbage-collect the image.
        self._linux_icon_photo = photo

    # ── Tray (AyatanaAppIndicator3 on a daemon GLib thread) ───────────────

    def _setup_tray(self) -> bool:
        if os.environ.get("RCFLOW_DISABLE_TRAY", "").lower() in {"1", "true", "yes"}:
            logger.info("RCFLOW_DISABLE_TRAY set — running RCFlow Worker without a tray icon.")
            return False

        try:
            import gi  # noqa: PLC0415  # ty:ignore[unresolved-import]

            gi.require_version("Gtk", "3.0")
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import (  # noqa: PLC0415  # ty:ignore[unresolved-import]
                AyatanaAppIndicator3 as AppIndicator,
            )
            from gi.repository import (  # noqa: PLC0415  # ty:ignore[unresolved-import]
                GLib,
                Gtk,
            )
        except (ImportError, ValueError) as exc:
            logger.info(
                "AyatanaAppIndicator3 GI bindings not available (%s); running "
                "RCFlow Worker without a tray icon.  Install "
                "gir1.2-ayatanaappindicator3-0.1 to enable the tray.",
                exc,
            )
            return False

        icon_path = _resolve_icon_path()
        indicator = AppIndicator.Indicator.new(
            "rcflow-worker",
            icon_path or "rcflow-worker",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        if icon_path:
            indicator.set_icon_full(icon_path, "RCFlow Worker")
        indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        indicator.set_title("RCFlow Worker")

        self._gtk_module = Gtk
        self._gtk_indicator = indicator
        # Sentinel so the dashboard treats the tray as live (controls
        # close-to-tray / quit semantics in RCFlowDashboard).
        self._tray_icon = _AppIndicatorWrapper(indicator, self)

        indicator.set_menu(self._build_indicator_menu())

        def _glib_thread() -> None:
            loop = GLib.MainLoop()
            self._gtk_loop = loop
            loop.run()

        threading.Thread(target=_glib_thread, name="rcflow-glib", daemon=True).start()

        # Subscribe to portal theme changes — bounce onto Tk thread.
        def _on_scheme(value: int) -> None:
            self._root.after(0, lambda: ctk.set_appearance_mode(_color_scheme_to_ctk(value)))

        _start_color_scheme_subscriber(_on_scheme)

        return True

    def _build_indicator_menu(self) -> Any:
        """Build the AppIndicator menu.  Re-built on each refresh so the
        labels can reflect current server state without a separate update
        path.
        """
        gtk = self._gtk_module
        if gtk is None:
            raise RuntimeError("Gtk module unavailable")
        menu = gtk.Menu()

        running = self._server.is_running()
        status_label = gtk.MenuItem.new_with_label(f"RCFlow Worker: {'Running' if running else 'Stopped'}")
        status_label.set_sensitive(False)
        menu.append(status_label)

        if self._upnp_enabled_mirror or self._natpmp_enabled_mirror:
            ext = gtk.MenuItem.new_with_label(f"External: {self._external_addr_mirror or '—'}")
            ext.set_sensitive(False)
            menu.append(ext)

        if self._update_available_mirror:
            update_item = gtk.MenuItem.new_with_label(f"Update available — install v{self._update_latest_mirror}")
            update_item.connect("activate", lambda _w: self._on_tray_install_update())
            menu.append(update_item)

        menu.append(gtk.SeparatorMenuItem.new())

        dashboard_item = gtk.MenuItem.new_with_label("Dashboard")
        dashboard_item.connect("activate", lambda _w: self._on_tray_open())
        menu.append(dashboard_item)

        toggle_item = gtk.MenuItem.new_with_label("Stop Server" if running else "Start Server")
        toggle_item.connect("activate", lambda _w: self._on_tray_toggle_server())
        menu.append(toggle_item)

        menu.append(gtk.SeparatorMenuItem.new())

        copy_item = gtk.MenuItem.new_with_label("Copy Token")
        copy_item.connect("activate", lambda _w: self._on_tray_copy_token())
        menu.append(copy_item)

        client_item = gtk.MenuItem.new_with_label("Add to Client…")
        client_item.connect("activate", lambda _w: self._on_tray_add_to_client())
        menu.append(client_item)

        menu.append(gtk.SeparatorMenuItem.new())

        autostart_item = gtk.CheckMenuItem.new_with_label(autostart_menu_label())
        autostart_item.set_active(is_autostart_enabled())
        autostart_item.connect("toggled", lambda _w: self._on_tray_toggle_autostart())
        menu.append(autostart_item)

        check_item = gtk.MenuItem.new_with_label("Check for Updates")
        check_item.connect("activate", lambda _w: self._on_tray_check_updates())
        menu.append(check_item)

        menu.append(gtk.SeparatorMenuItem.new())

        quit_item = gtk.MenuItem.new_with_label("Quit")
        quit_item.connect("activate", lambda _w: self._on_tray_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _update_tray_status(self) -> None:
        """Rebuild the AppIndicator menu so labels reflect the current state."""
        if self._gtk_indicator is None or self._gtk_module is None:
            return

        indicator = self._gtk_indicator

        def _rebuild() -> bool:
            with contextlib.suppress(Exception):
                indicator.set_menu(self._build_indicator_menu())
            return False  # one-shot idle callback

        try:
            from gi.repository import GLib  # noqa: PLC0415  # ty:ignore[unresolved-import]

            GLib.idle_add(_rebuild)
        except ImportError:
            pass

    def _on_tray_toggle_autostart(self) -> None:
        set_autostart(not is_autostart_enabled())
        self._update_tray_status()

    def _on_tray_quit(self, *_args: object) -> None:
        # Stop the GLib loop before delegating to the base class so the
        # daemon thread doesn't hold the process alive after destroy().
        if self._gtk_loop is not None:
            with contextlib.suppress(Exception):
                self._gtk_loop.quit()
            self._gtk_loop = None
        super()._on_tray_quit()


class _AppIndicatorWrapper:
    """Adapt :class:`gi.repository.AyatanaAppIndicator3.Indicator` to the
    ``TrayIconProtocol`` shape (``update_menu``, ``stop``) used by the
    base :class:`RCFlowDashboard` close-to-tray / quit logic.
    """

    def __init__(self, indicator: Any, gui: LinuxGUI) -> None:
        self._indicator = indicator
        self._gui = gui

    def update_menu(self) -> None:
        self._gui._update_tray_status()

    def stop(self) -> None:
        try:
            from gi.repository import (  # noqa: PLC0415  # ty:ignore[unresolved-import]
                AyatanaAppIndicator3 as AppIndicator,
            )

            self._indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
        except ImportError:
            pass


# ── CLI entrypoint ───────────────────────────────────────────────────────────


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="linux_gui_window",
        description="RCFlow worker GUI launcher (system-python under the frozen worker).",
    )
    parser.add_argument("--host", default=None, help="Override RCFLOW_HOST.")
    parser.add_argument("--port", default=None, help="Override RCFLOW_PORT.")
    parser.add_argument("--scheme", choices=("http", "https"), default=None, help="http or https.")
    parser.add_argument("--api-key", default=None, help="API key reported by the running worker.")
    parser.add_argument("--minimized", action="store_true", help="Start with the dashboard hidden (tray-only).")
    # Use ``parse_known_args`` so any positional left over from an older
    # frozen dispatcher (which passed the dashboard URL as argv[1]) is
    # silently dropped instead of erroring out.
    parsed, _ = parser.parse_known_args(argv[1:])
    return parsed


def main(argv: list[str]) -> int:
    """Entry point used by ``scripts/linux_gui_window.py`` under system Python."""
    args = _parse_argv(argv)

    if not acquire_singleton_lock():
        if not args.minimized:
            delivered = send_show_to_existing()
            if not delivered:
                print(
                    "RCFlow Worker is already running. Look for its icon in the system tray.",
                    file=sys.stderr,
                )
        return 0

    # Resolve the API key the running systemd worker actually uses *before*
    # constructing the dashboard so ``poll_server_status`` (which reads the
    # key from ``Settings()``) hits ``/api/info`` with a key the worker
    # accepts.  When the dispatcher passes ``--api-key`` we trust it; else
    # fall back to ``/opt/rcflow/settings.json`` (group-readable per the
    # deb postinst), then to whatever the user-side ``Settings()`` had.
    if not args.api_key:
        args.api_key = _read_install_api_key()
    if args.api_key:
        os.environ["RCFLOW_API_KEY"] = args.api_key

    # Point ``ServerManager.start`` at the frozen rcflow binary so the
    # dashboard's Start button — which only fires after the user already
    # used Stop — respawns the FastAPI worker via the bundled entry
    # point.  Otherwise the manager's dev branch tries
    # ``/usr/bin/python3 -m src run`` and fails with "No module named src"
    # because the launcher's sys.path doesn't propagate to the child env.
    install_bin = Path("/opt/rcflow/rcflow")
    if install_bin.exists() and "RCFLOW_SERVER_BIN" not in os.environ:
        os.environ["RCFLOW_SERVER_BIN"] = str(install_bin)

    gui = LinuxGUI()

    # Apply CLI overrides over whatever ``Settings()`` already populated.
    if args.host:
        gui._ip_var.set(args.host)
    if args.port:
        gui._port_var.set(args.port)
    if args.scheme:
        gui._wss_var.set(args.scheme.lower() == "https")

    gui.run(minimized=args.minimized)
    return 0
