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

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime

    print(f"Generating self-signed certificate: {certfile}")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "RCFlow"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RCFlow"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    certfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    )
    print("Self-signed certificate generated successfully.")


def _cmd_run(args: argparse.Namespace) -> None:
    """Start the RCFlow server (default command)."""
    _check_not_root()
    settings = get_settings()

    _check_port_available(settings.RCFLOW_HOST, settings.RCFLOW_PORT)

    ssl_kwargs: dict[str, str] = {}
    if settings.WSS_ENABLED:
        from src.paths import get_install_dir

        certfile = Path(settings.SSL_CERTFILE) if settings.SSL_CERTFILE else get_install_dir() / "certs" / "cert.pem"
        keyfile = Path(settings.SSL_KEYFILE) if settings.SSL_KEYFILE else get_install_dir() / "certs" / "key.pem"
        _ensure_self_signed_certs(certfile, keyfile)
        ssl_kwargs["ssl_certfile"] = str(certfile)
        ssl_kwargs["ssl_keyfile"] = str(keyfile)
    elif settings.SSL_CERTFILE and settings.SSL_KEYFILE:
        ssl_kwargs["ssl_certfile"] = settings.SSL_CERTFILE
        ssl_kwargs["ssl_keyfile"] = settings.SSL_KEYFILE

    uvicorn.run(
        "src.main:app",
        host=settings.RCFLOW_HOST,
        port=settings.RCFLOW_PORT,
        reload=False,
        **ssl_kwargs,
    )


def _cmd_migrate(args: argparse.Namespace) -> None:
    """Run database migrations to the latest version."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    from src.paths import get_alembic_ini, get_install_dir, get_migrations_dir  # noqa: PLC0415

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


def _cmd_gui(args: argparse.Namespace) -> None:
    """Run RCFlow with a graphical window interface."""
    _check_not_root()
    from src.gui import run_gui  # noqa: PLC0415

    run_gui()


def _cmd_tray(args: argparse.Namespace) -> None:
    """Run RCFlow as a Windows tray application (delegates to GUI mode)."""
    _check_not_root()
    from src.gui import run_gui  # noqa: PLC0415

    run_gui()


def _cmd_version(args: argparse.Namespace) -> None:
    """Print the RCFlow version."""
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    from src.paths import get_install_dir, is_frozen  # noqa: PLC0415

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
    settings = get_settings()
    protocol = "wss" if settings.WSS_ENABLED else "ws"
    logs_dir = get_install_dir() / "logs"
    print("RCFlow Server Info")
    print(f"  Bind address : {settings.RCFLOW_HOST}")
    print(f"  Port         : {settings.RCFLOW_PORT}")
    print(f"  WSS enabled  : {'yes' if settings.WSS_ENABLED else 'no'}")
    print(f"  URL          : {protocol}://{settings.RCFLOW_HOST}:{settings.RCFLOW_PORT}")
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
    run_parser.set_defaults(func=_cmd_run)

    # rcflow migrate [revision]
    migrate_parser = subparsers.add_parser("migrate", help="Run database migrations")
    migrate_parser.add_argument("revision", nargs="?", default="head", help="Target revision (default: head)")
    migrate_parser.set_defaults(func=_cmd_migrate)

    # rcflow gui
    gui_parser = subparsers.add_parser("gui", help="Run with graphical window interface")
    gui_parser.set_defaults(func=_cmd_gui)

    # rcflow tray
    tray_parser = subparsers.add_parser("tray", help="Run as Windows tray application")
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
        # On Windows frozen builds, default to GUI mode so the user sees a UI.
        # Otherwise fall back to plain server mode (Linux, dev, etc.).
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            _cmd_gui(args)
        else:
            _cmd_run(args)
    elif hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
