"""Deep-link URL builder for the RCFlow desktop GUI.

The worker GUI's "Add to client" button emits a ``rcflow://add-worker?...``
URL that the installed RCFlow Flutter client handles (via registered URL
scheme). Clicking the button launches the client and pre-fills the Add
Worker dialog with the host, port, token and wss flag taken from the worker.

Both ``src/gui/windows.py`` and ``src/gui/macos.py`` call
:func:`build_add_worker_url` so the format stays consistent.
"""

from __future__ import annotations

import socket
from urllib.parse import urlencode

URL_SCHEME = "rcflow"
ADD_WORKER_HOST = "add-worker"


def default_worker_name() -> str:
    """Return a suggested worker label for deep links.

    Uses the OS hostname so the receiving client pre-fills a sensible name
    (e.g. ``"Fox-Desktop"``). Falls back to an empty string on failure so the
    client prompts the user to type one.
    """
    try:
        name = socket.gethostname()
    except OSError:
        return ""
    return name.strip()


def build_add_worker_url(
    host: str,
    port: int,
    token: str,
    *,
    wss: bool,
    name: str | None = None,
) -> str:
    """Build a ``rcflow://add-worker?...`` deep-link URL.

    Args:
        host: Worker host the client should connect to (e.g. ``127.0.0.1``).
        port: Worker TCP port.
        token: Worker API token (URL-encoded in the query string).
        wss: True if the worker is configured with ``WSS_ENABLED``; becomes
            ``ssl=1`` so the client checks its "Use SSL" toggle.
        name: Optional suggested worker label. When omitted,
            :func:`default_worker_name` is used.

    Returns:
        The complete URL string, ready to pass to ``webbrowser.open``.
    """
    params: list[tuple[str, str]] = [
        ("host", host),
        ("port", str(port)),
        ("token", token),
        ("ssl", "1" if wss else "0"),
    ]
    label = name if name is not None else default_worker_name()
    if label:
        params.append(("name", label))
    return f"{URL_SCHEME}://{ADD_WORKER_HOST}?{urlencode(params)}"
