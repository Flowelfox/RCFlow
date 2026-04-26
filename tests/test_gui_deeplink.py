"""Tests for :mod:`src.gui.deeplink`."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from src.gui.deeplink import (
    ADD_WORKER_HOST,
    URL_SCHEME,
    build_add_worker_url,
    resolve_reachable_host,
)


def test_build_add_worker_url_basic() -> None:
    url = build_add_worker_url("127.0.0.1", 53890, "secret", wss=False, name="Desk")
    parsed = urlparse(url)

    assert parsed.scheme == URL_SCHEME
    assert parsed.hostname == ADD_WORKER_HOST
    params = parse_qs(parsed.query)
    assert params == {
        "host": ["127.0.0.1"],
        "port": ["53890"],
        "token": ["secret"],
        "ssl": ["0"],
        "name": ["Desk"],
    }


def test_build_add_worker_url_encodes_token_reserved_chars() -> None:
    # Tokens containing &, =, +, / must not break the query string.
    token = "a+b/c=d&e"
    url = build_add_worker_url("example.com", 9000, token, wss=True, name="Host")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["token"] == [token]
    assert params["ssl"] == ["1"]


def test_build_add_worker_url_omits_empty_name() -> None:
    url = build_add_worker_url("10.0.0.1", 22, "tok", wss=False, name="")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert "name" not in params


def test_build_add_worker_url_default_name_uses_hostname(monkeypatch) -> None:
    from src.gui import deeplink  # noqa: PLC0415

    monkeypatch.setattr(deeplink, "default_worker_name", lambda: "My-Box")
    url = build_add_worker_url("1.2.3.4", 8080, "t", wss=False)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["name"] == ["My-Box"]


def test_resolve_reachable_host_returns_concrete_address_verbatim() -> None:
    # Any host that is not a bind-all sentinel must pass through unchanged —
    # mapping e.g. a LAN IP to something else would break remote clients.
    assert resolve_reachable_host("192.168.1.42") == "192.168.1.42"
    assert resolve_reachable_host("rcflow.local") == "rcflow.local"
    assert resolve_reachable_host("127.0.0.1") == "127.0.0.1"


def test_resolve_reachable_host_rewrites_bind_all_with_lan_ip(monkeypatch) -> None:
    from src.gui import deeplink  # noqa: PLC0415

    monkeypatch.setattr(deeplink, "_detect_primary_lan_ip", lambda: "10.0.0.7")
    assert resolve_reachable_host("0.0.0.0") == "10.0.0.7"
    assert resolve_reachable_host("::") == "10.0.0.7"
    assert resolve_reachable_host("") == "10.0.0.7"


def test_resolve_reachable_host_falls_back_to_loopback_when_offline(monkeypatch) -> None:
    from src.gui import deeplink  # noqa: PLC0415

    monkeypatch.setattr(deeplink, "_detect_primary_lan_ip", lambda: None)
    assert resolve_reachable_host("0.0.0.0") == "127.0.0.1"


def test_build_add_worker_url_rewrites_bind_all_host(monkeypatch) -> None:
    from src.gui import deeplink  # noqa: PLC0415

    monkeypatch.setattr(deeplink, "_detect_primary_lan_ip", lambda: "10.0.0.7")
    url = build_add_worker_url("0.0.0.0", 8765, "tok", wss=False, name="Host")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["host"] == ["10.0.0.7"]
