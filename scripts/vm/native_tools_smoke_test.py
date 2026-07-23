#!/usr/bin/env python3
"""Native RCFlow tools E2E against a live worker (VM).

Drives an OpenCode-over-ACP session and makes the agent call each
``python``-executor native tool through the MCP bridge — one tool per turn so
the assertions stay deterministic. Proves the full chain on the *frozen* build:
agent → rcflow-mcp proxy → /api/mcp/call → bridge gate → NativeToolRegistry
(importlib dispatch) → real session/DB side effect.

Coverage:
  * rcflow_session_status   (agent_safe, read-only)   → live session JSON
  * rcflow_notify           (agent_safe)              → NOTIFICATION on the WS
  * rcflow_rename_session   (agent_safe)              → session title changes
  * rcflow_task_create      (GATED — auto-approved)   → new task row, returns id
  * rcflow_task_list        (agent_safe, read-only)   → lists the created task
  * rcflow_task_update      (GATED — auto-approved)   → status → in_progress
  * rcflow_register_artifact(agent_safe)              → artifact registered

Gated tools trip the bridge approval gate, which pushes a ``permission_request``
onto the session WS; the harness auto-approves it (decision=allow, scope=once),
exercising the real permission path rather than bypassing it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import ssl
import sys
import time
from typing import Any

import websockets

TURN_TIMEOUT = 240.0
STEP = {"n": 0}

# Absolute path to a .md file the worker can see (matches the default
# ARTIFACT_INCLUDE_PATTERN of *.md). Created by the vm.sh wrapper before the run.
# Under /opt/rcflow (not /tmp) because the service runs with PrivateTmp=yes.
ARTIFACT_PATH = "/opt/rcflow/rcflow_native_e2e.md"


def ok(msg: str) -> None:
    STEP["n"] += 1
    print(f"  PASS [{STEP['n']}] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"  FAIL {msg}", file=sys.stderr)
    sys.exit(1)


class Turn:
    """Messages collected for a single agent turn."""

    def __init__(self) -> None:
        self.tool_outputs: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.session_updates: list[dict[str, Any]] = []
        self.permissions: list[str] = []
        self.errors: list[str] = []
        self.text: list[str] = []

    def agent_tool_text(self) -> str:
        """Concatenated content of every origin=agent tool_output this turn."""
        return "\n".join(str(m.get("content", "")) for m in self.tool_outputs if m.get("origin") == "agent")


class Harness:
    """Drives the shared ACP session and collects per-turn WS messages."""

    def __init__(self, out_ws: Any, in_ws: Any, verbose: bool) -> None:
        self.out = out_ws
        self.in_ = in_ws
        self.verbose = verbose
        self.session_id: str | None = None

    async def _auto_approve(self, msg: dict[str, Any]) -> None:
        rid = msg.get("request_id")
        tool = msg.get("tool_name")
        print(f"    ↳ auto-approving gated call: {tool} (request {str(rid)[:8]}…)")
        await self.in_.send(
            json.dumps(
                {
                    "type": "permission_response",
                    "session_id": self.session_id,
                    "request_id": rid,
                    "decision": "allow",
                    "scope": "once",
                }
            )
        )

    async def run_turn(self, prompt: str) -> Turn:
        """Send *prompt* and collect messages until the agent turn ends."""
        first = self.session_id is None
        await self.in_.send(json.dumps({"type": "prompt", "text": prompt, "session_id": self.session_id}))
        if first:
            ack = json.loads(await asyncio.wait_for(self.in_.recv(), timeout=20))
            self.session_id = ack["session_id"]
            print(f"  session {self.session_id[:8]}…")
            await self.out.send(json.dumps({"type": "subscribe", "session_id": self.session_id}))

        turn = Turn()
        deadline = time.monotonic() + TURN_TIMEOUT
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(self.out.recv(), timeout=deadline - time.monotonic())
            except TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("session_id") != self.session_id:
                continue
            t = msg.get("type")
            if self.verbose:
                print(f"    << {raw[:160]}")
            if t == "tool_output":
                turn.tool_outputs.append(msg)
                if msg.get("origin") == "agent":
                    print(f"    << bridge tool_output ({msg.get('tool_name')}): {str(msg.get('content'))[:100]}")
            elif t == "notification":
                turn.notifications.append(msg)
            elif t == "session_update":
                turn.session_updates.append(msg)
            elif t == "permission_request":
                turn.permissions.append(str(msg.get("tool_name")))
                await self._auto_approve(msg)
            elif t == "text_chunk" and msg.get("role") != "user":
                turn.text.append(str(msg.get("content", "")))
            elif t == "error":
                turn.errors.append(f"{msg.get('code')}: {msg.get('content')}")
                print(f"    << ERROR {turn.errors[-1]}")
            elif t in ("agent_group_end", "session_end", "session_end_ask", "summary"):
                break
        if turn.errors:
            fail(f"errors during turn: {turn.errors}")
        return turn

    async def end(self) -> None:
        """End the shared session cleanly."""
        if self.session_id:
            await self.in_.send(json.dumps({"type": "end_session", "session_id": self.session_id}))
            await asyncio.sleep(1)


async def run(base_url: str, api_key: str, verbose: bool) -> None:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    sslctx = None
    if base_url.startswith("https"):
        sslctx = ssl.create_default_context()
        sslctx.check_hostname = False
        sslctx.verify_mode = ssl.CERT_NONE

    out_url = f"{ws_base}/ws/output/text?api_key={api_key}"
    in_url = f"{ws_base}/ws/input/text?api_key={api_key}"

    async with (
        websockets.connect(out_url, ssl=sslctx, open_timeout=10, max_size=None) as out_ws,
        websockets.connect(in_url, ssl=sslctx, open_timeout=10) as in_ws,
    ):
        h = Harness(out_ws, in_ws, verbose)
        preamble = (
            "#opencode You are validating RCFlow's native MCP tools. You have an MCP server named "
            "rcflow exposing tools like rcflow_session_status, rcflow_notify, rcflow_rename_session, "
            "rcflow_task_create, rcflow_task_list, rcflow_task_update and rcflow_register_artifact. "
            "For each instruction, call exactly the one requested rcflow tool with the given arguments, "
            "then reply with its raw result text. Never use shell commands. "
        )

        # 1. session_status (read-only, agent_safe) --------------------------
        turn = await h.run_turn(
            preamble + "Now call rcflow_session_status with no arguments and paste its full JSON output."
        )
        text = turn.agent_tool_text()
        if '"session_id"' not in text or '"agent_type"' not in text:
            fail(f"session_status did not return session JSON; got: {text[:300]}")
        ok("rcflow_session_status returned live session JSON")

        # 2. notify (agent_safe) --------------------------------------------
        turn = await h.run_turn("Call rcflow_notify with message 'native tools E2E in progress' and level 'success'.")
        if "Notification sent" not in turn.agent_tool_text():
            fail(f"notify did not confirm; got: {turn.agent_tool_text()[:300]}")
        if not turn.notifications:
            fail("no NOTIFICATION message pushed to the client WS")
        ok("rcflow_notify confirmed + NOTIFICATION delivered on the WS")

        # 3. rename_session (agent_safe) ------------------------------------
        new_title = "Native tools E2E ✅"
        turn = await h.run_turn(f"Call rcflow_rename_session with title '{new_title}'.")
        if "Session renamed to" not in turn.agent_tool_text():
            fail(f"rename_session did not confirm; got: {turn.agent_tool_text()[:300]}")
        renamed = any(u.get("title") == new_title for u in turn.session_updates)
        if not renamed:
            fail("no session_update reflected the new title")
        ok("rcflow_rename_session set + broadcast the new title")

        # 4. task_create (GATED) --------------------------------------------
        subject = "RCFlow native E2E task"
        turn = await h.run_turn(f"Call rcflow_task_create with subject '{subject}'.")
        # Permission tool_name is the bridge's registry name (task_create), while
        # the agent-facing display is rcflow_task_create — match either.
        if not any("task_create" in p for p in turn.permissions):
            fail("task_create was NOT gated — expected a permission_request (missing agent_safe gate)")
        created = turn.agent_tool_text()
        m = re.search(r"Task created:\s*([0-9a-fA-F-]{36})", created)
        if not m:
            fail(f"task_create did not return a task id; got: {created[:300]}")
        task_id = m.group(1)
        ok(f"rcflow_task_create gated → approved → created task {task_id[:8]}…")

        # 5. task_list (read-only, agent_safe) ------------------------------
        turn = await h.run_turn("Call rcflow_task_list with no arguments and paste the full JSON.")
        if subject not in turn.agent_tool_text():
            fail(f"task_list did not include the created task '{subject}'; got: {turn.agent_tool_text()[:400]}")
        if any("task_list" in p for p in turn.permissions):
            fail("task_list was gated — a read-only tool must be agent_safe (no permission_request)")
        ok("rcflow_task_list returned the created task without a permission prompt")

        # 6. task_update (GATED) --------------------------------------------
        turn = await h.run_turn(f"Call rcflow_task_update with task_id '{task_id}' and status 'in_progress'.")
        if not any("task_update" in p for p in turn.permissions):
            fail("task_update was NOT gated — expected a permission_request")
        if "status=in_progress" not in turn.agent_tool_text():
            fail(f"task_update did not confirm in_progress; got: {turn.agent_tool_text()[:300]}")
        ok("rcflow_task_update gated → approved → status advanced to in_progress")

        # 7. register_artifact (agent_safe) ---------------------------------
        turn = await h.run_turn(f"Call rcflow_register_artifact with file_path '{ARTIFACT_PATH}'.")
        art = turn.agent_tool_text()
        if "Artifact registered" not in art and "Artifact updated" not in art:
            fail(f"register_artifact did not register the file; got: {art[:300]}")
        ok("rcflow_register_artifact registered the .md file")

        await h.end()
        print("NATIVE TOOLS E2E PASSED")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.base_url, a.api_key, a.verbose))


if __name__ == "__main__":
    main()
