import argparse
import socket
import sys

import uvicorn

from src.config import get_settings


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
            f"Either stop the existing process or set RCFLOW_PORT to a different port in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        sock.close()


def _cmd_run(args: argparse.Namespace) -> None:
    """Start the RCFlow server (default command)."""
    settings = get_settings()

    _check_port_available(settings.RCFLOW_HOST, settings.RCFLOW_PORT)

    ssl_kwargs: dict[str, str] = {}
    if settings.SSL_CERTFILE and settings.SSL_KEYFILE:
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

    # rcflow version
    version_parser = subparsers.add_parser("version", help="Print version")
    version_parser.set_defaults(func=_cmd_version)

    args = parser.parse_args()

    if args.command is None:
        # Default: run the server (backwards-compatible)
        _cmd_run(args)
    elif hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
