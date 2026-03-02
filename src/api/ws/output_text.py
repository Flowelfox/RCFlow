import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.api.deps import verify_ws_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/output/text")
async def ws_output_text(
    websocket: WebSocket,
    api_key: str = Query(...),
) -> None:
    """WebSocket endpoint for streaming text responses to clients.

    Clients send subscribe/unsubscribe messages to control which sessions
    they receive output from. On subscribe, the full buffered history is
    replayed, then live updates follow.

    Query Parameters:
        api_key: API key for authentication.

    Client Control Messages:
        {"type": "subscribe", "session_id": "uuid"}
        {"type": "unsubscribe", "session_id": "uuid"}
        {"type": "subscribe_all"}
        {"type": "list_sessions"}

    Server Output Messages:
        {
            "type": "text_chunk" | "tool_start" | "tool_output" | "error" | "session_end",
            "session_id": "uuid",
            "sequence": 42,
            ...
        }
    """
    await verify_ws_api_key(api_key)
    await websocket.accept()

    session_manager = websocket.app.state.session_manager
    subscriber_id = str(uuid.uuid4())

    logger.info("Client %s connected to /ws/output/text", subscriber_id)

    # Auto-subscribe to session metadata updates (title/status changes)
    update_queue = session_manager.subscribe_updates(subscriber_id)

    async def stream_session_updates() -> None:
        """Stream session metadata updates to this WebSocket client."""
        try:
            while True:
                update = await update_queue.get()
                if update is None:
                    break
                await websocket.send_json(update)
        except (WebSocketDisconnect, RuntimeError):
            pass

    update_task = asyncio.create_task(stream_session_updates())

    active_tasks: dict[str, asyncio.Task] = {}

    async def stream_session(session_id: str) -> None:
        """Stream text messages from a session buffer to this WebSocket."""
        session = session_manager.get_session(session_id)
        if session is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "session_id": session_id,
                    "content": f"Session {session_id} not found",
                    "code": "SESSION_NOT_FOUND",
                }
            )
            return

        history_count = len(session.buffer.text_history)
        queue = session.buffer.subscribe_text(subscriber_id)

        try:
            replayed = 0
            while True:
                msg = await queue.get()
                if msg is None:
                    break  # Session ended or unsubscribed
                payload = {
                    "type": msg.message_type.value,
                    "session_id": session_id,
                    "sequence": msg.sequence,
                    **msg.data,
                }
                if replayed < history_count:
                    payload["replay"] = True
                    replayed += 1
                await websocket.send_json(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            session.buffer.unsubscribe_text(subscriber_id)

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
                if session_id:
                    # Cancel existing stream if re-subscribing to same session
                    if session_id in active_tasks:
                        session = session_manager.get_session(session_id)
                        if session:
                            session.buffer.unsubscribe_text(subscriber_id)
                        active_tasks[session_id].cancel()
                        del active_tasks[session_id]
                    task = asyncio.create_task(stream_session(session_id))
                    active_tasks[session_id] = task

            elif msg_type == "unsubscribe":
                session_id = message.get("session_id")
                if session_id and session_id in active_tasks:
                    session = session_manager.get_session(session_id)
                    if session:
                        session.buffer.unsubscribe_text(subscriber_id)
                    active_tasks[session_id].cancel()
                    del active_tasks[session_id]

            elif msg_type == "subscribe_all":
                for session in session_manager.list_active_sessions():
                    if session.id not in active_tasks:
                        task = asyncio.create_task(stream_session(session.id))
                        active_tasks[session.id] = task

            elif msg_type == "list_sessions":
                db_session_factory = websocket.app.state.db_session_factory
                if db_session_factory is not None:
                    async with db_session_factory() as db:
                        all_sessions = await session_manager.list_all_with_archived(db)
                    sessions = [
                        {
                            "session_id": s["session_id"],
                            "status": s["status"],
                            "activity_state": s.get("activity_state", "idle"),
                            "session_type": s["session_type"],
                            "created_at": s["created_at"].isoformat(),
                            "title": s.get("title"),
                        }
                        for s in all_sessions
                    ]
                else:
                    sessions = [
                        {
                            "session_id": s.id,
                            "status": s.status.value,
                            "activity_state": s.activity_state.value,
                            "session_type": s.session_type.value,
                            "created_at": s.created_at.isoformat(),
                            "title": s.title,
                        }
                        for s in session_manager.list_all_sessions()
                    ]
                await websocket.send_json({"type": "session_list", "sessions": sessions})

            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "content": f"Unknown message type: {msg_type}",
                        "code": "UNKNOWN_MESSAGE_TYPE",
                    }
                )

    except WebSocketDisconnect:
        logger.info("Client %s disconnected from /ws/output/text", subscriber_id)
    finally:
        session_manager.unsubscribe_updates(subscriber_id)
        update_task.cancel()
        for task in active_tasks.values():
            task.cancel()
        active_tasks.clear()
