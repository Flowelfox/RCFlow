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

# Bind-all sentinels the worker may be configured with. Passing these
# verbatim into the deep link gives the client something it cannot connect
# to ("0.0.0.0" / "::" are listen-only addresses), so they get swapped for
# a reachable address in :func:`resolve_reachable_host`.
_BIND_ALL_HOSTS = frozenset({"", "0.0.0.0", "::", "[::]"})


def _detect_primary_lan_ip() -> str | None:
    """Return the outbound-routable IPv4 address of this host, or None.

    Uses the well-known UDP-connect trick: connect a datagram socket to a
    public address (no packet is actually sent) so the kernel picks the
    source IP it would route through, then reads it back with
    ``getsockname()``. Works offline as long as the routing table has a
    default gateway; otherwise returns None and the caller falls back to
    loopback.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
    except OSError:
        return None
    if not ip or ip.startswith("0."):
        return None
    return ip


def resolve_reachable_host(host: str) -> str:
    """Map a worker bind host to an address a client can actually connect to.

    The worker typically binds to ``0.0.0.0`` (all interfaces) so clients on
    the LAN *and* on the same machine can reach it. That sentinel is
    useless in a deep link — the client would literally try to connect to
    ``0.0.0.0``. Replace it with the primary LAN IP so a phone or second
    desktop on the same network connects cleanly, falling back to
    ``127.0.0.1`` when there is no network (dev / offline).

    Concrete bind addresses (``192.168.x.x``, ``rcflow.local``, etc.) are
    returned unchanged.
    """
    stripped = host.strip()
    if stripped not in _BIND_ALL_HOSTS:
        return stripped
    lan = _detect_primary_lan_ip()
    return lan or "127.0.0.1"


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
            A bind-all sentinel (``0.0.0.0`` / ``::`` / empty) is rewritten
            to the primary LAN IP via :func:`resolve_reachable_host` so the
            emitted URL is actually dialable.
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
        ("host", resolve_reachable_host(host)),
        ("port", str(port)),
        ("token", token),
        ("ssl", "1" if wss else "0"),
    ]
    label = name if name is not None else default_worker_name()
    if label:
        params.append(("name", label))
    return f"{URL_SCHEME}://{ADD_WORKER_HOST}?{urlencode(params)}"
