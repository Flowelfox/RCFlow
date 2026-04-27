#!/usr/bin/env python3
"""Native Linux worker GUI window — wraps the /dashboard page in a GTK + WebKit window.

Used as the Linux equivalent of the CustomTkinter window on Windows and the
NSStatusBar app on macOS.  PyInstaller's bundled tcl/tk stack fails the
libxcb 1.17+ sequence-number assertion on Ubuntu 25.04, so the worker GUI
on Linux is rendered via WebKit instead.  This script runs against the
system Python interpreter (not the frozen one) so it can pick up the
GObject-Introspection bindings that ship with python3-gi / gir1.2-webkit2.

Invoked by `rcflow gui` via:
    /usr/bin/python3 /opt/rcflow/gui/linux_gui_window.py <dashboard URL>

Quitting the window only closes the dashboard frontend — the headless
``rcflow run`` worker keeps running (it is managed by the systemd service
or a child subprocess of the launcher).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import gi  # ty:ignore[unresolved-import]

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402  # ty:ignore[unresolved-import]


def _resolve_icon() -> str | None:
    """Return the worker tray icon path bundled alongside the rcflow binary."""
    candidates = (
        Path("/opt/rcflow/tray_icon.png"),
        Path("/usr/share/icons/hicolor/256x256/apps/rcflow-worker.png"),
        Path(__file__).resolve().parent.parent / "src" / "gui" / "assets" / "tray_icon.png",
    )
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _accept_self_signed(webview: WebKit2.WebView) -> None:
    """Mirror the browser "Accept the Risk" workflow non-interactively.

    The worker serves the dashboard over HTTPS with a self-signed
    certificate when ``WSS_ENABLED=true`` (default).  Without this hook
    WebKit shows an opaque "Cannot verify connection" page; the dashboard
    is local-only so we whitelist the loopback host instead of requiring
    the user to install a CA cert.  Re-issues the load after the host is
    whitelisted because WebKit does not retry the failed request itself.
    """
    context = webview.get_context()

    def _on_failed(view: WebKit2.WebView, failing_uri: str, certificate: object, _errors: object) -> bool:
        # Always trust — local URL only, never a remote host.
        context.allow_tls_certificate_for_host(certificate, "127.0.0.1")
        view.load_uri(failing_uri)
        return True

    webview.connect("load-failed-with-tls-errors", _on_failed)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: linux_gui_window.py <dashboard URL>", file=sys.stderr)
        return 2
    url = argv[1]

    win = Gtk.Window(title="RCFlow Worker")
    win.set_default_size(960, 720)
    win.set_wmclass("rcflow", "RCFlow Worker")
    icon = _resolve_icon()
    if icon:
        with contextlib.suppress(GLib.Error):
            win.set_icon_from_file(icon)
    win.connect("destroy", Gtk.main_quit)

    settings = WebKit2.Settings(
        enable_developer_extras=False,
        enable_javascript=True,
        enable_smooth_scrolling=True,
        javascript_can_access_clipboard=True,
    )
    # VM environments without OpenGL passthrough render the page to a
    # composited layer that never reaches the screen, leaving the window
    # blank.  Force the software path so the dashboard always paints.
    # No-op on hosts with working DRI / GLX.
    settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
    webview = WebKit2.WebView.new_with_settings(settings)
    _accept_self_signed(webview)
    webview.load_uri(url)
    win.add(webview)
    win.show_all()

    # Best-effort: don't print a blank-window splash if the worker is still
    # booting — the dashboard JS itself surfaces "Unreachable" / "Loading"
    # while it waits for /api/info to respond.
    os.environ.setdefault("GDK_BACKEND", "x11,wayland")

    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
