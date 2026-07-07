#!/usr/bin/env python3
"""End-to-end smoke test against a live RCFlow worker.

Exercises the full stack over real TLS + TCP: REST health/info, WebSocket
auth (bad key rejected, good key accepted), a direct-tool-mode prompt
(``#shell_exec echo <marker>``), streamed tool output, explicit session end,
and session persistence via the REST API.

Normally invoked through ``scripts/vm/vm.sh smoke``, which opens an SSH
tunnel to the VM worker and passes ``--base-url``/``--api-key``. Works
against any reachable worker, not just the VM.

Requires the worker to run with ``LLM_PROVIDER=none`` (direct tool mode) or
have the ``shell_exec`` tool available — no LLM API keys are needed.

Exit code 0 = all steps passed; 1 = a step failed (details on stderr).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time
import uuid

import httpx
import websockets

TURN_TIMEOUT = 60.0
STEP = {"n": 0}


def ok(msg: str) -> None:
    STEP["n"] += 1
    print(f"  PASS [{STEP['n']}] {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", file=sys.stderr)
    sys.exit(1)


def insecure_ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def run(base_url: str, api_key: str, verbose: bool) -> None:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    sslctx = insecure_ssl() if base_url.startswith("https") else None

    async with httpx.AsyncClient(base_url=base_url, verify=False, timeout=10) as http:
        # 1. Health (no auth)
        r = await http.get("/api/health")
        assert r.status_code == 200 and r.json().get("status") == "ok", r.text
        ok("GET /api/health → ok")

        # 2. Info (auth)
        r = await http.get("/api/info", headers={"X-API-Key": api_key})
        assert r.status_code == 200, f"/api/info returned {r.status_code}"
        info = r.json()
        ok(f"GET /api/info → version {info.get('version')}, backend {info.get('backend_id', '?')[:8]}…")

        # 3. Bad API key rejected on WebSocket
        try:
            async with websockets.connect(
                f"{ws_base}/ws/input/text?api_key=wrong-key", ssl=sslctx, open_timeout=10
            ) as ws:
                # Server may accept the socket then close it — any received
                # frame or clean close counts as rejection handling.
                await asyncio.wait_for(ws.recv(), timeout=5)
                fail("WebSocket accepted a wrong API key")
        except (TimeoutError, websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosed):
            ok("WebSocket rejects wrong API key")

        # 4. Full prompt round-trip in direct tool mode
        marker = f"rcflow-smoke-{uuid.uuid4().hex[:12]}"
        events: list[dict] = []
        session_id: str | None = None

        out_url = f"{ws_base}/ws/output/text?api_key={api_key}"
        in_url = f"{ws_base}/ws/input/text?api_key={api_key}"

        async with websockets.connect(out_url, ssl=sslctx, open_timeout=10) as out_ws:
            ok("output WebSocket connected")

            async with websockets.connect(in_url, ssl=sslctx, open_timeout=10) as in_ws:
                await in_ws.send(
                    json.dumps({"type": "prompt", "text": f"#shell_exec echo {marker}", "session_id": None})
                )
                ack = json.loads(await asyncio.wait_for(in_ws.recv(), timeout=15))
                assert ack.get("type") == "ack" and ack.get("session_id"), f"unexpected ack: {ack}"
                session_id = ack["session_id"]
                ok(f"prompt acked, session {session_id[:8]}…")

                # Subscribe *after* the ack: `subscribe_all` only covers sessions
                # that exist when it is sent, so a session created afterwards
                # would stream nothing but broadcast session_updates. Explicit
                # subscribe replays the session's history, so no events are lost.
                await out_ws.send(json.dumps({"type": "subscribe", "session_id": session_id}))
                ok("subscribed to session output")

                # Drain output until the tool ran and the turn finished.
                saw_marker = saw_tool_start = turn_done = False
                deadline = time.monotonic() + TURN_TIMEOUT
                while time.monotonic() < deadline and not (saw_marker and turn_done):
                    try:
                        raw = await asyncio.wait_for(out_ws.recv(), timeout=deadline - time.monotonic())
                    except TimeoutError:
                        break
                    msg = json.loads(raw)
                    events.append(msg)
                    if verbose:
                        print(f"    << {json.dumps(msg)[:200]}")
                    if msg.get("session_id") != session_id:
                        continue
                    t = msg.get("type")
                    if t == "tool_start":
                        saw_tool_start = True
                    elif t in ("tool_output", "text_chunk") and marker in str(msg.get("content", "")):
                        saw_marker = True
                    elif t == "error":
                        fail(f"server error during turn: {msg.get('code')}: {msg.get('content')}")
                    elif t in ("turn_complete", "summary", "session_end", "session_end_ask"):
                        turn_done = True

                assert saw_tool_start, f"no tool_start received (got types: {[e.get('type') for e in events]})"
                ok("tool_start received for shell_exec")
                assert saw_marker, f"marker '{marker}' never appeared in tool output"
                ok("tool output contains the echo marker")
                assert turn_done, "turn never completed (no turn_complete/summary/session_end)"
                ok("turn completed")

                # 5. Explicit session end
                await in_ws.send(json.dumps({"type": "end_session", "session_id": session_id}))
                ended = False
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(out_ws.recv(), timeout=deadline - time.monotonic())
                    except TimeoutError:
                        break
                    msg = json.loads(raw)
                    if verbose:
                        print(f"    << {json.dumps(msg)[:200]}")
                    if msg.get("type") == "session_end" and msg.get("session_id") == session_id:
                        ended = True
                        break
                assert ended, "no session_end broadcast after end_session"
                ok("session ended cleanly")

        # 6. Session persisted via REST
        r = await http.get("/api/sessions", headers={"X-API-Key": api_key})
        assert r.status_code == 200, f"/api/sessions returned {r.status_code}"
        body = r.json()
        sessions = body if isinstance(body, list) else body.get("sessions", [])
        match = [s for s in sessions if s.get("session_id") == session_id or s.get("id") == session_id]
        assert match, f"session {session_id} not found in /api/sessions"
        ok(f"session persisted (status: {match[0].get('status', '?')})")

    print("SMOKE TEST PASSED")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. https://127.0.0.1:12345")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--verbose", action="store_true", help="print every WS message received")
    args = parser.parse_args()

    print(f"Smoke test against {args.base_url}")
    try:
        asyncio.run(run(args.base_url, args.api_key, args.verbose))
    except AssertionError as exc:
        fail(str(exc))
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
