"""Self-update lifecycle for the RCFlow worker GUI.

Mirrors the Flutter client's update flow: poll the GitHub Releases API,
compare versions, cache the result for 24 h, and surface a download URL
that the GUI hands to the OS installer (NSIS / DMG / xdg-open).

The service is platform/UI-agnostic — ``windows.py`` and ``macos.py``
consume it through ``add_listener`` and marshal callbacks back to the
Tk event loop themselves.  Network I/O and download streaming happen on
a single daemon worker thread; the Tk thread never blocks.

A single ``UpdateService`` instance per process is expected.  In dev
(unfrozen) builds the updater no-ops on auto-check but still allows
manual ``check_now`` / ``download`` for ad-hoc testing.
"""

from __future__ import annotations

import contextlib
import json
import logging
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

GITHUB_LATEST_URL = "https://api.github.com/repos/Flowelfox/RCFlow/releases/latest"
DEFAULT_CACHE_TTL = timedelta(hours=24)
HTTP_TIMEOUT_S = 10
DOWNLOAD_CHUNK = 64 * 1024

# Settings keys persisted to settings.json.
KEY_LAST_CHECK = "RCFLOW_UPDATE_LAST_CHECK"
KEY_CACHED_VERSION = "RCFLOW_UPDATE_CACHED_VERSION"
KEY_CACHED_RELEASE_URL = "RCFLOW_UPDATE_CACHED_RELEASE_URL"
KEY_CACHED_DOWNLOAD_URL = "RCFLOW_UPDATE_CACHED_DOWNLOAD_URL"
KEY_CACHED_ASSET_NAME = "RCFLOW_UPDATE_CACHED_ASSET_NAME"
KEY_DISMISSED_VERSION = "RCFLOW_UPDATE_DISMISSED_VERSION"
KEY_AUTO_CHECK = "RCFLOW_UPDATE_AUTO_CHECK"


class UpdateInfo(NamedTuple):
    """Latest release metadata from GitHub.

    ``download_url`` is None when no asset matches the current
    platform/arch — the caller should fall back to ``release_url``.
    """

    version: str
    release_url: str
    download_url: str | None
    asset_name: str | None
    asset_size: int | None


def normalize_version(value: str) -> str:
    """Strip leading ``v``/``V`` and any ``+build`` suffix.

    Mirrors the client's ``UpdateFetcher.normalizeVersion`` so cached
    versions written by either side compare correctly.
    """
    v = value.strip()
    if v.startswith(("v", "V")):
        v = v[1:]
    plus = v.find("+")
    if plus != -1:
        v = v[:plus]
    return v


def is_newer(a: str, b: str) -> bool:
    """Return True when *a* is strictly newer than *b* by numeric segments.

    ``"1.10.0"`` is correctly treated as newer than ``"1.9.0"``.  Non-numeric
    segments (e.g. ``"1.0.0-rc1"``) coerce to 0 and are otherwise ignored —
    sufficient for the server's release tags which are pure ``MAJOR.MINOR.PATCH``.
    """
    aparts = _parse_parts(a)
    bparts = _parse_parts(b)
    n = max(len(aparts), len(bparts))
    for i in range(n):
        av = aparts[i] if i < len(aparts) else 0
        bv = bparts[i] if i < len(bparts) else 0
        if av > bv:
            return True
        if av < bv:
            return False
    return False


def _parse_parts(version: str) -> list[int]:
    out: list[int] = []
    for part in version.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return out


def asset_suffix(plat: str, arch: str) -> str | None:
    """Return the bundle filename suffix for the current platform/arch.

    Matches the asset naming used by ``scripts/bundle.py`` (e.g.
    ``rcflow-v0.43.0-linux-worker-amd64.deb``).  Returns None when the
    combination has no shipped artifact — the caller should fall back to
    the release page URL.
    """
    plat = plat.lower()
    arch = arch.lower()
    if plat == "linux":
        if arch in ("x86_64", "amd64"):
            return "linux-worker-amd64.deb"
        if arch in ("aarch64", "arm64"):
            return "linux-worker-arm64.deb"
    elif plat == "windows":
        if arch in ("x86_64", "amd64"):
            return "windows-worker-amd64.exe"
    elif plat == "darwin":
        if arch in ("arm64", "aarch64"):
            return "macos-worker-arm64.dmg"
        if arch in ("x86_64", "amd64"):
            return "macos-worker-x86_64.dmg"
    return None


