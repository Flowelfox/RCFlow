"""Claude Code agent methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
starting, streaming, forwarding, restarting, and ending Claude Code
subprocess sessions.

Composition collaborator — ``PromptRouter`` owns a :class:`ClaudeCodeAgent`
instance (``self._claude``) and delegates its public entry points to it.
Shared router state / sibling behaviour is reached through ``self._r``.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import logging
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from src.core.agent_auth import agent_configuration_issue
from src.core.agents import MAX_TOOL_OUTPUT_CHARS, truncate_tool_output
from src.core.buffer import MessageType
from src.core.cwd_tracking import (
    apply_agent_cwd,
    extract_paths_from_tool_input,
    infer_cwd_from_output,
    infer_cwd_from_tool_paths,
    looks_like_git_worktree_mutation,
    reset_worktree_cache,
)
from src.core.permissions import (
    PermissionDecision,
    PermissionManager,
    classify_risk,
    describe_tool_action,
    get_scope_options,
)
from src.core.session import ActivityState, MonitorState, SessionStatus, SessionType
from src.executors.claude_code_sdk import ClaudeCodeSdkExecutor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.core.llm import ToolCallRequest
    from src.core.prompt_router import PromptRouter
    from src.core.session import ActiveSession
    from src.executors.base import ExecutionChunk
    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

_MAX_TOOL_OUTPUT_CHARS = MAX_TOOL_OUTPUT_CHARS
_MAX_SNAPSHOT_BYTES = 1_000_000  # 1 MB limit for pre/post file snapshots
_MAX_DIFF_LINES = 200
_TOOL_OUTPUT_CHUNK_SIZE = 8_192
_PLAN_MODE_TIMEOUT = 3600  # 1 hour — wait for user to approve/deny plan mode
_PLAN_REVIEW_TIMEOUT = 3600  # 1 hour — wait for user to respond to plan review
_QUESTION_TIMEOUT = 3600  # 1 hour — wait for user to answer AskUserQuestion


_MONITOR_TERMINAL_PREFIXES = (
    "monitor exited",
    "monitor stopped",
    "monitor timed out",
    "monitor cancelled",
    "monitor canceled",
)


def _is_monitor_terminal(content: str, is_error: bool) -> bool:
    """Heuristic for whether a Monitor tool_result block ends the watch.

    Treat ``is_error=True`` as terminal so we never leak a live block on
    error.  Otherwise look for the ``"Monitor …"`` summary lines Claude
    Code emits when the watched script exits, times out, or is stopped.
    """
    if is_error:
        return True
    head = content.strip().lower()[:64]
    return any(head.startswith(p) for p in _MONITOR_TERMINAL_PREFIXES)


def _classify_monitor_termination(content: str, is_error: bool) -> tuple[str, int | None]:
    """Map a terminal Monitor payload to a (reason, exit_code) tuple.

    Reason is one of ``"exit" | "timeout" | "cancelled" | "error"``.
    ``exit_code`` is the integer extracted from ``"exit code N"`` if present,
    otherwise ``None``.
    """
    head = content.strip().lower()
    exit_code: int | None = None
    if "exit code" in head:
        # naive scan — pick the first integer after "exit code"
        try:
            tail = head.split("exit code", 1)[1]
            digits: list[str] = []
            for ch in tail:
                if ch.isdigit() or (ch == "-" and not digits):
                    digits.append(ch)
                elif digits:
                    break
            if digits:
                exit_code = int("".join(digits))
        except (ValueError, IndexError):
            exit_code = None
    if head.startswith("monitor timed out") or "timed out" in head[:80]:
        return "timeout", exit_code
    cancel_prefixes = ("monitor stopped", "monitor cancelled", "monitor canceled")
    if any(head.startswith(p) for p in cancel_prefixes):
        return "cancelled", exit_code
    if head.startswith("monitor exited"):
        return "exit", exit_code
    if is_error:
        return "error", exit_code
    return "exit", exit_code


def _classify_log_level(line: str) -> str:
    """Classify a non-JSON Claude Code stdout line into a log level.

    Returns one of ``"debug"``, ``"info"``, ``"warn"``, or ``"error"``.
    """
    lower = line.lower()
    if any(kw in lower for kw in ("error:", "exception:", "failed:", "failure:")):
        return "error"
    if any(kw in lower for kw in ("warn:", "warning:")):
        return "warn"
    if any(kw in lower for kw in ("[debug]", "debug:")):
        return "debug"
    return "info"


async def _read_file_snapshot(path: Path) -> str | None:
    """Read a file for pre/post diff comparison.

    Returns:
    - ``""`` if the file does not exist.
    - ``None`` if the file is binary or exceeds *_MAX_SNAPSHOT_BYTES*.
    - The file contents as a string otherwise.
    """
    if not path.exists():
        return ""
    try:
        if path.stat().st_size > _MAX_SNAPSHOT_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _compute_diff(before: str, after: str, filename: str) -> str | None:
    """Compute a unified diff between two file snapshots.

    Returns ``None`` when the contents are identical. Otherwise returns a
    unified-diff string truncated to at most *_MAX_DIFF_LINES* payload lines
    (plus one truncation notice line when the diff exceeds the limit).
    """
    if before == after:
        return None
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=False),
            after.splitlines(keepends=False),
            fromfile=filename,
            tofile=filename,
            lineterm="",
        )
    )
    if not diff_lines:
        return None
    if len(diff_lines) > _MAX_DIFF_LINES:
        total = len(diff_lines)
        diff_lines = [*diff_lines[:_MAX_DIFF_LINES], f"... diff truncated ({total} total lines)"]
    return "\n".join(diff_lines)


def _split_into_chunks(content: str, chunk_size: int) -> list[str]:
    """Split *content* into chunks of at most *chunk_size* characters.

    Splits preferentially at newline boundaries; falls back to a hard split
    when no newline exists within the window.
    """
    if len(content) <= chunk_size:
        return [content]
    chunks: list[str] = []
    remaining = content
    while len(remaining) > chunk_size:
        split_pos = remaining.rfind("\n", 0, chunk_size)
        if split_pos <= 0:
            split_pos = chunk_size
        else:
            split_pos += 1  # include the newline in the preceding chunk
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:]
    chunks.append(remaining)
    return chunks


_truncate_tool_output = truncate_tool_output


class ClaudeCodeAgent:
    """Claude Code subprocess lifecycle collaborator for PromptRouter."""

    def __init__(self, router: PromptRouter) -> None:
        self._r = router

    def _build_claude_code_extra_env(self) -> dict[str, str]:
        """Build extra environment variables for Claude Code subprocesses."""
        extra_env: dict[str, str] = {}

        # Check if the tool has its own provider configured — if so, skip
        # injecting the global ANTHROPIC_API_KEY so the settings.json env
        # section takes precedence.
        tool_provider = ""
        tool_cfg: dict[str, Any] = {}
        if self._r._tool_settings:
            tool_cfg = self._r._tool_settings.get_settings("claude_code")
            tool_provider = tool_cfg.get("provider", "")

        if tool_provider == "anthropic_login":
            # Anthropic Login uses OAuth tokens from .credentials.json —
            # ensure no ANTHROPIC_API_KEY leaks from the server process env,
            # which would override OAuth and cause "Invalid API key" errors.
            extra_env["ANTHROPIC_API_KEY"] = ""

        if self._r._tool_settings:
            config_dir = self._r._tool_settings.get_config_dir("claude_code")
            config_dir.mkdir(parents=True, exist_ok=True)
            extra_env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        # Ensure wt (bundled with RCFlow via wtpython) is on PATH so Claude Code
        # can call it. Fall back to adding the running Python's bin dir if wt is
        # not already resolvable via the inherited environment.
        if not shutil.which("wt"):
            venv_bin = Path(sys.executable).parent
            if (venv_bin / "wt").exists():
                current_path = os.environ.get("PATH", "")
                extra_env["PATH"] = f"{venv_bin}:{current_path}"

        # Signal to Claude Code that it is running under RCFlow orchestration
        # when the user has opted into undercover mode via tool settings.
        if tool_cfg.get("undercover", False):
            extra_env["CLAUDE_CODE_UNDERCOVER"] = "1"

        return extra_env

    async def _start_claude_code(
        self,
        session: ActiveSession,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> str:
        """Start a Claude Code session: spawn subprocess, begin background streaming."""
        # Preflight auth: catch missing API key / login *before* spawning the
        # PTY-backed CLI, which would otherwise hang on its own login prompt
        # and never emit a JSON event the user can see.
        auth_issue = agent_configuration_issue(
            "claude_code",
            self._r._settings,
            self._r._tool_settings,
            self._r._tool_manager,
        )
        if auth_issue is not None:
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": auth_issue,
                    "code": "AGENT_CONFIG_ERROR",
                    "agent_type": "claude_code",
                },
            )
            session.set_activity(ActivityState.IDLE)
            return auth_issue

        working_dir = tool_call.tool_input.get("working_directory", ".")
        # Priority 1: explicit worktree selection overrides everything.
        # Priority 2: session project (from picker) used when no worktree is set.
        # Priority 3: the LLM-chosen working_directory (default above).
        selected_wt = session.metadata.get("selected_worktree_path")
        if selected_wt:
            working_dir = selected_wt
        elif session.main_project_path:
            working_dir = session.main_project_path
        working_path = self._r._resolve_working_directory(working_dir)
        try:
            is_dir = working_path.is_dir()
        except OSError as e:
            error_msg = f"Cannot access directory {working_dir}: {e}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": error_msg,
                    "code": "INVALID_WORKING_DIRECTORY",
                },
            )
            return error_msg
        if not is_dir:
            error_msg = f"Directory does not exist: {working_dir}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": error_msg,
                    "code": "INVALID_WORKING_DIRECTORY",
                },
            )
            return error_msg

        # Replace the working_directory in tool_input with the resolved absolute path
        tool_call.tool_input["working_directory"] = str(working_path)

        executor = self._r._get_executor(tool_def.executor, tool_def)
        assert isinstance(executor, ClaudeCodeSdkExecutor)  # noqa: S101

        session.claude_code_executor = executor
        # Per-session model override (the model badge) — applied at executor
        # build so this and resumed turns run on the picked model.
        selected_model = session.metadata.get("selected_model")
        if selected_model:
            executor._config_overrides["model"] = selected_model
        session.session_type = SessionType.LONG_RUNNING
        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        # Enable interactive permissions if configured
        effective_config = {**tool_def.executor_config.get("claude_code", {})}
        for k, v in self._r._get_managed_config_overrides("claude_code").items():
            if v not in (None, ""):
                effective_config[k] = v
        if effective_config.get("default_permission_mode") == "interactive":
            session.permission_manager = PermissionManager()

        # Install the can_use_tool callback that resolves AskUserQuestion
        # (interactive widget) and permission prompts in-process.
        executor.set_can_use_tool(self._make_can_use_tool(session))

        # Stamp caveman mode so the badge system reflects the tool's current state.
        if self._r._tool_settings and self._r._tool_settings.is_caveman_active("claude_code"):
            session.metadata["caveman_mode"] = True

        # Store CC metadata for potential session restore
        session.metadata["claude_code_session_id"] = executor.session_id
        session.metadata["claude_code_working_directory"] = str(working_path)
        session.metadata["claude_code_tool_name"] = tool_def.name
        session.metadata["claude_code_parameters"] = tool_call.tool_input

        # Seed the live agent-cwd mirror used by the worktree badge so
        # the client UI starts in the right place before any tool call
        # lands.
        session.metadata["agent_cwd"] = str(working_path)
        session.agent_cwd = str(working_path)

        # Start streaming in a background task that reads events and pushes to buffer
        task = asyncio.create_task(self._stream_claude_code_events(session, executor, tool_def, tool_call))
        session._claude_code_stream_task = task

        # Record transient subprocess tracking fields and broadcast initial status
        session.subprocess_started_at = datetime.now(UTC)
        session.subprocess_current_tool = None
        session.subprocess_type = "claude_code"
        session.subprocess_display_name = tool_def.display_name or "Claude Code"
        session.subprocess_working_directory = str(working_path)
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": "claude_code",
                "display_name": session.subprocess_display_name,
                "working_directory": session.subprocess_working_directory,
                "current_tool": None,
                "started_at": session.subprocess_started_at_iso,
            },
        )

        return f"Claude Code session started in {working_path}"

    def build_session_executor(
        self,
        tool_def: ToolDefinition,
        session: ActiveSession,
        session_id: str,
    ) -> ClaudeCodeSdkExecutor:
        """Reconstruct the Agent-SDK executor for *session* (resume / restore).

        Wires the ``can_use_tool`` callback and pre-sets the tool def +
        parameters so a follow-up turn can resume.
        """
        binary_path = tool_def.get_claude_code_config().binary_path
        if self._r._tool_manager:
            resolved = self._r._tool_manager.get_binary_path("claude_code")
            if resolved:
                binary_path = resolved
        executor = ClaudeCodeSdkExecutor(
            binary_path=binary_path,
            session_id=session_id,
            extra_env=self._build_claude_code_extra_env(),
            config_overrides=self._r._get_managed_config_overrides("claude_code"),
        )
        executor.set_can_use_tool(self._make_can_use_tool(session))
        executor._tool_def = tool_def
        executor._last_parameters = session.metadata.get("claude_code_parameters", {})
        selected_model = session.metadata.get("selected_model")
        if selected_model:
            executor._config_overrides["model"] = selected_model
        return executor

    def reattach_executor(self, session: ActiveSession) -> bool:
        """Reconstruct a reloaded Claude Code session's executor for resume.

        Shared by ``restore_session`` and the lazy crash-resume path
        (``handle_prompt``): if *session* carries Claude Code resume metadata and
        has no live executor, rebuild it (with ``resume=session_id`` on the next
        turn) and re-arm any saved permission rules.  Returns ``True`` when an
        executor was attached.
        """
        if session.claude_code_executor is not None:
            return True
        cc_session_id = session.metadata.get("claude_code_session_id")
        cc_tool_name = session.metadata.get("claude_code_tool_name")
        if not (cc_session_id and cc_tool_name):
            return False
        tool_def = self._r._tool_registry.get(cc_tool_name)
        if tool_def is None or tool_def.executor != "claude_code":
            return False
        session.claude_code_executor = self.build_session_executor(tool_def, session, cc_session_id)
        session.session_type = SessionType.LONG_RUNNING
        saved_rules = session.metadata.get("permission_rules")
        if saved_rules:
            pm = PermissionManager()
            pm.restore_rules(saved_rules)
            session.permission_manager = pm
        return True

    def _make_can_use_tool(self, session: ActiveSession):
        """Build the SDK ``can_use_tool`` callback bound to *session*.

        Fires in-process before Claude Code runs a tool.  AskUserQuestion is
        resolved by surfacing the widget and waiting for the user's selection,
        which is returned as the tool's answer (``updated_input.answers``) so the
        model continues in the same turn.  Other tools are gated by the session's
        :class:`PermissionManager` when interactive permissions are enabled, and
        auto-allowed otherwise.
        """

        async def can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            context: object,  # claude_agent_sdk.ToolPermissionContext
        ) -> PermissionResultAllow | PermissionResultDeny:
            if tool_name == "AskUserQuestion":
                return await self._handle_ask_user_question(session, input_data)
            if tool_name == "EnterPlanMode":
                return await self._handle_enter_plan_mode(session)
            if tool_name == "ExitPlanMode":
                return await self._handle_exit_plan_mode(session, input_data)
            decision = await self._handle_permission_check(session, tool_name, input_data)
            if decision == PermissionDecision.DENY:
                return PermissionResultDeny(message="Denied by user.")
            return PermissionResultAllow()

        return can_use_tool

    async def _handle_enter_plan_mode(self, session: ActiveSession) -> PermissionResultAllow | PermissionResultDeny:
        """Gate EnterPlanMode on the user's approval (SDK ``can_use_tool`` path)."""
        session._plan_mode_event = asyncio.Event()
        session._plan_mode_approved = False
        session.begin_input_wait("awaiting_plan_approval")
        session.buffer.push_text(MessageType.PLAN_MODE_ASK, {"session_id": session.id})
        try:
            await asyncio.wait_for(session._plan_mode_event.wait(), timeout=_PLAN_MODE_TIMEOUT)
        except TimeoutError:
            session._plan_mode_event = None
            session.end_input_wait()
            return PermissionResultDeny(message="Plan mode timed out.", interrupt=True)
        session._plan_mode_event = None
        session.end_input_wait()
        if not session._plan_mode_approved:
            return PermissionResultDeny(message="Plan mode denied.", interrupt=True)
        return PermissionResultAllow()

    async def _handle_exit_plan_mode(
        self, session: ActiveSession, tool_input: dict[str, Any]
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Gate ExitPlanMode on plan review: approve → allow; feedback → deny+revise."""
        session._plan_review_event = asyncio.Event()
        session._plan_review_approved = False
        session._plan_review_feedback = None
        session.begin_input_wait("awaiting_plan_review")
        session.buffer.push_text(
            MessageType.PLAN_REVIEW_ASK,
            {"session_id": session.id, "plan_input": tool_input},
        )
        try:
            await asyncio.wait_for(session._plan_review_event.wait(), timeout=_PLAN_REVIEW_TIMEOUT)
        except TimeoutError:
            session._plan_review_event = None
            session.end_input_wait()
            return PermissionResultDeny(message="Plan review timed out.", interrupt=True)
        session._plan_review_event = None
        session.end_input_wait()
        feedback = session._plan_review_feedback or ""
        session._plan_review_feedback = None
        if session._plan_review_approved:
            return PermissionResultAllow()
        # Not approved — the feedback becomes the tool denial so the model revises.
        return PermissionResultDeny(message=feedback or "Plan revision requested.")

    async def _handle_ask_user_question(
        self,
        session: ActiveSession,
        tool_input: dict[str, Any],
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Surface the question widget, wait for the answer, return it to CC."""
        session.buffer.push_text(
            MessageType.TOOL_START,
            {
                "session_id": session.id,
                "tool_name": "AskUserQuestion",
                "tool_input": tool_input,
            },
        )
        session._question_event = asyncio.Event()
        session._question_answers = None
        session.begin_input_wait("awaiting_question")
        try:
            await asyncio.wait_for(session._question_event.wait(), timeout=_QUESTION_TIMEOUT)
        except TimeoutError:
            session._question_event = None
            session._question_answers = None
            session.end_input_wait()
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": "Question timed out.",
                    "code": "QUESTION_TIMEOUT",
                },
            )
            return PermissionResultDeny(message="Question timed out.", interrupt=True)

        answers = session._question_answers or {}
        session._question_event = None
        session._question_answers = None
        session.end_input_wait()

        # Persist the answer on the buffered TOOL_START so history replay shows
        # the question resolved.
        answer_text = "\n".join(f"{k}: {v}" for k, v in answers.items())
        for msg in reversed(session.buffer.text_history):
            if (
                msg.message_type == MessageType.TOOL_START
                and msg.data.get("tool_name") == "AskUserQuestion"
                and "answered" not in msg.data
            ):
                msg.data["answered"] = True
                msg.data["answer"] = answer_text
                break

        return PermissionResultAllow(updated_input={"questions": tool_input.get("questions", []), "answers": answers})

    async def _handle_permission_check(
        self,
        session: ActiveSession,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        """Check permissions for a tool use, potentially asking the user.

        If the session has a :class:`PermissionManager` and no cached rule
        covers the request, a ``PERMISSION_REQUEST`` message is pushed to the
        buffer and the coroutine blocks until the user responds or the timeout
        expires.

        Returns the final decision (``ALLOW`` or ``DENY``).
        """
        pm = session.permission_manager
        if pm is None:
            return PermissionDecision.ALLOW

        # 1. Check cached rules first
        cached = pm.check_cached(tool_name, tool_input)
        if cached is not None:
            logger.debug(
                "Permission cache hit: %s for %s (session=%s)",
                cached.value,
                tool_name,
                session.id,
            )
            return cached

        # 2. No cached rule — ask the user
        pending = pm.create_request(tool_name, tool_input)
        risk_level = classify_risk(tool_name, tool_input)
        description = describe_tool_action(tool_name, tool_input)

        session.set_activity(ActivityState.AWAITING_PERMISSION)

        session.buffer.push_text(
            MessageType.PERMISSION_REQUEST,
            {
                "session_id": session.id,
                "request_id": pending.request_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "description": description,
                "risk_level": risk_level,
                "scope_options": get_scope_options(tool_name),
            },
        )

        # 3. Wait for user response (blocks the stream reading)
        resolved = await pm.wait_for_response(pending.request_id)

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        if resolved.timed_out:
            logger.warning(
                "Permission request timed out for %s (session=%s, request=%s)",
                tool_name,
                session.id,
                pending.request_id,
            )

        return resolved.decision if resolved.decision else PermissionDecision.DENY

    async def _relay_claude_code_stream(
        self,
        session: ActiveSession,
        stream: AsyncGenerator[ExecutionChunk, None],
    ) -> None:
        """Parse Claude Code stream-json events and push structured buffer messages.

        Translates raw JSON event lines into the same message types used by the
        RCFlow LLM pipeline (TEXT_CHUNK, TOOL_START) instead of forwarding
        opaque TOOL_OUTPUT blobs.

        When the session has a :class:`PermissionManager`, ``tool_use`` blocks
        in ``assistant`` events are intercepted for permission approval before
        execution proceeds.
        """
        # Reset the pre-snapshot stack for this stream.
        session._pending_snapshots = []

        async for chunk in stream:
            line = chunk.content.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON output (e.g. startup banners, debug lines leaking to stdout).
                # Route to AGENT_LOG so it does NOT contaminate the TEXT_CHUNK stream —
                # a TEXT_CHUNK here would close the active agent tool group in the client,
                # causing subsequent TOOL_OUTPUT diffs to be applied to an orphaned block
                # instead of the correct Edit/Write tool block.
                session.buffer.push_text(
                    MessageType.AGENT_LOG,
                    {
                        "session_id": session.id,
                        "content": line,
                        "source": "stdout",
                        "level": _classify_log_level(line),
                    },
                )
                continue

            event_type = event.get("type")

            if event_type == "assistant":
                message = event.get("message", {})
                scan_texts: list[str] = []
                for block in message.get("content", []):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_val = block["text"]
                        session.buffer.push_text(
                            MessageType.TEXT_CHUNK,
                            {
                                "session_id": session.id,
                                "content": text_val,
                                "finished": False,
                            },
                        )
                        scan_texts.append(text_val)
                    elif block_type == "thinking":
                        thinking_text = block.get("thinking", "")
                        if thinking_text:
                            session.buffer.push_text(
                                MessageType.THINKING,
                                {
                                    "session_id": session.id,
                                    "content": thinking_text,
                                },
                            )
                    elif block_type == "tool_use":
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})

                        # Permissions + AskUserQuestion are resolved in-process by
                        # the SDK ``can_use_tool`` callback (see _make_can_use_tool)
                        # before the tool runs — the relay no longer gates here.

                        if tool_name == "TodoWrite":
                            todos = tool_input.get("todos", [])
                            session.update_todos(todos)
                            session.buffer.push_text(
                                MessageType.TODO_UPDATE,
                                {
                                    "session_id": session.id,
                                    "todos": todos,
                                },
                            )
                        elif tool_name in ("EnterPlanMode", "ExitPlanMode"):
                            # Plan mode is gated in-process by can_use_tool before the tool
                            # runs (it pushes PLAN_MODE_ASK / PLAN_REVIEW_ASK and resolves
                            # approve / deny / feedback).  The relay must not re-gate or
                            # display these tool_use blocks.
                            pass
                        elif tool_name == "ScheduleWakeup":
                            # Persist the wake, arm the scheduler, push
                            # WAKEUP_SCHEDULED.  The wake never blocks the
                            # CC relay — CC's own tool framework synthesises
                            # whatever tool_result it needs; our job is just
                            # to make sure the prompt actually fires when
                            # the delay expires (handled by WakeupScheduler).
                            await self._handle_schedule_wakeup_tool(session, tool_input, block)
                        elif tool_name == "Monitor":
                            monitor_id = block.get("id") or ""
                            if monitor_id:
                                started_at = datetime.now(UTC)
                                description = str(tool_input.get("description") or "")
                                command = str(tool_input.get("command") or "")
                                timeout_ms = int(tool_input.get("timeout_ms") or 300_000)
                                persistent = bool(tool_input.get("persistent") or False)
                                session._active_monitors[monitor_id] = MonitorState(
                                    description=description,
                                    command=command,
                                    timeout_ms=timeout_ms,
                                    persistent=persistent,
                                    started_at=started_at,
                                )
                                session.buffer.push_text(
                                    MessageType.MONITOR_START,
                                    {
                                        "session_id": session.id,
                                        "monitor_id": monitor_id,
                                        "description": description,
                                        "command": command,
                                        "timeout_ms": timeout_ms,
                                        "persistent": persistent,
                                        "started_at": started_at.isoformat(),
                                    },
                                )
                                # Note: no sentinel pushed to ``_pending_snapshots`` —
                                # Monitor's tool_result blocks are diverted to
                                # ``_process_monitor_event`` below and never call
                                # ``_process_tool_result``, so the snapshot stack
                                # remains 1:1 with non-monitor tool calls.
                        elif tool_name == "AskUserQuestion":
                            # The SDK ``can_use_tool`` callback already surfaced the
                            # widget and resolved the answer before the tool ran.
                            # Record the id so the resolved tool_result is dropped
                            # from the display (the widget shows the answer); push
                            # no TOOL_START / snapshot sentinel here.
                            session._question_tool_use_id = block.get("id") or ""
                        else:
                            session.buffer.push_text(
                                MessageType.TOOL_START,
                                {
                                    "session_id": session.id,
                                    "tool_name": tool_name,
                                    "tool_input": tool_input,
                                },
                            )
                            # A Claude Code native background command (``Bash`` with
                            # ``run_in_background``) finishes between turns and wakes
                            # the model.  Count it so the between-turns drain stays
                            # alive to stream the completion + continuation instead of
                            # leaving them buffered until the next user message.
                            if tool_name == "Bash" and tool_input.get("run_in_background"):
                                session._pending_bg_tasks += 1
                            # Snapshot file before Edit/Write so we can compute
                            # a diff once the tool_result arrives.
                            if tool_name in ("Edit", "Write") and isinstance(tool_input.get("file_path"), str):
                                fp = Path(tool_input["file_path"])
                                if not fp.is_absolute() and session.subprocess_working_directory:
                                    fp = Path(session.subprocess_working_directory) / fp
                                snapshot = await _read_file_snapshot(fp)
                                session._pending_snapshots.append((str(fp), snapshot))
                            else:
                                # Non-file tool — push sentinel so stack stays aligned
                                # with 1:1 tool_use/tool_result pairing.
                                session._pending_snapshots.append(None)
                            # Update subprocess current_tool tracking
                            session.subprocess_current_tool = tool_name
                            if session.subprocess_started_at is not None:
                                session.buffer.push_ephemeral(
                                    MessageType.SUBPROCESS_STATUS,
                                    {
                                        "session_id": session.id,
                                        "subprocess_type": session.subprocess_type,
                                        "display_name": session.subprocess_display_name,
                                        "working_directory": session.subprocess_working_directory,
                                        "current_tool": tool_name,
                                        "started_at": session.subprocess_started_at_iso,
                                    },
                                )
                            # Invalidate the worktree cache when a Bash
                            # command plausibly mutates the worktree set.
                            if tool_name == "Bash" and looks_like_git_worktree_mutation(
                                str(tool_input.get("command") or "")
                            ):
                                reset_worktree_cache()
                            # Tool-input file-path inference.  Any tool
                            # call that touches a file in a worktree
                            # tells us where the agent is working — no
                            # command parsing, no shell tracking
                            # required.  Catches Edit, Write, Read,
                            # Glob, Grep, NotebookEdit, MultiEdit etc.
                            # in one go.
                            paths = extract_paths_from_tool_input(tool_input)
                            if paths:
                                inferred = infer_cwd_from_tool_paths(
                                    paths,
                                    session.metadata.get("claude_code_working_directory"),
                                    session.main_project_path,
                                )
                                if (
                                    inferred
                                    and apply_agent_cwd(session, inferred)
                                    and self._r._session_manager is not None
                                ):
                                    self._r._session_manager.broadcast_session_update(session)
                        # Collect tool input values for scanning
                        for v in tool_input.values():
                            if isinstance(v, str):
                                scan_texts.append(v)
                # Fire artifact scan for this assistant message
                if scan_texts:
                    self._r._fire_text_artifact_scan(session, scan_texts)

            elif event_type == "user":
                # Claude Code emits tool_result events wrapped inside a "user"
                # message — iterate content blocks and process each tool_result.
                user_message = event.get("message", {})
                for block in user_message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id") or ""
                        if tool_use_id and tool_use_id in session._active_monitors:
                            await self._process_monitor_event(session, tool_use_id, block)
                        elif tool_use_id and tool_use_id == session._question_tool_use_id:
                            # Resolved AskUserQuestion — the widget already shows
                            # the answer; drop the synthetic result from the chat.
                            session._question_tool_use_id = None
                        elif block.get("task_notification"):
                            # A Claude Code native background command finished (not an
                            # RCFlow Monitor — its id isn't tracked).  Clear the pending
                            # count so the drain can stop, and relabel the output (it is
                            # not a "Monitor").
                            session._pending_bg_tasks = max(0, session._pending_bg_tasks - 1)
                            verb = block.get("task_verb", "exited")
                            summary = block.get("task_summary", "")
                            content = f"Background task {verb}: {summary}" if summary else f"Background task {verb}"
                            await self._process_tool_result(session, content, bool(block.get("is_error", False)))
                        else:
                            await self._process_tool_result(
                                session,
                                block.get("content", ""),
                                bool(block.get("is_error", False)),
                            )

            elif event_type == "tool_result":
                # Legacy shape kept for compatibility with synthetic test events.
                # Claude Code does not emit top-level tool_result events in
                # production; real results arrive as "user" events (above).
                await self._process_tool_result(
                    session,
                    event.get("content", ""),
                    bool(event.get("is_error", False)),
                )

            elif event_type == "result":
                session.set_activity(ActivityState.IDLE)
                result_text = event.get("result", "")
                result_subtype = event.get("subtype", "")
                # Extract cost and token data from Claude Code result
                cost_usd = event.get("cost_usd") or 0.0
                if cost_usd:
                    session.tool_cost_usd += float(cost_usd)
                cc_usage = event.get("usage") or {}
                cc_in = cc_usage.get("input_tokens") or 0
                cc_out = cc_usage.get("output_tokens") or 0
                if cc_in or cc_out:
                    session.tool_input_tokens += cc_in
                    session.tool_output_tokens += cc_out
                if (cost_usd or cc_in or cc_out) and session._on_update:
                    session._on_update()

                if result_subtype == "max_turns":
                    # Claude Code hit --max-turns limit; pause the session so the
                    # user sees a distinctive "turn limit reached" indicator and can
                    # resume to continue rather than being asked to end the session.
                    summary_text = result_text or "Claude Code reached the maximum number of turns for this invocation."
                    # Close any live monitor watches before pausing so the UI
                    # does not show them as still ticking.
                    await self._terminate_active_monitors(session, reason="session_end")
                    # Push AGENT_GROUP_END here so the agent block is closed before
                    # the SESSION_PAUSED message appears in the stream.
                    session.buffer.push_text(
                        MessageType.AGENT_GROUP_END,
                        {"session_id": session.id},
                    )
                    session.pause(reason="max_turns")
                    session.buffer.push_text(
                        MessageType.SESSION_PAUSED,
                        {
                            "session_id": session.id,
                            "paused_at": session.paused_at.isoformat() if session.paused_at else None,
                            "reason": "max_turns",
                            "claude_code_interrupted": False,
                        },
                    )
                    self._r._fire_summary_task(session, summary_text)
                    self._r._fire_task_update_task(session, summary_text)
                elif result_text:
                    self._r._fire_summary_task(session, result_text)
                    self._r._fire_task_update_task(session, result_text)

            elif event_type == "system":
                # Claude Code may emit system events with usage data
                subtype = event.get("subtype", "")
                if subtype == "usage":
                    sys_usage = event.get("usage") or {}
                    sys_in = sys_usage.get("input_tokens") or 0
                    sys_out = sys_usage.get("output_tokens") or 0
                    if sys_in or sys_out:
                        session.tool_input_tokens += sys_in
                        session.tool_output_tokens += sys_out
                        if session._on_update:
                            session._on_update()

            else:
                logger.debug("Skipping unknown Claude Code event type: %s", event_type)

    async def _handle_schedule_wakeup_tool(
        self,
        session: ActiveSession,
        tool_input: dict[str, Any],
        block: dict[str, Any],
    ) -> None:
        """Persist + arm a ``ScheduleWakeup`` request.

        Validates the input, writes a row via the wakeup store, asks
        the scheduler to arm a timer, and pushes a TOOL_START so the
        chat history reflects the call.  The actual inline card /
        activity strip update is fired by the store as part of
        WAKEUP_SCHEDULED.
        """
        store = self._r._wakeup_store
        scheduler = self._r._wakeup_scheduler
        if store is None or scheduler is None:
            # No persistence layer configured (test harness, etc.) — fall
            # through to the generic TOOL_START so the call is still
            # visible in chat history but nothing is armed.
            session.buffer.push_text(
                MessageType.TOOL_START,
                {
                    "session_id": session.id,
                    "tool_name": "ScheduleWakeup",
                    "tool_input": tool_input,
                },
            )
            return

        prompt = str(tool_input.get("prompt") or "").strip()
        reason = str(tool_input.get("reason") or "").strip()
        delay_raw = tool_input.get("delaySeconds")
        try:
            delay = int(delay_raw) if delay_raw is not None else 0
        except (TypeError, ValueError):
            delay = 0
        # Clamp to the same window the upstream tool description
        # advertises (60 s lower bound, 1 h upper bound).  This also
        # protects us from a runaway recursive ``/loop`` that schedules
        # near-zero delays.
        delay = max(60, min(3600, delay))

        if not prompt:
            # Reject gracefully — push a TOOL_OUTPUT explaining why so
            # the user can see what happened.
            session.buffer.push_text(
                MessageType.TOOL_START,
                {
                    "session_id": session.id,
                    "tool_name": "ScheduleWakeup",
                    "tool_input": tool_input,
                },
            )
            session.buffer.push_text(
                MessageType.TOOL_OUTPUT,
                {
                    "session_id": session.id,
                    "tool_name": "ScheduleWakeup",
                    "content": "ScheduleWakeup ignored: no prompt provided.",
                    "is_error": True,
                },
            )
            return

        fire_at = datetime.now(UTC) + timedelta(seconds=delay)
        # Render the call itself so chat history shows the request
        # alongside the per-wake events the store emits.
        session.buffer.push_text(
            MessageType.TOOL_START,
            {
                "session_id": session.id,
                "tool_name": "ScheduleWakeup",
                "tool_input": {
                    "delaySeconds": delay,
                    "reason": reason,
                    "prompt": prompt,
                },
            },
        )

        wake = await store.enqueue(
            session,
            prompt=prompt,
            reason=reason,
            fire_at=fire_at,
        )
        scheduler.arm(session.id, wake)
        logger.info(
            "ScheduleWakeup armed: session=%s wake_id=%s delay=%ds fire_at=%s wakes_in_mirror=%d",
            session.id,
            wake.wake_id,
            delay,
            fire_at.isoformat(),
            len(session.scheduled_wakes),
        )
        if self._r._session_manager is not None:
            self._r._session_manager.broadcast_session_update(session)
        # Acknowledge so the assistant message lists a tool_result.
        # We rely on the generic TOOL_OUTPUT path; ScheduleWakeup
        # doesn't produce its own result event from CC.
        session.buffer.push_text(
            MessageType.TOOL_OUTPUT,
            {
                "session_id": session.id,
                "tool_name": "ScheduleWakeup",
                "content": (f"Wake scheduled in {delay}s (at {fire_at.isoformat()}). wake_id={wake.wake_id}"),
                "is_error": False,
            },
        )
        _ = block  # placeholder — kept for future tool_use_id linkage.

    async def _process_tool_result(
        self,
        session: ActiveSession,
        raw_content: Any,
        is_error: bool,
    ) -> None:
        """Emit a TOOL_OUTPUT message for a single tool_result payload.

        Computes a unified diff against the pre-snapshot when the matching
        tool_use was Edit/Write, splits large outputs into chunks, and clears
        the session's current_tool tracking.
        """
        if isinstance(raw_content, list):
            # Content blocks format — extract text parts only
            parts = []
            for block in raw_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content = "\n".join(parts)
        else:
            content = str(raw_content) if raw_content is not None else ""

        content = _truncate_tool_output(content)

        diff: str | None = None
        snapshots: list = getattr(session, "_pending_snapshots", [])
        if snapshots:
            pending = snapshots.pop(0)
            if pending is not None:
                filepath_str, before_text = pending
                if before_text is not None:
                    after_text = await _read_file_snapshot(Path(filepath_str))
                    if after_text is not None:
                        diff = _compute_diff(before_text, after_text, filepath_str)

        if content or diff:
            chunks = _split_into_chunks(content, _TOOL_OUTPUT_CHUNK_SIZE) if content else [""]
            if len(chunks) == 1:
                tool_output_data: dict[str, Any] = {
                    "session_id": session.id,
                    "content": chunks[0],
                    "is_error": is_error,
                }
                if diff:
                    tool_output_data["diff"] = diff
                session.buffer.push_text(
                    MessageType.TOOL_OUTPUT,
                    tool_output_data,
                )
            else:
                for i, chunk in enumerate(chunks):
                    chunk_data: dict[str, Any] = {
                        "session_id": session.id,
                        "content": chunk,
                        "is_error": is_error,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    }
                    # Attach diff to the last chunk only
                    if diff and i == len(chunks) - 1:
                        chunk_data["diff"] = diff
                    session.buffer.push_text(
                        MessageType.TOOL_OUTPUT,
                        chunk_data,
                    )
            if content:
                self._r._fire_text_artifact_scan(session, [content])

        session.subprocess_current_tool = None
        if session.subprocess_started_at is not None:
            session.buffer.push_ephemeral(
                MessageType.SUBPROCESS_STATUS,
                {
                    "session_id": session.id,
                    "subprocess_type": session.subprocess_type,
                    "display_name": session.subprocess_display_name,
                    "working_directory": session.subprocess_working_directory,
                    "current_tool": None,
                    "started_at": session.subprocess_started_at_iso,
                },
            )

        # Output-side cwd inference.  Many tools that change the agent's
        # working location (wt attach, git worktree add, custom switch
        # scripts) don't actually ``cd`` Claude Code's shell — the cwd
        # field in the CC jsonl stays put — but their *output* names the
        # target worktree's absolute path.  We scan the tool result for
        # any string that exactly matches a known Git worktree of the
        # session's project; unrelated paths in the output are ignored,
        # so this is conservative and command-agnostic.
        inferred = infer_cwd_from_output(content, session.main_project_path)
        if inferred and apply_agent_cwd(session, inferred) and self._r._session_manager is not None:
            self._r._session_manager.broadcast_session_update(session)

    async def _process_monitor_event(
        self,
        session: ActiveSession,
        monitor_id: str,
        block: dict[str, Any],
    ) -> None:
        """Route a Monitor tool_result block to MONITOR_EVENT or MONITOR_END.

        Each stdout-line batch from Claude Code arrives as a ``tool_result``
        with the same ``tool_use_id`` as the original ``Monitor`` invocation.
        Most are intermediate events; the final one is a terminal payload
        (script exit, timeout, or TaskStop). Terminal detection uses
        ``is_error`` plus content prefix sniffing — ``is_error=True`` is
        always treated as terminal so a misclassified non-terminal error
        block still closes the monitor cleanly.
        """
        state = session._active_monitors.get(monitor_id)
        if state is None:
            return

        raw_content = block.get("content", "")
        if isinstance(raw_content, list):
            parts: list[str] = []
            for sub in raw_content:
                if isinstance(sub, dict) and sub.get("type") == "text":
                    parts.append(sub.get("text", ""))
            content = "\n".join(parts)
        else:
            content = str(raw_content) if raw_content is not None else ""

        is_error = bool(block.get("is_error", False))
        is_terminal = _is_monitor_terminal(content, is_error)

        if not is_terminal:
            state.event_count += 1
            session.buffer.push_text(
                MessageType.MONITOR_EVENT,
                {
                    "session_id": session.id,
                    "monitor_id": monitor_id,
                    "content": content,
                    "is_error": is_error,
                    "received_at": datetime.now(UTC).isoformat(),
                    "sequence": state.event_count,
                },
            )
            return

        reason, exit_code = _classify_monitor_termination(content, is_error)
        session._active_monitors.pop(monitor_id, None)
        end_payload: dict[str, Any] = {
            "session_id": session.id,
            "monitor_id": monitor_id,
            "reason": reason,
            "ended_at": datetime.now(UTC).isoformat(),
            "total_events": state.event_count,
            "final_content": content,
        }
        if exit_code is not None:
            end_payload["exit_code"] = exit_code
        session.buffer.push_text(MessageType.MONITOR_END, end_payload)

    async def _terminate_active_monitors(
        self,
        session: ActiveSession,
        reason: str,
    ) -> None:
        """Emit MONITOR_END for every live monitor on the session.

        Called from session-end / interrupt / pause hooks so persistent or
        in-flight monitors do not look perpetually alive in the UI.
        """
        # A terminated/paused session must not keep the between-turns drain alive
        # waiting on a background command that will never report now.
        session._pending_bg_tasks = 0
        if not session._active_monitors:
            return
        live = list(session._active_monitors.items())
        session._active_monitors.clear()
        for monitor_id, state in live:
            session.buffer.push_text(
                MessageType.MONITOR_END,
                {
                    "session_id": session.id,
                    "monitor_id": monitor_id,
                    "reason": reason,
                    "ended_at": datetime.now(UTC).isoformat(),
                    "total_events": state.event_count,
                    "final_content": "",
                },
            )

    async def cancel_monitor(self, session_id: str, monitor_id: str) -> None:
        """Stop a live Claude Code Monitor watch from the user UI.

        Emits ``MONITOR_END(reason="cancelled")`` for instant client feedback,
        then asks Claude Code to actually stop the underlying watcher by
        sending a follow-up instruction to its stdin.  If Claude Code is not
        currently running the cancellation is local-only.

        Raises:
            ValueError: If the session does not exist or no live monitor
                with the given id is tracked.
        """
        sm = self._r._session_manager
        session = sm.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        state = session._active_monitors.get(monitor_id)
        if state is None:
            raise ValueError(f"Monitor not active: {monitor_id}")

        session._active_monitors.pop(monitor_id, None)
        session.buffer.push_text(
            MessageType.MONITOR_END,
            {
                "session_id": session.id,
                "monitor_id": monitor_id,
                "reason": "cancelled",
                "ended_at": datetime.now(UTC).isoformat(),
                "total_events": state.event_count,
                "final_content": "",
            },
        )

        executor = session.claude_code_executor
        if executor is None or not executor.is_running:
            return
        # Inject a short, unambiguous instruction so Claude Code calls TaskStop
        # on the matching watcher.  The description is the most reliable handle
        # the user-facing model has — Claude Code's TaskStop targets running
        # tasks by id internally, but it can map descriptions for us.
        desc = state.description or monitor_id
        with contextlib.suppress(RuntimeError):
            await executor.send_input(f"Stop the active monitor: {desc!r}")

    async def _stream_claude_code_events(
        self,
        session: ActiveSession,
        executor: ClaudeCodeSdkExecutor,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> None:
        """Background task: read Claude Code events and push to session buffer."""

        # Forward error/warning-level stderr lines to the session buffer so the
        # user can see them in the UI.  Debug/info noise is kept server-side.
        def _on_stderr(line: str) -> None:
            level = _classify_log_level(line)
            if level in ("error", "warn"):
                session.buffer.push_text(
                    MessageType.AGENT_LOG,
                    {
                        "session_id": session.id,
                        "content": line,
                        "source": "stderr",
                        "level": level,
                    },
                )

        executor._on_stderr_line = _on_stderr
        try:
            await self._relay_claude_code_stream(session, executor.execute_streaming(tool_def, tool_call.tool_input))
        except Exception as e:
            logger.exception("Claude Code streaming error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Claude Code error: {e}",
                    "code": "CLAUDE_CODE_ERROR",
                },
            )
            await self._end_claude_code_session(session)
            return

        # If the relay ended the session itself (e.g. plan mode denied), it already
        # pushed AGENT_GROUP_END/SESSION_END and called _end_claude_code_session —
        # nothing left to do here.
        if session.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED, SessionStatus.FAILED):
            return

        # If the session was paused during relay (e.g. max_turns), the relay already
        # pushed AGENT_GROUP_END and SESSION_PAUSED — just clean up and return.
        if session.status == SessionStatus.PAUSED:
            if session.claude_code_executor is not None:
                await session.claude_code_executor.stop_process()
            session.claude_code_executor = None
            session._claude_code_stream_task = None
            logger.info("Session %s paused after stream (reason=%s)", session.id, session.paused_reason)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            session.set_activity(ActivityState.IDLE)
            session.clear_subprocess_tracking()
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            if exit_code == 0:
                # Clean exit with no result event — treat as normal completion.
                logger.info(
                    "Claude Code exited cleanly without result event (session=%s)",
                    session.id,
                )
            else:
                logger.warning(
                    "Claude Code exited without result event (session=%s, exit_code=%s)",
                    session.id,
                    exit_code,
                )
                session.buffer.push_text(
                    MessageType.ERROR,
                    {
                        "session_id": session.id,
                        "content": (
                            f"Claude Code process exited unexpectedly (exit code: {exit_code}). "
                            "This may be caused by a timeout, out-of-memory condition, or internal error. "
                            "You can send another message to restart it."
                        ),
                        "code": "CLAUDE_CODE_UNEXPECTED_EXIT",
                    },
                )
            await executor.stop_process()
            return

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

        logger.info(
            "Claude Code initial streaming finished (session=%s)",
            session.id,
        )
        self._schedule_drain_after_stream_task(session)

        await self._drain_monitor_events(session, executor)

        # Clear the "subprocess running" indicator now that the turn is fully
        # over (any active Monitor watches have also finished or been
        # surrendered above).  If a queued message is about to drain,
        # ``_forward_to_claude_code`` will re-set subprocess tracking for the
        # next turn; otherwise the UI correctly stops showing the running
        # indicator instead of leaving it pinned indefinitely.
        session.clear_subprocess_tracking()

    def _schedule_drain_after_stream_task(self, session: ActiveSession) -> None:
        """Schedule pending-message drain to fire after the active CC stream task is done.

        Called from inside ``_stream_claude_code_events`` /
        ``_restart_claude_code_with_prompt`` — i.e. *while* the owning
        task is still running its trailing
        ``_drain_monitor_events``.  Scheduling the drain inline used to
        race that await: ``_drain_one`` could reach
        ``_forward_to_claude_code`` while the stream task was still alive,
        triggering a cancel of the very task whose ``stdin`` write was
        about to land.  Hooking ``add_done_callback`` guarantees the
        drain runs only once the stream task has fully unwound.
        """
        task = session._claude_code_stream_task
        if task is None or task.done():
            self._r.schedule_pending_drain(session)
            return
        task.add_done_callback(lambda _t: self._r.schedule_pending_drain(session))

    async def _drain_monitor_events(
        self,
        session: ActiveSession,
        executor: ClaudeCodeSdkExecutor,
    ) -> None:
        """Keep reading Claude Code stdout while ``Monitor`` watches are alive.

        Claude Code's deferred ``Monitor`` tool emits its ``tool_result``
        events (including the terminal "Monitor exited/timed out/stopped"
        payload) on stdout *after* the turn-ending ``result`` event, between
        user turns.  The default executor read loop breaks on ``result`` and
        leaves those events sitting in the OS pipe buffer until the next user
        input — which is why MONITOR_END never reaches the client and the
        live-monitor strip never clears on its own.

        This helper resumes reading after every ``result`` event for as long
        as at least one monitor is still tracked.  It is awaited inline by
        the post-turn streaming hooks so the encompassing
        ``_claude_code_stream_task`` stays the cancel target for
        ``_forward_to_claude_code`` when the user sends a new message.
        """
        terminal_statuses = (
            SessionStatus.COMPLETED,
            SessionStatus.CANCELLED,
            SessionStatus.FAILED,
            SessionStatus.PAUSED,
        )
        while (session._active_monitors or session._pending_bg_tasks > 0) and executor.is_running:
            if session.status in terminal_statuses:
                return
            try:
                # While a Claude Code native background command is pending, stream
                # the FULL continuation (the model's woken response), not just the
                # Monitor-style user/tool_result events — otherwise it buffers in
                # the queue until the next user message.
                include_assistant = session._pending_bg_tasks > 0
                await self._relay_claude_code_stream(
                    session, executor.read_more_events(include_assistant=include_assistant)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Claude Code monitor drain error in session %s",
                    session.id,
                )
                return
            if session.status in terminal_statuses:
                return
            if not executor.got_result:
                # Process exited mid-drain.
                break
        # If CC died while monitors were still tracked, flush them so the UI
        # never sees a perpetually live block.
        if session._active_monitors and not executor.is_running:
            await self._terminate_active_monitors(session, reason="executor_exit")

    async def _end_claude_code_session(self, session: ActiveSession) -> None:
        """Clean up Claude Code state when the session ends."""
        if session.claude_code_executor is not None:
            await session.claude_code_executor.stop_process()
        session.claude_code_executor = None
        session._claude_code_stream_task = None

        # Close out any live Monitor watches before broadcasting SESSION_END
        # so the client never sees a perpetually-live monitor block.
        await self._terminate_active_monitors(session, reason="session_end")

        # Clear subprocess tracking and broadcast null status
        session.clear_subprocess_tracking()

        if session.status == SessionStatus.PAUSED:
            # Defer end/archive — user will see output on resume
            session.complete()  # sets completed_while_paused flag
            return

        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "claude_code_finished",
            },
        )
        session.complete()
        self._r._fire_archive_task(session.id)

    async def _forward_to_claude_code(self, session: ActiveSession, text: str) -> None:
        """Forward a follow-up message to the active Claude Code subprocess.

        The process normally stays alive between turns, so messages are sent
        via stdin.  If the process has unexpectedly exited, fall back to
        restarting it with the same ``--session-id``.
        """
        executor = session.claude_code_executor
        stream_task = session._claude_code_stream_task
        logger.info(
            "_forward_to_claude_code: entry (session=%s, executor=%s, executor_running=%s, stream_task_done=%s)",
            session.id,
            executor is not None,
            executor.is_running if executor is not None else False,
            stream_task.done() if stream_task is not None else None,
        )
        if executor is None:
            return

        if session.status == SessionStatus.PAUSED:
            return

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        # Re-broadcast subprocess status so the client shows the indicator again
        if session.subprocess_started_at is None:
            session.subprocess_started_at = datetime.now(UTC)
            session.subprocess_type = "claude_code"
            cc_def_for_name = self._r._tool_registry.get("claude_code")
            session.subprocess_display_name = (
                cc_def_for_name.display_name if cc_def_for_name and cc_def_for_name.display_name else "Claude Code"
            )
            session.subprocess_working_directory = session.metadata.get("claude_code_working_directory", "")
        session.subprocess_current_tool = None
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": session.subprocess_type,
                "display_name": session.subprocess_display_name,
                "working_directory": session.subprocess_working_directory,
                "current_tool": None,
                "started_at": session.subprocess_started_at_iso,
            },
        )

        # Open a new agent group for this follow-up turn
        cc_def = self._r._tool_registry.get("claude_code")
        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {
                "session_id": session.id,
                "tool_name": "claude_code",
                "display_name": cc_def.display_name if cc_def and cc_def.display_name else "Claude Code",
            },
        )

        # Always restart the subprocess for follow-ups.  Claude Code's
        # ``--print`` mode terminates after each turn (regardless of
        # ``--session-id`` vs ``--resume``), so the "process kept alive
        # between turns" assumption no longer holds — empirically the
        # process exits within seconds of the ``result`` event.  When a
        # queued message drained right after a turn, ``send_input`` would
        # race that exit: stdin write succeeded but the process tore down
        # before producing a result event, and the queued message was
        # silently lost.  ``restart_with_prompt`` uses ``--resume`` +
        # the prompt as the initial CLI argument so the new process
        # picks up the conversation history and reliably produces a
        # result event.
        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = asyncio.create_task(
            self._restart_claude_code_with_prompt(session, executor, text)
        )

    async def _restart_claude_code_with_prompt(
        self,
        session: ActiveSession,
        executor: ClaudeCodeSdkExecutor,
        prompt: str,
    ) -> None:
        """Restart Claude Code process and stream events for a follow-up."""
        try:
            await self._relay_claude_code_stream(session, executor.restart_with_prompt(prompt))
        except Exception as e:
            logger.exception("Claude Code restart error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Claude Code error: {e}",
                    "code": "CLAUDE_CODE_ERROR",
                },
            )
            await self._end_claude_code_session(session)
            return

        # If the relay ended the session itself (e.g. plan mode denied), already done.
        if session.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED, SessionStatus.FAILED):
            return

        # If the session was paused during relay (e.g. max_turns), relay already
        # pushed AGENT_GROUP_END and SESSION_PAUSED — just clean up and return.
        if session.status == SessionStatus.PAUSED:
            if session.claude_code_executor is not None:
                await session.claude_code_executor.stop_process()
            session.claude_code_executor = None
            session._claude_code_stream_task = None
            logger.info("Session %s paused after restart stream (reason=%s)", session.id, session.paused_reason)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            session.set_activity(ActivityState.IDLE)
            session.clear_subprocess_tracking()
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            if exit_code == 0:
                logger.info(
                    "Claude Code (restart) exited cleanly without result event (session=%s)",
                    session.id,
                )
            else:
                logger.warning(
                    "Claude Code (restart) exited without result event (session=%s, exit_code=%s)",
                    session.id,
                    exit_code,
                )
                session.buffer.push_text(
                    MessageType.ERROR,
                    {
                        "session_id": session.id,
                        "content": (
                            f"Claude Code process exited unexpectedly (exit code: {exit_code}). "
                            "This may be caused by a timeout, out-of-memory condition, or internal error. "
                            "You can send another message to restart it."
                        ),
                        "code": "CLAUDE_CODE_UNEXPECTED_EXIT",
                    },
                )
            return

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )
        self._schedule_drain_after_stream_task(session)

        await self._drain_monitor_events(session, executor)

        # See ``_stream_claude_code_events`` for rationale.
        session.clear_subprocess_tracking()
