import asyncio
import json
import logging
import struct
import uuid as uuid_mod

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.api.deps import verify_ws_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/output/audio")
async def ws_output_audio(
    websocket: WebSocket,
    api_key: str = Query(...),
) -> None:
    """WebSocket endpoint for streaming audio responses to clients.

    Clients send subscribe/unsubscribe JSON messages to control which sessions
    they receive audio from. Audio is sent as binary frames with a header:
        [session_id: 16 bytes UUID][sequence: 4 bytes uint32][opus frame data]

    Query Parameters:
        api_key: API key for authentication.

    Client Control Messages (JSON text frames):
        {"type": "subscribe", "session_id": "uuid"}
        {"type": "unsubscribe", "session_id": "uuid"}
        {"type": "subscribe_all"}

    Server Output: Binary frames with session_id + sequence + audio data.
    """
    await verify_ws_api_key(api_key)
    await websocket.accept()

    session_manager = websocket.app.state.session_manager
    subscriber_id = str(uuid_mod.uuid4())

    logger.info("Client %s connected to /ws/output/audio", subscriber_id)

    active_tasks: dict[str, asyncio.Task] = {}

    async def stream_session_audio(session_id: str) -> None:
        """Stream audio chunks from a session buffer to this WebSocket."""
        session = session_manager.get_session(session_id)
        if session is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "content": f"Session {session_id} not found",
                    "code": "SESSION_NOT_FOUND",
                }
            )
            return

        queue = session.buffer.subscribe_audio(subscriber_id)
        session_uuid_bytes = uuid_mod.UUID(session_id).bytes

        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break

                # Binary frame: [16-byte UUID][4-byte sequence][audio data]
                header = session_uuid_bytes + struct.pack(">I", chunk.sequence)
                await websocket.send_bytes(header + chunk.data)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            session.buffer.unsubscribe_audio(subscriber_id)

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "content": "Invalid JSON", "code": "INVALID_JSON"})
                continue

            msg_type = message.get("type")

            if msg_type == "subscribe":
                session_id = message.get("session_id")
                if session_id and session_id not in active_tasks:
                    task = asyncio.create_task(stream_session_audio(session_id))
                    active_tasks[session_id] = task

            elif msg_type == "unsubscribe":
                session_id = message.get("session_id")
                if session_id and session_id in active_tasks:
                    session = session_manager.get_session(session_id)
                    if session:
                        session.buffer.unsubscribe_audio(subscriber_id)
                    active_tasks[session_id].cancel()
                    del active_tasks[session_id]

            elif msg_type == "subscribe_all":
                for session in session_manager.list_active_sessions():
                    if session.id not in active_tasks:
                        task = asyncio.create_task(stream_session_audio(session.id))
                        active_tasks[session.id] = task

            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "content": f"Unknown message type: {msg_type}",
                        "code": "UNKNOWN_MESSAGE_TYPE",
                    }
                )

    except WebSocketDisconnect:
        logger.info("Client %s disconnected from /ws/output/audio", subscriber_id)
    finally:
        for task in active_tasks.values():
            task.cancel()
        active_tasks.clear()
