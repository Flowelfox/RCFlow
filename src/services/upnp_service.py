"""UPnP IGD port-mapping service.

Manages a single TCP port mapping on the local router via UPnP-IGD so remote
clients can reach the worker without manual port forwarding.  Disabled by
default and always non-fatal: any discovery / mapping failure leaves the
worker running normally and reports the error through :meth:`UpnpService.snapshot`.

Thread-safety: the service owns an :class:`asyncio.Lock` that guards state
transitions.  ``miniupnpc`` is a blocking C-extension; every call is wrapped
in :func:`asyncio.to_thread` so the event loop is never stalled.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# Retry up to 5 external ports on ConflictInMappingEntry (internal_port + 0..4).
_CONFLICT_RETRY_OFFSETS: tuple[int, ...] = (0, 1, 2, 3, 4)

# Renew mapping when the lease has elapsed by this fraction.
_RENEWAL_FRACTION: float = 0.5

# Periodic re-check of the router's reported external IP.
_IP_WATCH_INTERVAL_SECONDS: float = 300.0

# Consecutive renewal failures tolerated before giving up.
_MAX_RENEWAL_FAILURES: int = 3

# Error-message fragments that indicate the router rejected the port as
# already-mapped (as opposed to a transport-level failure).  Case-insensitive
# substring match — miniupnpc's error strings vary across router firmware.
_CONFLICT_ERROR_MARKERS: tuple[str, ...] = (
    "conflict",  # "ConflictInMappingEntry" (IGD error 718)
    "same port",  # "SamePortValuesRequired" (IGD 724)
    "mapping entry",
)


_SSDP_MULTICAST = ("239.255.255.250", 1900)
# Service types a real IGD v1 / v2 router advertises.  Chromecasts, DLNA
# renderers, smart TVs, printers etc. do not match any of these.
_IGD_SEARCH_TYPES: tuple[str, ...] = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
)


def _ssdp_search_igd(timeout_seconds: float) -> list[str]:
    """Send a targeted SSDP M-SEARCH for IGD devices and return LOCATION URLs.

    Broad ``ssdp:all`` searches (what miniupnpc does internally) match every
    UPnP-advertising device on the LAN — Chromecasts, DLNA servers, printers,
    smart TVs — and miniupnpc then picks the first responder, often wrongly.
    Filtering the Service Type to ``InternetGatewayDevice`` at the M-SEARCH
    level keeps Chromecasts etc. silent and leaves only actual routers to
    respond.

    Returns an ordered list of description URLs (duplicates removed, insertion
    order preserved).  Empty list means nothing IGD-ish responded; the caller
    should fall back to broad discovery.
    """
    import socket as _socket  # noqa: PLC0415

    locations: list[str] = []
    seen: set[str] = set()

    # Send one M-SEARCH per service type so legacy IGDv1-only and IGDv2
    # routers are both covered.  Collect responses on a single socket.
    try:
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM, _socket.IPPROTO_UDP)
    except OSError as exc:
        logger.info("UPnP: SSDP socket creation failed: %s", exc)
        return []

    try:
        sock.setsockopt(_socket.IPPROTO_IP, _socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout_seconds)
        # Bind to all interfaces — on Windows with Hyper-V, letting the OS
        # pick the outbound interface avoids being stuck on a virtual adapter.
        # Let ephemeral port selection happen automatically.
        for service_type in _IGD_SEARCH_TYPES:
            mx = max(1, int(timeout_seconds))
            message = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {_SSDP_MULTICAST[0]}:{_SSDP_MULTICAST[1]}\r\n"
                'MAN: "ssdp:discover"\r\n'
                f"MX: {mx}\r\n"
                f"ST: {service_type}\r\n"
                "\r\n"
            ).encode("ascii")
            try:
                sock.sendto(message, _SSDP_MULTICAST)
            except OSError as exc:
                logger.info("UPnP: SSDP sendto failed: %s", exc)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(8192)
            except TimeoutError:
                break
            except OSError:
                break
            location = _parse_ssdp_location(data)
            if location and location not in seen:
                seen.add(location)
                locations.append(location)
    finally:
        sock.close()

    return locations


def _parse_ssdp_location(raw: bytes) -> str | None:
    """Extract the ``LOCATION`` header from a raw SSDP response datagram."""
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.lower().startswith("location:"):
            return line.split(":", 1)[1].strip()
    return None


def _ssdp_search_all_filtered_to_igd(timeout_seconds: float) -> tuple[list[str], int]:
    """Broad ``ssdp:all`` search + XML deviceType filtering.

    Some routers (notably cheap consumer APs and some ISP-provided gateways)
    only respond to ``ST: ssdp:all`` M-SEARCH and stay silent on targeted
    queries.  Chromecasts, smart TVs, and DLNA renderers also respond to
    ssdp:all — so the responses must be filtered by fetching each device's
    UPnP description XML and keeping only those whose ``<deviceType>``
    element contains ``InternetGatewayDevice``.

    Returns ``(igd_urls, raw_response_count)``:
    - ``igd_urls`` — ordered list of confirmed IGD description URLs.
    - ``raw_response_count`` — total number of distinct SSDP responders,
      *before* XML filtering.  Callers use this to distinguish "multicast
      blocked / firewall" (count=0) from "only non-router UPnP devices on
      LAN" (count>0, filtered list empty) — the latter means miniupnpc's
      own broad discover() will just pick the same non-router devices and
      should be skipped.
    """
    import socket as _socket  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    try:
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM, _socket.IPPROTO_UDP)
    except OSError as exc:
        logger.info("UPnP: ssdp:all socket creation failed: %s", exc)
        return [], 0

    seen: set[str] = set()
    raw_locations: list[str] = []

    try:
        sock.setsockopt(_socket.IPPROTO_IP, _socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout_seconds)
        mx = max(1, int(timeout_seconds))
        message = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {_SSDP_MULTICAST[0]}:{_SSDP_MULTICAST[1]}\r\n"
            'MAN: "ssdp:discover"\r\n'
            f"MX: {mx}\r\n"
            "ST: ssdp:all\r\n"
            "\r\n"
        ).encode("ascii")
        try:
            sock.sendto(message, _SSDP_MULTICAST)
        except OSError as exc:
            logger.info("UPnP: ssdp:all sendto failed: %s", exc)
            return [], 0

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(8192)
            except TimeoutError:
                break
            except OSError:
                break
            location = _parse_ssdp_location(data)
            if location and location not in seen:
                seen.add(location)
                raw_locations.append(location)
    finally:
        sock.close()

    raw_count = len(raw_locations)
    if raw_count == 0:
        return [], 0

    logger.info("UPnP: ssdp:all returned %d location(s); filtering by deviceType", raw_count)
    igd_locations: list[str] = []
    for location in raw_locations:
        try:
            req = urllib.request.Request(location, headers={"User-Agent": "RCFlow-UPnP-Discovery"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                xml_bytes = resp.read(65536)
        except Exception as exc:
            logger.info("UPnP: description fetch %s failed: %s", location, exc)
            continue
        xml_text = xml_bytes.decode("utf-8", errors="replace")
        if "InternetGatewayDevice" in xml_text:
            logger.info("UPnP: %s is an IGD (deviceType matched)", location)
            igd_locations.append(location)
        else:
            logger.info("UPnP: %s is not an IGD (deviceType mismatch) — skipping", location)

    return igd_locations, raw_count


def _is_conflict_error(exc: BaseException) -> bool:
    """Return True when *exc* looks like a port-already-mapped fault.

    Used to decide whether retrying ``addportmapping`` with an incremented
    external port is worth the round-trip.  Transport / SOAP faults do not
    match and cause the caller to fail fast.
    """
    return _is_conflict_error_str(str(exc))


def _is_conflict_error_str(msg: str) -> bool:
    """String variant of :func:`_is_conflict_error` — used when only the message survives."""
    low = msg.lower()
    return any(marker in low for marker in _CONFLICT_ERROR_MARKERS)


_PUBLIC_IP_ECHO_SERVICES: tuple[str, ...] = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)


def _classify_wan_reachability() -> tuple[str, str] | None:
    """Probe a public IP-echo service and classify the returned address.

    Returns ``(ip, kind)`` where ``kind`` is one of:
    - ``"public"``  — routable public IPv4
    - ``"cgnat"``   — RFC 6598 shared address space (100.64.0.0/10); behind CGNAT
    - ``"private"`` — RFC 1918 address; double-NAT (another NAT device upstream)

    Returns None when no echo service could be reached (no internet, DNS
    broken, etc.) — the caller should not make any claim in that case.
    """
    import ipaddress  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    cgnat_net = ipaddress.IPv4Network("100.64.0.0/10")
    for service in _PUBLIC_IP_ECHO_SERVICES:
        try:
            req = urllib.request.Request(service, headers={"User-Agent": "RCFlow-UPnP-Diagnostic"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                text = resp.read(64).decode("utf-8", errors="replace").strip()
        except Exception as exc:
            logger.info("UPnP: public-IP probe %s failed: %s", service, exc)
            continue
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            logger.info("UPnP: public-IP probe %s returned non-IP %r", service, text)
            continue
        if not isinstance(ip, ipaddress.IPv4Address):
            # IPv6 reachability is a separate question; return None so the
            # caller doesn't confuse the user with a v4 NAT verdict.
            continue
        if ip in cgnat_net:
            return str(ip), "cgnat"
        # ``is_private`` in Python 3.9+ already includes 100.64/10, which we
        # handled above — so what remains in ``is_private`` is RFC 1918 + a
        # handful of link-local / loopback ranges.  Any of those on the
        # public-facing side means double-NAT.
        if ip.is_private:
            return str(ip), "private"
        return str(ip), "public"
    return None


def _derive_root_desc_candidates(igd_url: str) -> list[str]:
    """Guess plausible rootDesc URLs from an IGD control URL.

    miniupnpc's ``selectigd()`` returns a control URL such as
    ``http://192.168.8.1:5000/ctl/IPConn`` but not the rootDesc.  Most
    consumer routers host their device description at one of a handful of
    well-known paths on the same host:port, so we probe those as a
    diagnostic fallback when the real SSDP LOCATION isn't available.
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    try:
        parsed = urlparse(igd_url)
    except Exception:
        return []
    if not parsed.scheme or not parsed.netloc:
        return []
    base = f"{parsed.scheme}://{parsed.netloc}"
    common_paths = (
        "/rootDesc.xml",
        "/description.xml",
        "/DeviceDescription.xml",
        "/gateway.xml",
        "/IGatewayDeviceDCP.xml",
        "/",
    )
    return [base + path for path in common_paths]


