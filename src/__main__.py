# ── Startup trace — uses only stdlib builtins available before any other import ──
import datetime as _dt
import os as _os
import sys as _sys

try:
    _tdir = _os.path.expanduser("~/Library/Logs")
    _os.makedirs(_tdir, exist_ok=True)
    with open(_os.path.join(_tdir, "rcflow-worker-trace.log"), "a") as _tf:
        _tf.write(f"{_dt.datetime.now().isoformat()} __main__ module loaded  frozen={getattr(_sys, 'frozen', False)}\n")
except Exception:
    pass
# ────────────────────────────────────────────────────────────────────────────────

import argparse
import ipaddress
import os
import socket
import sys
from pathlib import Path

import uvicorn

from src.config import _get_settings_path, get_settings
from src.paths import get_install_dir


def _check_not_root() -> None:
    """Refuse to start when running as root/sudo.

    Many tools (e.g. Claude Code) refuse to run with elevated privileges for
    security reasons.  Running the entire backend as root is unnecessary and
    dangerous — use a dedicated ``rcflow`` service user instead.

    On Linux the systemd installer already handles this.  On macOS use
    ``scripts/install-macos.sh`` to create a launchd service running as
    a dedicated user.
    """
    if os.name != "posix":
        return  # Windows doesn't have uid 0

    if os.getuid() != 0:
        return  # Not root — all good

    print(
        "ERROR: RCFlow must not run as root or with sudo.\n"
        "\n"
        "Running as root is a security risk and many tools (e.g. Claude Code)\n"
        "refuse to operate under elevated privileges.\n"
        "\n"
        "Instead, run RCFlow as a dedicated unprivileged user:\n"
        "\n"
        "  Linux  — use scripts/install.sh to set up a systemd service\n"
        "           (creates an 'rcflow' user automatically).\n"
        "  macOS  — use scripts/install-macos.sh to set up a launchd service,\n"
        "           or run manually:  sudo -u rcflow ./rcflow\n"
        "\n"
        "To run directly as your own user (development):\n"
        "  Don't use sudo — just run:  python -m src  or  ./rcflow\n",
        file=sys.stderr,
    )
    sys.exit(1)


def _check_port_available(host: str, port: int) -> None:
    """Check if the port is available before starting the server.

    Exits with a clear error message if the port is already in use.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        print(
            f"ERROR: Cannot bind to {host}:{port} — {exc}\n"
            f"\nAnother process is already using port {port}.\n"
            f"Either stop the existing process or set RCFLOW_PORT to a different port in settings.json",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        sock.close()


def _ensure_self_signed_certs(certfile: Path, keyfile: Path) -> None:
    """Generate a self-signed certificate and key if they don't already exist."""
    if certfile.exists() and keyfile.exists():
        return

    import datetime  # noqa: PLC0415

    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    print(f"Generating self-signed certificate: {certfile}")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "RCFlow"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RCFlow"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    certfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    print("Self-signed certificate generated successfully.")


