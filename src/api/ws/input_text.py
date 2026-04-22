import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from src.api.deps import handle_ws_first_message_auth, verify_ws_api_key
from src.core.attachment_store import AttachmentStore, ResolvedAttachment
from src.database.models import LinearIssue as LinearIssueModel

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/input/text")
async def ws_input_text(
    websocket: WebSocket,
    api_key: str | None = Query(None),
) -> None:
    """WebSocket endpoint for receiving user text prompts.

    Clients send JSON messages with natural language prompts.
    The server routes them through the LLM pipeline for tool execution.

    Query Parameters:
        api_key: API key for authentication.

    Input Message Format:
        {
            "type": "prompt",
            "text": "describe this image",
            "session_id": null | "uuid",
            "attachments": [
                {"id": "<attachment_id>", "name": "photo.jpg", "mime_type": "image/jpeg"}
            ]
        }

    The ``attachments`` field is optional. Each entry must reference an ID
    previously returned by ``POST /api/uploads``.

    Response Message Format:
        {
            "type": "ack",
            "session_id": "uuid"
        }
    """
    if api_key is not None:
        await verify_ws_api_key(websocket, api_key)
        await websocket.accept()
    else:
        await websocket.accept()
        if not await handle_ws_first_message_auth(websocket):
            return

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
                    await websocket.send_json({"type": "error", "content": str(e), "code": "PERMISSION_RESPONSE_ERROR"})
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
                    await websocket.send_json({"type": "error", "content": "Empty response", "code": "EMPTY_RESPONSE"})
                    continue
                ir_accepted = bool(message.get("accepted", True))
                try:
                    await prompt_router.send_interactive_response(ir_session_id, ir_text, accepted=ir_accepted)
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json(
                        {"type": "error", "content": str(e), "code": "INTERACTIVE_RESPONSE_ERROR"}
                    )
                continue

            if msg_type == "interrupt_subprocess":
                interrupt_session_id = message.get("session_id")
                if not interrupt_session_id:
                    await websocket.send_json(
                        {"type": "error", "content": "Missing session_id", "code": "MISSING_SESSION_ID"}
                    )
                    continue
                try:
                    await prompt_router.interrupt_subprocess(interrupt_session_id)
                    await websocket.send_json({"type": "ack", "session_id": interrupt_session_id})
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json(
                        {"type": "error", "content": str(e), "code": "INTERRUPT_SUBPROCESS_ERROR"}
                    )
                continue

            if msg_type == "cancel_queued":
                cq_session_id = message.get("session_id")
                cq_queued_id = message.get("queued_id")
                if not cq_session_id or not cq_queued_id:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "content": "Missing session_id or queued_id",
                            "code": "MISSING_QUEUED_ID",
                        }
                    )
                    continue
                session_obj = prompt_router._session_manager.get_session(cq_session_id)
                store = getattr(websocket.app.state, "pending_store", None)
                if session_obj is None or store is None:
                    await websocket.send_json(
                        {
                            "type": "cancel_ack",
                            "session_id": cq_session_id,
                            "queued_id": cq_queued_id,
                            "ok": False,
                            "reason": "not_found",
                        }
                    )
                    continue
                removed = await store.cancel(session_obj, queued_id=cq_queued_id)
                await websocket.send_json(
                    {
                        "type": "cancel_ack",
                        "session_id": cq_session_id,
                        "queued_id": cq_queued_id,
                        "ok": removed is not None,
                        **({} if removed is not None else {"reason": "already_delivered"}),
                    }
                )
                continue

            if msg_type == "edit_queued":
                eq_session_id = message.get("session_id")
                eq_queued_id = message.get("queued_id")
                eq_content = (message.get("content") or "").strip()
                eq_display = message.get("display_content")
                if not eq_session_id or not eq_queued_id:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "content": "Missing session_id or queued_id",
                            "code": "MISSING_QUEUED_ID",
                        }
                    )
                    continue
                if not eq_content:
                    await websocket.send_json(
                        {
                            "type": "edit_ack",
                            "session_id": eq_session_id,
                            "queued_id": eq_queued_id,
                            "ok": False,
                            "reason": "empty",
                        }
                    )
                    continue
                session_obj = prompt_router._session_manager.get_session(eq_session_id)
                store = getattr(websocket.app.state, "pending_store", None)
                if session_obj is None or store is None:
                    await websocket.send_json(
                        {
                            "type": "edit_ack",
                            "session_id": eq_session_id,
                            "queued_id": eq_queued_id,
                            "ok": False,
                            "reason": "not_found",
                        }
                    )
                    continue
                updated = await store.edit(
                    session_obj,
                    queued_id=eq_queued_id,
                    content=eq_content,
                    display_content=eq_display if isinstance(eq_display, str) else eq_content,
                )
                await websocket.send_json(
                    {
                        "type": "edit_ack",
                        "session_id": eq_session_id,
                        "queued_id": eq_queued_id,
                        "ok": updated is not None,
                        **({} if updated is not None else {"reason": "already_delivered"}),
                    }
                )
                continue

            if msg_type == "list_linear_issues":
                import json as _json  # noqa: PLC0415

                db_session_factory = websocket.app.state.db_session_factory
                if db_session_factory is not None:
                    settings = websocket.app.state.settings
                    async with db_session_factory() as db:
                        stmt = (
                            select(LinearIssueModel)
                            .where(LinearIssueModel.backend_id == settings.RCFLOW_BACKEND_ID)
                            .order_by(LinearIssueModel.updated_at.desc())
                        )
                        result = await db.execute(stmt)
                        issue_rows = result.scalars().all()
                        issues_out = [
                            {
                                "id": str(i.id),
                                "linear_id": i.linear_id,
                                "identifier": i.identifier,
                                "title": i.title,
                                "description": i.description,
                                "priority": i.priority,
                                "state_name": i.state_name,
                                "state_type": i.state_type,
                                "assignee_id": i.assignee_id,
                                "assignee_name": i.assignee_name,
                                "team_id": i.team_id,
                                "team_name": i.team_name,
                                "url": i.url,
                                "labels": _json.loads(i.labels or "[]"),
                                "created_at": i.created_at.isoformat() if i.created_at else "",
                                "updated_at": i.updated_at.isoformat() if i.updated_at else "",
                                "synced_at": i.synced_at.isoformat() if i.synced_at else "",
                                "task_id": str(i.task_id) if i.task_id else None,
                            }
                            for i in issue_rows
                        ]
                    await websocket.send_json({"type": "linear_issue_list", "issues": issues_out})
                else:
                    await websocket.send_json({"type": "linear_issue_list", "issues": []})
                continue

            if msg_type == "start_plan_session":
                task_id_str = message.get("task_id")
                plan_project_name: str | None = message.get("project_name") or None
                plan_worktree_path: str | None = message.get("selected_worktree_path") or None
                if not task_id_str:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "content": "Missing task_id",
                            "code": "MISSING_TASK_ID",
                        }
                    )
                    continue
                try:
                    plan_session_id, planning_prompt = await prompt_router.prepare_plan_session(
                        task_id=task_id_str,
                        project_name=plan_project_name,
                        selected_worktree_path=plan_worktree_path,
                    )
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "session_id": plan_session_id,
                            "purpose": "plan",
                        }
                    )
                    # Fire agentic loop as background task (same pattern as "prompt")
                    plan_task = asyncio.create_task(
                        prompt_router.handle_prompt(
                            planning_prompt,
                            plan_session_id,
                            project_name=plan_project_name,
                            selected_worktree_path=plan_worktree_path,
                            task_id=task_id_str,
                        )
                    )
                    background_tasks.add(plan_task)
                    plan_task.add_done_callback(background_tasks.discard)
                except (ValueError, RuntimeError) as e:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "content": str(e),
                            "code": "PLAN_SESSION_ERROR",
                        }
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
            project_name: str | None = message.get("project_name") or None
            selected_worktree_path: str | None = message.get("selected_worktree_path") or None
            prompt_task_id: str | None = message.get("task_id") or None
            # display_text is the clean user message without prepended agent tags.
            # The client sends effectiveText (e.g. "#claude_code <msg>") as text
            # for backend routing, but also sends display_text ("<msg>") for buffer
            # storage and history display so agent-tag prefixes never appear in logs.
            display_text: str | None = message.get("display_text") or None

            # Resolve optional attachments
            resolved_attachments: list[ResolvedAttachment] | None = None
            raw_attachments = message.get("attachments")
            if raw_attachments and isinstance(raw_attachments, list):
                attachment_store: AttachmentStore | None = getattr(websocket.app.state, "attachment_store", None)
                if attachment_store is not None:
                    resolved_attachments = []
                    for att in raw_attachments:
                        if not isinstance(att, dict):
                            continue
                        att_id = att.get("id")
                        if not att_id:
                            continue
                        stored = attachment_store.pop(att_id)
                        if stored is None:
                            logger.warning("Attachment %s not found or expired", att_id)
                            continue
                        resolved_attachments.append(
                            ResolvedAttachment(
                                file_name=stored.file_name,
                                mime_type=stored.mime_type,
                                data=stored.data,
                            )
                        )
                    if not resolved_attachments:
                        resolved_attachments = None

            # Create/resolve session and check whether the agent is currently
            # busy.  Busy sessions enqueue the prompt in the persistent pending
            # queue (see ``Queued User Messages`` in ``Design.md``); the ack
            # carries the assigned ``queued_id`` so the client can pin the
            # message at the bottom of chat until drain.
            result_session_id = prompt_router.ensure_session(session_id)
            session_obj = prompt_router._session_manager.get_session(result_session_id)
            queued_id: str | None = None
            if session_obj is not None:
                queued_id = await prompt_router.enqueue_user_prompt(
                    session_obj,
                    text=text,
                    display_text=display_text,
                    attachments=resolved_attachments,
                    project_name=project_name,
                    selected_worktree_path=selected_worktree_path,
                    task_id=prompt_task_id,
                )
            ack_payload: dict[str, object] = {
                "type": "ack",
                "session_id": result_session_id,
                "queued": queued_id is not None,
            }
            if queued_id is not None:
                ack_payload["queued_id"] = queued_id
            await websocket.send_json(ack_payload)

            # If the prompt was enqueued, delivery happens on the next turn
            # boundary via ``PromptRouter.schedule_pending_drain``.
            if queued_id is not None:
                continue

            # Process prompt in the background
            task = asyncio.create_task(
                prompt_router.handle_prompt(
                    text,
                    result_session_id,
                    attachments=resolved_attachments,
                    project_name=project_name,
                    selected_worktree_path=selected_worktree_path,
                    task_id=prompt_task_id,
                    display_text=display_text,
                )
            )
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/input/text")