def _log_available_wan_services(root_desc_urls: list[str]) -> None:
    """Fetch and parse IGD rootDesc XMLs to list their WAN*Connection services.

    Diagnostic only.  When ``addportmapping`` fails with "Action Failed" but
    ``statusinfo`` / ``connectiontype`` succeed, the router usually has both
    ``WANIPConnection`` and ``WANPPPConnection`` services but only one is
    actually connected to the WAN.  miniupnpc may have picked the dead one.
    Logging the services + their control URLs makes the mismatch visible.
    """
    import re  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    service_re = re.compile(
        r"<service>\s*"
        r"<serviceType>([^<]+)</serviceType>.*?"
        r"<controlURL>([^<]+)</controlURL>",
        re.DOTALL,
    )

    for url in root_desc_urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RCFlow-UPnP-Discovery"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                xml_text = resp.read(65536).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("UPnP: could not fetch rootDesc %s: %s", url, exc)
            continue
        services = service_re.findall(xml_text)
        wan_services = [(st, ctl) for st, ctl in services if "WAN" in st and "Connection" in st]
        if wan_services:
            logger.info("UPnP: rootDesc %s advertises WAN services:", url)
            for st, ctl in wan_services:
                logger.info("UPnP:   serviceType=%s controlURL=%s", st.strip(), ctl.strip())
            # Found a real IGD rootDesc — stop probing fallback paths.
            return
        logger.debug("UPnP: rootDesc %s advertises no WAN*Connection services", url)
    logger.info("UPnP: no rootDesc among %d probed URL(s) advertised WAN services", len(root_desc_urls))