def _install_parent_death_watchdog() -> None:
    """Exit when the GUI process that spawned us is gone.

    The GUI sets ``RCFLOW_PARENT_PID`` to its own pid before spawning this
    server via ``subprocess.Popen``.  If the GUI crashes (e.g. a Cocoa
    re-entrancy after macOS auto-lock / sleep-wake), the child is reparented
    to ``launchd`` / ``init`` and would otherwise keep serving clients
    indefinitely with no UI to stop it.  This watchdog polls the expected
    parent pid every two seconds and sends ``SIGTERM`` to the server when the
    parent is no longer alive so uvicorn can shut down gracefully.

    No-op when ``RCFLOW_PARENT_PID`` is absent (systemd / launchd daemon
    installs do not set it) or invalid.
    """
    raw = os.environ.get("RCFLOW_PARENT_PID")
    if not raw:
        return
    try:
        parent_pid = int(raw)
    except ValueError:
        return
    if parent_pid <= 0:
        return

    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    def _watch() -> None:
        # Poll interval chosen to bound orphan lifetime without measurable
        # load.  Two seconds is small relative to typical GUI-restart delays
        # but large enough to avoid racing a brief launchd respawn.
        while True:
            time.sleep(2.0)
            alive = True
            if sys.platform == "win32":
                try:
                    import ctypes  # noqa: PLC0415

                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # noqa: N806
                    STILL_ACTIVE = 259  # noqa: N806
                    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid)
                    if not handle:
                        alive = False
                    else:
                        try:
                            exit_code = ctypes.c_ulong()
                            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                                alive = False
                            else:
                                alive = exit_code.value == STILL_ACTIVE
                        finally:
                            ctypes.windll.kernel32.CloseHandle(handle)
                except Exception:
                    alive = False
            else:
                try:
                    os.kill(parent_pid, 0)
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    # Parent still exists but is owned by a different uid
                    # (e.g. pid was recycled by another user).  Treat this as
                    # "parent gone" for our purposes since it is no longer our
                    # GUI.
                    alive = False
                except OSError:
                    alive = True
            if not alive:
                try:
                    import signal as _signal  # noqa: PLC0415

                    if sys.platform == "win32":
                        os.kill(os.getpid(), _signal.SIGTERM)
                    else:
                        os.kill(os.getpid(), _signal.SIGTERM)
                except Exception:
                    os._exit(0)
                return

    threading.Thread(target=_watch, name="parent-death-watchdog", daemon=True).start()


def _cmd_run(args: argparse.Namespace) -> None:
    """Start the RCFlow server (default command)."""
    _check_not_root()
    _install_parent_death_watchdog()

    # Apply --upnp / --no-upnp override before Settings is built so the lifespan
    # sees the effective value.  Per-invocation only — not persisted to
    # settings.json (matches how WSS_ENABLED is handled).
    upnp_override = getattr(args, "upnp", None)
    if upnp_override is not None:
        os.environ["UPNP_ENABLED"] = "true" if upnp_override else "false"

    natpmp_override = getattr(args, "natpmp", None)
    if natpmp_override is not None:
        os.environ["NATPMP_ENABLED"] = "true" if natpmp_override else "false"

    settings = get_settings()

    _check_port_available(settings.RCFLOW_HOST, settings.RCFLOW_PORT)

    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    if settings.WSS_ENABLED:
        from src.paths import get_data_dir  # noqa: PLC0415

        certfile = Path(settings.SSL_CERTFILE) if settings.SSL_CERTFILE else get_data_dir() / "certs" / "cert.pem"
        keyfile = Path(settings.SSL_KEYFILE) if settings.SSL_KEYFILE else get_data_dir() / "certs" / "key.pem"
        _ensure_self_signed_certs(certfile, keyfile)
        ssl_certfile = str(certfile)
        ssl_keyfile = str(keyfile)

        # Publish the resolved paths so the lifespan validation in main.py
        # sees them (it re-reads Settings from env vars).
        os.environ["SSL_CERTFILE"] = ssl_certfile
        os.environ["SSL_KEYFILE"] = ssl_keyfile
    elif settings.SSL_CERTFILE and settings.SSL_KEYFILE:
        ssl_certfile = settings.SSL_CERTFILE
        ssl_keyfile = settings.SSL_KEYFILE

    uvicorn.run(
        "src.main:app",
        host=settings.RCFLOW_HOST,
        port=settings.RCFLOW_PORT,
        reload=False,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )


