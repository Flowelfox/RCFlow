"""Tests for the user-PATH CLI launcher helper in src/services/cli_link.py."""

from __future__ import annotations

import sys

import pytest

from src.services import cli_link
from src.services.cli_link import CliLinkStatus

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink behaviour")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # A stand-in "executable" the launcher should point at.
    exe = tmp_path / "app" / "rcflow"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(exe))
    return tmp_path, exe


def test_link_path_and_bin_dir(fake_home):
    tmp_path, _ = fake_home
    assert cli_link.bin_dir() == tmp_path / ".local" / "bin"
    assert cli_link.link_path() == tmp_path / ".local" / "bin" / "rcflow"


def test_status_missing_then_installed(fake_home):
    assert cli_link.status() is CliLinkStatus.MISSING
    assert not cli_link.is_installed()

    path = cli_link.install()
    assert path == cli_link.link_path()
    assert path.is_symlink()
    assert cli_link.status() is CliLinkStatus.INSTALLED
    assert cli_link.is_installed()


def test_status_mismatch_when_points_elsewhere(fake_home):
    tmp_path, _ = fake_home
    other = tmp_path / "other"
    other.write_text("x")
    link = cli_link.link_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(other)
    assert cli_link.status() is CliLinkStatus.MISMATCH
    assert not cli_link.is_installed()


def test_install_replaces_stale_link(fake_home):
    tmp_path, exe = fake_home
    link = cli_link.link_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "stale")
    # install() must overwrite the stale symlink, not raise.
    cli_link.install()
    assert link.resolve() == exe.resolve()
    assert cli_link.is_installed()


def test_bin_dir_on_path(fake_home, monkeypatch):
    bin_dir = cli_link.bin_dir()
    monkeypatch.setenv("PATH", f"/usr/bin:{bin_dir}:/bin")
    assert cli_link.bin_dir_on_path()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert not cli_link.bin_dir_on_path()


def test_is_supported_follows_frozen(monkeypatch):
    monkeypatch.setattr(cli_link, "is_frozen", lambda: True)
    assert cli_link.is_supported()
    monkeypatch.setattr(cli_link, "is_frozen", lambda: False)
    assert not cli_link.is_supported()
