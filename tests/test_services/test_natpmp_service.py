"""Tests for NatPmpService.

All tests mock ``socket.socket`` to feed synthetic NAT-PMP frames so no real
UDP traffic is generated.  The key invariants are (1) ``start()`` never
raises regardless of what the gateway does, (2) successful mapping updates
state, (3) ``stop()`` releases the mapping with lifetime=0.
"""

from __future__ import annotations

import socket
import struct
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from src.services.natpmp_service import NatPmpService, NatPmpStatus

_GATEWAY = "10.2.0.1"


def _public_ip_response(public_ip: str = "203.0.113.5", result: int = 0) -> bytes:
    """Build a NAT-PMP op=0 response: version, op|0x80, result, epoch, ip(4)."""
    ip_int = struct.unpack(">I", socket.inet_aton(public_ip))[0]
    return struct.pack(">BBHII", 0, 0 | 0x80, result, 0, ip_int)


def _map_response(
    *,
    internal_port: int = 53890,
    external_port: int = 53890,
    lifetime: int = 60,
    result: int = 0,
) -> bytes:
    """Build a NAT-PMP op=2 response."""
    return struct.pack(">BBHIHHI", 0, 2 | 0x80, result, 0, internal_port, external_port, lifetime)


class _FakeSocket:
    """Minimal socket replacement for NatPmpService unit tests.

    Records every ``sendto`` payload + destination, replays a queue of
    pre-canned ``recvfrom`` responses (or raises :class:`TimeoutError`).
    """

    def __init__(self, responses: Iterator[bytes | type[BaseException]]) -> None:
        self._responses = responses
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self._closed = False
        self._timeout: float = 0.0

    def settimeout(self, value: float) -> None:
        self._timeout = value

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        self.sent.append((data, addr))
        return len(data)

    def recvfrom(self, _bufsize: int) -> tuple[bytes, tuple[str, int]]:
        try:
            item = next(self._responses)
        except StopIteration as exc:  # pragma: no cover — test bug
            raise AssertionError("socket recvfrom called more times than test expected") from exc
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        return item, (_GATEWAY, 5351)

    def close(self) -> None:
        self._closed = True


def _install_fake_socket(
    monkeypatch: pytest.MonkeyPatch,
    *,
    responses: list[bytes | type[BaseException]],
    record: list[_FakeSocket] | None = None,
) -> None:
    """Patch :func:`socket.socket` to return _FakeSocket instances feeding *responses*."""
    iterator = iter(responses)

    def _factory(*_args: Any, **_kwargs: Any) -> _FakeSocket:
        sock = _FakeSocket(iterator)
        if record is not None:
            record.append(sock)
        return sock

    monkeypatch.setattr("src.services.natpmp_service.socket.socket", _factory)


@pytest.mark.asyncio
async def test_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    sockets: list[_FakeSocket] = []
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("203.0.113.5"),  # gateway probe
            _public_ip_response("203.0.113.5"),  # GetExternalAddress in _discover_and_map
            _map_response(internal_port=53890, external_port=53890, lifetime=60),
        ],
        record=sockets,
    )

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap = svc.snapshot()
    assert snap.status == NatPmpStatus.MAPPED
    assert snap.gateway == _GATEWAY
    assert snap.public_ip == "203.0.113.5"
    assert snap.external_port == 53890
    assert snap.internal_port == 53890
    assert snap.error is None
    await svc.stop()


@pytest.mark.asyncio
async def test_no_response_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """All retry attempts time out → state=FAILED with 'not responding'."""
    # 5 retries x 1 candidate = 5 timeouts.  No public IP, no map.
    _install_fake_socket(monkeypatch, responses=[TimeoutError] * 10)

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60, initial_timeout_ms=50)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap = svc.snapshot()
    assert snap.status == NatPmpStatus.FAILED
    assert snap.error is not None
    assert "responding" in snap.error.lower() or "no NAT-PMP gateway" in snap.error


@pytest.mark.asyncio
async def test_not_authorized_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """result_code=2 → message hints at ProtonVPN P2P server requirement."""
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("203.0.113.5"),
            _public_ip_response("203.0.113.5"),
            _map_response(result=2),
        ],
    )

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap = svc.snapshot()
    assert snap.status == NatPmpStatus.FAILED
    assert snap.error is not None
    assert "NotAuthorized" in snap.error
    assert "P2P" in snap.error  # actionable hint


