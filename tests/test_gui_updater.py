"""Unit tests for ``src.gui.updater`` — version logic, fetcher, cache TTL."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

import pytest

from src.gui import updater
from src.gui.updater import (
    HttpUpdateFetcher,
    UpdateInfo,
    UpdateService,
    asset_suffix,
    cleanup_partial_downloads,
    is_newer,
    normalize_version,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeFetcher:
    """Test double for ``UpdateFetcher`` that returns a queued result."""

    def __init__(self) -> None:
        self.next: UpdateInfo | None | Exception = None
        self.calls: int = 0

    def fetch_latest(self) -> UpdateInfo | None:
        self.calls += 1
        if isinstance(self.next, Exception):
            raise self.next
        return self.next


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ``_get_settings_path`` to a temp file for isolated state."""
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")

    monkeypatch.setattr("src.config._get_settings_path", lambda: target)
    yield target


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("predicate never became true within timeout")


# ── normalize_version ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v1.2.3", "1.2.3"),
        ("V1.2.3", "1.2.3"),
        ("1.2.3", "1.2.3"),
        ("1.2.3+45", "1.2.3"),
        ("v1.2.3+build", "1.2.3"),
        ("  v0.43.0+91  ", "0.43.0"),
    ],
)
def test_normalize_version(raw: str, expected: str) -> None:
    assert normalize_version(raw) == expected


# ── is_newer ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("1.10.0", "1.9.0", True),
        ("1.9.0", "1.10.0", False),
        ("2.0.0", "1.99.99", True),
        ("1.0.0", "1.0.0", False),
        ("1.0.1", "1.0.0", True),
        ("1.0", "1.0.0", False),
        ("1.0.0.1", "1.0.0", True),
    ],
)
def test_is_newer(a: str, b: str, expected: bool) -> None:
    assert is_newer(a, b) is expected


# ── asset_suffix ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("plat", "arch", "expected"),
    [
        ("linux", "x86_64", "linux-worker-amd64.deb"),
        ("linux", "amd64", "linux-worker-amd64.deb"),
        ("linux", "aarch64", "linux-worker-arm64.deb"),
        ("linux", "arm64", "linux-worker-arm64.deb"),
        ("windows", "amd64", "windows-worker-amd64.exe"),
        ("windows", "x86_64", "windows-worker-amd64.exe"),
        ("darwin", "arm64", "macos-worker-arm64.dmg"),
        ("darwin", "x86_64", "macos-worker-x86_64.dmg"),
        ("freebsd", "amd64", None),
        ("windows", "arm64", None),
    ],
)
def test_asset_suffix(plat: str, arch: str, expected: str | None) -> None:
    assert asset_suffix(plat, arch) == expected


# ── UpdateService — restore_cached_state ────────────────────────────────────


def test_restore_cached_state_loads_cache(settings_file: Path) -> None:
    settings_file.write_text(
        json.dumps(
            {
                "RCFLOW_UPDATE_CACHED_VERSION": "1.5.0",
                "RCFLOW_UPDATE_CACHED_RELEASE_URL": "https://example/release",
                "RCFLOW_UPDATE_CACHED_DOWNLOAD_URL": "https://example/asset",
                "RCFLOW_UPDATE_CACHED_ASSET_NAME": "rcflow-v1.5.0-linux-worker-amd64.deb",
            }
        ),
        encoding="utf-8",
    )

    svc = UpdateService(current_version="1.4.0", fetcher=FakeFetcher())
    svc.restore_cached_state()

    assert svc.latest is not None
    assert svc.latest.version == "1.5.0"
    assert svc.latest.release_url == "https://example/release"
    assert svc.latest.download_url == "https://example/asset"
    assert svc.update_available is True


def test_restore_cached_state_clears_when_running_is_newer(settings_file: Path) -> None:
    settings_file.write_text(
        json.dumps(
            {
                "RCFLOW_UPDATE_CACHED_VERSION": "1.4.0",
                "RCFLOW_UPDATE_DISMISSED_VERSION": "1.4.0",
            }
        ),
        encoding="utf-8",
    )

    svc = UpdateService(current_version="1.5.0", fetcher=FakeFetcher())
    svc.restore_cached_state()

    assert svc.latest is None
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted.get("RCFLOW_UPDATE_CACHED_VERSION") == ""
    assert persisted.get("RCFLOW_UPDATE_DISMISSED_VERSION") == ""


# ── UpdateService — check_now / maybe_check ─────────────────────────────────


def test_check_now_persists_result_and_notifies(settings_file: Path) -> None:
    fetcher = FakeFetcher()
    fetcher.next = UpdateInfo(
        version="2.0.0",
        release_url="https://example/2.0",
        download_url="https://example/2.0.exe",
        asset_name="rcflow-v2.0.0-windows-worker-amd64.exe",
        asset_size=12345,
    )
    notifications: list[None] = []

    svc = UpdateService(current_version="1.0.0", fetcher=fetcher)
    svc.add_listener(lambda: notifications.append(None))
    svc.check_now()

    _wait_until(lambda: not svc.is_checking)

    assert fetcher.calls == 1
    assert svc.latest is not None
    assert svc.latest.version == "2.0.0"
    assert svc.update_available is True
    assert len(notifications) >= 2  # one for is_checking=True, one for completion

    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["RCFLOW_UPDATE_CACHED_VERSION"] == "2.0.0"
    assert persisted["RCFLOW_UPDATE_CACHED_DOWNLOAD_URL"] == "https://example/2.0.exe"
    assert persisted["RCFLOW_UPDATE_LAST_CHECK"]


