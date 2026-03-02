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


def main() -> None:
    parser = argparse.ArgumentParser(prog="rcflow", description="RCFlow action server")
    subparsers = parser.add_subparsers(dest="command")

    # rcflow run
    run_parser = subparsers.add_parser("run", help="Start the RCFlow server")
    run_parser.set_defaults(func=_cmd_run)

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
