"""Tests for the unified worker-service controller (config + macOS launchd)."""

from __future__ import annotations

import sys

import pytest

from src.services.worker_service import ServiceStatus, get_controller
from src.services.worker_service import config as cfg
from src.services.worker_service.macos import MacOSWorkerService


class TestCanonicalPlist:
    """The plist is the install contract — pin the safety-critical fields."""

    def test_keepalive_is_crash_only(self):
        plist = cfg.macos_plist_dict(run_at_load=True)
        # The whole point: KeepAlive must NOT be `True` (which respawns after an
        # explicit stop). Crash-only = respawn only on abnormal exit.
        assert plist["KeepAlive"] == {"SuccessfulExit": False}

    def test_keepalive_can_be_disabled(self):
        plist = cfg.macos_plist_dict(run_at_load=False, keep_alive_crash_only=False)
        assert plist["KeepAlive"] is False

    def test_program_arguments_end_with_run(self):
        plist = cfg.macos_plist_dict(run_at_load=True)
        args = plist["ProgramArguments"]
        assert isinstance(args, list)
        assert args[-1] == "run"

    def test_run_at_load_reflects_enable(self):
        assert cfg.macos_plist_dict(run_at_load=True)["RunAtLoad"] is True
        assert cfg.macos_plist_dict(run_at_load=False)["RunAtLoad"] is False

    def test_label_matches_service_label(self):
        assert cfg.macos_plist_dict(run_at_load=True)["Label"] == cfg.WORKER_SERVICE_LABEL_MACOS

    def test_dev_worker_binary_runs_module(self):
        argv, _cwd = cfg.resolve_worker_binary()
        # In dev (not frozen) the launch command runs the package module.
        assert argv[-2:] == ["-m", "src"]


class TestFactory:
    def test_dispatch_per_platform(self):
        assert type(get_controller("darwin")).__name__ == "MacOSWorkerService"
        assert type(get_controller("linux")).__name__ == "SystemdWorkerService"
        assert type(get_controller("win32")).__name__ == "WindowsWorkerService"

    def test_unknown_platform_raises(self):
        with pytest.raises(RuntimeError):
            get_controller("plan9")