def _cmd_migrate(args: argparse.Namespace) -> None:
    """Run database migrations to the latest version."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    from src.paths import get_alembic_ini, get_migrations_dir  # noqa: PLC0415

    ini_path = get_alembic_ini()
    if not ini_path.exists():
        print(f"ERROR: alembic.ini not found at {ini_path}", file=sys.stderr)
        sys.exit(1)

    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("script_location", str(get_migrations_dir()))
    alembic_cfg.set_main_option("prepend_sys_path", str(get_install_dir()))

    revision = getattr(args, "revision", "head")
    print(f"Running migrations to: {revision}")
    command.upgrade(alembic_cfg, revision)
    print("Migrations complete.")


def _resolve_running_worker_api_key(*, default: str) -> str:
    """Return the API key in use by whichever worker owns the configured port.

    The bundled deb installs a systemd worker that runs as the dedicated
    ``rcflow`` user and stores its API key in ``/opt/rcflow/settings.json``;
    interactive ``rcflow ...`` invocations by other users use a per-user
    settings file under ``~/.local/share/rcflow/`` with a *different* key.
    The launcher must hand the dashboard the running worker's key so it
    authenticates against the actual server.

    Order of preference:
      1. ``/opt/rcflow/settings.json`` if readable (system-wide install).
      2. The ``default`` provided by the caller — usually
         ``settings.RCFLOW_API_KEY`` from ``get_settings()``.
    """
    import json  # noqa: PLC0415

    candidates = [
        Path("/opt/rcflow/settings.json"),
    ]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = data.get("RCFLOW_API_KEY")
        if isinstance(key, str) and key:
            return key
    return default


def _resolve_linux_gui_window_script() -> Path | None:
    """Locate the GTK + WebKit launcher script shipped alongside the binary.

    Frozen builds ship the script under ``<install_dir>/gui/linux_gui_window.py``;
    dev runs use ``<repo>/scripts/linux_gui_window.py``.
    """
    candidates = [
        get_install_dir() / "gui" / "linux_gui_window.py",
        Path(__file__).resolve().parent.parent / "scripts" / "linux_gui_window.py",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _run_linux_browser_dashboard(*, minimized: bool) -> None:
    """Open the worker dashboard as a native Linux app window.

    Starts the worker as a child subprocess if the systemd service is not
    already serving the configured port, polls until ``/health`` responds,
    then launches a small GTK + WebKit window (``scripts/linux_gui_window.py``,
    invoked via the system Python interpreter) that hosts the existing
    ``/dashboard`` HTML so the worker presents a real desktop window with
    the RCFlow icon in the dock — the same UX users get from the
    CustomTkinter window on Windows and the NSStatusBar app on macOS.

    Falls back to launching the URL in a stand-alone browser
    (firefox / chrome / chromium / epiphany), and finally to ``xdg-open``,
    if the GTK/WebKit launcher cannot be loaded (system ``python3-gi`` /
    ``gir1.2-webkit2-4.x`` missing — declared as deb Recommends, so this
    only happens on stripped-down installs).
    """
    import shutil  # noqa: PLC0415
    import socket  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import time  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    settings = get_settings()
    host = settings.RCFLOW_HOST or "0.0.0.0"
    port = int(settings.RCFLOW_PORT)
    scheme = "https" if settings.WSS_ENABLED else "http"
    # Always talk to localhost from the launcher; the configured bind address
    # may be 0.0.0.0 (any interface) but the dashboard is meant for the local
    # user and avoids advertising on the LAN.
    dashboard_host = "127.0.0.1"

    def _port_open() -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((dashboard_host, port))
            except OSError:
                return False
            return True

    started_child: subprocess.Popen[bytes] | None = None
    if not _port_open():
        # No worker on the port — start one in the background and let it
        # outlive this launcher (detached process group).  Skip if the
        # configured host is not 0.0.0.0/127.0.0.1 since the launcher would
        # be unable to talk to a remote instance anyway.
        if host not in {"0.0.0.0", "127.0.0.1", "::"}:
            print(
                f"Worker is configured to bind {host}:{port} which the "
                "launcher cannot reach.  Start the worker manually with "
                "`rcflow run` and reopen the dashboard.",
                file=sys.stderr,
            )
            sys.exit(1)
        argv = [sys.executable, "run"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "src", "run"]
        try:
            started_child = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            print(f"Failed to start worker subprocess: {exc}", file=sys.stderr)
            sys.exit(1)
        # Poll /health until it answers (max ~10s).  The server prints
        # nothing here so we silently wait.
        deadline = time.monotonic() + 10.0
        url = f"{scheme}://{dashboard_host}:{port}/api/health"
        ctx = None
        if scheme == "https":
            import ssl  # noqa: PLC0415

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.0, context=ctx):
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.3)
        else:
            print("Worker did not become reachable in time; aborting.", file=sys.stderr)
            if started_child is not None:
                started_child.terminate()
            sys.exit(1)

    if minimized:
        # Login-autostart path — server is up, do not pop a browser tab.
        print(f"Worker running at {scheme}://{dashboard_host}:{port}/dashboard", file=sys.stderr)
        return

    # When the systemd worker is already on the port it uses its OWN
    # ``/opt/rcflow/settings.json`` (owned by the ``rcflow`` service user),
    # which usually holds a different ``RCFLOW_API_KEY`` than the per-user
    # ``~/.local/share/rcflow/settings.json`` ``get_settings()`` resolved
    # for the launcher.  Send the user the *running* worker's key so the
    # dashboard authenticates instead of greeting them with "API key
    # rejected".  Falls back to the user-level key when the system file
    # is unreadable (no install-time grant) — same UX as before.
    api_key = _resolve_running_worker_api_key(default=settings.RCFLOW_API_KEY or "")
    url = f"{scheme}://{dashboard_host}:{port}/dashboard"
    if api_key:
        url = f"{url}#key={api_key}"

    # Prefer the native GTK + WebKit window (matches Win/macOS UX — the user
    # sees a real desktop window with the RCFlow icon, not a browser tab).
    # Spawn it under the *system* Python so the python3-gi / WebKit2 GIR
    # bindings are available; the frozen interpreter cannot load those C
    # extensions.
    system_python = shutil.which("python3") or shutil.which("python")
    launcher_script = _resolve_linux_gui_window_script()
    if system_python and launcher_script and launcher_script.exists():
        # Scrub PyInstaller bootloader env so the system python3 process
        # loads the host's libgio / libgirepository / WebKit GObject types
        # cleanly.  Inheriting LD_LIBRARY_PATH from the frozen runtime
        # makes ``WebKit2.WebView()`` raise ``TypeError: could not get a
        # reference to type class`` because it pulls bundled .so files
        # built against an older libgobject ABI.
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "LD_LIBRARY_PATH",
                "LD_PRELOAD",
                "PYTHONPATH",
                "PYTHONHOME",
                "GI_TYPELIB_PATH",
                "GIO_MODULE_DIR",
                "GTK_PATH",
                "GTK_EXE_PREFIX",
            }
        }
        try:
            subprocess.Popen(
                [system_python, str(launcher_script), url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=clean_env,
            )
            return
        except OSError as exc:
            print(
                f"Failed to launch native GTK dashboard ({exc}); falling back to a browser tab.",
                file=sys.stderr,
            )

    # Browser fallback for installs missing python3-gi / WebKit GIR bindings.
    direct_browsers = (
        "firefox",
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "epiphany-browser",
    )
    browser_path = next((p for p in (shutil.which(b) for b in direct_browsers) if p), None)
    if browser_path is not None:
        try:
            subprocess.Popen(
                [browser_path, "--new-window", url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            if scheme == "https":
                print(
                    "Opened RCFlow dashboard in a browser. The browser will "
                    "show a self-signed certificate warning the first time — "
                    "click Advanced → Accept the Risk and Continue once and "
                    "the dashboard will load.",
                    file=sys.stderr,
                )
            return
        except OSError as exc:
            print(f"Failed to launch {browser_path} ({exc}); falling back to xdg-open.", file=sys.stderr)
    opener = shutil.which("xdg-open") or shutil.which("gio") or shutil.which("gnome-open")
    if opener is None:
        print(f"Open this URL in your browser: {url}", file=sys.stderr)
        return
    try:
        subprocess.Popen(
            [opener, url] if "xdg-open" in opener else [opener, "open", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(f"Failed to open browser ({exc}). URL: {url}", file=sys.stderr)


def _cmd_gui(args: argparse.Namespace) -> None:
    """Run RCFlow with a graphical window interface.

    On macOS: launches the native menu bar icon + settings panel (Aqua theme).
    On Windows: launches the CustomTkinter dashboard + system tray icon.
    On Linux: launches the browser-based dashboard at ``/dashboard`` because
    PyInstaller's bundled tcl/tk fails the libxcb 1.17+ sequence-number
    assertion on Ubuntu 25.04.  The browser dashboard offers the same
    status / token / log surface as the native windows.

    The ``--minimized`` flag starts the app with the dashboard hidden (tray
    only).  Used by the login autostart entries so rebooting does not pop a
    window in the user's face; they can open the dashboard from the tray
    icon or by launching the app manually.
    """
    _check_not_root()
    minimized = bool(getattr(args, "minimized", False))
    if sys.platform.startswith("linux"):
        _run_linux_browser_dashboard(minimized=minimized)
        return
    if sys.platform == "darwin":
        import datetime  # noqa: PLC0415
        import traceback as _tb  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415

        _trace = _Path.home() / "Library" / "Logs" / "rcflow-worker-trace.log"

        def _t(msg: str) -> None:
            try:
                _trace.parent.mkdir(parents=True, exist_ok=True)
                with _trace.open("a", encoding="utf-8") as _f:
                    _f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
            except OSError:
                pass

        _t(f"_cmd_gui() entered — frozen={getattr(sys, 'frozen', False)} minimized={minimized}")
        try:
            from src.gui.macos import run_gui_macos  # noqa: PLC0415

            _t("src.gui.macos imported OK")
        except Exception:
            _t(f"IMPORT FAILED:\n{_tb.format_exc()}")
            raise

        run_gui_macos(minimized=minimized)
    else:
        from src.gui.windows import run_gui  # noqa: PLC0415

        run_gui(minimized=minimized)


def _cmd_tray(args: argparse.Namespace) -> None:
    """Run RCFlow as a system tray / menu bar application (delegates to GUI mode)."""
    _check_not_root()
    minimized = bool(getattr(args, "minimized", False))
    if sys.platform.startswith("linux"):
        _run_linux_browser_dashboard(minimized=minimized)
        return
    if sys.platform == "darwin":
        from src.gui.macos import run_gui_macos  # noqa: PLC0415

        run_gui_macos(minimized=minimized)
    else:
        from src.gui.windows import run_gui  # noqa: PLC0415

        run_gui(minimized=minimized)


def _cmd_version(args: argparse.Namespace) -> None:
    """Print the RCFlow version."""
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    from src.paths import is_frozen  # noqa: PLC0415

    if is_frozen():
        version_file = get_install_dir() / "VERSION"
        if version_file.exists():
            print(f"rcflow {version_file.read_text().strip()}")
        else:
            print("rcflow (version unknown — frozen build)")
    else:
        try:
            print(f"rcflow {version('rcflow')}")
        except PackageNotFoundError:
            print("rcflow (development — version not installed)")


def _cmd_info(args: argparse.Namespace) -> None:
    """Print server configuration info (bind IP, port, WSS status)."""
    from src.paths import get_data_dir  # noqa: PLC0415

    settings = get_settings()
    protocol = "wss" if settings.WSS_ENABLED else "ws"
    logs_dir = get_data_dir() / "logs"
    print("RCFlow Server Info")
    print(f"  Bind address : {settings.RCFLOW_HOST}")
    print(f"  Port         : {settings.RCFLOW_PORT}")
    print(f"  WSS enabled  : {'yes' if settings.WSS_ENABLED else 'no'}")
    print(f"  URL          : {protocol}://{settings.RCFLOW_HOST}:{settings.RCFLOW_PORT}")
    print(f"  UPnP enabled : {'yes' if settings.UPNP_ENABLED else 'no'}")
    if settings.UPNP_ENABLED:
        print(f"  UPnP lease   : {settings.UPNP_LEASE_SECONDS}s")
    print(f"  NAT-PMP      : {'yes' if settings.NATPMP_ENABLED else 'no'}")
    if settings.NATPMP_ENABLED:
        print(f"  NAT-PMP gw   : {settings.NATPMP_GATEWAY}")
        print(f"  NAT-PMP lease: {settings.NATPMP_LEASE_SECONDS}s")
    print(f"  Settings     : {_get_settings_path()}")
    print(f"  Logs         : {logs_dir}")


def _cmd_api_key(args: argparse.Namespace) -> None:
    """Print the current API key."""
    settings = get_settings()
    if settings.RCFLOW_API_KEY:
        print(settings.RCFLOW_API_KEY)
    else:
        print("No API key configured.", file=sys.stderr)
        sys.exit(1)


def _cmd_set_api_key(args: argparse.Namespace) -> None:
    """Set a new API key value."""
    from src.config import update_settings_file  # noqa: PLC0415

    new_key = args.value
    if not new_key:
        print("ERROR: API key value cannot be empty.", file=sys.stderr)
        sys.exit(1)
    update_settings_file({"RCFLOW_API_KEY": new_key})
    print("API key updated successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="rcflow", description="RCFlow action server")
    subparsers = parser.add_subparsers(dest="command")

    # rcflow run
    run_parser = subparsers.add_parser("run", help="Start the RCFlow server")
    run_parser.add_argument(
        "--upnp",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable (--upnp) or disable (--no-upnp) UPnP port forwarding for this run. "
            "Overrides UPNP_ENABLED in settings.json. Not persisted."
        ),
    )
    run_parser.add_argument(
        "--natpmp",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable (--natpmp) or disable (--no-natpmp) VPN-gateway port forwarding (NAT-PMP) "
            "for this run. Overrides NATPMP_ENABLED in settings.json. Not persisted."
        ),
    )
    run_parser.set_defaults(func=_cmd_run)

    # rcflow migrate [revision]
    migrate_parser = subparsers.add_parser("migrate", help="Run database migrations")
    migrate_parser.add_argument("revision", nargs="?", default="head", help="Target revision (default: head)")
    migrate_parser.set_defaults(func=_cmd_migrate)

    # rcflow gui
    gui_parser = subparsers.add_parser("gui", help="Run with graphical window interface")
    gui_parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start with the dashboard hidden (tray icon only). Used by login autostart.",
    )
    gui_parser.set_defaults(func=_cmd_gui)

    # rcflow tray
    tray_parser = subparsers.add_parser("tray", help="Run as system tray / menu bar application")
    tray_parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start with the dashboard hidden (tray icon only). Used by login autostart.",
    )
    tray_parser.set_defaults(func=_cmd_tray)

    # rcflow version
    version_parser = subparsers.add_parser("version", help="Print version")
    version_parser.set_defaults(func=_cmd_version)

    # rcflow info
    info_parser = subparsers.add_parser("info", help="Show server configuration info")
    info_parser.set_defaults(func=_cmd_info)

    # rcflow api-key
    api_key_parser = subparsers.add_parser("api-key", help="Print the current API key")
    api_key_parser.set_defaults(func=_cmd_api_key)

    # rcflow set-api-key <value>
    set_api_key_parser = subparsers.add_parser("set-api-key", help="Set a new API key")
    set_api_key_parser.add_argument("value", help="The new API key value")
    set_api_key_parser.set_defaults(func=_cmd_set_api_key)

    args = parser.parse_args()

    if args.command is None:
        # Windows / macOS frozen builds default to GUI / menu bar mode so that
        # double-clicking the .app / .exe launches the desktop experience.
        # Everywhere else (Linux service install, dev runs) print help — the
        # worker must be started explicitly with `rcflow run`.
        if sys.platform in ("win32", "darwin") and getattr(sys, "frozen", False):
            _cmd_gui(args)
        else:
            parser.print_help()
            sys.exit(0)
    elif hasattr(args, "func"):
        try:
            args.func(args)
        except KeyboardInterrupt:
            # Ctrl+C from a terminal: exit cleanly without dumping a Python
            # traceback at the user.  Graceful subprocess teardown still runs
            # via the GUI's atexit / SIGTERM handlers and uvicorn's lifespan.
            print("\nInterrupted by user — shutting down.", file=sys.stderr)
            sys.exit(130)  # 128 + SIGINT, the conventional shell exit code
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
