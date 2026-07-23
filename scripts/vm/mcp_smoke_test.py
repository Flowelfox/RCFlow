#!/usr/bin/env python3
"""Live MCP-bridge E2E: OpenCode-over-ACP calls mcp__rcflow__system_info.

Asserts the full chain: session/new mcp_servers param → rcflow-mcp proxy →
/api/mcp/tools + /api/mcp/call with the per-session token → bridge dispatch →
tool_output with origin=agent on the WebSocket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import sys
import time

import websockets

TURN_TIMEOUT = 240.0


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", file=sys.stderr)
    sys.exit(1)


async def run(base_url: str, api_key: str) -> None:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    sslctx = None
    if base_url.startswith("https"):
        sslctx = ssl.create_default_context()
        sslctx.check_hostname = False
        sslctx.verify_mode = ssl.CERT_NONE

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
                    "text": (
                        "#opencode You have an MCP server named rcflow with a tool called system_info. "
                        "Call the rcflow system_info tool with category 'os' and tell me what it returns. "
                        "Do not use shell commands."
                    ),
                    "session_id": None,
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(in_ws.recv(), timeout=20))
        session_id = ack["session_id"]
        print(f"  session {session_id[:8]}…")
        await out_ws.send(json.dumps({"type": "subscribe", "session_id": session_id}))

        saw_agent_tool_output = False
        agent_text: list[str] = []
        errors: list[str] = []
        deadline = time.monotonic() + TURN_TIMEOUT
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(out_ws.recv(), timeout=deadline - time.monotonic())
            except TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("session_id") != session_id:
                continue
            t = msg.get("type")
            if t == "tool_output" and msg.get("origin") == "agent":
                print(f"  << bridge tool_output ({msg.get('tool_name')}): {str(msg.get('content'))[:120]}")
                if msg.get("tool_name") == "system_info":
                    if msg.get("is_error"):
                        fail(f"bridge tool call errored: {str(msg.get('content'))[:300]}")
                    saw_agent_tool_output = True
            elif t == "tool_start":
                print(f"  << tool_start: {msg.get('tool_name')} origin={msg.get('origin')}")
            elif t == "text_chunk" and msg.get("role") != "user":
                agent_text.append(str(msg.get("content", "")))
            elif t == "error":
                errors.append(f"{msg.get('code')}: {msg.get('content')}")
                print(f"  << ERROR {errors[-1]}")
            elif t in ("session_end_ask", "agent_group_end", "session_end", "summary"):
                break

        joined = "".join(agent_text)
        print(f"  agent said: {joined[:300]}")
        await in_ws.send(json.dumps({"type": "end_session", "session_id": session_id}))
        await asyncio.sleep(1)

        if errors:
            fail(f"errors during turn: {errors}")
        if not saw_agent_tool_output:
            fail("no origin=agent tool_output for system_info — bridge call never reached the worker")
        print("MCP BRIDGE E2E PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    a = p.parse_args()
    asyncio.run(run(a.base_url, a.api_key))


if __name__ == "__main__":
    main()
