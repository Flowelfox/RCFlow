"""Tests for :mod:`src.gui.deeplink`."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from src.gui.deeplink import ADD_WORKER_HOST, URL_SCHEME, build_add_worker_url


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
