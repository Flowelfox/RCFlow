"""``rcflow-mcp`` — stdio MCP server proxying to the RCFlow worker.

Spawned by Codex (registered in the managed ``CODEX_HOME/config.toml``) as an
MCP server. Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout and forwards
``tools/list`` / ``tools/call`` to the worker's ``/api/mcp/*`` endpoints.

Connection parameters come from the process environment, inherited from the
Codex subprocess whose env RCFlow fully controls:

- ``RCFLOW_MCP_URL``   — worker base URL (defaults to ``http://127.0.0.1:8765``)
- ``RCFLOW_MCP_TOKEN`` — per-session bridge token issued at agent spawn

Deliberately stdlib-only (no ``mcp`` package, no httpx): the protocol surface
is three methods, and keeping the proxy dependency-free means the PyInstaller
bundle and the venv entrypoint stay trivial. Tool calls run on worker threads
so a slow RCFlow tool doesn't block concurrent requests; stdout writes are
serialised by a lock.
"""

from __future__ import annotations

import contextlib
import json
import os
import ssl
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "rcflow", "version": "1.0.0"}
_HTTP_TIMEOUT = 660.0  # generous — bounded by the tool's own executor timeout server-side

_stdout_lock = threading.Lock()


def _base_url() -> str:
    return os.environ.get("RCFLOW_MCP_URL", "http://127.0.0.1:8765").rstrip("/")


def _token() -> str:
    return os.environ.get("RCFLOW_MCP_TOKEN", "")


def _ssl_context() -> ssl.SSLContext | None:
    """Unverified TLS context for https worker URLs.

    The worker serves a self-signed certificate on loopback by default;
    authentication is the per-session bearer token, not the certificate.
    """
    if not _base_url().startswith("https"):
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _worker_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call one worker endpoint; raises ``RuntimeError`` with a readable message."""
    req = urllib.request.Request(
        f"{_base_url()}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "X-RCFlow-MCP-Token": _token(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        with contextlib.suppress(Exception):
            detail = json.loads(e.read().decode()).get("detail", "")
        raise RuntimeError(f"RCFlow worker returned {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"RCFlow worker unreachable at {_base_url()}: {e.reason}") from e


def _write_message(message: dict[str, Any]) -> None:
    line = json.dumps(message, separators=(",", ":"))
    with _stdout_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _result(request_id: Any, result: dict[str, Any]) -> None:
    _write_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> None:
    _write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _handle_initialize(request_id: Any, params: dict[str, Any]) -> None:
    # Echo the client's protocol version when given; ours is the fallback.
    version = params.get("protocolVersion") or PROTOCOL_VERSION
    _result(
        request_id,
        {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        },
    )


def _handle_tools_list(request_id: Any) -> None:
    try:
        data = _worker_request("GET", "/api/mcp/tools")
    except RuntimeError as e:
        _error(request_id, -32000, str(e))
        return
    _result(request_id, {"tools": data.get("tools", [])})


def _handle_tools_call(request_id: Any, params: dict[str, Any]) -> None:
    name = params.get("name", "")
    arguments = params.get("arguments") or {}
    try:
        data = _worker_request("POST", "/api/mcp/call", {"tool": name, "arguments": arguments})
    except RuntimeError as e:
        # Transport/auth failure — surface as an in-band tool error so the
        # agent sees what went wrong instead of a protocol fault.
        _result(request_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        return
    _result(
        request_id,
        {
            "content": [{"type": "text", "text": data.get("content", "")}],
            "isError": bool(data.get("is_error")),
        },
    )


def _dispatch(message: dict[str, Any]) -> None:
    method = message.get("method", "")
    request_id = message.get("id")
    params = message.get("params") or {}

    if request_id is None:
        # Notification (e.g. "notifications/initialized") — nothing to answer.
        return
    if method == "initialize":
        _handle_initialize(request_id, params)
    elif method == "tools/list":
        _handle_tools_list(request_id)
    elif method == "tools/call":
        _handle_tools_call(request_id, params)
    elif method == "ping":
        _result(request_id, {})
    else:
        _error(request_id, -32601, f"Method not found: {method}")


def serve(stdin: Any = None) -> None:
    """Run the newline-delimited JSON-RPC loop until stdin closes."""
    stdin = stdin or sys.stdin
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="rcflow-mcp") as pool:
        for raw_line in stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _error(None, -32700, "Parse error")
                continue
            # tools/call can be slow (real tool execution) — run every request
            # off-thread so concurrent calls don't queue behind it.
            pool.submit(_dispatch, message)


def main() -> None:
    """Console entrypoint for ``rcflow-mcp``."""
    if not _token():
        print("rcflow-mcp: RCFLOW_MCP_TOKEN not set — refusing to start", file=sys.stderr)
        raise SystemExit(2)
    serve()


if __name__ == "__main__":
    main()