def _log_igd_details(upnp: Any, igd_url: object) -> None:
    """Dump the selected IGD's routing details so users can diagnose failures.

    The typical failure mode for "Miniupnpc HTTP error" is picking the wrong
    IGD — e.g. a Hyper-V virtual switch or a repeater/AP responds to SSDP but
    its SOAP control URL returns HTTP 500/404.  Logging the IGD's device
    description URL, LAN address, connection type, and the result of a
    direct HTTP probe of that URL makes the wrong-device case obvious.
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    lanaddr = _safe_attr(upnp, "lanaddr")
    igd_url_str = str(igd_url) if igd_url is not None else None
    logger.info(
        "UPnP: selected IGD — lanaddr=%s igdURL=%s",
        lanaddr or "?",
        igd_url_str or "?",
    )

    # connectiontype() is another SOAP call that fails fast if the control
    # URL is unreachable — useful corroboration before we burn attempts on
    # addportmapping.
    try:
        conn_type = upnp.connectiontype()
        logger.info("UPnP: IGD connectiontype() -> %s", conn_type)
    except Exception as exc:
        logger.info("UPnP: IGD connectiontype() raised: %s", exc)

    # statusinfo() — returns (status, uptime, lastconnerror) on success.
    try:
        status = upnp.statusinfo()
        logger.info("UPnP: IGD statusinfo() -> %s", status)
    except Exception as exc:
        logger.warning(
            "UPnP: IGD statusinfo() raised %s — selected device is likely the wrong IGD "
            "(virtual adapter / repeater) or router UPnP is half-broken. Inspect the "
            "igdURL above.",
            exc,
        )

    # Direct HTTP probe of the IGD URL.  Note: miniupnpc's selectigd() often
    # returns the SOAP **control URL**, not the rootDesc URL — control URLs
    # only accept POST and legitimately return 405 on GET.  We treat anything
    # other than connection-level errors as "endpoint is reachable" since the
    # goal is just to distinguish network-unreachable from SOAP-level issues.
    if igd_url_str:
        try:
            req = urllib.request.Request(igd_url_str, headers={"User-Agent": "RCFlow-UPnP-Diagnostic"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                body = resp.read(256)
                logger.info(
                    "UPnP: IGD URL probe — HTTP %d, %d bytes, preview=%r",
                    resp.status,
                    len(body),
                    body[:120],
                )
        except urllib.error.HTTPError as exc:
            # Any HTTP response (even 404/405) proves the endpoint is alive.
            logger.info(
                "UPnP: IGD URL probe — HTTP %d (expected for SOAP control URLs, endpoint is alive)",
                exc.code,
            )
        except Exception as exc:
            logger.warning(
                "UPnP: IGD URL probe (%s) failed at transport level: %s — router is not "
                "reachable from this machine (firewall? wrong interface? stale SSDP cache?)",
                igd_url_str,
                exc,
            )


def _safe_attr(obj: Any, name: str) -> str | None:
    """Fetch an attribute, swallowing any exception (miniupnpc lazily resolves some).

    Returns None when missing so the caller can render a single ``?`` placeholder.
    """
    try:
        value = getattr(obj, name, None)
    except Exception:
        return None
    if value is None:
        return None
    return str(value)


class UpnpStatus(StrEnum):
    DISABLED = "disabled"
    DISCOVERING = "discovering"
    MAPPED = "mapped"
    FAILED = "failed"
    CLOSING = "closing"


@dataclass
class UpnpState:
    status: UpnpStatus = UpnpStatus.DISABLED
    external_ip: str | None = None
    external_port: int | None = None
    internal_port: int | None = None
    error: str | None = None
    last_updated_monotonic: float = 0.0


class UpnpService:
    """Manage an IGD UPnP port mapping for the worker's bound TCP port.

    The :meth:`start` method is non-blocking: it kicks off discovery and
    mapping in the background and returns immediately.  Callers read the
    current state via :meth:`snapshot`.  :meth:`stop` deletes the mapping
    (best-effort) and cancels renewal tasks.
    """

    def __init__(
        self,
        *,
        internal_port: int,
        lease_seconds: int = 3600,
        discovery_timeout_ms: int = 2000,
        description: str = "RCFlow worker",
        protocol: str = "TCP",
    ) -> None:
        self._internal_port = internal_port
        self._lease_seconds = max(0, lease_seconds)
        self._discovery_timeout_ms = max(250, discovery_timeout_ms)
        self._description = description
        self._protocol = protocol

        self._state = UpnpState(internal_port=internal_port)
        self._lock = asyncio.Lock()
        # miniupnpc has no type stubs, so Any is the least-astonishing type here.
        self._upnp: Any | None = None
        self._renewal_task: asyncio.Task[None] | None = None
        self._ip_watch_task: asyncio.Task[None] | None = None
        self._bootstrap_task: asyncio.Task[None] | None = None
        # Set by ``_discover_and_map`` if the router only accepted lease=0;
        # the renewal loop is skipped when this drops to zero so we don't
        # hammer a router that can't honour leases anyway.
        self._effective_lease_seconds: int = lease_seconds

    def snapshot(self) -> UpnpState:
        """Return a shallow copy of the current state (safe for concurrent readers)."""
        return replace(self._state)

    async def start(self) -> None:
        """Kick off IGD discovery + port mapping in the background.

        Returns immediately — discovery + SOAP calls run in a background task
        so the caller (FastAPI lifespan) is never blocked.  Clients poll state
        via :meth:`snapshot` or ``/api/info``; it reports ``DISCOVERING`` while
        the bootstrap task runs, then transitions to ``MAPPED`` or ``FAILED``.
        """
        async with self._lock:
            if self._state.status in (UpnpStatus.MAPPED, UpnpStatus.DISCOVERING):
                return
            self._state = replace(
                self._state,
                status=UpnpStatus.DISCOVERING,
                error=None,
                last_updated_monotonic=time.monotonic(),
            )

        self._bootstrap_task = asyncio.create_task(self._bootstrap())

    async def _bootstrap(self) -> None:
        """Run discovery + mapping off the main event-loop critical path."""
        try:
            result = await asyncio.to_thread(self._discover_and_map)
        except Exception as exc:  # pragma: no cover — defensive; _discover_and_map already catches
            logger.exception("UPnP: unexpected error during start (non-fatal)")
            async with self._lock:
                self._state = replace(
                    self._state,
                    status=UpnpStatus.FAILED,
                    error=f"Unexpected error: {exc}",
                    last_updated_monotonic=time.monotonic(),
                )
            return

        if result is None:
            return

        upnp_obj, external_ip, external_port = result
        async with self._lock:
            # If stop() ran during discovery, honour the teardown request.
            if self._state.status == UpnpStatus.CLOSING:
                return
            self._upnp = upnp_obj
            self._state = replace(
                self._state,
                status=UpnpStatus.MAPPED,
                external_ip=external_ip,
                external_port=external_port,
                error=None,
                last_updated_monotonic=time.monotonic(),
            )

        logger.info(
            "UPnP: mapped %d -> %s:%d (lease %ds)",
            self._internal_port,
            external_ip or "?",
            external_port,
            self._effective_lease_seconds,
        )

        if self._effective_lease_seconds > 0:
            self._renewal_task = asyncio.create_task(self._renewal_loop())
        self._ip_watch_task = asyncio.create_task(self._ip_watch_loop())

    async def stop(self) -> None:
        """Cancel background tasks and delete the mapping (best-effort)."""
        async with self._lock:
            if self._state.status == UpnpStatus.DISABLED:
                return
            self._state = replace(
                self._state,
                status=UpnpStatus.CLOSING,
                last_updated_monotonic=time.monotonic(),
            )
            renewal = self._renewal_task
            ip_watch = self._ip_watch_task
            bootstrap = self._bootstrap_task
            self._renewal_task = None
            self._ip_watch_task = None
            self._bootstrap_task = None
            upnp_obj = self._upnp
            external_port = self._state.external_port

        for task in (renewal, ip_watch, bootstrap):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        if upnp_obj is not None and external_port is not None:
            logger.info("UPnP: removing mapping %d on shutdown", external_port)
            try:
                await asyncio.to_thread(upnp_obj.deleteportmapping, external_port, self._protocol)
            except Exception as exc:
                logger.warning(
                    "UPnP: deleteportmapping failed: %s — router will clear the lease in %ds",
                    exc,
                    self._lease_seconds,
                )

        async with self._lock:
            self._upnp = None
            self._state = replace(
                self._state,
                status=UpnpStatus.DISABLED,
                external_ip=None,
                external_port=None,
                last_updated_monotonic=time.monotonic(),
            )

    # ── Internal (blocking) helpers — all invoked via asyncio.to_thread ──

    def _discover_and_map(self) -> tuple[Any, str | None, int] | None:
        """Blocking: discover IGD and create the mapping.

        Returns ``(upnp_obj, external_ip, external_port)`` on success, or None
        on failure (state is mutated to FAILED in place).  Never raises —
        unexpected exceptions are caught and recorded.
        """
        try:
            import miniupnpc  # noqa: PLC0415  # ty: ignore[unresolved-import]
        except ImportError as exc:
            self._mark_failed(f"miniupnpc not installed: {exc}")
            logger.warning("UPnP: miniupnpc is not installed — UPnP support disabled")
            return None

        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = self._discovery_timeout_ms

        # SSDP-based IGD discovery.  miniupnpc's own ``discover()`` uses a
        # broad ssdp:all search and picks the first responder, which on
        # networks with Chromecast / smart TVs / DLNA renderers routinely
        # picks a non-router advertising UPnP that has no SOAP IGD endpoint
        # — yielding endless "Miniupnpc HTTP error" results.  We try two
        # filtering strategies before falling back to miniupnpc's discover:
        #   1. Targeted M-SEARCH with ST=InternetGatewayDevice/WANIPConnection
        #      — most efficient, but some cheap routers only respond to
        #      ssdp:all and stay silent on targeted requests.
        #   2. Broad ssdp:all M-SEARCH + fetch each device's description XML
        #      + keep only those whose ``<deviceType>`` contains
        #      ``InternetGatewayDevice``.  Heavier but handles routers
        #      that ignore targeted queries.
        timeout_seconds = max(1.0, self._discovery_timeout_ms / 1000.0)
        igd_candidates = _ssdp_search_igd(timeout_seconds)
        # Tracks whether ssdp:all saw any responders at all.  When it did but
        # none of them turned out to be an IGD, miniupnpc's fallback broad
        # discover() would just pick the same non-router devices — no point
        # running it.
        ssdp_all_responders: int | None = None
        if not igd_candidates:
            logger.info("UPnP: targeted SSDP empty, trying broad ssdp:all with XML filtering")
            igd_candidates, ssdp_all_responders = _ssdp_search_all_filtered_to_igd(timeout_seconds)

        igd_url: str | None = None
        if igd_candidates:
            logger.info(
                "UPnP: targeted SSDP found %d IGD candidate(s): %s",
                len(igd_candidates),
                igd_candidates,
            )
            for candidate_url in igd_candidates:
                try:
                    result_url = upnp.selectigd(candidate_url)
                except Exception as exc:
                    logger.info("UPnP: selectigd(%s) raised: %s", candidate_url, exc)
                    continue
                # Validate the candidate by probing a cheap SOAP call.  If
                # ``connectiontype()`` fails, the router pointed us at a
                # dead control URL — try the next candidate.
                try:
                    upnp.connectiontype()
                except Exception as exc:
                    logger.info(
                        "UPnP: candidate %s rejected — connectiontype() raised: %s",
                        candidate_url,
                        exc,
                    )
                    continue
                igd_url = result_url or candidate_url
                break

        if igd_url is None:
            # When our own ssdp:all already saw responders but none were an
            # IGD, miniupnpc's broad discover() will just pick the same
            # non-routers.  Fail fast with a clear message instead of burning
            # 20+ seconds on guaranteed-same-failures.
            if ssdp_all_responders is not None and ssdp_all_responders > 0:
                self._mark_failed(
                    "No UPnP IGD on LAN — only non-router UPnP devices (Chromecast / DLNA / etc.) "
                    "responded to SSDP.  Enable UPnP/IGD in your router's admin panel or add a "
                    "manual port forward."
                )
                logger.warning(
                    "UPnP: %d SSDP responder(s) found, none were IGDs — router UPnP appears to be "
                    "disabled.  Enable it in the router's admin UI (typically under 'NAT' / 'UPnP') "
                    "or forward port %d manually.",
                    ssdp_all_responders,
                    self._internal_port,
                )
                return None

            # ssdp:all returned nothing (multicast blocked / odd network stack).
            # Fall back to miniupnpc's broad discovery as a last resort — it
            # sometimes succeeds where our own UDP multicast couldn't bind.
            logger.info("UPnP: no SSDP responders from our own M-SEARCH, trying miniupnpc discover()")
            try:
                num_devices = upnp.discover()
            except Exception as exc:
                self._mark_failed(f"Discovery error: {exc}")
                logger.warning("UPnP: discover() raised: %s", exc)
                return None

            if num_devices == 0:
                self._mark_failed("No UPnP IGD found on LAN")
                logger.warning(
                    "UPnP: no IGD discovered within %d ms — continuing without external port",
                    self._discovery_timeout_ms,
                )
                return None

            try:
                igd_url = upnp.selectigd()
            except Exception as exc:
                self._mark_failed(f"selectigd failed: {exc}")
                logger.warning("UPnP: selectigd() raised: %s", exc)
                return None

        # Diagnostic dump — helps track down "Miniupnpc HTTP error" cases where
        # SSDP returns a device but its SOAP control URL is broken / on a
        # virtual adapter / wrong model.  Everything here is best-effort; we
        # still continue to addportmapping even if these queries fail.
        _log_igd_details(upnp, igd_url)

        external_ip: str | None = None
        try:
            external_ip = upnp.externalipaddress()
        except Exception as exc:
            logger.info("UPnP: externalipaddress() raised: %s — continuing without IP", exc)

        logger.info("UPnP: IGD discovered (external IP %s)", external_ip or "unknown")

        mapped_port, last_error = self._attempt_mapping_ladder(upnp, self._lease_seconds)
        if mapped_port is None and self._lease_seconds > 0 and last_error and not _is_conflict_error_str(last_error):
            # Many consumer routers reject non-zero lease durations with a
            # generic "Action Failed" SOAP fault.  Try once more with
            # lease=0 (permanent) before giving up.  When this succeeds we
            # skip the renewal task since the mapping has no TTL.
            logger.info(
                "UPnP: initial lease=%ds rejected (%s); retrying with lease=0 (permanent)",
                self._lease_seconds,
                last_error,
            )
            mapped_port, last_error = self._attempt_mapping_ladder(upnp, 0)
            if mapped_port is not None:
                # Communicate the downgrade to the bootstrap caller via an
                # attribute — the renewal loop skips when lease_seconds is 0.
                self._effective_lease_seconds = 0

        if mapped_port is None:
            # "Action Failed" on AddPortMapping with no external IP is the
            # classic miniupnpd "private/reserved address ... is not suitable
            # for external IP" signature — the router's WAN IP is private,
            # meaning CGNAT or an upstream NAT device.  Compare the router's
            # public-facing IP (via an echo service) against the network the
            # router thinks it's on to classify precisely.
            if last_error and "action failed" in last_error.lower() and not external_ip:
                classification = _classify_wan_reachability()
                if classification is not None:
                    pub_ip, kind = classification
                    logger.info("UPnP: public-IP probe returned %s (classified as %s)", pub_ip, kind)
                    if kind == "cgnat":
                        self._mark_failed(
                            f"CGNAT detected — your ISP-facing IP {pub_ip} is in the RFC 6598 "
                            "shared address space (100.64.0.0/10).  UPnP cannot help: port "
                            "mappings you create locally are not reachable from the public "
                            "internet.  Use a tunnel service (Tailscale Funnel, Cloudflare "
                            "Tunnel, ngrok), ask ISP for a public IPv4, or switch to IPv6."
                        )
                        logger.warning(
                            "UPnP: CGNAT confirmed — public-facing IP %s is in 100.64.0.0/10 "
                            "(ISP carrier-grade NAT).  UPnP-IGD cannot punch CGNAT.",
                            pub_ip,
                        )
                        return None
                    if kind == "private":
                        self._mark_failed(
                            f"Double-NAT detected — public-facing IP {pub_ip} is in an RFC 1918 "
                            "range.  Another NAT device sits upstream of this router.  UPnP "
                            "mappings created here do not reach the internet — configure UPnP "
                            "on the upstream device too, or use a tunnel service."
                        )
                        logger.warning(
                            "UPnP: double-NAT confirmed — public-facing IP %s is RFC 1918.",
                            pub_ip,
                        )
                        return None
                    # kind == "public" — echo returned a routable IP, but the
                    # router still refused the mapping with "Action Failed" +
                    # no external IP.  That's the miniupnpd signature for
                    # CGNAT too: the router sees a private WAN IP
                    # (10.x / 100.64.x) while the public-facing IP seen by
                    # echo.ipify.org is the ISP's shared carrier NAT pool
                    # (which IS public from the internet's perspective but
                    # still unreachable because the ISP's CGNAT doesn't
                    # forward unsolicited inbound).
                    self._mark_failed(
                        f"CGNAT / upstream NAT detected — router's WAN interface reports the "
                        f"connection is up but refuses to expose an external IP, while your "
                        f"public-facing IP is {pub_ip}.  This means the router is behind "
                        "carrier-grade NAT (shared ISP public IP) or another NAT device.  UPnP "
                        "mappings created here cannot be reached from the internet.  Use a "
                        "tunnel service (Tailscale Funnel, Cloudflare Tunnel, ngrok), ask the "
                        "ISP for a public IPv4, or switch to IPv6."
                    )
                    logger.warning(
                        "UPnP: CGNAT/upstream-NAT signature — router WAN private + public echo "
                        "returned %s.  UPnP cannot punch upstream NAT.",
                        pub_ip,
                    )
                    return None
                # Public echo failed entirely (no internet? blocked? timeout?).
                # The miniupnpd signature (connected + no external IP +
                # Action Failed) is still near-diagnostic on its own.
                self._mark_failed(
                    "Likely CGNAT or upstream NAT — router reports the WAN is connected but "
                    "will not expose an external IP and refuses AddPortMapping.  This is the "
                    "miniupnpd signature for a private/reserved WAN address.  (Public-IP probe "
                    "could not confirm — network may be offline.)  If internet works "
                    "otherwise, use a tunnel service (Tailscale Funnel, Cloudflare Tunnel, "
                    "ngrok), ask ISP for a public IPv4, or switch to IPv6."
                )
                logger.warning(
                    "UPnP: CGNAT/upstream-NAT likely — router signature matches and public-IP "
                    "probe could not confirm externally.  UPnP-IGD cannot help here.",
                )
                return None
                diag_urls = list(igd_candidates)
                if not diag_urls and igd_url:
                    diag_urls.extend(_derive_root_desc_candidates(igd_url))
                logger.warning(
                    "UPnP: AddPortMapping → 'Action Failed' and the router cannot report its "
                    "external IP.  The router's UPnP-IGD service appears to be partially broken: "
                    "read-only SOAP calls (connectiontype, statusinfo) work but the mapping and "
                    "IP-query actions do not.  Dumping advertised WAN services for the record:"
                )
                _log_available_wan_services(diag_urls)
                logger.warning(
                    "UPnP: not retriable from the client side.  Workarounds: (1) add a manual "
                    "port forward in the router admin (WAN port %d → LAN %s:%d), (2) try a "
                    "different router, or (3) use a tunnel / reverse proxy service instead.",
                    self._internal_port,
                    upnp.lanaddr if hasattr(upnp, "lanaddr") else "?",
                    self._internal_port,
                )
            self._mark_failed(last_error or "addportmapping failed")
            return None
        return upnp, external_ip, mapped_port

    def _attempt_mapping_ladder(self, upnp: Any, lease_seconds: int) -> tuple[int | None, str | None]:
        """Try ``addportmapping`` with the given lease, walking the conflict ladder.

        Returns ``(mapped_external_port, last_error)``.  On success
        ``mapped_external_port`` is the port the router accepted; on failure
        it is None and ``last_error`` describes the final fault.
        """
        last_error: str | None = None
        for offset in _CONFLICT_RETRY_OFFSETS:
            candidate = self._internal_port + offset
            try:
                ok = upnp.addportmapping(
                    candidate,
                    self._protocol,
                    upnp.lanaddr,
                    self._internal_port,
                    self._description,
                    "",
                    lease_seconds,
                )
            except Exception as exc:
                last_error = str(exc)
                # Show a retry counter only when we're actually going to
                # retry (a conflict fault); non-conflict faults bail after
                # this single attempt, so reporting "1/5" would be misleading.
                if _is_conflict_error(exc):
                    logger.warning(
                        "UPnP: addportmapping(%d -> %d, lease=%ds) conflict (attempt %d/%d): %s",
                        candidate,
                        self._internal_port,
                        lease_seconds,
                        offset + 1,
                        len(_CONFLICT_RETRY_OFFSETS),
                        exc,
                    )
                    continue
                logger.warning(
                    "UPnP: addportmapping(%d -> %d, lease=%ds) failed: %s",
                    candidate,
                    self._internal_port,
                    lease_seconds,
                    exc,
                )
                # Non-conflict faults (transport error, SOAP "Action Failed"
                # etc.) are deterministic — retrying with a different port
                # burns startup time for nothing.  Bail out so the caller
                # can try a different lease value or give up.
                break
            if ok:
                return candidate, None
            last_error = f"Router refused mapping on external port {candidate}"
        return None, last_error

    def _mark_failed(self, error: str) -> None:
        """Set state to FAILED with the given error message.

        Called from blocking code paths where we do not have the asyncio lock;
        safe because dataclass field assignment is atomic in CPython and
        readers always go through :meth:`snapshot` which makes a copy.
        """
        self._state = replace(
            self._state,
            status=UpnpStatus.FAILED,
            error=error,
            last_updated_monotonic=time.monotonic(),
        )

    async def _renewal_loop(self) -> None:
        """Periodically refresh the port mapping before the router's lease expires."""
        interval = max(30.0, self._effective_lease_seconds * _RENEWAL_FRACTION)
        failures = 0
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

            async with self._lock:
                upnp_obj = self._upnp
                external_port = self._state.external_port
                status = self._state.status
            if upnp_obj is None or external_port is None or status != UpnpStatus.MAPPED:
                return

            try:
                ok = await asyncio.to_thread(
                    upnp_obj.addportmapping,
                    external_port,
                    self._protocol,
                    upnp_obj.lanaddr,
                    self._internal_port,
                    self._description,
                    "",
                    self._effective_lease_seconds,
                )
            except Exception as exc:
                failures += 1
                logger.warning(
                    "UPnP: renewal failed (%d/%d): %s",
                    failures,
                    _MAX_RENEWAL_FAILURES,
                    exc,
                )
                if failures >= _MAX_RENEWAL_FAILURES:
                    async with self._lock:
                        self._state = replace(
                            self._state,
                            status=UpnpStatus.FAILED,
                            error=f"Renewal failed {failures} times in a row: {exc}",
                            last_updated_monotonic=time.monotonic(),
                        )
                    return
                await asyncio.sleep(60.0)
                continue

            if not ok:
                failures += 1
                logger.warning(
                    "UPnP: router refused renewal (%d/%d)",
                    failures,
                    _MAX_RENEWAL_FAILURES,
                )
                if failures >= _MAX_RENEWAL_FAILURES:
                    async with self._lock:
                        self._state = replace(
                            self._state,
                            status=UpnpStatus.FAILED,
                            error="Router refused renewal",
                            last_updated_monotonic=time.monotonic(),
                        )
                    return
                await asyncio.sleep(60.0)
                continue

            failures = 0
            logger.info(
                "UPnP: renewed mapping %d -> %d (lease %ds)",
                external_port,
                self._internal_port,
                self._effective_lease_seconds,
            )
            async with self._lock:
                self._state = replace(self._state, last_updated_monotonic=time.monotonic())

    async def _ip_watch_loop(self) -> None:
        """Detect router-side external-IP changes (ISP re-lease / reboot)."""
        while True:
            try:
                await asyncio.sleep(_IP_WATCH_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return

            async with self._lock:
                upnp_obj = self._upnp
                current_ip = self._state.external_ip
                status = self._state.status
            if upnp_obj is None or status != UpnpStatus.MAPPED:
                return

            try:
                new_ip = await asyncio.to_thread(upnp_obj.externalipaddress)
            except Exception as exc:
                logger.info("UPnP: externalipaddress() raised during watch: %s", exc)
                continue

            if new_ip and new_ip != current_ip:
                logger.info("UPnP: external IP changed %s -> %s", current_ip, new_ip)
                async with self._lock:
                    self._state = replace(
                        self._state,
                        external_ip=new_ip,
                        last_updated_monotonic=time.monotonic(),
                    )
