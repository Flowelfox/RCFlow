from __future__ import annotations

import asyncio
import json as json_mod
import logging
import os
import platform
import re as re_mod
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from src.api.deps import verify_http_api_key
from src.config import CONFIGURABLE_KEYS, Settings, get_config_schema, update_env_file
from src.core.llm import LLMClient
from src.models.db import Session as SessionModel
from src.models.db import SessionMessage as SessionMessageModel
from src.speech.stt import create_stt_provider
from src.speech.tts import create_tts_provider

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.core.prompt_router import PromptRouter
    from src.core.session import SessionManager
    from src.services.tool_manager import ManagedTool, ToolManager
    from src.services.tool_settings import ToolSettingsManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["API"])


@router.get(
    "/health",
    summary="Health check",
    description="Returns server status. Does not require authentication.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/info",
    summary="Server information",
    description="Returns server metadata including operating system and platform details.",
    dependencies=[Depends(verify_http_api_key)],
)
async def server_info() -> dict[str, Any]:
    """Return server metadata so clients can display OS and platform info."""
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "hostname": platform.node(),
    }


@router.get(
    "/sessions",
    summary="List all sessions",
    description=(
        "Returns all sessions (in-memory and archived) with their status, "
        "type, and creation time, sorted by created_at descending."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_sessions(
    request: Request,
) -> dict[str, Any]:
    session_manager = request.app.state.session_manager
    db_session_factory = request.app.state.db_session_factory
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

    return {"sessions": sessions}


@router.get(
    "/sessions/{session_id}/messages",
    summary="Get session messages",
    description=(
        "Returns the message history for a session. Checks in-memory buffer "
        "first, then falls back to the database for archived sessions."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def get_session_messages(
    session_id: str,
    request: Request,
    before: int | None = Query(None, description="Cursor: return messages with sequence < this value"),
    limit: int | None = Query(None, ge=1, le=200, description="Max messages to return (enables pagination)"),
) -> dict[str, Any]:
    session_manager = request.app.state.session_manager
    db_session_factory = request.app.state.db_session_factory

    # Check in-memory first
    session = session_manager.get_session(session_id)
    if session is not None:
        all_msgs = session.buffer.text_history
        total_count = len(all_msgs)

        if limit is not None:
            # Apply pagination to in-memory messages
            filtered = [m for m in all_msgs if m.sequence < before] if before is not None else list(all_msgs)
            # Take the last `limit` messages (most recent)
            page = filtered[-limit:] if len(filtered) > limit else filtered
            has_more = len(filtered) > limit
            next_cursor = page[0].sequence if has_more and page else None
        else:
            page = all_msgs
            has_more = False
            next_cursor = None

        messages = [
            {
                "type": msg.message_type.value,
                "sequence": msg.sequence,
                "content": msg.data.get("content", ""),
                "metadata": msg.data,
            }
            for msg in page
        ]
        return {
            "session_id": session_id,
            "messages": messages,
            "pagination": {
                "total_count": total_count,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
        }

    # Fall back to database
    if db_session_factory is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id}") from None

    async with db_session_factory() as db:
        # Verify the session exists in the DB
        session_row = await db.get(SessionModel, session_uuid)
        if session_row is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        # Total count for pagination metadata
        count_stmt = (
            select(func.count()).select_from(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
        )
        count_result = await db.execute(count_stmt)
        total_count = count_result.scalar_one()

        if limit is not None:
            # Paginated query: fetch `limit` messages ordered by sequence DESC
            stmt = select(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
            if before is not None:
                stmt = stmt.where(SessionMessageModel.sequence < before)
            stmt = stmt.order_by(SessionMessageModel.sequence.desc()).limit(limit)

            result = await db.execute(stmt)
            rows = list(reversed(result.scalars().all()))  # Reverse to chronological order

            has_more = bool(rows) and rows[0].sequence > 1
            # Verify has_more by checking if there are messages before our oldest
            if has_more and rows:
                check_stmt = (
                    select(func.count())
                    .select_from(SessionMessageModel)
                    .where(SessionMessageModel.session_id == session_uuid)
                    .where(SessionMessageModel.sequence < rows[0].sequence)
                )
                check_result = await db.execute(check_stmt)
                has_more = check_result.scalar_one() > 0
            next_cursor = rows[0].sequence if has_more and rows else None
        else:
            # No pagination — return all messages
            stmt = (
                select(SessionMessageModel)
                .where(SessionMessageModel.session_id == session_uuid)
                .order_by(SessionMessageModel.sequence)
            )
            result = await db.execute(stmt)
            rows = list(result.scalars().all())
            has_more = False
            next_cursor = None

    messages = [
        {
            "type": row.message_type,
            "sequence": row.sequence,
            "content": row.content or "",
            "metadata": row.metadata_,
        }
        for row in rows
    ]
    return {
        "session_id": session_id,
        "messages": messages,
        "pagination": {
            "total_count": total_count,
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
    }


@router.get(
    "/tools",
    summary="List available tools",
    description="Returns all registered tool definitions that the LLM can use.",
    dependencies=[Depends(verify_http_api_key)],
)
async def list_tools(request: Request) -> dict[str, Any]:
    tool_registry = request.app.state.tool_registry
    tools = [
        {
            "name": t.name,
            "description": t.description,
            "version": t.version,
            "session_type": t.session_type,
            "executor": t.executor,
        }
        for t in tool_registry.list_tools()
    ]
    return {"tools": tools}


@router.get(
    "/tools/status",
    summary="Get managed tool status",
    description=(
        "Returns installation status, versions, and update availability for managed CLI tools (Claude Code, Codex)."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def get_tool_status(request: Request) -> dict[str, Any]:
    """Return installation status, current/latest versions, and update availability."""
    tool_manager: ToolManager = request.app.state.tool_manager
    tools = await tool_manager.check_updates()
    return {"tools": {name: _tool_dict(tool) for name, tool in tools.items()}}


def _tool_dict(tool: ManagedTool) -> dict[str, Any]:
    """Serialise a single ManagedTool to a JSON-friendly dict."""
    return {
        "installed": tool.binary_path is not None,
        "managed": tool.managed,
        "binary_path": tool.binary_path,
        "current_version": tool.current_version,
        "latest_version": tool.latest_version,
        "update_available": (
            tool.current_version is not None
            and tool.latest_version is not None
            and tool.current_version != tool.latest_version
        ),
        "error": tool.error,
        "managed_path": tool.managed_path,
        "external_path": tool.external_path,
    }


@router.post(
    "/tools/update",
    summary="Trigger tool updates",
    description=(
        "Checks for updates to managed CLI tools and installs them if available. "
        "Only updates tools that RCFlow manages (not user-installed ones)."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def trigger_tool_update(request: Request) -> dict[str, Any]:
    """Trigger an update check and install for all RCFlow-managed tools."""
    tool_manager: ToolManager = request.app.state.tool_manager
    results = await tool_manager.update_all()
    return {"tools": {name: _tool_dict(tool) for name, tool in results.items()}}


@router.post(
    "/tools/update/{tool_name}",
    summary="Update a single tool",
    description=(
        "Updates a specific managed CLI tool by name (e.g. claude_code, codex). "
        "Streams NDJSON progress events. Final event has step='complete' with updated tool data."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def trigger_single_tool_update(request: Request, tool_name: str) -> StreamingResponse:
    """Update a single tool with streaming progress."""
    tool_manager: ToolManager = request.app.state.tool_manager
    if tool_name not in tool_manager.tool_names:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    async def _stream():
        try:
            async for event in tool_manager.update_tool_streaming(tool_name):
                yield json_mod.dumps(event) + "\n"
            # Re-detect to refresh paths
            tool = await tool_manager.detect_tool(tool_name)
            tool_manager._tools[tool_name] = tool
            yield json_mod.dumps({"step": "complete", "tool": _tool_dict(tool)}) + "\n"
        except Exception as e:
            logger.exception("Update failed for '%s'", tool_name)
            yield json_mod.dumps({"step": "error", "message": str(e)}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.post(
    "/tools/{tool_name}/install",
    summary="Install managed version of a tool",
    description=(
        "Downloads and installs the RCFlow-managed version of a CLI tool. "
        "Streams NDJSON progress events. Final event has step='done'."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def install_managed_tool(request: Request, tool_name: str) -> StreamingResponse:
    """Install the managed version of a tool with streaming progress."""
    tool_manager: ToolManager = request.app.state.tool_manager
    if tool_name not in tool_manager.tool_names:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    async def _stream():
        try:
            async for event in tool_manager.install_tool_streaming(tool_name):
                yield json_mod.dumps(event) + "\n"
            # Re-detect after install so managed_path/external_path are populated
            tool = await tool_manager.detect_tool(tool_name)
            tool_manager._tools[tool_name] = tool
            yield json_mod.dumps({"step": "complete", "tool": _tool_dict(tool)}) + "\n"
        except Exception as e:
            logger.exception("Install failed for '%s'", tool_name)
            yield json_mod.dumps({"step": "error", "message": str(e)}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.delete(
    "/tools/{tool_name}/install",
    summary="Uninstall managed version of a tool",
    description=(
        "Removes the managed binary and version file but preserves tool settings. "
        "If an external binary exists on PATH the tool falls back to it."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def uninstall_managed_tool(request: Request, tool_name: str) -> dict[str, Any]:
    """Uninstall the managed version of a tool, preserving settings."""
    tool_manager: ToolManager = request.app.state.tool_manager
    if tool_name not in tool_manager.tool_names:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    try:
        tool = await tool_manager.uninstall_tool(tool_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"tool": _tool_dict(tool)}


@router.post(
    "/tools/{tool_name}/source",
    summary="Switch tool source",
    description=(
        "Switch a tool between managed (RCFlow-installed) and external (PATH) source. "
        'Send {"use_managed": true} to use the managed install, or false to use '
        "the external binary found on PATH."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def switch_tool_source(request: Request, tool_name: str) -> dict[str, Any]:
    """Switch a tool between managed and external source."""
    tool_manager: ToolManager = request.app.state.tool_manager
    if tool_name not in tool_manager.tool_names:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    body = await request.json()
    use_managed = body.get("use_managed", True)
    try:
        tool = await tool_manager.switch_source(tool_name, use_managed=use_managed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"tool": _tool_dict(tool)}


def _is_tool_managed(tool_manager: ToolManager, tool_name: str) -> bool:
    """Check whether *tool_name* is currently using its managed binary."""
    tool = tool_manager._tools.get(tool_name)
    return tool.managed if tool else False


class UpdateToolSettingsRequest(BaseModel):
    """Body for the PATCH /api/tools/{tool_name}/settings endpoint."""

    updates: dict[str, Any]


@router.get(
    "/tools/{tool_name}/settings",
    summary="Get per-tool settings",
    description=(
        "Returns the settings schema and current values for a managed CLI tool. "
        "Each field includes key, label, type, current value, default, and description."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def get_tool_settings(request: Request, tool_name: str) -> dict[str, Any]:
    """Return settings schema with current values for a tool."""
    tool_settings: ToolSettingsManager = request.app.state.tool_settings
    tool_manager: ToolManager = request.app.state.tool_manager
    managed = _is_tool_managed(tool_manager, tool_name)
    try:
        return tool_settings.get_settings_with_schema(tool_name, managed=managed)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.patch(
    "/tools/{tool_name}/settings",
    summary="Update per-tool settings",
    description=(
        "Apply partial updates to a managed CLI tool's settings. "
        'Body: {"updates": {"key": value, ...}}. Returns the updated schema+values.'
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def update_tool_settings(request: Request, tool_name: str, body: UpdateToolSettingsRequest) -> dict[str, Any]:
    """Apply partial settings updates for a tool."""
    tool_settings: ToolSettingsManager = request.app.state.tool_settings
    tool_manager: ToolManager = request.app.state.tool_manager
    managed = _is_tool_managed(tool_manager, tool_name)
    try:
        result = tool_settings.update_settings(tool_name, body.updates, managed=managed)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    logger.info("Tool settings updated for '%s': %s", tool_name, list(body.updates.keys()))
    return result


# ---------------------------------------------------------------------------
# Codex ChatGPT login
# ---------------------------------------------------------------------------

# Regex to strip ANSI escape codes from CLI output.
_ANSI_RE = re_mod.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Timeout for the interactive device-auth flow (user must complete in browser).
_CODEX_LOGIN_TIMEOUT = 300  # 5 minutes


@router.post(
    "/tools/codex/login",
    summary="Start Codex ChatGPT login",
    description=(
        "Spawns `codex login` with the managed CODEX_HOME and streams NDJSON "
        "progress events. Use `?device_code=true` for device-auth flow (shows "
        "a code to enter in the browser). Without it, uses browser-based OAuth "
        "(returns a URL for the client to open)."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def codex_login(
    request: Request,
    device_code: bool = Query(False, description="Use device-code auth instead of browser OAuth"),
) -> StreamingResponse:
    """Stream login progress for Codex ChatGPT subscription."""
    tool_manager: ToolManager = request.app.state.tool_manager
    tool_settings: ToolSettingsManager = request.app.state.tool_settings

    binary_path = tool_manager.get_binary_path("codex")
    if not binary_path:
        raise HTTPException(status_code=400, detail="Codex is not installed")

    config_dir = tool_settings.get_config_dir("codex")
    config_dir.mkdir(parents=True, exist_ok=True)

    if device_code:
        return StreamingResponse(
            _stream_device_auth(binary_path, config_dir),
            media_type="application/x-ndjson",
        )
    return StreamingResponse(
        _stream_browser_auth(binary_path, config_dir),
        media_type="application/x-ndjson",
    )


async def _stream_browser_auth(binary_path: str, config_dir: Path) -> AsyncGenerator[str, None]:
    """Run ``codex login`` (browser OAuth) and stream progress events.

    The CLI starts a local callback server, then prints a URL for the user
    to open. We extract that URL and send it to the client, then wait for
    the process to exit (which means auth completed or was cancelled).
    """
    env = dict(os.environ)
    env["CODEX_HOME"] = str(config_dir)
    # Prevent the CLI from trying to open a browser on the server.
    env["BROWSER"] = "echo"

    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            "login",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except Exception as exc:
        yield json_mod.dumps({"step": "error", "message": str(exc)}) + "\n"
        return

    try:
        assert proc.stdout is not None
        deadline = asyncio.get_event_loop().time() + _CODEX_LOGIN_TIMEOUT
        url_sent = False

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                yield json_mod.dumps({"step": "error", "message": "Login timed out"}) + "\n"
                break

            try:
                raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except TimeoutError:
                yield json_mod.dumps({"step": "error", "message": "Login timed out"}) + "\n"
                break

            if not raw_line:
                break

            line = _ANSI_RE.sub("", raw_line.decode("utf-8", errors="replace")).strip()
            if not line:
                continue

            # Look for the OAuth URL
            url_match = re_mod.search(r"(https?://\S+)", line)
            if url_match and not url_sent:
                auth_url = url_match.group(1)
                # The CLI prints a localhost URL first (callback server), then the
                # real auth URL.  Only send the auth.openai.com one.
                # Check the host portion only — the query string contains an
                # encoded localhost redirect_uri which is expected.
                if auth_url.lower().startswith("https://auth."):
                    yield json_mod.dumps({"step": "auth_url", "url": auth_url}) + "\n"
                    yield (
                        json_mod.dumps({"step": "waiting", "message": "Waiting for browser authentication..."}) + "\n"
                    )
                    url_sent = True

            lower = line.lower()
            if "logged in" in lower or "success" in lower or "authenticated" in lower:
                yield json_mod.dumps({"step": "complete", "message": "Logged in successfully"}) + "\n"
                break

        # Wait for process to finish
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()

        verify_proc = await asyncio.create_subprocess_exec(
            binary_path,
            "login",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        verify_out, _ = await asyncio.wait_for(verify_proc.communicate(), timeout=10)
        verify_text = verify_out.decode("utf-8", errors="replace").lower() if verify_out else ""

        if verify_proc.returncode == 0 and ("logged in" in verify_text or "chatgpt" in verify_text):
            yield json_mod.dumps({"step": "complete", "message": "Logged in successfully"}) + "\n"
        elif proc.returncode == 0:
            yield json_mod.dumps({"step": "complete", "message": "Login completed"}) + "\n"

    except Exception as exc:
        logger.exception("Codex browser login failed")
        yield json_mod.dumps({"step": "error", "message": str(exc)}) + "\n"
    finally:
        if proc.returncode is None:
            proc.kill()


async def _stream_device_auth(binary_path: str, config_dir: Path) -> AsyncGenerator[str, None]:
    """Run ``codex login --device-auth`` and stream progress events."""
    env = dict(os.environ)
    env["CODEX_HOME"] = str(config_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            "login",
            "--device-auth",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except Exception as exc:
        yield json_mod.dumps({"step": "error", "message": str(exc)}) + "\n"
        return

    found_url: str | None = None
    found_code: str | None = None

    try:
        assert proc.stdout is not None
        deadline = asyncio.get_event_loop().time() + _CODEX_LOGIN_TIMEOUT

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                yield json_mod.dumps({"step": "error", "message": "Login timed out"}) + "\n"
                break

            try:
                raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except TimeoutError:
                yield json_mod.dumps({"step": "error", "message": "Login timed out"}) + "\n"
                break

            if not raw_line:
                break

            line = _ANSI_RE.sub("", raw_line.decode("utf-8", errors="replace")).strip()
            if not line:
                continue

            # Extract device URL (on its own line)
            url_match = re_mod.search(r"(https?://\S+)", line)
            if url_match and "auth" in url_match.group(1).lower():
                found_url = url_match.group(1)

            # Extract device code — variable length alphanumeric groups
            # separated by a dash (e.g. "DI4H-4AL16").
            # The URL and code appear on separate lines, so we accumulate
            # them and emit once both are captured.
            code_match = re_mod.search(r"\b([A-Z0-9]{4,6}-[A-Z0-9]{4,6})\b", line)
            if code_match:
                found_code = code_match.group(1)

            if found_url and found_code:
                yield (json_mod.dumps({"step": "device_code", "url": found_url, "code": found_code}) + "\n")
                yield (json_mod.dumps({"step": "waiting", "message": "Waiting for browser authentication..."}) + "\n")
                found_url = None
                found_code = None

            lower = line.lower()
            if "logged in" in lower or "success" in lower or "authenticated" in lower:
                yield (json_mod.dumps({"step": "complete", "message": "Logged in successfully"}) + "\n")
                break

        # Wait for process to finish
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()

        # Verify login status
        verify_proc = await asyncio.create_subprocess_exec(
            binary_path,
            "login",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        verify_out, _ = await asyncio.wait_for(verify_proc.communicate(), timeout=10)
        verify_text = verify_out.decode("utf-8", errors="replace").lower() if verify_out else ""

        if verify_proc.returncode == 0 and ("logged in" in verify_text or "chatgpt" in verify_text):
            yield (json_mod.dumps({"step": "complete", "message": "Logged in successfully"}) + "\n")
        elif proc.returncode == 0:
            yield json_mod.dumps({"step": "complete", "message": "Login completed"}) + "\n"

    except Exception as exc:
        logger.exception("Codex device-auth login failed")
        yield json_mod.dumps({"step": "error", "message": str(exc)}) + "\n"
    finally:
        if proc.returncode is None:
            proc.kill()


@router.get(
    "/tools/codex/login/status",
    summary="Check Codex ChatGPT login status",
    description=(
        "Runs `codex login status` with the managed CODEX_HOME and returns "
        "whether the user is logged in via ChatGPT subscription."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def codex_login_status(request: Request) -> dict[str, Any]:
    """Check whether Codex is authenticated via ChatGPT subscription."""
    tool_manager: ToolManager = request.app.state.tool_manager
    tool_settings: ToolSettingsManager = request.app.state.tool_settings

    binary_path = tool_manager.get_binary_path("codex")
    if not binary_path:
        return {"logged_in": False, "method": None}

    config_dir = tool_settings.get_config_dir("codex")
    config_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["CODEX_HOME"] = str(config_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            "login",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = _ANSI_RE.sub("", stdout.decode("utf-8", errors="replace")).lower() if stdout else ""

        if proc.returncode == 0 and ("logged in" in output or "chatgpt" in output):
            method = "ChatGPT" if "chatgpt" in output else None
            return {"logged_in": True, "method": method}

        return {"logged_in": False, "method": None}
    except Exception:
        logger.warning("Failed to check Codex login status", exc_info=True)
        return {"logged_in": False, "method": None}


@router.post(
    "/sessions/{session_id}/cancel",
    summary="Cancel a running session",
    description=(
        "Terminates a running session by killing any active subprocess, "
        "cancelling background tasks, and marking the session as CANCELLED."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def cancel_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.cancel_session(session_id)
    except ValueError:
        # Session not in memory — check if it exists in the DB with a non-terminal status
        db_session_factory = request.app.state.db_session_factory
        if db_session_factory is not None:
            try:
                session_uuid = uuid.UUID(session_id)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id}") from None
            async with db_session_factory() as db:
                row = await db.get(SessionModel, session_uuid)
                if row is not None and row.status not in ("completed", "failed", "cancelled"):
                    row.status = "cancelled"
                    row.ended_at = datetime.now(UTC)
                    await db.commit()
                    logger.info("Session %s cancelled in DB (was not in memory)", session_id)
                    return {
                        "session_id": session_id,
                        "status": "cancelled",
                        "cancelled_at": row.ended_at.isoformat(),
                    }
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s cancelled via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "cancelled_at": session.ended_at.isoformat() if session.ended_at else None,
    }


@router.post(
    "/sessions/{session_id}/end",
    summary="End a session",
    description=(
        "Gracefully ends a session after user confirmation. "
        "Kills any active subprocess and marks the session as COMPLETED."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def end_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.end_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s ended via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }


@router.post(
    "/sessions/{session_id}/pause",
    summary="Pause a session",
    description=(
        "Pauses an active session. The session's subprocess (if any) continues "
        "running and output is buffered. New prompts are rejected until resumed."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def pause_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.pause_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s paused via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "paused_at": session.paused_at.isoformat() if session.paused_at else None,
    }


@router.post(
    "/sessions/{session_id}/resume",
    summary="Resume a paused session",
    description=(
        "Resumes a paused session. The client can then subscribe to the session's "
        "output channel to receive all buffered messages, then send new prompts."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def resume_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.resume_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s resumed via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
    }


@router.post(
    "/sessions/{session_id}/restore",
    summary="Restore an archived session",
    description=(
        "Restores a completed/failed/cancelled session from the database back to "
        "active state. Rebuilds conversation history and buffer. For Claude Code "
        "sessions, prepares the executor for lazy restart on the next message."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def restore_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.restore_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s restored via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "session_type": session.session_type.value,
        "title": session.title,
    }


class RenameSessionRequest(BaseModel):
    """Body for the rename-session endpoint."""

    title: str | None = None


@router.patch(
    "/sessions/{session_id}/title",
    summary="Rename a session",
    description=(
        "Set or clear a session's title. The title must be at most 200 characters. "
        "Sending null or a blank string clears the title."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    request: Request,
) -> dict[str, Any]:
    session_manager: SessionManager = request.app.state.session_manager
    db_session_factory = request.app.state.db_session_factory

    # Normalize: strip whitespace, blank → None
    title = body.title.strip() if body.title else None
    if title == "":
        title = None

    if title is not None and len(title) > 200:
        raise HTTPException(status_code=422, detail="Title must be at most 200 characters")

    # Try in-memory first
    session = session_manager.get_session(session_id)
    if session is not None:
        session.title = title
        # Also update DB if available (archived sessions may exist in DB)
        if db_session_factory is not None:
            try:
                session_uuid = uuid.UUID(session_id)
            except ValueError:
                pass
            else:
                async with db_session_factory() as db:
                    row = await db.get(SessionModel, session_uuid)
                    if row is not None:
                        row.title = title
                        await db.commit()
        logger.info("Session %s renamed to %r via HTTP API", session_id, title)
        return {"session_id": session_id, "title": title}

    # Fall back to DB-only (archived sessions)
    if db_session_factory is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id}") from None

    async with db_session_factory() as db:
        row = await db.get(SessionModel, session_uuid)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        row.title = title
        await db.commit()

    logger.info("Session %s (archived) renamed to %r via HTTP API", session_id, title)
    return {"session_id": session_id, "title": title}


@router.get(
    "/projects",
    summary="List project directories",
    description=(
        "Returns directory names directly under the configured PROJECTS_DIR. "
        "Optionally filters by a case-insensitive substring match on the name."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_projects(
    request: Request,
    q: str | None = Query(None, description="Case-insensitive substring filter for project names"),
) -> dict[str, list[str]]:
    settings = request.app.state.settings
    projects_dir = Path(settings.PROJECTS_DIR).expanduser()

    if not projects_dir.is_dir():
        return {"projects": []}

    names = sorted(entry.name for entry in projects_dir.iterdir() if entry.is_dir() and not entry.name.startswith("."))

    if q:
        q_lower = q.lower()
        names = [n for n in names if q_lower in n.lower()]

    return {"projects": names}


@router.get(
    "/config",
    summary="Get server configuration",
    description=(
        "Returns all configurable server options with their current values, types, "
        "and available choices. Secret values (API keys) are masked. Options are "
        "grouped into logical sections (LLM, STT, TTS, Executors, Paths, Logging)."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def get_config(request: Request) -> dict[str, Any]:
    """Return the server configuration schema with current values."""
    settings: Settings = request.app.state.settings
    return {"options": get_config_schema(settings)}


class UpdateConfigRequest(BaseModel):
    """Body for the PATCH /api/config endpoint.

    Keys are setting names (e.g. ``LLM_PROVIDER``), values are the new
    string values to apply.
    """

    updates: dict[str, Any]


@router.patch(
    "/config",
    summary="Update server configuration",
    description=(
        "Accepts partial config updates. Validates keys against the configurable "
        "set, persists changes to the .env file, reloads settings, and hot-reloads "
        "affected components (LLM client, STT/TTS providers). Returns the updated "
        "config schema."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def update_config(body: UpdateConfigRequest, request: Request) -> dict[str, Any]:
    """Apply partial config updates and return the refreshed config schema."""
    invalid_keys = set(body.updates.keys()) - CONFIGURABLE_KEYS
    if invalid_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or non-configurable keys: {', '.join(sorted(invalid_keys))}",
        )

    env_updates: dict[str, str] = {}
    for key, value in body.updates.items():
        if isinstance(value, bool):
            env_updates[key] = "true" if value else "false"
        else:
            env_updates[key] = str(value)

    update_env_file(env_updates)

    new_settings = Settings()  # type: ignore[call-arg]
    request.app.state.settings = new_settings

    _reload_components(request, new_settings)

    logger.info("Config updated: %s", ", ".join(sorted(body.updates.keys())))
    return {"options": get_config_schema(new_settings)}


def _reload_components(request: Request, settings: Settings) -> None:
    """Hot-reload server components that depend on settings.

    Recreates the LLM client, STT provider, and TTS provider from the new
    settings and patches the prompt router to use the new LLM client.
    """
    tool_registry = request.app.state.tool_registry

    old_llm: LLMClient = request.app.state.llm_client
    new_llm = LLMClient(settings, tool_registry)
    request.app.state.llm_client = new_llm

    prompt_router = request.app.state.prompt_router
    prompt_router._llm = new_llm
    prompt_router._settings = settings

    request.app.state.stt_provider = create_stt_provider(settings.STT_PROVIDER, settings.STT_API_KEY)
    request.app.state.tts_provider = create_tts_provider(settings.TTS_PROVIDER, settings.TTS_API_KEY)

    asyncio.get_event_loop().create_task(old_llm.close())
