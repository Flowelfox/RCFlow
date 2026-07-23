"""Scripted ACP agent for executor integration tests.

Speaks raw newline-delimited JSON-RPC 2.0 on stdio — deliberately *not* built
on the ``acp`` SDK so the tests exercise RCFlow's client-side parsing against
the actual wire format, independent of SDK helper churn.

Behaviour is driven by keywords in the prompt text:

- ``USE_TOOL``  — emits a tool_call (pending) + tool_call_update (completed)
- ``ASK_PERM``  — issues ``session/request_permission`` mid-turn, then emits a
  text chunk ``PERM:<outcome-json>`` so the test can assert the round-trip
- ``SHOW_MCP``  — emits a text chunk ``MCP:<json of mcpServers from session/new>``
- ``EMIT_ALL``  — emits thought, plan, usage, available_commands updates
- ``FAIL_TURN`` — responds to the prompt with a JSON-RPC error
- ``SLOW``      — waits 10s before finishing (cancel/timeout tests)

Every prompt otherwise streams one ``agent_message_chunk`` ("hello from fake
agent") and finishes with ``stopReason: end_turn`` (or ``cancelled`` after a
``session/cancel``).
"""

import json
import sys
import threading
import time

_out_lock = threading.Lock()
_next_id = 1000
_pending: dict[int, dict] = {}
_pending_lock = threading.Lock()
_pending_events: dict[int, threading.Event] = {}
_mcp_servers: list = []
_cancelled = False


def send(msg: dict) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    send({"jsonrpc": "2.0", "method": method, "params": params})


def update(session_id: str, upd: dict) -> None:
    notify("session/update", {"sessionId": session_id, "update": upd})


def request(method: str, params: dict) -> dict:
    global _next_id
    with _pending_lock:
        _next_id += 1
        rid = _next_id
        event = threading.Event()
        _pending_events[rid] = event
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    event.wait(timeout=30)
    with _pending_lock:
        _pending_events.pop(rid, None)
        return _pending.pop(rid, {})


def handle_prompt(rid: int, params: dict) -> None:
    global _cancelled
    _cancelled = False
    sid = params.get("sessionId", "")
    text = " ".join(block.get("text", "") for block in params.get("prompt", []) if isinstance(block, dict))

    if "FAIL_TURN" in text:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "scripted failure"}})
        return

    update(
        sid,
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "hello from fake agent"},
            "messageId": "m1",
        },
    )

    if "SHOW_MCP" in text:
        update(
            sid,
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "MCP:" + json.dumps(_mcp_servers)},
            },
        )

    if "EMIT_ALL" in text:
        update(
            sid,
            {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "thinking..."}},
        )
        update(
            sid,
            {
                "sessionUpdate": "plan",
                "entries": [{"content": "step one", "priority": "medium", "status": "pending"}],
            },
        )
        update(
            sid,
            {"sessionUpdate": "usage_update", "used": 123, "size": 1000, "cost": {"amount": 0.5, "currency": "USD"}},
        )
        update(
            sid,
            {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [{"name": "fake-cmd", "description": "a fake command"}],
            },
        )

    if "USE_TOOL" in text:
        update(
            sid,
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "tc1",
                "title": "write",
                "kind": "edit",
                "status": "pending",
                "rawInput": {"filePath": "/tmp/fake.txt"},
                "locations": [{"path": "/tmp/fake.txt"}],
            },
        )
        update(
            sid,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc1",
                "status": "completed",
                "title": "write",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "wrote the file"}},
                ],
            },
        )

    if "ASK_PERM" in text:
        outcome = request(
            "session/request_permission",
            {
                "sessionId": sid,
                "toolCall": {"toolCallId": "tc-perm", "title": "dangerous_tool", "rawInput": {"arg": 1}},
                "options": [
                    {"optionId": "opt-allow", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "opt-reject", "name": "Reject", "kind": "reject_once"},
                ],
            },
        )
        update(
            sid,
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "PERM:" + json.dumps(outcome)},
            },
        )

    if "SLOW" in text:
        for _ in range(100):
            if _cancelled:
                break
            time.sleep(0.1)

    stop = "cancelled" if _cancelled else "end_turn"
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": stop}})


def main() -> None:
    global _mcp_servers, _cancelled
    session_counter = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)

        # Response to one of our own requests (request_permission)
        if "method" not in msg and "id" in msg:
            with _pending_lock:
                rid = msg["id"]
                _pending[rid] = msg.get("result", msg.get("error", {}))
                event = _pending_events.get(rid)
            if event:
                event.set()
            continue

        method = msg.get("method", "")
        rid = msg.get("id")

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {"loadSession": True},
                    },
                }
            )
        elif method == "session/new":
            session_counter += 1
            _mcp_servers = msg.get("params", {}).get("mcpServers", [])
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": f"fake-sess-{session_counter}"}})
        elif method == "session/load":
            _mcp_servers = msg.get("params", {}).get("mcpServers", [])
            sid = msg.get("params", {}).get("sessionId", "")
            update(
                sid,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "REPLAY:previous conversation"},
                },
            )
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/prompt":
            threading.Thread(target=handle_prompt, args=(rid, msg.get("params", {})), daemon=True).start()
        elif method == "session/cancel":
            _cancelled = True
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"nope: {method}"}})


if __name__ == "__main__":
    main()
