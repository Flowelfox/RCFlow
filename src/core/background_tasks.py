"""Background task methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
fire-and-forget background operations: LLM call logging, session archiving,
title generation, task creation/updates, summary generation, and artifact
scanning.

Used as a mixin class — ``PromptRouter`` inherits from
``BackgroundTasksMixin`` to gain these methods.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from src.core.buffer import MessageType
from src.models.db import Artifact as ArtifactModel
from src.models.db import LLMCall
from src.models.db import Session as SessionModel
from src.models.db import Task as TaskModel
from src.models.db import TaskSession as TaskSessionModel

if TYPE_CHECKING:
    from src.core.llm import TurnUsage
    from src.core.session import ActiveSession

logger = logging.getLogger(__name__)


class BackgroundTasksMixin:
    """Mixin providing background task methods for PromptRouter."""

    # --- LLM call logging ---

    def _fire_log_task(
        self,
        *,
        session_id: str,
        usage: TurnUsage,
        has_tool_calls: bool,
        request_messages: list[dict[str, Any]],
        response_text: str | None,
    ) -> None:
        """Schedule a fire-and-forget background task to log an LLM call to the database."""
        if self._db_session_factory is None or self._llm is None:  # ty:ignore[unresolved-attribute]
            return
        task = asyncio.create_task(
            self._log_llm_call(
                session_id=session_id,
                usage=usage,
                has_tool_calls=has_tool_calls,
                request_messages=request_messages,
                response_text=response_text,
            )
        )
        self._pending_log_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_log_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _log_llm_call(
        self,
        *,
        session_id: str,
        usage: TurnUsage,
        has_tool_calls: bool,
        request_messages: list[dict[str, Any]],
        response_text: str | None,
    ) -> None:
        """Write a single LLM call record to the database. Never raises."""
        assert self._db_session_factory is not None  # ty:ignore[unresolved-attribute]
        try:
            async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
                row = LLMCall(
                    session_id=uuid.UUID(session_id),
                    message_id=usage.message_id,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=usage.cache_creation_input_tokens,
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                    started_at=usage.started_at,
                    ended_at=usage.ended_at,
                    stop_reason=usage.stop_reason,
                    has_tool_calls=has_tool_calls,
                    request_messages=request_messages,
                    response_text=response_text,
                    service_tier=usage.service_tier,
                    inference_geo=usage.inference_geo,
                )
                db.add(row)
                await db.commit()
                logger.debug("Logged LLM call %s for session %s", usage.message_id, session_id)
        except Exception:
            logger.exception("Failed to log LLM call for session %s", session_id)

    # --- Session row pre-creation ---

    async def _ensure_session_row_in_db(self, session: ActiveSession) -> None:
        """Create a stub sessions row in the DB if one does not already exist.

        Telemetry tables (session_turns, tool_calls) reference sessions.id via a
        foreign key. Sessions are normally archived to the DB only when they
        complete, but telemetry events are written during the active lifetime of
        the session. This method eagerly creates the sessions row so FK
        constraints are satisfied from the first telemetry insert.

        The archive_session path updates all fields on completion, so the stub
        row is always superseded by the final values.
        """
        if self._db_session_factory is None:  # ty:ignore[unresolved-attribute]
            return
        session_uuid = uuid.UUID(session.id)
        backend_id = self._settings.RCFLOW_BACKEND_ID if self._settings else ""  # ty:ignore[unresolved-attribute]
        try:
            async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
                existing = await db.get(SessionModel, session_uuid)
                if existing is None:
                    db.add(
                        SessionModel(
                            id=session_uuid,
                            backend_id=backend_id,
                            created_at=session.created_at,
                            session_type=session.session_type.value,
                            status=session.status.value,
                            main_project_path=session.main_project_path,
                            metadata_=session.metadata,
                        )
                    )
                    await db.commit()
                elif existing.main_project_path != session.main_project_path:
                    # Update main_project_path if it was set after the initial stub write.
                    existing.main_project_path = session.main_project_path
                    await db.commit()
        except Exception:
            logger.exception("Failed to pre-create session row for %s", session.id)

    # --- Session archiving ---

    def _fire_archive_task(self, session_id: str) -> None:
        """Schedule a fire-and-forget background task to archive a session to the database."""
        # Snapshot permission rules into metadata before archiving
        session = self._session_manager.get_session(session_id)  # ty:ignore[unresolved-attribute]
        if session is not None and session.permission_manager is not None:
            session.metadata["permission_rules"] = session.permission_manager.get_rules_snapshot()

        if self._db_session_factory is None:  # ty:ignore[unresolved-attribute]
            return
        task = asyncio.create_task(self._archive_session(session_id))
        self._pending_archive_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_archive_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _archive_session(self, session_id: str) -> None:
        """Archive a completed session to the database and optionally extract artifacts. Never raises."""
        assert self._db_session_factory is not None  # ty:ignore[unresolved-attribute]
        try:
            async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
                await self._session_manager.archive_session(session_id, db)  # ty:ignore[unresolved-attribute]

            # Extract artifacts from session messages if auto-scan is enabled
            if self._artifact_scanner and self._settings and self._settings.ARTIFACT_AUTO_SCAN:  # ty:ignore[unresolved-attribute]
                try:
                    new_count = await self._artifact_scanner.scan(session_id)  # ty:ignore[unresolved-attribute]
                    if new_count > 0:
                        await self._broadcast_artifact_list()
                except Exception:
                    logger.exception("Failed to extract artifacts from session %s", session_id)
        except Exception:
            logger.exception("Failed to archive session %s", session_id)

    # --- Summary generation ---

    def _fire_summary_task(self, session: ActiveSession, text: str, *, push_session_end_ask: bool = False) -> None:
        """Schedule a background task to summarize the result text and push it to the session buffer."""
        if self._llm is None:  # ty:ignore[unresolved-attribute]
            # No LLM — skip summary, but still push SESSION_END_ASK if requested
            if push_session_end_ask:
                session.buffer.push_text(
                    MessageType.SESSION_END_ASK,
                    {"session_id": session.id},
                )
            return
        task = asyncio.create_task(self._summarize_and_push(session, text, push_session_end_ask=push_session_end_ask))
        self._pending_summary_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_summary_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _summarize_and_push(
        self, session: ActiveSession, text: str, *, push_session_end_ask: bool = False
    ) -> None:
        """Generate a concise summary and push it to the session buffer. Never raises."""
        try:
            summary = await self._llm.summarize(text)  # ty:ignore[unresolved-attribute]
            session.buffer.push_text(
                MessageType.SUMMARY,
                {
                    "session_id": session.id,
                    "content": summary,
                },
            )
        except Exception:
            logger.exception("Failed to generate summary for session %s", session.id)
        finally:
            if push_session_end_ask:
                session.buffer.push_text(
                    MessageType.SESSION_END_ASK,
                    {"session_id": session.id},
                )

    # --- Title generation ---

    def _fire_title_task(self, session: ActiveSession, user_text: str, assistant_text: str) -> None:
        """Schedule a background task to generate a session title."""
        if self._llm is None:  # ty:ignore[unresolved-attribute]
            # Direct tool mode: set title from truncated user text
            title = user_text[:50]
            if len(user_text) > 50:
                space_idx = title.rfind(" ")
                if space_idx > 20:
                    title = title[:space_idx]
                title += "..."
            session.title = title
            return
        task = asyncio.create_task(self._generate_and_set_title(session, user_text, assistant_text))
        self._pending_title_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_title_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _generate_and_set_title(self, session: ActiveSession, user_text: str, assistant_text: str) -> None:
        """Generate a title and assign it to the session. Never raises."""
        assert self._llm is not None  # ty:ignore[unresolved-attribute]
        try:
            title = await self._llm.generate_title(user_text, assistant_text)  # ty:ignore[unresolved-attribute]
            session.title = title
            logger.info("Generated title for session %s: %s", session.id, title)
        except Exception:
            logger.exception("Failed to generate title for session %s", session.id)
        finally:
            # Ensure the session always gets *some* title even if the LLM call
            # failed or was cancelled, so it never persists as NULL.
            if session.title is None:
                fallback = user_text[:50]
                if len(user_text) > 50:
                    space_idx = fallback.rfind(" ")
                    if space_idx > 20:
                        fallback = fallback[:space_idx]
                    fallback += "..."
                session.title = fallback
                logger.info("Set fallback title for session %s: %s", session.id, fallback)

    # --- Task creation/update agents ---

    def _fire_task_creation_task(self, session: ActiveSession, user_text: str, assistant_text: str) -> None:
        """Schedule a background task to extract or match tasks from the session."""
        if self._llm is None:  # ty:ignore[unresolved-attribute]
            return
        task = asyncio.create_task(self._create_tasks_from_session(session, user_text, assistant_text))
        self._pending_task_creation_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_task_creation_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _create_tasks_from_session(
        self,
        session: ActiveSession,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """Extract or match tasks for this session. Never raises."""
        try:
            if self._db_session_factory is None or self._settings is None:  # ty:ignore[unresolved-attribute]
                return

            backend_id = self._settings.RCFLOW_BACKEND_ID  # ty:ignore[unresolved-attribute]

            # Fetch existing non-done tasks for matching
            async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
                stmt = select(TaskModel).where(
                    TaskModel.backend_id == backend_id,
                    TaskModel.status.in_(["todo", "in_progress", "review"]),
                )
                result = await db.execute(stmt)
                existing = [
                    {
                        "task_id": str(t.id),
                        "title": t.title,
                        "description": t.description or "",
                        "status": t.status,
                    }
                    for t in result.scalars().all()
                ]

            # Ask LLM to extract/match
            llm_result = await self._llm.extract_or_match_tasks(user_text, assistant_text, existing)  # ty:ignore[unresolved-attribute]
            new_tasks = llm_result.get("new_tasks") or []
            attach_ids = llm_result.get("attach_task_ids") or []

            attached_task_ids: list[str] = []
            from datetime import UTC  # noqa: PLC0415
            from datetime import datetime as dt  # noqa: PLC0415

            async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
                # Ensure session row exists in DB (it may not be archived yet)
                session_uuid = uuid.UUID(session.id)
                existing_session = await db.get(SessionModel, session_uuid)
                if existing_session is None:
                    db.add(
                        SessionModel(
                            id=session_uuid,
                            backend_id=backend_id,
                            created_at=session.created_at,
                            ended_at=session.ended_at,
                            session_type=session.session_type.value,
                            status=session.status.value,
                            title=session.title,
                            metadata_={},
                        )
                    )
                    await db.flush()

                # Attach existing tasks
                for tid in attach_ids:
                    try:
                        task_uuid = uuid.UUID(tid)
                    except ValueError:
                        continue
                    task = await db.get(TaskModel, task_uuid)
                    if task is None:
                        continue
                    # Create link if not exists
                    existing_link = await db.execute(
                        select(TaskSessionModel).where(
                            TaskSessionModel.task_id == task_uuid,
                            TaskSessionModel.session_id == session_uuid,
                        )
                    )
                    if existing_link.scalar_one_or_none() is None:
                        link = TaskSessionModel(
                            task_id=task_uuid,
                            session_id=session_uuid,
                        )
                        db.add(link)
                    # Auto-promote to in_progress if not already
                    if task.status in ("todo", "review"):
                        task.status = "in_progress"
                        task.updated_at = dt.now(UTC)
                    attached_task_ids.append(tid)

                # Create new tasks
                for new_t in new_tasks:
                    title = (new_t.get("title") or "")[:300]
                    if not title:
                        continue
                    description = new_t.get("description")
                    now = dt.now(UTC)
                    task = TaskModel(
                        backend_id=backend_id,
                        title=title,
                        description=description,
                        status="in_progress",
                        source="ai",
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(task)
                    await db.flush()  # get task.id
                    link = TaskSessionModel(
                        task_id=task.id,
                        session_id=session_uuid,
                    )
                    db.add(link)
                    attached_task_ids.append(str(task.id))

                await db.commit()

                # Broadcast task updates
                for tid in attached_task_ids:
                    try:
                        task_uuid = uuid.UUID(tid)
                        task = await db.get(TaskModel, task_uuid)
                        if task is not None:
                            # Build task dict with session refs
                            sess_stmt = (
                                select(TaskSessionModel, SessionModel)
                                .join(SessionModel, TaskSessionModel.session_id == SessionModel.id)
                                .where(TaskSessionModel.task_id == task.id)
                            )
                            sess_result = await db.execute(sess_stmt)
                            sessions_data = []
                            for ts_row, sess_row in sess_result.all():
                                sessions_data.append(
                                    {
                                        "session_id": str(sess_row.id),
                                        "title": sess_row.title,
                                        "status": sess_row.status,
                                        "attached_at": ts_row.attached_at.isoformat() if ts_row.attached_at else "",
                                    }
                                )
                            task_data = {
                                "task_id": str(task.id),
                                "title": task.title,
                                "description": task.description,
                                "status": task.status,
                                "source": task.source,
                                "created_at": task.created_at.isoformat() if task.created_at else "",
                                "updated_at": task.updated_at.isoformat() if task.updated_at else "",
                                "sessions": sessions_data,
                            }
                            self._session_manager.broadcast_task_update(task_data)  # ty:ignore[unresolved-attribute]
                    except Exception:
                        logger.exception("Failed to broadcast task update for %s", tid)

            # Store on session for the update agent
            session.metadata["attached_task_ids"] = attached_task_ids
            logger.info(
                "Task creation for session %s: %d attached, %d new",
                session.id,
                len(attach_ids),
                len(new_tasks),
            )

        except Exception:
            logger.exception("Failed to create/match tasks for session %s", session.id)

    def _fire_task_update_task(self, session: ActiveSession, session_result_text: str) -> None:
        """Schedule a background task to update tasks based on session results."""
        if self._llm is None:  # ty:ignore[unresolved-attribute]
            return
        task_ids = session.metadata.get("attached_task_ids", [])
        if not task_ids:
            return
        session.metadata["_task_update_fired"] = True
        task = asyncio.create_task(self._update_tasks_from_session(session, session_result_text, task_ids))
        self._pending_task_update_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_task_update_tasks.discard)  # ty:ignore[unresolved-attribute]

    def _fire_task_update_on_session_end(self, session: ActiveSession) -> None:
        """Fire task update when a session ends, if not already fired by a tool result."""
        if self._llm is None:  # ty:ignore[unresolved-attribute]
            return
        if session.metadata.get("_task_update_fired"):
            return
        result_text = self._extract_session_context(session)
        if result_text:
            self._fire_task_update_task(session, result_text)

    @staticmethod
    def _extract_session_context(session: ActiveSession) -> str:
        """Extract a context summary from the session's conversation history for task evaluation."""
        parts: list[str] = []
        for msg in session.conversation_history:
            role = msg.get("role", "")
            content = msg.get("content")
            if role not in ("user", "assistant"):
                continue
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            else:
                continue
            if text.strip():
                parts.append(f"{role}: {text.strip()}")
        # Limit to last ~4000 chars to avoid huge LLM calls
        combined = "\n".join(parts)
        if len(combined) > 4000:
            combined = combined[-4000:]
        return combined

    async def _update_tasks_from_session(
        self,
        session: ActiveSession,
        session_result_text: str,
        task_ids: list[str],
    ) -> None:
        """Update tasks based on session results. Never raises."""
        from sqlite3 import OperationalError as SQLiteOperationalError  # noqa: PLC0415

        try:
            if self._db_session_factory is None:  # ty:ignore[unresolved-attribute]
                return

            from datetime import UTC  # noqa: PLC0415
            from datetime import datetime as dt  # noqa: PLC0415

            async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
                for tid in task_ids:
                    try:
                        task_uuid = uuid.UUID(tid)
                    except ValueError:
                        continue

                    task = await db.get(TaskModel, task_uuid)
                    if task is None:
                        continue

                    # Ask LLM to evaluate
                    result = await self._llm.evaluate_task_status(  # ty:ignore[unresolved-attribute]
                        task.title,
                        task.description,
                        task.status,
                        session_result_text,
                    )
                    new_status = result.get("status", task.status)
                    new_description = result.get("description", task.description)

                    changed = False
                    # Validate transition and enforce AI can't set done
                    if new_status != task.status and new_status != "done":
                        from src.api.http import VALID_TASK_TRANSITIONS  # noqa: PLC0415

                        allowed = VALID_TASK_TRANSITIONS.get(task.status, set())
                        if new_status in allowed:
                            task.status = new_status
                            changed = True
                    if new_description and new_description != task.description:
                        task.description = new_description
                        changed = True

                    if changed:
                        task.updated_at = dt.now(UTC)
                        await db.commit()

                        # Build and broadcast
                        sess_stmt = (
                            select(TaskSessionModel, SessionModel)
                            .join(SessionModel, TaskSessionModel.session_id == SessionModel.id)
                            .where(TaskSessionModel.task_id == task.id)
                        )
                        sess_result = await db.execute(sess_stmt)
                        sessions_data = []
                        for ts_row, sess_row in sess_result.all():
                            sessions_data.append(
                                {
                                    "session_id": str(sess_row.id),
                                    "title": sess_row.title,
                                    "status": sess_row.status,
                                    "attached_at": ts_row.attached_at.isoformat() if ts_row.attached_at else "",
                                }
                            )
                        task_data = {
                            "task_id": str(task.id),
                            "title": task.title,
                            "description": task.description,
                            "status": task.status,
                            "source": task.source,
                            "created_at": task.created_at.isoformat() if task.created_at else "",
                            "updated_at": task.updated_at.isoformat() if task.updated_at else "",
                            "sessions": sessions_data,
                        }
                        self._session_manager.broadcast_task_update(task_data)  # ty:ignore[unresolved-attribute]

            logger.info("Task update for session %s: checked %d tasks", session.id, len(task_ids))

        except (asyncio.CancelledError, SQLiteOperationalError):
            logger.debug("Task update for session %s aborted (shutdown)", session.id)
        except Exception:
            logger.exception("Failed to update tasks for session %s", session.id)

    # --- Artifact scanning ---

    def _fire_realtime_artifact_scan(self, session: ActiveSession) -> None:
        """Schedule a fire-and-forget background task to scan conversation history for artifacts."""
        if self._artifact_scanner is None:  # ty:ignore[unresolved-attribute]
            return
        history = list(session.conversation_history)
        project_path = Path(session.main_project_path) if session.main_project_path else None
        task = asyncio.create_task(self._realtime_artifact_scan(session.id, history, project_path))
        self._pending_archive_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_archive_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _realtime_artifact_scan(
        self,
        session_id: str,
        conversation_history: list[dict],
        project_path: Path | None,
    ) -> None:
        """Extract artifacts from in-memory conversation history. Never raises."""
        assert self._artifact_scanner is not None  # ty:ignore[unresolved-attribute]
        try:
            new_count, updated_count = await self._artifact_scanner.scan_from_history(  # ty:ignore[unresolved-attribute]
                session_id, conversation_history, project_path
            )
            if new_count > 0 or updated_count > 0:
                await self._broadcast_artifact_list()
        except Exception:
            logger.exception("Real-time artifact scan failed for session %s", session_id)

    def _fire_text_artifact_scan(self, session: ActiveSession, texts: list[str]) -> None:
        """Schedule a fire-and-forget background task to scan text strings for artifacts."""
        if self._artifact_scanner is None or not self._settings or not self._settings.ARTIFACT_AUTO_SCAN:  # ty:ignore[unresolved-attribute]
            return
        project_path = Path(session.main_project_path) if session.main_project_path else None
        task = asyncio.create_task(self._text_artifact_scan(session.id, texts, project_path))
        self._pending_archive_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_archive_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _text_artifact_scan(self, session_id: str, texts: list[str], project_path: Path | None) -> None:
        """Extract artifacts from raw text strings. Never raises."""
        assert self._artifact_scanner is not None  # ty:ignore[unresolved-attribute]
        try:
            new_count, updated_count = await self._artifact_scanner.scan_texts(  # ty:ignore[unresolved-attribute]
                session_id, texts, project_path
            )
            if new_count > 0 or updated_count > 0:
                await self._broadcast_artifact_list()
        except Exception:
            logger.exception("Real-time text artifact scan failed for session %s", session_id)

    @staticmethod
    def _resolve_artifact_project(file_path: str, projects_dirs: list[Path]) -> str | None:
        """Determine which project an artifact belongs to based on its file path.

        Checks if the artifact's path falls under any subdirectory of the
        configured project directories. Returns the project directory name,
        or ``None`` if the artifact is not inside any project.
        """
        try:
            artifact_path = Path(file_path).resolve()
        except (OSError, ValueError):
            return None
        for projects_dir in projects_dirs:
            try:
                rel = artifact_path.relative_to(projects_dir)
            except ValueError:
                continue
            # The first component of the relative path is the project name
            parts = rel.parts
            if parts:
                return parts[0]
        return None

    def _enrich_artifact_dict(self, artifact_data: dict[str, Any]) -> dict[str, Any]:
        """Add ``project_name`` to an artifact dict based on its file path."""
        projects_dirs = self._settings.projects_dirs if self._settings else []  # ty:ignore[unresolved-attribute]
        artifact_data["project_name"] = self._resolve_artifact_project(
            artifact_data.get("file_path", ""), projects_dirs
        )
        return artifact_data

    # --- Plan finalization ---

    def _fire_plan_finalization_task(self, session: ActiveSession) -> None:
        """Schedule plan artifact registration after a plan session ends (any reason)."""
        if session.metadata.get("session_purpose") != "plan":
            return
        task = asyncio.create_task(self._finalize_plan_session(session))
        self._pending_plan_finalization_tasks.add(task)  # ty:ignore[unresolved-attribute]
        task.add_done_callback(self._pending_plan_finalization_tasks.discard)  # ty:ignore[unresolved-attribute]

    async def _finalize_plan_session(self, session: ActiveSession) -> None:
        """Register the plan file as an artifact and link it to the task. Never raises."""
        try:
            plan_path_str = session.metadata.get("plan_output_path")
            task_id_str = session.metadata.get("task_id")
            if not plan_path_str or not task_id_str:
                return

            plan_path = Path(plan_path_str)
            if not plan_path.exists():
                logger.warning(
                    "Plan session %s ended but plan file not found at %s",
                    session.id,
                    plan_path,
                )
                return

            if self._db_session_factory is None or self._settings is None:  # ty:ignore[unresolved-attribute]
                return

            backend_id = self._settings.RCFLOW_BACKEND_ID  # ty:ignore[unresolved-attribute]
            task_uuid = uuid.UUID(task_id_str)
            stat = plan_path.stat()
            now = datetime.now(UTC)

            # Ensure the plan directory exists before inserting (defensive)
            plan_path.parent.mkdir(parents=True, exist_ok=True)

            task_obj: TaskModel | None = None
            task_dict: dict | None = None
            artifact_id: uuid.UUID | None = None

            async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
                # Upsert the artifact record.
                # Race condition note: ArtifactScanner may have already inserted
                # this file (if the LLM printed the path in its output). Handle
                # the (backend_id, file_path) UniqueConstraint by attempting a
                # select first; if absent, insert; if IntegrityError on insert,
                # select again.
                stmt = select(ArtifactModel).where(
                    ArtifactModel.backend_id == backend_id,
                    ArtifactModel.file_path == str(plan_path),
                )
                result = await db.execute(stmt)
                artifact = result.scalar_one_or_none()

                if artifact is None:
                    artifact = ArtifactModel(
                        backend_id=backend_id,
                        file_path=str(plan_path),
                        file_name=plan_path.name,
                        file_extension=plan_path.suffix,
                        file_size=stat.st_size,
                        mime_type="text/markdown",
                        file_exists=True,
                        discovered_at=now,
                        modified_at=now,
                        session_id=uuid.UUID(session.id),
                    )
                    db.add(artifact)
                    try:
                        await db.flush()  # generates artifact.id before commit
                    except Exception:
                        # Another writer (ArtifactScanner) beat us — re-fetch
                        await db.rollback()
                        result2 = await db.execute(stmt)
                        artifact = result2.scalar_one()
                else:
                    artifact.file_size = stat.st_size
                    artifact.file_exists = True
                    artifact.modified_at = now
                    artifact.session_id = uuid.UUID(session.id)

                artifact_id = artifact.id

                # Link to task
                task_obj = await db.get(TaskModel, task_uuid)
                if task_obj is not None:
                    task_obj.plan_artifact_id = artifact_id
                    task_obj.updated_at = now

                # Build broadcast payload BEFORE commit so SQLAlchemy attributes
                # are still accessible (commit() expires all loaded attributes).
                if task_obj is not None:
                    task_dict = {
                        "task_id": str(task_obj.id),
                        "title": task_obj.title,
                        "description": task_obj.description,
                        "status": task_obj.status,
                        "source": task_obj.source,
                        "created_at": task_obj.created_at.isoformat() if task_obj.created_at else "",
                        "updated_at": task_obj.updated_at.isoformat() if task_obj.updated_at else "",
                        "plan_artifact_id": str(task_obj.plan_artifact_id) if task_obj.plan_artifact_id else None,
                        "sessions": [],  # client refreshes sessions on demand
                    }

                await db.commit()

            # Broadcast outside the DB session
            if task_dict is not None:
                self._session_manager.broadcast_task_update(task_dict)  # ty:ignore[unresolved-attribute]

            logger.info(
                "Plan artifact saved and linked: task=%s artifact=%s",
                task_id_str,
                artifact_id,
            )

        except Exception:
            logger.exception("Failed to finalize plan session %s", session.id)

    async def _broadcast_artifact_list(self) -> None:
        """Fetch all artifacts for this backend and broadcast to connected clients."""
        if self._db_session_factory is None or self._settings is None:  # ty:ignore[unresolved-attribute]
            return
        async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
            stmt = (
                select(ArtifactModel)
                .where(ArtifactModel.backend_id == self._settings.RCFLOW_BACKEND_ID)  # ty:ignore[unresolved-attribute]
                .order_by(ArtifactModel.discovered_at.desc())
            )
            result = await db.execute(stmt)
            projects_dirs = self._settings.projects_dirs  # ty:ignore[unresolved-attribute]
            artifacts = [
                {
                    "artifact_id": str(a.id),
                    "file_path": a.file_path,
                    "file_name": a.file_name,
                    "file_extension": a.file_extension,
                    "file_size": a.file_size,
                    "mime_type": a.mime_type,
                    "file_exists": a.file_exists,
                    "discovered_at": a.discovered_at.isoformat() if a.discovered_at else "",
                    "modified_at": a.modified_at.isoformat() if a.modified_at else "",
                    "session_id": str(a.session_id) if a.session_id else None,
                    "project_name": self._resolve_artifact_project(a.file_path, projects_dirs),
                }
                for a in result.scalars()
            ]
        self._session_manager.broadcast_artifact_list(artifacts)  # ty:ignore[unresolved-attribute]
