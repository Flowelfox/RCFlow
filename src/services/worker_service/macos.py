"""macOS launchd backend for the worker service.

Drives the ``com.rcflow.server`` LaunchAgent through ``launchctl`` using the
modern domain-target syntax (``gui/<uid>/<label>``).  The crucial behaviour is
that :meth:`stop` performs ``launchctl bootout`` — it removes the job from the
domain so launchd's ``KeepAlive`` contract no longer applies and the worker stays
down.  Combined with the crash-only ``KeepAlive`` written by
:func:`config.macos_plist_dict`, this gives "stop always means stop" while real
crashes still recover.
"""

from __future__ import annotations

import contextlib
import logging
import os
import plistlib
import signal
import subprocess
import time
from typing import TYPE_CHECKING

from src.services.worker_service.base import (
    ServiceStatus,
    find_listening_pid,
    port_probe,
    resolve_port,
)
from src.services.worker_service.config import (
    WORKER_SERVICE_LABEL_MACOS,
    macos_plist_dict,
    macos_plist_path,
    worker_log_paths,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

_LABEL = WORKER_SERVICE_LABEL_MACOS


class MacOSWorkerService:
    """``launchctl``-backed controller for the macOS worker LaunchAgent."""

    def __init__(self) -> None:
        self._uid = os.getuid()
        self._domain = f"gui/{self._uid}"
        self._target = f"gui/{self._uid}/{_LABEL}"
        self._plist = macos_plist_path()

    # ── launchctl plumbing ──────────────────────────────────────────────

    def _launchctl(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        cmd = ["launchctl", *args]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.debug("launchctl %s -> rc=%s stderr=%s", " ".join(args), result.returncode, result.stderr.strip())
            if check:
                raise RuntimeError(
                    f"launchctl {' '.join(args)} failed (rc={result.returncode}): {result.stderr.strip()}"
                )
        return result

    def _is_loaded(self) -> bool:
        return self._launchctl("print", self._target).returncode == 0

    def _write_plist(self, *, run_at_load: bool) -> None:
        self._plist.parent.mkdir(parents=True, exist_ok=True)
        data = macos_plist_dict(run_at_load=run_at_load)
        with self._plist.open("wb") as fh:
            plistlib.dump(data, fh)

    def _read_plist(self) -> dict[str, object] | None:
        try:
            with self._plist.open("rb") as fh:
                return plistlib.load(fh)
        except (OSError, plistlib.InvalidFileException):
            return None

    def _migrate_legacy_plist(self) -> None:
        """Rewrite an old ``KeepAlive=true`` plist to the crash-only form.

        Neutralises the historical respawn-after-stop bug on upgrade, preserving
        the existing autostart (``RunAtLoad``) choice.  Idempotent.
        """
        existing = self._read_plist()
        if existing is None:
            return
        if existing.get("KeepAlive") is True:
            run_at_load = bool(existing.get("RunAtLoad", True))
            logger.info("Migrating legacy %s plist (KeepAlive=true -> crash-only)", _LABEL)
            was_loaded = self._is_loaded()
            self._write_plist(run_at_load=run_at_load)
            if was_loaded:
                # Reload so the corrected plist takes effect.
                self._launchctl("bootout", self._target)
                self._launchctl("bootstrap", self._domain, str(self._plist))

    # ── lifecycle ───────────────────────────────────────────────────────

    def install(self, *, enable: bool, port: int | None = None) -> None:
        """Write the canonical plist and bootstrap it.

        ``enable`` is the *autostart-at-login* axis (``RunAtLoad``), separate from
        launchd's enable/disable override (whether the label may load at all).  We
        always clear that override (``launchctl enable``) so a label left
        ``disabled`` by a previous ``uninstall`` can be bootstrapped again — a
        ``disabled`` label makes ``bootstrap`` fail with the opaque
        "5: Input/output error".
        """
        if port is not None:
            from src.config import update_settings_file  # noqa: PLC0415

            update_settings_file({"RCFLOW_PORT": str(port)})
        # Ensure the log directory exists so launchd can open the redirect files.
        for log_path in worker_log_paths():
            log_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_plist(run_at_load=enable)
        self._launchctl("enable", self._target)
        # Bootstrap (load) the job; tolerate "already bootstrapped".
        if not self._is_loaded():
            self._launchctl("bootstrap", self._domain, str(self._plist))

    def uninstall(self) -> None:
        """Stop and delete the plist (does not leave the label disabled).

        Deliberately does **not** ``launchctl disable`` the label: that override
        persists in launchd's user database and would make a later
        install/start fail to bootstrap (rc=5).  Unload + remove the plist is
        enough — with no plist nothing can load.
        """
        self._launchctl("bootout", self._target)
        with contextlib.suppress(OSError):
            self._plist.unlink()

    def start(self) -> None:
        """Enable (clear any stale disable), bootstrap if needed, and kickstart."""
        if not self._plist.exists():
            raise RuntimeError(f"{_LABEL} is not installed; run `rcflow install` first")
        self._migrate_legacy_plist()
        # Clear any persistent launchd "disabled" override (e.g. from an old
        # uninstall) so bootstrap doesn't fail with "5: Input/output error".
        self._launchctl("enable", self._target)
        if not self._is_loaded():
            self._launchctl("bootstrap", self._domain, str(self._plist), check=True)
        self._launchctl("kickstart", self._target, check=True)

    def stop(self) -> None:
        """Stop the worker finally — bootout, then reap any respawn-race orphan.

        ``bootout`` removes the job from the domain and SIGTERMs it, so launchd's
        KeepAlive contract no longer applies and the worker stays stopped.  The
        plist is kept on disk so the enabled-for-next-login choice is preserved.

        A subtlety observed on real macOS: the crash-only KeepAlive
        (``SuccessfulExit=false``) treats the bootout SIGTERM as an abnormal exit
        and can respawn an instance *during* teardown, leaving an orphan that
        launchd no longer tracks.  So after bootout we confirm the port is freed,
        re-booting (if launchd somehow still tracks the job) or directly killing a
        leaked listener — making the stop genuinely final.
        """
        self._launchctl("bootout", self._target)
        port = resolve_port()
        deadline = 3.0  # seconds
        waited = 0.0
        while waited < deadline:
            pid = find_listening_pid(port)
            if pid is None:
                return
            if self._is_loaded():
                # launchd still tracks the job — bootout again (race retry).
                self._launchctl("bootout", self._target)
            else:
                # Orphan from a teardown-race respawn: terminate it directly,
                # escalating to SIGKILL if it does not exit promptly.
                sig = signal.SIGTERM if waited < deadline / 2 else signal.SIGKILL
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.kill(pid, sig)
            time.sleep(0.2)
            waited += 0.2

    def restart(self) -> None:
        """Restart the worker (kickstart -k, or start if unloaded)."""
        if not self._is_loaded():
            self.start()
            return
        self._launchctl("kickstart", "-k", self._target, check=True)

    def enable(self) -> None:
        """Enable autostart at login (RunAtLoad=true + launchctl enable)."""
        self._write_plist(run_at_load=True)
        self._launchctl("enable", self._target)
        if not self._is_loaded():
            self._launchctl("bootstrap", self._domain, str(self._plist))

    def disable(self) -> None:
        """Disable autostart-at-login without stopping or blocking the worker.

        Only flips ``RunAtLoad`` off.  Deliberately does NOT ``launchctl
        disable`` — that override would persist and prevent any later manual
        start/bootstrap (rc=5).  A running worker keeps running; it just won't
        auto-start at the next login.
        """
        if self._plist.exists():
            self._write_plist(run_at_load=False)

    # ── introspection ───────────────────────────────────────────────────

    def _is_enabled(self) -> bool:
        # "Enabled" is the autostart-at-login axis = the plist's RunAtLoad.
        plist = self._read_plist()
        return bool(plist and plist.get("RunAtLoad", False))

    def status(self) -> ServiceStatus:
        """Full state from ``launchctl print`` corroborated by a port probe."""
        self._migrate_legacy_plist()
        installed = self._plist.exists()
        port = resolve_port()
        printed = self._launchctl("print", self._target)
        running = False
        pid: int | None = None
        if printed.returncode == 0:
            for raw in printed.stdout.splitlines():
                line = raw.strip()
                if line.startswith("state ="):
                    running = "running" in line
                elif line.startswith("pid ="):
                    with contextlib.suppress(ValueError):
                        pid = int(line.split("=", 1)[1].strip())
        # Port probe corroborates / covers a worker we don't own.
        if not running and port_probe(port):
            running = True
        if running and pid is None:
            pid = find_listening_pid(port)
        return ServiceStatus(
            installed=installed,
            running=running,
            enabled=self._is_enabled(),
            pid=pid,
            port=port,
            detail=printed.stdout.strip() if printed.returncode == 0 else "",
        )

    def detect(self) -> ServiceStatus:
        """Lightweight adopt probe (port + listening pid), supervisor-independent."""
        port = resolve_port()
        running = port_probe(port)
        return ServiceStatus(
            installed=self._plist.exists(),
            running=running,
            enabled=self._is_enabled(),
            pid=find_listening_pid(port) if running else None,
            port=port,
        )

    def logs(self, *, follow: bool = False, lines: int = 200) -> Iterator[str]:
        """Tail the service stdout log (``follow`` streams indefinitely)."""
        stdout_path, _ = worker_log_paths()
        if not stdout_path.exists():
            return
        args = ["tail"]
        if follow:
            args.append("-F")
        args += ["-n", str(lines), str(stdout_path)]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
        assert proc.stdout is not None  # noqa: S101 — PIPE guarantees a stream
        try:
            for line in proc.stdout:
                yield line.rstrip("\n")
        finally:
            proc.terminate()
            with contextlib.suppress(Exception):
                proc.wait(timeout=2)


def _macos_plist_path() -> Path:  # re-export for tests/back-compat
    return macos_plist_path()
