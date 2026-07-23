"""Tests for the ``rcflow update`` CLI flow (src/services/cli_update.py)."""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from src.gui.updater import UpdateInfo
from src.services import cli_update
from src.services.cli_update import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_UNSUPPORTED,
    EXIT_UPDATE_AVAILABLE,
    run_update,
)


class FakeFetcher:
    """UpdateFetcher double: returns or raises whatever is queued in ``next``."""

    def __init__(self, next_value: UpdateInfo | Exception | None) -> None:
        self.next = next_value

    def fetch_latest(self) -> UpdateInfo | None:
        if isinstance(self.next, Exception):
            raise self.next
        return self.next


def _info(
    version: str = "9.9.9",
    download_url: str | None = "https://dl.example/rcflow.deb",
    asset_name: str = "rcflow-v9.9.9-linux-worker-amd64.deb",
    asset_size: int | None = None,
) -> UpdateInfo:
    return UpdateInfo(
        version=version,
        release_url="https://github.com/Flowelfox/RCFlow/releases/latest",
        download_url=download_url,
        asset_name=asset_name,
        asset_size=asset_size,
    )


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Frozen install, known current version, isolated download cache, tty stdin."""
    monkeypatch.setattr(cli_update, "is_frozen", lambda: True)
    monkeypatch.setattr(cli_update, "resolve_current_version", lambda: "1.0.0")
    monkeypatch.setattr("src.gui.updater._download_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(cli_update.sys.stdin, "isatty", lambda: True, raising=False)


def _no_download(monkeypatch: pytest.MonkeyPatch) -> list:
    """Stub stream_download; record calls."""
    calls: list = []
    monkeypatch.setattr(cli_update, "stream_download", lambda *a, **k: calls.append(a))
    return calls


def _no_dpkg(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list[list[str]]:
    """Stub subprocess.run for dpkg; record argv."""
    calls: list[list[str]] = []

    class _Result:
        def __init__(self) -> None:
            self.returncode = returncode

    def _run(cmd, check):
        calls.append(list(cmd))
        return _Result()

    monkeypatch.setattr(cli_update.subprocess, "run", _run)
    return calls


# ── --check ──────────────────────────────────────────────────────────────────


class TestCheck:
    def test_up_to_date(self, capsys: pytest.CaptureFixture) -> None:
        rc = run_update(check_only=True, assume_yes=False, fetcher=FakeFetcher(_info("1.0.0")), plat="linux")
        assert rc == EXIT_OK
        out = capsys.readouterr().out
        assert "Current version: 1.0.0" in out
        assert "up to date" in out

    def test_update_available(self, capsys: pytest.CaptureFixture) -> None:
        rc = run_update(check_only=True, assume_yes=False, fetcher=FakeFetcher(_info("1.1.0")), plat="linux")
        assert rc == EXIT_UPDATE_AVAILABLE
        assert "Update available" in capsys.readouterr().out

    def test_network_error(self, capsys: pytest.CaptureFixture) -> None:
        rc = run_update(check_only=True, assume_yes=False, fetcher=FakeFetcher(RuntimeError("boom")), plat="linux")
        assert rc == EXIT_ERROR
        assert "update check failed: boom" in capsys.readouterr().err

    def test_no_release_info(self, capsys: pytest.CaptureFixture) -> None:
        rc = run_update(check_only=True, assume_yes=False, fetcher=FakeFetcher(None), plat="linux")
        assert rc == EXIT_ERROR
        assert "no release information" in capsys.readouterr().err

    def test_works_unfrozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_update, "is_frozen", lambda: False)
        rc = run_update(check_only=True, assume_yes=False, fetcher=FakeFetcher(_info("1.1.0")), plat="linux")
        assert rc == EXIT_UPDATE_AVAILABLE

    def test_unknown_current_version(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr(cli_update, "resolve_current_version", lambda: "")
        rc = run_update(check_only=True, assume_yes=False, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_ERROR
        assert "cannot determine the current version" in capsys.readouterr().err


# ── Full update flow ─────────────────────────────────────────────────────────


class TestUpdate:
    def test_already_current_skips_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        downloads = _no_download(monkeypatch)
        rc = run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(_info("1.0.0")), plat="linux")
        assert rc == EXIT_OK
        assert downloads == []

    def test_refuses_unfrozen(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr(cli_update, "is_frozen", lambda: False)
        rc = run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_UNSUPPORTED
        assert "git pull && uv sync" in capsys.readouterr().err

    def test_no_asset_for_platform(self, capsys: pytest.CaptureFixture) -> None:
        rc = run_update(
            check_only=False,
            assume_yes=True,
            fetcher=FakeFetcher(_info(download_url=None)),
            plat="linux",
        )
        assert rc == EXIT_UNSUPPORTED
        assert "releases/latest" in capsys.readouterr().err

    def test_non_tty_without_yes_aborts(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        monkeypatch.setattr(cli_update.sys.stdin, "isatty", lambda: False, raising=False)
        downloads = _no_download(monkeypatch)
        rc = run_update(check_only=False, assume_yes=False, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_ERROR
        assert "--yes" in capsys.readouterr().err
        assert downloads == []

    def test_confirm_decline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(builtins, "input", lambda _prompt: "n")
        downloads = _no_download(monkeypatch)
        rc = run_update(check_only=False, assume_yes=False, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_ERROR
        assert downloads == []

    def test_confirm_accept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(builtins, "input", lambda _prompt: "y")
        _no_download(monkeypatch)
        dpkg_calls = _no_dpkg(monkeypatch)
        rc = run_update(check_only=False, assume_yes=False, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_OK
        assert len(dpkg_calls) == 1

    def test_yes_skips_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail(_prompt: str) -> str:
            raise AssertionError("input() must not be called with --yes")

        monkeypatch.setattr(builtins, "input", _fail)
        _no_download(monkeypatch)
        _no_dpkg(monkeypatch)
        rc = run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_OK

    def test_download_cache_reuse(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        info = _info(asset_size=4)
        (tmp_path / info.asset_name).write_bytes(b"debb")

        def _fail(*a, **k):
            raise AssertionError("stream_download must not be called for a cached file")

        monkeypatch.setattr(cli_update, "stream_download", _fail)
        _no_dpkg(monkeypatch)
        rc = run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(info), plat="linux")
        assert rc == EXIT_OK
        assert "Using cached download" in capsys.readouterr().out

    def test_download_failure(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        def _boom(*a, **k):
            raise RuntimeError("Download truncated")

        monkeypatch.setattr(cli_update, "stream_download", _boom)
        rc = run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_ERROR
        assert "download failed" in capsys.readouterr().err

    def test_linux_sudo_prefix_when_not_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_update.os, "geteuid", lambda: 1000)
        _no_download(monkeypatch)
        calls = _no_dpkg(monkeypatch)
        run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(_info()), plat="linux")
        assert calls[0][:3] == ["sudo", "dpkg", "-i"]

    def test_linux_no_sudo_when_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_update.os, "geteuid", lambda: 0)
        _no_download(monkeypatch)
        calls = _no_dpkg(monkeypatch)
        run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(_info()), plat="linux")
        assert calls[0][:2] == ["dpkg", "-i"]

    def test_linux_dpkg_failure(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        _no_download(monkeypatch)
        _no_dpkg(monkeypatch, returncode=1)
        rc = run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(_info()), plat="linux")
        assert rc == EXIT_ERROR
        assert "dpkg --configure -a" in capsys.readouterr().err

    def test_darwin_launches_installer(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
        _no_download(monkeypatch)
        launched: list[tuple[Path, str]] = []
        monkeypatch.setattr(cli_update, "launch_installer", lambda p, plat: launched.append((p, plat)))
        info = _info(asset_name="rcflow-v9.9.9-macos-worker-arm64.dmg", download_url="https://dl/x.dmg")
        rc = run_update(check_only=False, assume_yes=True, fetcher=FakeFetcher(info), plat="darwin")
        assert rc == EXIT_OK
        assert launched and launched[0][1] == "darwin"
        assert "Installer launched" in capsys.readouterr().out