def detect_platform() -> str:
    """Return ``"linux"`` / ``"windows"`` / ``"darwin"`` for the running OS."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


def detect_arch() -> str:
    """Return the running install's CPU arch (e.g. ``x86_64``, ``arm64``).

    macOS Rosetta 2 caveat: an arm64 host running an x86_64 binary reports
    ``x86_64`` here — which is what we want, since the user installed the
    x86_64 build and should receive the x86_64 update artifact.
    """
    return platform.machine() or ""


def resolve_current_version() -> str:
    """Best-effort lookup of the running worker's version string.

    Tries ``importlib.metadata`` first (the dev path); falls back to the
    bundled ``VERSION`` file dropped next to the frozen executable by
    ``scripts/bundle.py``; returns ``""`` when neither is available so the
    caller can short-circuit auto-check in dev/unknown environments.
    """
    try:
        from importlib.metadata import version as _pkg_version  # noqa: PLC0415

        return normalize_version(_pkg_version("rcflow"))
    except Exception:
        pass
    with contextlib.suppress(Exception):
        from src.paths import get_install_dir  # noqa: PLC0415

        version_file = get_install_dir() / "VERSION"
        if version_file.exists():
            return normalize_version(version_file.read_text(encoding="utf-8").strip())
    return ""


# ── Fetcher ────────────────────────────────────────────────────────────────


class UpdateFetcher(Protocol):
    """Pluggable interface so tests can inject a fake without network I/O."""

    def fetch_latest(self) -> UpdateInfo | None: ...


class HttpUpdateFetcher:
    """Production fetcher backed by the GitHub Releases REST API."""

    def __init__(self, url: str = GITHUB_LATEST_URL, *, plat: str | None = None, arch: str | None = None) -> None:
        self._url = url
        self._plat = plat or detect_platform()
        self._arch = arch or detect_arch()

    def fetch_latest(self) -> UpdateInfo | None:
        req = urllib.request.Request(
            self._url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "rcflow-worker",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"GitHub returned HTTP {resp.status}")
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error: {exc.reason}") from exc

        tag = payload.get("tag_name")
        html_url = payload.get("html_url")
        if not isinstance(tag, str) or not isinstance(html_url, str):
            return None

        version = normalize_version(tag)
        assets = payload.get("assets") or []
        download_url, asset_name, asset_size = self._select_asset(assets)

        return UpdateInfo(
            version=version,
            release_url=html_url,
            download_url=download_url,
            asset_name=asset_name,
            asset_size=asset_size,
        )

    def _select_asset(self, assets: list[dict[str, object]]) -> tuple[str | None, str | None, int | None]:
        suffix = asset_suffix(self._plat, self._arch)
        if suffix is None:
            return None, None, None
        for asset in assets:
            name = asset.get("name")
            if isinstance(name, str) and name.endswith(suffix):
                url = asset.get("browser_download_url")
                size = asset.get("size")
                return (
                    url if isinstance(url, str) else None,
                    name,
                    size if isinstance(size, int) else None,
                )
        return None, None, None


# ── Service ────────────────────────────────────────────────────────────────


class UpdateService:
    """Worker-side counterpart of the Flutter client's ``UpdateService``.

    Thread-safe: ``check_now`` / ``maybe_check`` / ``download`` may be invoked
    from any thread and complete on a single daemon worker.  Listeners fire
    on the worker thread — UI integrations must marshal back to Tk via
    ``root.after(0, ...)``.
    """

    def __init__(
        self,
        *,
        current_version: str,
        fetcher: UpdateFetcher | None = None,
        plat: str | None = None,
        arch: str | None = None,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
    ) -> None:
        self._current_version = current_version
        self._plat = plat or detect_platform()
        self._arch = arch or detect_arch()
        self._fetcher = fetcher or HttpUpdateFetcher(plat=self._plat, arch=self._arch)
        self._cache_ttl = cache_ttl

        self._latest: UpdateInfo | None = None
        self._is_checking = False
        self._is_downloading = False
        self._last_error: str | None = None

        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._listeners: list[Callable[[], None]] = []

    # ── Public state ───────────────────────────────────────────────────

    @property
    def current_version(self) -> str:
        return self._current_version

    @property
    def latest(self) -> UpdateInfo | None:
        return self._latest

    @property
    def is_checking(self) -> bool:
        return self._is_checking

    @property
    def is_downloading(self) -> bool:
        return self._is_downloading

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def update_available(self) -> bool:
        if not self._current_version or self._latest is None:
            return False
        return is_newer(self._latest.version, self._current_version)

    @property
    def is_dismissed(self) -> bool:
        if self._latest is None:
            return False
        return _read_setting(KEY_DISMISSED_VERSION) == self._latest.version

    @property
    def show_banner(self) -> bool:
        return self.update_available and not self.is_dismissed

    # ── Listener registration ──────────────────────────────────────────

    def add_listener(self, fn: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[], None]) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(fn)

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn()
            except Exception:
                logger.exception("Update listener raised")

    # ── Lifecycle ──────────────────────────────────────────────────────

    def restore_cached_state(self) -> None:
        """Populate ``latest`` from settings.json synchronously.

        Drops a stale cache when the running version is already at or above
        the cached latest — handles the post-install case where the user
        relaunched after applying the update.  Does NOT notify listeners; UI
        consumers should refresh their initial render after construction.
        """
        cached = _read_setting(KEY_CACHED_VERSION)
        if not cached:
            return
        if self._current_version and not is_newer(cached, self._current_version):
            _clear_cached_state()
            return
        self._latest = UpdateInfo(
            version=cached,
            release_url=_read_setting(KEY_CACHED_RELEASE_URL) or "",
            download_url=_read_setting(KEY_CACHED_DOWNLOAD_URL) or None,
            asset_name=_read_setting(KEY_CACHED_ASSET_NAME) or None,
            asset_size=None,
        )

    def maybe_check(self) -> None:
        """Run a network check unless the cached result is still fresh."""
        last = _read_setting(KEY_LAST_CHECK)
        cached = _read_setting(KEY_CACHED_VERSION)
        if last and cached:
            try:
                last_dt = datetime.fromisoformat(last)
            except ValueError:
                last_dt = None
            if last_dt is not None and (datetime.now(UTC) - last_dt) < self._cache_ttl:
                return
        self.check_now()

    def check_now(self) -> None:
        """Force an immediate network check on the worker thread."""
        with self._lock:
            if self._is_checking:
                return
            self._is_checking = True
            self._last_error = None
        self._notify()

        def _run() -> None:
            try:
                info = self._fetcher.fetch_latest()
            except Exception as exc:
                logger.warning("Update check failed: %s", exc)
                self._last_error = str(exc)
                info = None

            if info is not None:
                self._latest = info
                _write_settings(
                    {
                        KEY_LAST_CHECK: datetime.now(UTC).isoformat(),
                        KEY_CACHED_VERSION: info.version,
                        KEY_CACHED_RELEASE_URL: info.release_url,
                        KEY_CACHED_DOWNLOAD_URL: info.download_url or "",
                        KEY_CACHED_ASSET_NAME: info.asset_name or "",
                    }
                )
                # Clear dismissal when a strictly newer release appears so
                # the banner reappears even if the user dismissed the
                # previous version.
                dismissed = _read_setting(KEY_DISMISSED_VERSION)
                if dismissed and is_newer(info.version, dismissed):
                    _write_settings({KEY_DISMISSED_VERSION: ""})

            with self._lock:
                self._is_checking = False
            self._notify()

        self._worker = threading.Thread(target=_run, daemon=True, name="rcflow-updater")
        self._worker.start()

    def dismiss_current(self) -> None:
        """Persist the current ``latest.version`` as dismissed (banner hides)."""
        if self._latest is None:
            return
        _write_settings({KEY_DISMISSED_VERSION: self._latest.version})
        self._notify()

    def open_release_page(self) -> None:
        if self._latest is None or not self._latest.release_url:
            return
        with contextlib.suppress(Exception):
            webbrowser.open(self._latest.release_url)

    # ── Download + install ─────────────────────────────────────────────

    def download(
        self,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        on_done: Callable[[Path], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Download the platform-specific asset to a temp path on the worker thread.

        Atomic write: streams to ``<dest>.partial`` then renames on success.
        Reuses an existing complete download with the matching expected size.
        Callbacks fire on the worker thread.
        """
        info = self._latest
        if info is None or not info.download_url:
            on_error("No download available for this platform.")
            return

        with self._lock:
            if self._is_downloading:
                return
            self._is_downloading = True
        self._notify()

        def _run() -> None:
            assert info is not None
            try:
                dest = self._download_path(info)
                if info.asset_size is not None and dest.exists() and dest.stat().st_size == info.asset_size:
                    on_done(dest)
                    return
                self._stream_download(info, dest, on_progress)
                on_done(dest)
            except Exception as exc:
                logger.warning("Update download failed: %s", exc)
                on_error(str(exc))
            finally:
                with self._lock:
                    self._is_downloading = False
                self._notify()

        threading.Thread(target=_run, daemon=True, name="rcflow-updater-dl").start()

    def _download_path(self, info: UpdateInfo) -> Path:
        cache_dir = _download_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        name = info.asset_name or f"rcflow-update-{info.version}{_default_ext(self._plat)}"
        return cache_dir / name

    def _stream_download(
        self,
        info: UpdateInfo,
        dest: Path,
        on_progress: Callable[[int, int], None] | None,
    ) -> None:
        partial = dest.with_suffix(dest.suffix + ".partial")
        with contextlib.suppress(OSError):
            partial.unlink()

        req = urllib.request.Request(info.download_url or "", headers={"User-Agent": "rcflow-worker"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            total = info.asset_size
            if total is None:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit():
                    total = int(length)
            received = 0
            with partial.open("wb") as out:
                while True:
                    chunk = resp.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
                    if on_progress is not None and total:
                        try:
                            on_progress(received, total)
                        except Exception:
                            logger.exception("download progress callback raised")

        if total is not None and received != total:
            with contextlib.suppress(OSError):
                partial.unlink()
            raise RuntimeError(f"Download truncated: got {received} bytes, expected {total}")
        partial.replace(dest)

    def launch_installer(self, path: Path) -> None:
        """Hand the downloaded artifact to the OS installer.

        - Windows: ``os.startfile`` runs the NSIS bootstrapper.
        - macOS: ``open`` mounts the DMG in Finder.
        - Linux: ``xdg-open`` invokes the distro package GUI (gdebi, gnome-software).

        The worker process keeps running — the installer prompts the user to
        close the worker when it needs to overwrite the binary.
        """
        if not path.exists():
            raise FileNotFoundError(str(path))
        if self._plat == "windows":
            import os  # noqa: PLC0415

            os.startfile(str(path))  # ty:ignore[unresolved-attribute]  # Windows-only
        elif self._plat == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


# ── Settings I/O helpers ────────────────────────────────────────────────────


def _read_setting(key: str) -> str:
    """Read a single setting from settings.json; return ``""`` when absent."""
    try:
        from src.config import _get_settings_path  # noqa: PLC0415
    except Exception:
        return ""
    path = _get_settings_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    value = data.get(key, "")
    return str(value) if value is not None else ""


def _write_settings(updates: dict[str, str]) -> None:
    try:
        from src.config import update_settings_file  # noqa: PLC0415
    except Exception:
        return
    update_settings_file(updates)


def _clear_cached_state() -> None:
    _write_settings(
        {
            KEY_CACHED_VERSION: "",
            KEY_CACHED_RELEASE_URL: "",
            KEY_CACHED_DOWNLOAD_URL: "",
            KEY_CACHED_ASSET_NAME: "",
            KEY_DISMISSED_VERSION: "",
        }
    )


def _download_cache_dir() -> Path:
    """Per-platform cache directory for downloaded installers."""
    plat = detect_platform()
    if plat == "windows":
        import os  # noqa: PLC0415

        base = os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home())
        return Path(base) / "rcflow-updates"
    if plat == "darwin":
        return Path.home() / "Library" / "Caches" / "rcflow" / "updates"
    return Path.home() / ".cache" / "rcflow" / "updates"


def _default_ext(plat: str) -> str:
    if plat == "windows":
        return ".exe"
    if plat == "darwin":
        return ".dmg"
    return ".deb"


def cleanup_partial_downloads(max_age: timedelta = timedelta(days=1)) -> None:
    """Remove ``*.partial`` files older than *max_age* from the cache dir.

    Called once at GUI startup to garbage-collect stalled downloads from
    previous sessions.  Best-effort — any I/O error is swallowed.
    """
    cache_dir = _download_cache_dir()
    if not cache_dir.exists():
        return
    cutoff = time.time() - max_age.total_seconds()
    for partial in cache_dir.glob("*.partial"):
        with contextlib.suppress(OSError):
            if partial.stat().st_mtime < cutoff:
                partial.unlink()