@pytest.mark.asyncio
async def test_unsupported_opcode_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """result_code=5 (UnsupportedOpcode) → suggests gateway misconfiguration."""
    # Probe succeeds (op=0 returns success), but op=2 returns UnsupportedOpcode.
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("203.0.113.5"),
            _public_ip_response("203.0.113.5"),
            _map_response(result=5),
        ],
    )

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap = svc.snapshot()
    assert snap.status == NatPmpStatus.FAILED
    assert snap.error is not None
    assert "UnsupportedOpcode" in snap.error
    assert "NATPMP_GATEWAY" in snap.error


@pytest.mark.asyncio
async def test_stop_sends_release_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop() must send op=2 with lifetime=0 to release the mapping."""
    sockets: list[_FakeSocket] = []
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("203.0.113.5"),  # probe
            _public_ip_response("203.0.113.5"),  # external addr
            _map_response(),  # add mapping
            _map_response(lifetime=0),  # release on stop
        ],
        record=sockets,
    )

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task
    assert svc.snapshot().status == NatPmpStatus.MAPPED

    await svc.stop()

    # Last sent packet must be op=2 with lifetime=0.
    last_payload = sockets[-1].sent[-1][0]
    version, op, _reserved, _internal, _suggested, lifetime = struct.unpack(">BBHHHI", last_payload)
    assert version == 0
    assert op == 2
    assert lifetime == 0


@pytest.mark.asyncio
async def test_start_never_raises_on_socket_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError from sendto must be caught; state=FAILED, no exception escapes."""

    def _bad_socket(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("no such network")

    monkeypatch.setattr("src.services.natpmp_service.socket.socket", _bad_socket)

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()  # must not raise
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap = svc.snapshot()
    assert snap.status == NatPmpStatus.FAILED
    assert snap.error is not None


@pytest.mark.asyncio
async def test_renewal_task_alive_after_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a successful map, _renewal_task must be set and not done."""
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("203.0.113.5"),
            _public_ip_response("203.0.113.5"),
            _map_response(),
        ],
    )

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    assert svc._renewal_task is not None
    assert not svc._renewal_task.done()
    await svc.stop()


@pytest.mark.asyncio
async def test_public_ip_populated(monkeypatch: pytest.MonkeyPatch) -> None:
    """state.public_ip must come from the op=0 GetExternalAddress response."""
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("198.51.100.42"),  # probe
            _public_ip_response("198.51.100.42"),  # _discover_and_map
            _map_response(),
        ],
    )

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap = svc.snapshot()
    assert snap.public_ip == "198.51.100.42"
    await svc.stop()


@pytest.mark.asyncio
async def test_snapshot_is_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("203.0.113.5"),
            _public_ip_response("203.0.113.5"),
            _map_response(),
        ],
    )

    svc = NatPmpService(internal_port=53890, gateway=_GATEWAY, lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap1 = svc.snapshot()
    snap2 = svc.snapshot()
    assert snap1 is not snap2
    snap1.external_port = 99999
    assert snap2.external_port == 53890
    assert svc.snapshot().external_port == 53890
    await svc.stop()


@pytest.mark.asyncio
async def test_auto_gateway_picks_responsive_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """gateway='auto' tries ProtonVPN default first; first responsive candidate wins."""
    # Probe succeeds on 1st attempt → ProtonVPN default `10.2.0.1` adopted.
    _install_fake_socket(
        monkeypatch,
        responses=[
            _public_ip_response("203.0.113.5"),  # probe
            _public_ip_response("203.0.113.5"),  # _discover_and_map
            _map_response(),
        ],
    )
    # _detect_default_gateway is called only when ProtonVPN fails — return None
    # so the test stays focused on the ProtonVPN-first behaviour.
    monkeypatch.setattr("src.services.natpmp_service._detect_default_gateway", lambda: None)

    svc = NatPmpService(internal_port=53890, gateway="auto", lease_seconds=60)
    await svc.start()
    if svc._bootstrap_task is not None:
        await svc._bootstrap_task

    snap = svc.snapshot()
    assert snap.status == NatPmpStatus.MAPPED
    assert snap.gateway == "10.2.0.1"
    await svc.stop()