def test_check_now_records_error(settings_file: Path) -> None:
    fetcher = FakeFetcher()
    fetcher.next = RuntimeError("network down")

    svc = UpdateService(current_version="1.0.0", fetcher=fetcher)
    svc.check_now()
    _wait_until(lambda: not svc.is_checking)

    assert svc.last_error == "network down"
    assert svc.latest is None


def test_check_now_is_reentrancy_safe(settings_file: Path) -> None:
    fetcher = FakeFetcher()
    started = threading.Event()
    release = threading.Event()

    def _slow() -> UpdateInfo | None:
        fetcher.calls += 1
        started.set()
        release.wait(timeout=1.0)
        return UpdateInfo("1.1.0", "https://e", None, None, None)

    fetcher.fetch_latest = _slow  # type: ignore[method-assign]

    svc = UpdateService(current_version="1.0.0", fetcher=fetcher)
    svc.check_now()
    started.wait(timeout=1.0)
    svc.check_now()  # second call should be a no-op while first runs
    release.set()
    _wait_until(lambda: not svc.is_checking)

    assert fetcher.calls == 1


def test_maybe_check_skips_within_ttl(settings_file: Path) -> None:
    settings_file.write_text(
        json.dumps(
            {
                "RCFLOW_UPDATE_LAST_CHECK": datetime.now(UTC).isoformat(),
                "RCFLOW_UPDATE_CACHED_VERSION": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    fetcher = FakeFetcher()
    svc = UpdateService(current_version="1.0.0", fetcher=fetcher)
    svc.maybe_check()
    _wait_until(lambda: not svc.is_checking, timeout=0.5)
    assert fetcher.calls == 0


def test_maybe_check_runs_when_ttl_expired(settings_file: Path) -> None:
    stale = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    settings_file.write_text(
        json.dumps(
            {
                "RCFLOW_UPDATE_LAST_CHECK": stale,
                "RCFLOW_UPDATE_CACHED_VERSION": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    fetcher = FakeFetcher()
    fetcher.next = UpdateInfo("1.1.0", "https://e", None, None, None)
    svc = UpdateService(current_version="1.0.0", fetcher=fetcher)
    svc.maybe_check()
    _wait_until(lambda: not svc.is_checking)
    assert fetcher.calls == 1


# ── Dismissal ───────────────────────────────────────────────────────────────


def test_dismiss_then_newer_release_clears_dismissal(settings_file: Path) -> None:
    fetcher = FakeFetcher()
    fetcher.next = UpdateInfo("1.5.0", "https://e", None, None, None)
    svc = UpdateService(current_version="1.0.0", fetcher=fetcher)
    svc.check_now()
    _wait_until(lambda: not svc.is_checking)
    svc.dismiss_current()
    assert svc.is_dismissed is True
    assert svc.show_banner is False

    fetcher.next = UpdateInfo("1.6.0", "https://e", None, None, None)
    svc.check_now()
    _wait_until(lambda: not svc.is_checking)

    assert svc.latest is not None
    assert svc.latest.version == "1.6.0"
    assert svc.is_dismissed is False
    assert svc.show_banner is True


# ── HttpUpdateFetcher.fetch_latest (asset selection only) ───────────────────


def test_http_fetcher_picks_matching_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "tag_name": "v0.44.0",
        "html_url": "https://github.com/Flowelfox/RCFlow/releases/tag/v0.44.0",
        "assets": [
            {
                "name": "rcflow-v0.44.0-linux-worker-amd64.deb",
                "browser_download_url": "https://example/deb",
                "size": 9999,
            },
            {
                "name": "rcflow-v0.44.0-windows-worker-amd64.exe",
                "browser_download_url": "https://example/exe",
                "size": 8888,
            },
        ],
    }

    class _Resp:
        status = 200
        headers: ClassVar[dict[str, str]] = {}

        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            pass

    def _urlopen(req, timeout=None):
        del req, timeout
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    fetcher = HttpUpdateFetcher(plat="windows", arch="amd64")
    info = fetcher.fetch_latest()

    assert info is not None
    assert info.version == "0.44.0"
    assert info.download_url == "https://example/exe"
    assert info.asset_name == "rcflow-v0.44.0-windows-worker-amd64.exe"
    assert info.asset_size == 8888


def test_http_fetcher_returns_no_url_when_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "tag_name": "v0.44.0",
        "html_url": "https://example/release",
        "assets": [
            {"name": "rcflow-v0.44.0-android-client-arm64.apk", "browser_download_url": "https://x"},
        ],
    }

    class _Resp:
        status = 200
        headers: ClassVar[dict[str, str]] = {}

        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            pass

    def _urlopen(req, timeout=None):
        del req, timeout
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    fetcher = HttpUpdateFetcher(plat="linux", arch="amd64")
    info = fetcher.fetch_latest()

    assert info is not None
    assert info.version == "0.44.0"
    assert info.download_url is None
    assert info.asset_name is None


# ── Partial-download cleanup ────────────────────────────────────────────────


def test_cleanup_partial_downloads_removes_old(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    fresh = cache / "fresh.deb.partial"
    stale = cache / "stale.deb.partial"
    fresh.write_bytes(b"x")
    stale.write_bytes(b"x")

    old = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(stale, (old, old))

    monkeypatch.setattr(updater, "_download_cache_dir", lambda: cache)
    cleanup_partial_downloads()

    assert fresh.exists()
    assert not stale.exists()
