"""NAT-PMP (RFC 6886) port-mapping service.

Targets VPN gateways that expose port forwarding via NAT-PMP — most notably
ProtonVPN Plus on P2P-capable servers (gateway ``10.2.0.1``) and Mullvad.
Lets workers behind ISP CGNAT expose a public address through the VPN.

Mirrors the shape of :mod:`src.services.upnp_service`: a non-blocking
``start()`` that spawns a bootstrap task, a renewal loop that keeps the
mapping alive, ``stop()`` that releases the mapping, and a thread-safe
``snapshot()`` for ``/api/info`` consumers.

The protocol is small enough to implement directly over UDP with
:mod:`struct` — no external dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket
import struct
import sys
import time
from dataclasses import dataclass, replace
from enum import StrEnum

logger = logging.getLogger(__name__)

# NAT-PMP wire constants.
_NATPMP_PORT = 5351
_NATPMP_VERSION = 0
_OP_EXTERNAL_ADDRESS = 0
_OP_MAP_TCP = 2
# Server responses set the high bit on the opcode (op | 0x80).
_RESPONSE_BIT = 0x80

# RFC 6886 §3.5 result codes.
_RESULT_NAMES: dict[int, str] = {
    0: "Success",
    1: "UnsupportedVersion",
    2: "NotAuthorized",
    3: "NetworkFailure",
    4: "OutOfResources",
    5: "UnsupportedOpcode",
}

# RFC 6886 §3.1 retry policy: initial timeout, double each retry, max 5 attempts.
_MAX_RETRIES = 5

# Lease renewal at this fraction of granted lifetime.
_RENEWAL_FRACTION = 0.5

# Periodic re-query of the public IP (VPN exit may rotate).
_PUBLIC_IP_WATCH_INTERVAL_SECONDS = 300.0

# Tolerated consecutive renewal failures before giving up.
_MAX_RENEWAL_FAILURES = 3

# ProtonVPN's default NAT-PMP gateway over its OpenVPN / WireGuard tunnels.
_PROTONVPN_DEFAULT_GATEWAY = "10.2.0.1"


class NatPmpStatus(StrEnum):
    DISABLED = "disabled"
    DISCOVERING = "discovering"
    MAPPED = "mapped"
    FAILED = "failed"
    CLOSING = "closing"


@dataclass
class NatPmpState:
    status: NatPmpStatus = NatPmpStatus.DISABLED
    gateway: str | None = None
    public_ip: str | None = None
    external_port: int | None = None
    internal_port: int | None = None
    error: str | None = None
    last_updated_monotonic: float = 0.0


class NatPmpService:
    """Manage a NAT-PMP TCP port mapping against a VPN / NAT gateway.

    Lifecycle:
        svc = NatPmpService(internal_port=53890, gateway="auto",
                            lease_seconds=60, initial_timeout_ms=250)
        await svc.start()        # non-blocking; spawns bootstrap task
        snapshot = svc.snapshot() # thread-safe state read
        await svc.stop()          # cancels tasks, sends release mapping

    ``start()`` never raises — failures land in ``state.error`` with
    ``status=FAILED``.  Callers (lifespan, GUI) must handle the FAILED state
    rather than rely on exceptions.
    """

    def __init__(
        self,
        *,
        internal_port: int,
        gateway: str = "auto",
        lease_seconds: int = 60,
        initial_timeout_ms: int = 250,
    ) -> None:
        self._internal_port = internal_port
        self._gateway_setting = gateway
        self._lease_seconds = max(1, lease_seconds)
        self._initial_timeout_ms = max(50, initial_timeout_ms)

        self._state = NatPmpState(internal_port=internal_port)
        self._lock = asyncio.Lock()
        self._gateway_resolved: str | None = None
        self._granted_lifetime: int = self._lease_seconds
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._renewal_task: asyncio.Task[None] | None = None
        self._ip_watch_task: asyncio.Task[None] | None = None

    def snapshot(self) -> NatPmpState:
        """Return a shallow copy of the current state (safe for concurrent readers)."""
        return replace(self._state)

    async def start(self) -> None:
        """Discover the gateway + create the port mapping in the background.

        Returns immediately — discovery and the SOAP-equivalent request run
        in a background task so the caller (FastAPI lifespan) is never
        blocked.  Clients poll state via :meth:`snapshot` or ``/api/info``;
        it transitions ``DISCOVERING`` → ``MAPPED`` or ``FAILED``.
        """
        async with self._lock:
            if self._state.status in (NatPmpStatus.MAPPED, NatPmpStatus.DISCOVERING):
                return
            self._state = replace(
                self._state,
                status=NatPmpStatus.DISCOVERING,
                error=None,
                last_updated_monotonic=time.monotonic(),
            )
        self._bootstrap_task = asyncio.create_task(self._bootstrap())

    async def _bootstrap(self) -> None:
        """Run gateway discovery + mapping off the main event-loop critical path."""
        try:
            ok = await asyncio.to_thread(self._discover_and_map)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("NAT-PMP: unexpected error during start (non-fatal)")
            async with self._lock:
                self._state = replace(
                    self._state,
                    status=NatPmpStatus.FAILED,
                    error=f"Unexpected error: {exc}",
                    last_updated_monotonic=time.monotonic(),
                )
            return

        if not ok:
            return  # state already populated by _discover_and_map

        async with self._lock:
            if self._state.status == NatPmpStatus.CLOSING:
                return  # stop() ran during discovery; honour teardown
            status = self._state.status
        if status != NatPmpStatus.MAPPED:
            return

        # Spawn renewal + IP-watch loops only after a confirmed mapping.
        self._renewal_task = asyncio.create_task(self._renewal_loop())
        self._ip_watch_task = asyncio.create_task(self._public_ip_watch_loop())

    async def stop(self) -> None:
        """Cancel background tasks and release the mapping (best-effort)."""
        async with self._lock:
            if self._state.status == NatPmpStatus.DISABLED:
                return
            self._state = replace(
                self._state,
                status=NatPmpStatus.CLOSING,
                last_updated_monotonic=time.monotonic(),
            )
            renewal = self._renewal_task
            ip_watch = self._ip_watch_task
            bootstrap = self._bootstrap_task
            self._renewal_task = None
            self._ip_watch_task = None
            self._bootstrap_task = None
            gateway = self._gateway_resolved
            external_port = self._state.external_port

        for task in (renewal, ip_watch, bootstrap):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        # Send release mapping (lifetime=0).  Best-effort; the mapping
        # expires on its own after the lease anyway.
        if gateway is not None and external_port is not None:
            logger.info("NAT-PMP: releasing mapping %d on shutdown", external_port)
            try:
                await asyncio.to_thread(
                    self._request_map,
                    gateway,
                    self._internal_port,
                    external_port,
                    0,  # lifetime=0 = release
                )
            except Exception as exc:
                logger.warning(
                    "NAT-PMP: release mapping failed: %s — gateway will clear lease in %ds",
                    exc,
                    self._lease_seconds,
                )

        async with self._lock:
            self._gateway_resolved = None
            self._state = replace(
                self._state,
                status=NatPmpStatus.DISABLED,
                public_ip=None,
                external_port=None,
                gateway=None,
                last_updated_monotonic=time.monotonic(),
            )

    # ── Internal blocking helpers (run via asyncio.to_thread) ────────────

    def _discover_and_map(self) -> bool:
        """Resolve gateway, query public IP, request mapping.

        Returns ``True`` on success (state mutated to MAPPED).  Returns
        ``False`` and sets state to FAILED on any failure.  Never raises.
        """
        gateway = self._resolve_gateway()
        if gateway is None:
            self._mark_failed("No NAT-PMP gateway responding (try setting NATPMP_GATEWAY explicitly)")
            return False

        self._gateway_resolved = gateway
        logger.info("NAT-PMP: using gateway %s", gateway)

        # Query public IP first (op=0).  Failures here are non-fatal — we
        # can still request a mapping without knowing the public IP — but
        # the user-facing display loses its punch.
        public_ip: str | None = None
        try:
            public_ip = self._request_public_ip(gateway)
        except _NatPmpError as exc:
            logger.info("NAT-PMP: GetExternalAddress failed: %s — continuing without public IP", exc)

        # Request the actual port mapping.
        try:
            external_port, granted_lifetime = self._request_map(
                gateway, self._internal_port, self._internal_port, self._lease_seconds
            )
        except _NatPmpError as exc:
            self._mark_failed(str(exc))
            logger.warning("NAT-PMP: AddPortMapping failed: %s", exc)
            return False

        self._granted_lifetime = max(1, granted_lifetime)

        self._state = replace(
            self._state,
            status=NatPmpStatus.MAPPED,
            gateway=gateway,
            public_ip=public_ip,
            external_port=external_port,
            error=None,
            last_updated_monotonic=time.monotonic(),
        )
        logger.info(
            "NAT-PMP: mapped %d → %s:%d (lease %ds)",
            self._internal_port,
            public_ip or "?",
            external_port,
            self._granted_lifetime,
        )
        return True

    def _resolve_gateway(self) -> str | None:
        """Return the IP of a responsive NAT-PMP gateway, or None if none works.

        Order of preference: explicit user setting → ProtonVPN default →
        OS default-route gateway.  Each candidate is probed with op=0
        (GetExternalAddress) using the standard retry ladder; the first that
        replies wins.
        """
        candidates: list[str] = []
        if self._gateway_setting and self._gateway_setting != "auto":
            try:
                ipaddress.ip_address(self._gateway_setting)
                candidates.append(self._gateway_setting)
            except ValueError:
                logger.warning(
                    "NAT-PMP: NATPMP_GATEWAY=%r is not a valid IP — falling back to auto",
                    self._gateway_setting,
                )

        if not candidates or self._gateway_setting == "auto":
            candidates.append(_PROTONVPN_DEFAULT_GATEWAY)
            os_gateway = _detect_default_gateway()
            if os_gateway and os_gateway not in candidates:
                candidates.append(os_gateway)

        for candidate in candidates:
            try:
                self._request_public_ip(candidate)
            except _NatPmpError as exc:
                logger.info("NAT-PMP: probe %s failed: %s", candidate, exc)
                continue
            return candidate
        return None

    def _request_public_ip(self, gateway: str) -> str:
        """Send op=0 (GetExternalAddress) and return the public IP string."""
        request = struct.pack(">BB", _NATPMP_VERSION, _OP_EXTERNAL_ADDRESS)
        response = self._exchange(gateway, request, expected_size=12)
        version, op, result, _epoch, ip_int = struct.unpack(">BBHII", response)
        _validate_response(version, op, _OP_EXTERNAL_ADDRESS, result)
        return socket.inet_ntoa(struct.pack(">I", ip_int))

    def _request_map(
        self, gateway: str, internal_port: int, suggested_external_port: int, lifetime: int
    ) -> tuple[int, int]:
        """Send op=2 (AddPortMapping TCP).

        Returns ``(mapped_external_port, granted_lifetime)`` on success.
        Raises :class:`_NatPmpError` on protocol or transport failure.
        ``lifetime=0`` releases the mapping (still returns the response).
        """
        request = struct.pack(
            ">BBHHHI",
            _NATPMP_VERSION,
            _OP_MAP_TCP,
            0,  # reserved
            internal_port,
            suggested_external_port,
            lifetime,
        )
        response = self._exchange(gateway, request, expected_size=16)
        version, op, result, _epoch, _internal, mapped_external, granted_lifetime = struct.unpack(">BBHIHHI", response)
        _validate_response(version, op, _OP_MAP_TCP, result)
        return mapped_external, granted_lifetime

    def _exchange(self, gateway: str, request: bytes, *, expected_size: int) -> bytes:
        """Send *request* and return the response, retrying per RFC 6886.

        Raises :class:`_NatPmpError` after :data:`_MAX_RETRIES` consecutive
        timeouts or on socket-level errors.
        """
        timeout_ms = self._initial_timeout_ms
        last_error: str = "no response"
        for _attempt in range(_MAX_RETRIES):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except OSError as exc:
                raise _NatPmpError(f"socket creation failed: {exc}") from exc
            try:
                sock.settimeout(timeout_ms / 1000.0)
                sock.sendto(request, (gateway, _NATPMP_PORT))
                # Filter responses by source IP — stray broadcasts from other
                # devices on the LAN must not poison the result.
                while True:
                    data, addr = sock.recvfrom(64)
                    if addr[0] == gateway and len(data) >= expected_size:
                        return data[:expected_size]
            except TimeoutError:
                last_error = f"timeout after {timeout_ms} ms"
            except OSError as exc:
                last_error = f"socket error: {exc}"
            finally:
                sock.close()
            timeout_ms *= 2
        raise _NatPmpError(f"gateway {gateway} not responding ({last_error})")

    def _mark_failed(self, error: str) -> None:
        """Set state to FAILED with the given error.

        Called from blocking code paths without the asyncio lock; safe
        because dataclass field assignment is atomic in CPython and readers
        always go through :meth:`snapshot` (which makes a copy).
        """
        self._state = replace(
            self._state,
            status=NatPmpStatus.FAILED,
            error=error,
            last_updated_monotonic=time.monotonic(),
        )

    async def _renewal_loop(self) -> None:
        """Periodically refresh the port mapping before the lease expires."""
        interval = max(15.0, self._granted_lifetime * _RENEWAL_FRACTION)
        failures = 0
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

            async with self._lock:
                gateway = self._gateway_resolved
                external_port = self._state.external_port
                status = self._state.status
            if gateway is None or external_port is None or status != NatPmpStatus.MAPPED:
                return

            try:
                new_external, granted_lifetime = await asyncio.to_thread(
                    self._request_map,
                    gateway,
                    self._internal_port,
                    external_port,
                    self._lease_seconds,
                )
            except _NatPmpError as exc:
                failures += 1
                logger.warning(
                    "NAT-PMP: renewal failed (%d/%d): %s",
                    failures,
                    _MAX_RENEWAL_FAILURES,
                    exc,
                )
                if failures >= _MAX_RENEWAL_FAILURES:
                    async with self._lock:
                        self._state = replace(
                            self._state,
                            status=NatPmpStatus.FAILED,
                            error=f"Renewal failed {failures} times in a row: {exc}",
                            last_updated_monotonic=time.monotonic(),
                        )
                    return
                await asyncio.sleep(min(60.0, interval))
                continue

            failures = 0
            self._granted_lifetime = max(1, granted_lifetime)
            interval = max(15.0, self._granted_lifetime * _RENEWAL_FRACTION)
            async with self._lock:
                self._state = replace(
                    self._state,
                    external_port=new_external,
                    last_updated_monotonic=time.monotonic(),
                )
            logger.info(
                "NAT-PMP: renewed mapping → %s:%d (lease %ds)",
                self._state.public_ip or "?",
                new_external,
                self._granted_lifetime,
            )

    async def _public_ip_watch_loop(self) -> None:
        """Detect VPN exit IP rotation and update state accordingly."""
        while True:
            try:
                await asyncio.sleep(_PUBLIC_IP_WATCH_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return

            async with self._lock:
                gateway = self._gateway_resolved
                current_ip = self._state.public_ip
                status = self._state.status
            if gateway is None or status != NatPmpStatus.MAPPED:
                return

            try:
                new_ip = await asyncio.to_thread(self._request_public_ip, gateway)
            except _NatPmpError as exc:
                logger.info("NAT-PMP: public-IP refresh failed: %s", exc)
                continue

            if new_ip and new_ip != current_ip:
                logger.info("NAT-PMP: public IP changed %s → %s", current_ip, new_ip)
                async with self._lock:
                    self._state = replace(
                        self._state,
                        public_ip=new_ip,
                        last_updated_monotonic=time.monotonic(),
                    )


class _NatPmpError(Exception):
    """Internal protocol / transport error — never escapes the public API."""


def _validate_response(version: int, op: int, expected_op: int, result: int) -> None:
    """Raise :class:`_NatPmpError` when *response* is malformed or carries an error code."""
    if version != _NATPMP_VERSION:
        raise _NatPmpError(f"unexpected NAT-PMP version {version}")
    if op != (expected_op | _RESPONSE_BIT):
        raise _NatPmpError(f"unexpected response opcode {op:#x}")
    if result != 0:
        name = _RESULT_NAMES.get(result, f"code {result}")
        hint = ""
        if result == 2:
            hint = " — enable port forwarding on a P2P-capable VPN server (ProtonVPN Plus / Mullvad)"
        elif result == 5:
            hint = " — gateway does not speak NAT-PMP (wrong NATPMP_GATEWAY?)"
        raise _NatPmpError(f"NAT-PMP error: {name}{hint}")


def _detect_default_gateway() -> str | None:
    """Return the system's default IPv4 gateway, or None if unavailable.

    Best-effort across platforms.  Used as a last-resort candidate when
    ``NATPMP_GATEWAY=auto`` and the ProtonVPN default doesn't respond.
    """
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/net/route", encoding="utf-8") as fh:
                next(fh)  # header
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == "00000000":
                        # Gateway is little-endian hex
                        gw_hex = parts[2]
                        gw_bytes = bytes.fromhex(gw_hex)
                        return socket.inet_ntoa(gw_bytes[::-1])
        except OSError:
            return None
        return None

    # macOS / Windows: use the UDP-connect trick to find the outbound IP for
    # a public destination, then assume the gateway is at .1 on the same /24.
    # Imperfect but covers the common case (single-homed laptops on
    # 192.168.x / 10.x / 172.16.x networks).
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()

    try:
        ip = ipaddress.IPv4Address(local_ip)
    except ValueError:
        return None
    if ip.is_loopback:
        return None
    # Replace last octet with .1.
    octets = local_ip.split(".")
    if len(octets) != 4:
        return None
    octets[3] = "1"
    return ".".join(octets)
