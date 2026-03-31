"""WebSocket endpoint for interactive terminal sessions.

Multiplexes multiple terminal sessions over a single connection.
Uses JSON text frames for control messages (create, resize, close)
and binary frames for terminal I/O data.

Binary frame format:
    [1 byte direction][16 bytes terminal UUID][payload]
    direction: 0x00 = client→server (input), 0x01 = server→client (output)
"""

import asyncio
import json
import logging
import uuid as uuid_mod

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.api.deps import handle_ws_first_message_auth, verify_ws_api_key
from src.terminal.manager import MAX_TERMINALS_PER_CONNECTION

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/terminal")
async def ws_terminal(
    websocket: WebSocket,
    api_key: str | None = Query(None),
) -> None:
    """WebSocket endpoint for interactive terminal sessions.

    Multiplexes multiple terminal sessions over a single connection.
    Uses JSON text frames for control messages and binary frames for
    terminal I/O data.

    Query Parameters:
        api_key: API key for authentication.

    Control Messages (JSON text frames):
        Client → Server:
            {"type": "create", "terminal_id": "uuid", "cols": 80, "rows": 24}
            {"type": "resize", "terminal_id": "uuid", "cols": 120, "rows": 40}
            {"type": "close", "terminal_id": "uuid"}

        Server → Client:
            {"type": "created", "terminal_id": "uuid"}
            {"type": "closed", "terminal_id": "uuid", "exit_code": 0, "reason": "exited"}
            {"type": "error", "terminal_id": "uuid", "message": "..."}

    I/O Data (binary frames):
        Header: 1 byte direction + 16 bytes terminal UUID
        Payload: raw terminal data (UTF-8 + ANSI escape sequences)
    """
    if api_key is not None:
        await verify_ws_api_key(websocket, api_key)
        await websocket.accept()
    else:
        await websocket.accept()
        if not await handle_ws_first_message_auth(websocket):
            return

    terminal_manager = websocket.app.state.terminal_manager
    connection_terminals: set[str] = set()

    client_id = str(uuid_mod.uuid4())[:8]
    logger.info("Client %s connected to /ws/terminal", client_id)

    # Lock to serialize WebSocket sends (WebSocket is not concurrency-safe)
    send_lock = asyncio.Lock()

    async def send_output(terminal_id: str, data: bytes) -> None:
        """Callback: send PTY output to client as binary frame."""
        try:
            tid_bytes = uuid_mod.UUID(terminal_id).bytes
            frame = b"\x01" + tid_bytes + data
            async with send_lock:
                await websocket.send_bytes(frame)
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def handle_exit(terminal_id: str, exit_code: int | None) -> None:
        """Callback: notify client when PTY process exits."""
        connection_terminals.discard(terminal_id)
        # Remove from manager (it already exited, just clean up the entry)
        terminal_manager._sessions.pop(terminal_id, None)
        try:
            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "closed",
                        "terminal_id": terminal_id,
                        "exit_code": exit_code,
                        "reason": "exited" if exit_code is not None else "killed",
                    }
                )
        except (WebSocketDisconnect, RuntimeError):
            pass

    try:
        while True:
            message = await websocket.receive()

            if "text" in message:
                try:
                    msg = json.loads(message["text"])
                except json.JSONDecodeError:
                    async with send_lock:
                        await websocket.send_json(
                            {"type": "error", "message": "Invalid JSON"}
                        )
                    continue

                msg_type = msg.get("type")
                terminal_id = msg.get("terminal_id")

                if msg_type == "create":
                    if not terminal_id:
                        terminal_id = str(uuid_mod.uuid4())

                    if len(connection_terminals) >= MAX_TERMINALS_PER_CONNECTION:
                        async with send_lock:
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "terminal_id": terminal_id,
                                    "message": f"Maximum {MAX_TERMINALS_PER_CONNECTION} terminals per connection",
                                }
                            )
                        continue

                    cols = msg.get("cols", 80)
                    rows = msg.get("rows", 24)
                    shell = msg.get("shell")
                    cwd = msg.get("cwd")

                    try:
                        tid = terminal_id  # capture for closures

                        await terminal_manager.create_session(
                            terminal_id=tid,
                            cols=cols,
                            rows=rows,
                            shell=shell,
                            cwd=cwd,
                            on_output=lambda data, _tid=tid: send_output(_tid, data),
                            on_exit=lambda code, _tid=tid: handle_exit(_tid, code),
                        )
                        connection_terminals.add(tid)
                        async with send_lock:
                            await websocket.send_json(
                                {"type": "created", "terminal_id": tid}
                            )
                    except Exception as e:
                        logger.error(
                            "Failed to create terminal %s: %s", terminal_id, e
                        )
                        async with send_lock:
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "terminal_id": terminal_id,
                                    "message": str(e),
                                }
                            )

                elif msg_type == "resize":
                    if terminal_id:
                        session = terminal_manager.get_session(terminal_id)
                        if session:
                            session.resize(
                                cols=msg.get("cols", 80),
                                rows=msg.get("rows", 24),
                            )

                elif msg_type == "close":
                    if terminal_id:
                        connection_terminals.discard(terminal_id)
                        await terminal_manager.close_session(terminal_id)
                        async with send_lock:
                            await websocket.send_json(
                                {
                                    "type": "closed",
                                    "terminal_id": terminal_id,
                                    "exit_code": None,
                                    "reason": "closed",
                                }
                            )

            elif "bytes" in message:
                data = message["bytes"]
                if len(data) < 17:
                    continue

                direction = data[0]
                terminal_id_bytes = data[1:17]
                payload = data[17:]

                if direction == 0x00:
                    terminal_id = str(uuid_mod.UUID(bytes=bytes(terminal_id_bytes)))
                    session = terminal_manager.get_session(terminal_id)
                    if session:
                        await session.write(payload)

    except (WebSocketDisconnect, RuntimeError):
        logger.info("Client %s disconnected from /ws/terminal", client_id)
    finally:
        for tid in list(connection_terminals):
            await terminal_manager.close_session(tid)
        connection_terminals.clear()
