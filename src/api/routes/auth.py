from __future__ import annotations

import asyncio
import json as json_mod
import logging
import os
import re as re_mod
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.deps import verify_http_api_key

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from src.services.tool_manager import ToolManager
    from src.services.tool_settings import ToolSettingsManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Auth"])

# ---------------------------------------------------------------------------
# Codex ChatGPT Login
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Claude Code Anthropic Subscription Login
# ---------------------------------------------------------------------------

_CLAUDE_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_CLAUDE_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_CLAUDE_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_CLAUDE_OAUTH_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
_CLAUDE_OAUTH_SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers"


class _ClaudeCodeLoginBody(BaseModel):
    code: str


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    import base64  # noqa: PLC0415
    import hashlib  # noqa: PLC0415
    import secrets  # noqa: PLC0415

    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@router.post(
    "/tools/claude_code/login",
    summary="Start Claude Code Anthropic login",
    description=(
        "Generates a PKCE OAuth URL for Anthropic login and returns it. "
        "The client opens this URL in a browser. After the user authenticates, "
        "they receive a code to submit via POST /tools/claude_code/login/code."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def claude_code_login(request: Request) -> dict[str, Any]:
    """Generate OAuth URL and return it for the client to open."""
    import secrets  # noqa: PLC0415
    from urllib.parse import urlencode  # noqa: PLC0415

    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    # Match the JS URLSearchParams behavior (uses + for spaces, same as default urlencode)
    params = {
        "code": "true",
        "client_id": _CLAUDE_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _CLAUDE_OAUTH_REDIRECT_URI,
        "scope": _CLAUDE_OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{_CLAUDE_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"

    # Store verifier for the code exchange step
    request.app.state._claude_login_verifier = verifier
    request.app.state._claude_login_state = state

    return {"auth_url": auth_url}


@router.post(
    "/tools/claude_code/login/code",
    summary="Submit OAuth code to complete Claude Code login",
    description=(
        "After the user authenticates in the browser and receives a code, "
        "submit it here. The server exchanges the code for OAuth tokens "
        "using PKCE and stores the credentials for Claude Code."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def claude_code_login_code(request: Request, body: _ClaudeCodeLoginBody) -> dict[str, Any]:
    """Exchange the OAuth code for tokens and store credentials."""
    import httpx  # noqa: PLC0415

    tool_settings: ToolSettingsManager = request.app.state.tool_settings

    verifier: str | None = getattr(request.app.state, "_claude_login_verifier", None)
    if not verifier:
        raise HTTPException(status_code=409, detail="No active login. Call POST /tools/claude_code/login first.")

    config_dir = tool_settings.get_config_dir("claude_code")
    config_dir.mkdir(parents=True, exist_ok=True)

    raw_code = body.code.strip()
    # The callback page concatenates code#state — split and use only the code part
    code = raw_code.split("#")[0] if "#" in raw_code else raw_code
    state: str | None = getattr(request.app.state, "_claude_login_state", None)

    payload = {
        "grant_type": "authorization_code",
        "client_id": _CLAUDE_OAUTH_CLIENT_ID,
        "code": code,
        "redirect_uri": _CLAUDE_OAUTH_REDIRECT_URI,
        "code_verifier": verifier,
        "state": state or "",
    }
    # Exchange authorization code for tokens (Anthropic expects JSON, not form-encoded)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _CLAUDE_OAUTH_TOKEN_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}") from exc
    finally:
        # Clear verifier regardless of outcome
        request.app.state._claude_login_verifier = None
        request.app.state._claude_login_state = None

    if resp.status_code != 200:
        detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
        logger.warning("Claude Code OAuth token exchange failed (%d): %s", resp.status_code, detail)
        raise HTTPException(status_code=502, detail=f"Token exchange returned {resp.status_code}: {detail}")

    token_data = resp.json()
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)

    if not access_token:
        raise HTTPException(status_code=502, detail="Token exchange succeeded but no access_token returned")

    # Build credentials in the format Claude Code expects
    import time  # noqa: PLC0415

    credentials = {
        "claudeAiOauth": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": int(time.time() * 1000) + expires_in * 1000,
            "scopes": _CLAUDE_OAUTH_SCOPES.split(),
            "subscriptionType": None,
            "rateLimitTier": None,
        }
    }

    # Write credentials file
    cred_path = config_dir / ".credentials.json"
    cred_path.write_text(json_mod.dumps(credentials) + "\n", encoding="utf-8")
    cred_path.chmod(0o600)

    logger.info("Claude Code OAuth credentials saved to %s", cred_path)

    # Auto-set provider to anthropic_login on successful login
    tool_settings.update_settings("claude_code", {"provider": "anthropic_login"})

    # Verify login via CLI to get subscription info
    tool_manager: ToolManager = request.app.state.tool_manager
    binary_path = tool_manager.get_binary_path("claude_code")
    if binary_path:
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        try:
            verify_proc = await asyncio.create_subprocess_exec(
                binary_path,
                "auth",
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            verify_out, _ = await asyncio.wait_for(verify_proc.communicate(), timeout=10)
            output = verify_out.decode("utf-8", errors="replace") if verify_out else ""
            data = json_mod.loads(output)
            if data.get("loggedIn"):
                # Update credentials with subscription info from CLI
                credentials["claudeAiOauth"]["subscriptionType"] = data.get("subscriptionType")
                cred_path.write_text(json_mod.dumps(credentials) + "\n", encoding="utf-8")
                return {
                    "logged_in": True,
                    "email": data.get("email"),
                    "subscription": data.get("subscriptionType"),
                }
        except Exception:
            logger.warning("Failed to verify login after token exchange", exc_info=True)

    return {"logged_in": True, "email": None, "subscription": None}


@router.get(
    "/tools/claude_code/login/status",
    summary="Check Claude Code Anthropic login status",
    description=(
        "Runs `claude auth status --json` with the managed config directory and "
        "returns whether the user is logged in via Anthropic subscription."
    ),
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def claude_code_login_status(request: Request) -> dict[str, Any]:
    """Check whether Claude Code is authenticated via Anthropic subscription."""
    tool_manager: ToolManager = request.app.state.tool_manager
    tool_settings: ToolSettingsManager = request.app.state.tool_settings

    binary_path = tool_manager.get_binary_path("claude_code")
    if not binary_path:
        return {"logged_in": False, "method": None, "email": None, "subscription": None}

    config_dir = tool_settings.get_config_dir("claude_code")
    config_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            "auth",
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""

        try:
            data = json_mod.loads(output)
            if data.get("loggedIn"):
                return {
                    "logged_in": True,
                    "method": data.get("authMethod"),
                    "email": data.get("email"),
                    "subscription": data.get("subscriptionType"),
                }
        except (json_mod.JSONDecodeError, AttributeError):
            pass

        return {"logged_in": False, "method": None, "email": None, "subscription": None}
    except Exception:
        logger.warning("Failed to check Claude Code login status", exc_info=True)
        return {"logged_in": False, "method": None, "email": None, "subscription": None}


@router.post(
    "/tools/claude_code/logout",
    summary="Log out of Claude Code Anthropic account",
    description="Runs `claude auth logout` with the managed config directory.",
    tags=["Tools"],
    dependencies=[Depends(verify_http_api_key)],
)
async def claude_code_logout(request: Request) -> dict[str, Any]:
    """Log out from Claude Code Anthropic subscription."""
    tool_manager: ToolManager = request.app.state.tool_manager
    tool_settings: ToolSettingsManager = request.app.state.tool_settings

    binary_path = tool_manager.get_binary_path("claude_code")
    if not binary_path:
        raise HTTPException(status_code=400, detail="Claude Code is not installed")

    config_dir = tool_settings.get_config_dir("claude_code")

    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)

    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            "auth",
            "logout",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        # Reset provider to global on logout
        tool_settings.update_settings("claude_code", {"provider": ""})
        return {"logged_out": True}
    except Exception:
        logger.warning("Failed to log out of Claude Code", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to log out") from None
