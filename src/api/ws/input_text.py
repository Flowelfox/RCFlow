import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.api.deps import verify_ws_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/input/text")
async def ws_input_text(
    websocket: WebSocket,
    api_key: str = Query(...),
) -> None:
    """WebSocket endpoint for receiving user text prompts.

    Clients send JSON messages with natural language prompts.
    The server routes them through the LLM pipeline for tool execution.

    Query Parameters:
        api_key: API key for authentication.

    Input Message Format:
        {
            "type": "prompt",
            "text": "list all files in the current directory",
            "session_id": null | "uuid"
        }

    Response Message Format:
        {
            "type": "ack",
            "session_id": "uuid"
        }
    """
    await verify_ws_api_key(api_key)
    await websocket.accept()

    prompt_router = websocket.app.state.prompt_router
    background_tasks: set[asyncio.Task[str]] = set()

    logger.info("Client connected to /ws/input/text")

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "content": "Invalid JSON", "code": "INVALID_JSON"})
                continue

            msg_type = message.get("type")

            if msg_type == "end_session":
                end_session_id = message.get("session_id")
                if not end_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                try:
                    await prompt_router.end_session(end_session_id)
                    await websocket.send_json({"type": "ack", "session_id": end_session_id})
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json({"type": "error", "content": str(e), "code": "END_SESSION_ERROR"})
                continue

            if msg_type == "pause_session":
                pause_session_id = message.get("session_id")
                if not pause_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                try:
                    await prompt_router.pause_session(pause_session_id)
                    await websocket.send_json({"type": "ack", "session_id": pause_session_id})
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json({"type": "error", "content": str(e), "code": "PAUSE_SESSION_ERROR"})
                continue

            if msg_type == "resume_session":
                resume_session_id = message.get("session_id")
                if not resume_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                try:
                    await prompt_router.resume_session(resume_session_id)
                    await websocket.send_json({"type": "ack", "session_id": resume_session_id})
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json({"type": "error", "content": str(e), "code": "RESUME_SESSION_ERROR"})
                continue

            if msg_type == "restore_session":
                restore_session_id = message.get("session_id")
                if not restore_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                try:
                    session = await prompt_router.restore_session(restore_session_id)
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "session_id": restore_session_id,
                            "status": session.status.value,
                            "session_type": session.session_type.value,
                        }
                    )
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json({"type": "error", "content": str(e), "code": "RESTORE_SESSION_ERROR"})
                continue

            if msg_type == "dismiss_session_end_ask":
                dismiss_session_id = message.get("session_id")
                if not dismiss_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                try:
                    prompt_router.dismiss_session_end_ask(dismiss_session_id)
                    await websocket.send_json({"type": "ack", "session_id": dismiss_session_id})
                except ValueError as e:
                    await websocket.send_json(
                        {"type": "error", "content": str(e), "code": "DISMISS_SESSION_END_ASK_ERROR"}
                    )
                continue

            if msg_type == "permission_response":
                pr_session_id = message.get("session_id")
                if not pr_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                request_id = message.get("request_id")
                if not request_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing request_id", "code": "MISSING_REQUEST_ID"}
                    )
                    continue
                decision = message.get("decision", "deny")
                scope = message.get("scope", "once")
                path_prefix = message.get("path_prefix")
                try:
                    prompt_router.resolve_permission(pr_session_id, request_id, decision, scope, path_prefix)
                    await websocket.send_json({"type": "ack", "session_id": pr_session_id})
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json(
                        {"type": "error", "content": str(e), "code": "PERMISSION_RESPONSE_ERROR"}
                    )
                continue

            if msg_type == "question_answer":
                qa_session_id = message.get("session_id")
                if not qa_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                # Extract answer text from the message
                answer_text = message.get("text", "")
                if not answer_text:
                    answers = message.get("answers")
                    if isinstance(answers, dict):
                        answer_text = "\n".join(f"{k}: {v}" for k, v in answers.items())
                if not answer_text:
                    await websocket.send_json({"type": "error", "content": "Empty answer", "code": "EMPTY_ANSWER"})
                    continue
                # Send directly to Claude Code stdin (mid-turn interactive response)
                try:
                    await prompt_router.send_interactive_response(qa_session_id, answer_text)
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json(
                        {"type": "error", "content": str(e), "code": "INTERACTIVE_RESPONSE_ERROR"}
                    )
                continue

            if msg_type == "interactive_response":
                ir_session_id = message.get("session_id")
                if not ir_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                ir_text = message.get("text", "").strip()
                if not ir_text:
                    await websocket.send_json(
                        {"type": "error", "content": "Empty response", "code": "EMPTY_RESPONSE"}
                    )
                    continue
                try:
                    await prompt_router.send_interactive_response(ir_session_id, ir_text)
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json(
                        {"type": "error", "content": str(e), "code": "INTERACTIVE_RESPONSE_ERROR"}
                    )
                continue

            if msg_type != "prompt":
                await websocket.send_json(
                    {
                        "type": "error",
                        "content": f"Unknown message type: {msg_type}",
                        "code": "UNKNOWN_MESSAGE_TYPE",
                    }
                )
                continue

            text = message.get("text", "").strip()
            if not text:
                await websocket.send_json({"type": "error", "content": "Empty prompt", "code": "EMPTY_PROMPT"})
                continue

            session_id = message.get("session_id")

            # Create/resolve session and acknowledge immediately so the
            # client can subscribe to the output channel *before* chunks
            # start flowing.
            result_session_id = prompt_router.ensure_session(session_id)
            await websocket.send_json({"type": "ack", "session_id": result_session_id})

            # Process prompt in the background
            task = asyncio.create_task(prompt_router.handle_prompt(text, result_session_id))
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/input/text")