class _Recorder:
    """Captures launchctl argv tuples and returns scripted results."""

    def __init__(self, *, print_rc: int = 0, print_out: str = "") -> None:
        self.calls: list[tuple[str, ...]] = []
        self._print_rc = print_rc
        self._print_out = print_out

    def __call__(self, *args: str, check: bool = False):
        import subprocess  # noqa: PLC0415

        self.calls.append(args)
        rc, out = 0, ""
        if args and args[0] == "print":
            rc, out = self._print_rc, self._print_out
        return subprocess.CompletedProcess(args=list(args), returncode=rc, stdout=out, stderr="")

    def verbs(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def macos(monkeypatch, tmp_path):
    """Build a MacOSWorkerService with launchctl + plist path stubbed onto tmp_path."""
    svc = MacOSWorkerService()
    plist = tmp_path / "com.rcflow.server.plist"
    svc._plist = plist
    rec = _Recorder()
    monkeypatch.setattr(svc, "_launchctl", rec)
    return svc, rec, plist


class TestMacOSCommandMapping:
    def test_stop_uses_bootout_then_confirms_port_freed(self, macos, monkeypatch):
        svc, rec, _ = macos
        # No listener after bootout → stop returns immediately (no orphan reap).
        monkeypatch.setattr("src.services.worker_service.macos.find_listening_pid", lambda _p: None)
        svc.stop()
        # bootout removes the job so KeepAlive cannot respawn — "stop is final".
        assert rec.calls == [("bootout", svc._target)]

    def test_stop_reaps_respawn_race_orphan(self, macos, monkeypatch):
        svc, _rec, _ = macos
        # Simulate a teardown-race orphan that launchd no longer tracks: a
        # listener present once, gone after we signal it.
        pids = iter([4242, 4242, None])
        monkeypatch.setattr(
            "src.services.worker_service.macos.find_listening_pid",
            lambda _p: next(pids, None),
        )
        monkeypatch.setattr(svc, "_is_loaded", lambda: False)  # orphan, not tracked
        monkeypatch.setattr("src.services.worker_service.macos.time.sleep", lambda _s: None)
        killed: list[int] = []
        monkeypatch.setattr("src.services.worker_service.macos.os.kill", lambda pid, _sig: killed.append(pid))
        svc.stop()
        assert 4242 in killed  # the leaked listener was terminated

    def test_start_bootstraps_then_kickstarts(self, macos, monkeypatch):
        svc, _rec, plist = macos
        plist.write_bytes(b"")  # exists
        # not loaded -> print returns rc!=0 so bootstrap runs
        monkeypatch.setattr(svc, "_launchctl", _Recorder(print_rc=1))
        monkeypatch.setattr(svc, "_migrate_legacy_plist", lambda: None)
        svc.start()
        verbs = svc._launchctl.verbs()  # type: ignore[attr-defined]
        assert "bootstrap" in verbs
        assert "kickstart" in verbs

    def test_start_without_install_raises(self, macos):
        svc, _rec, plist = macos
        assert not plist.exists()
        with pytest.raises(RuntimeError):
            svc.start()

    def test_install_writes_crashonly_plist_and_enables(self, macos, monkeypatch):
        svc, _rec, plist = macos
        monkeypatch.setattr(svc, "_launchctl", _Recorder(print_rc=1))
        svc.install(enable=True)
        assert plist.exists()
        import plistlib  # noqa: PLC0415

        with plist.open("rb") as fh:
            written = plistlib.load(fh)
        assert written["KeepAlive"] == {"SuccessfulExit": False}
        assert written["RunAtLoad"] is True
        assert "enable" in svc._launchctl.verbs()  # type: ignore[attr-defined]

    def test_uninstall_removes_plist(self, macos):
        svc, rec, plist = macos
        plist.write_bytes(b"x")
        svc.uninstall()
        assert not plist.exists()
        assert "bootout" in rec.verbs()
        assert "disable" in rec.verbs()

    def test_disable_does_not_bootout(self, macos):
        svc, rec, plist = macos
        plist.write_bytes(b"x")
        svc.disable()
        assert "disable" in rec.verbs()
        assert "bootout" not in rec.verbs()  # never stops a running worker

    def test_legacy_keepalive_true_is_migrated(self, macos, monkeypatch):
        svc, _rec, plist = macos
        import plistlib  # noqa: PLC0415

        # Seed an old KeepAlive=true plist (the respawn-bug shape).
        with plist.open("wb") as fh:
            plistlib.dump({"Label": "com.rcflow.server", "KeepAlive": True, "RunAtLoad": True}, fh)
        monkeypatch.setattr(svc, "_launchctl", _Recorder(print_rc=1))
        svc._migrate_legacy_plist()
        with plist.open("rb") as fh:
            fixed = plistlib.load(fh)
        assert fixed["KeepAlive"] == {"SuccessfulExit": False}
        assert fixed["RunAtLoad"] is True  # preserved


class TestStatusProbe:
    def test_running_via_port_probe_when_print_absent(self, macos, monkeypatch):
        svc, _rec, _plist = macos
        monkeypatch.setattr("src.services.worker_service.macos.port_probe", lambda _p: True)
        monkeypatch.setattr(svc, "_migrate_legacy_plist", lambda: None)
        st = svc.status()
        assert isinstance(st, ServiceStatus)
        assert st.running is True

    def test_detect_reports_not_running_when_port_closed(self, macos, monkeypatch):
        svc, _rec, _plist = macos
        monkeypatch.setattr("src.services.worker_service.macos.port_probe", lambda _p: False)
        st = svc.detect()
        assert st.running is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX uid required")
class TestProtocolConformance:
    def test_macos_controller_satisfies_protocol(self):
        from src.services.worker_service.base import WorkerServiceController  # noqa: PLC0415

        assert isinstance(get_controller("darwin"), WorkerServiceController)
