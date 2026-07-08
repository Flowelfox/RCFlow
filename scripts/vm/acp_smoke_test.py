#!/usr/bin/env python3
"""ACP E2E smoke against a live RCFlow worker (VM).

Drives a `#opencode` direct-tool prompt with RCFLOW_OPENCODE_EXECUTOR=acp on
the worker and asserts the ACP relay path end-to-end: agent session banner,
streamed text through the ACP translation, turn completion, clean session end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time

import httpx
import websockets

TURN_TIMEOUT = 180.0
STEP = {"n": 0}


def ok(msg: str) -> None:
    STEP["n"] += 1
    print(f"  PASS [{STEP['n']}] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", file=sys.stderr)
    sys.exit(1)


async def run(base_url: str, api_key: str, verbose: bool) -> None:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    sslctx = None
    if base_url.startswith("https"):
        sslctx = ssl.create_default_context()
        sslctx.check_hostname = False
        sslctx.verify_mode = ssl.CERT_NONE

    async with httpx.AsyncClient(base_url=base_url, verify=False, timeout=15) as http:
        r = await http.get("/api/health")
        assert r.status_code == 200, r.text
        ok("health ok")

        r = await http.get("/api/tools/status", headers={"X-API-Key": api_key})
        assert r.status_code == 200, r.text
        tools = r.json().get("tools", {})
        oc = tools.get("opencode", {})
        assert oc.get("installed"), f"opencode not installed on worker: {oc}"
        ok(f"opencode installed on worker (version {oc.get('current_version')})")

        out_url = f"{ws_base}/ws/output/text?api_key={api_key}"
        in_url = f"{ws_base}/ws/input/text?api_key={api_key}"

        async with (
            websockets.connect(out_url, ssl=sslctx, open_timeout=10) as out_ws,
            websockets.connect(in_url, ssl=sslctx, open_timeout=10) as in_ws,
        ):
            await in_ws.send(
                json.dumps(
                    {
                        "type": "prompt",
                        "text": "#opencode Reply with exactly the word PONG and stop. No tools.",
                        "session_id": None,
                    }
                )
            )
            ack = json.loads(await asyncio.wait_for(in_ws.recv(), timeout=20))
            assert ack.get("type") == "ack" and ack.get("session_id"), f"unexpected ack: {ack}"
            session_id = ack["session_id"]
            ok(f"prompt acked, session {session_id[:8]}…")

            await out_ws.send(json.dumps({"type": "subscribe", "session_id": session_id}))

            saw_banner = saw_text = saw_pong = turn_done = False
            text_seen: list[str] = []
            subprocess_type = None
            events: list[dict] = []
            deadline = time.monotonic() + TURN_TIMEOUT
            while time.monotonic() < deadline and not turn_done:
                try:
                    raw = await asyncio.wait_for(out_ws.recv(), timeout=deadline - time.monotonic())
                except TimeoutError:
                    break
                msg = json.loads(raw)
                events.append(msg)
                if verbose:
                    print(f"    << {json.dumps(msg)[:220]}", flush=True)
                if msg.get("session_id") != session_id:
                    continue
                t = msg.get("type")
                if t == "agent_session_start":
                    saw_banner = True
                elif t == "subprocess_status" and msg.get("subprocess_type"):
                    subprocess_type = msg.get("subprocess_type")
                elif t == "text_chunk" and msg.get("role") != "user":
                    # Agent-origin text only — the echoed user prompt also
                    # contains PONG and must not satisfy the assertions.
                    # Aggregate across chunks: streaming may split the word.
                    content = str(msg.get("content", ""))
                    if content:
                        saw_text = True
                        text_seen.append(content)
                        if "PONG" in "".join(text_seen).upper():
                            saw_pong = True
                elif t == "error":
                    fail(f"server error: {msg.get('code')}: {msg.get('content')}")
                # NOTE: direct-tool mode pushes turn_complete right after the
                # agent *starts* (the tool call returned) — it is NOT the end
                # of the agent's turn. Only agent-side end markers count.
                elif t in ("summary", "session_end_ask", "agent_group_end", "session_end"):
                    turn_done = True

            assert saw_banner, f"no agent_session_start (types: {[e.get('type') for e in events]})"
            ok("agent_session_start banner received")
            assert subprocess_type == "opencode", f"subprocess_type={subprocess_type}"
            ok("subprocess status reports opencode")
            assert saw_text, "no agent text streamed through the ACP relay"
            ok("agent text streamed")
            assert saw_pong, "PONG never appeared in agent output"
            ok("agent answered PONG")
            assert turn_done, "turn never completed"
            ok("turn completed")

            # Follow-up turn: same session, same live ACP process.
            await in_ws.send(
                json.dumps(
                    {
                        "type": "prompt",
                        "text": "Now reply with exactly the word DING and nothing else.",
                        "session_id": session_id,
                    }
                )
            )
            follow_text: list[str] = []
            follow_done = False
            deadline = time.monotonic() + TURN_TIMEOUT
            while time.monotonic() < deadline and not follow_done:
                try:
                    raw = await asyncio.wait_for(out_ws.recv(), timeout=deadline - time.monotonic())
                except TimeoutError:
                    break
                msg = json.loads(raw)
                if verbose:
                    print(f"    << {json.dumps(msg)[:220]}", flush=True)
                if msg.get("session_id") != session_id:
                    continue
                t = msg.get("type")
                if t == "text_chunk" and msg.get("role") != "user":
                    follow_text.append(str(msg.get("content", "")))
                    if "DING" in "".join(follow_text).upper():
                        follow_done = True
                elif t == "error":
                    fail(f"server error on follow-up: {msg.get('code')}: {msg.get('content')}")
            assert follow_done, f"follow-up answer missing; saw: {''.join(follow_text)[:200]!r}"
            ok("follow-up turn answered on the same live ACP session")

            await in_ws.send(json.dumps({"type": "end_session", "session_id": session_id}))
            ended = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(out_ws.recv(), timeout=deadline - time.monotonic())
                except TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "session_end" and msg.get("session_id") == session_id:
                    ended = True
                    break
            assert ended, "no session_end broadcast"
            ok("session ended cleanly")

    print("ACP VM SMOKE PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()
    print(f"ACP smoke against {a.base_url}")
    try:
        asyncio.run(run(a.base_url, a.api_key, a.verbose))
    except AssertionError as exc:
        fail(str(exc))
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
