import asyncio
import hmac
import json
import logging

from fastapi import Depends, HTTPException, WebSocket, WebSocketException, status
from fastapi.security import APIKeyHeader

from src.config import get_settings

logger = logging.getLogger(__name__)

_FIRST_MESSAGE_AUTH_TIMEOUT = 10.0  # seconds to wait for auth message


async def verify_ws_api_key(
    websocket: WebSocket,
    api_key: str,
) -> str:
    """Verify the API key and optional Origin header for WebSocket connections.

    Call BEFORE ``websocket.accept()`` when authenticating via query parameter.

    Origin validation (F6): when ``WS_ALLOWED_ORIGINS`` is configured, any
    connection that supplies an Origin header must match the allowlist.
    Native-app clients that omit the Origin header are always allowed.
    """
    settings = get_settings()

    # --- Origin validation ---
    if settings.WS_ALLOWED_ORIGINS:
        origin = websocket.headers.get("origin", "")
        if origin:
            allowed = {o.strip().rstrip("/") for o in settings.WS_ALLOWED_ORIGINS.split(",") if o.strip()}
            if origin.rstrip("/") not in allowed:
                logger.warning("WebSocket connection rejected: origin %r not in allowlist", origin)
                raise WebSocketException(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Origin not allowed",
                )

    # --- API key validation ---
    if not hmac.compare_digest(api_key, settings.RCFLOW_API_KEY):
        logger.warning("Invalid API key attempt")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")

    return api_key


async def handle_ws_first_message_auth(websocket: WebSocket) -> bool:
    """Authenticate a WebSocket connection via the first message.

    Call AFTER ``websocket.accept()`` when the ``api_key`` was not provided as
    a query parameter.  Expects the client to send a JSON message of the form::

        {"type": "auth", "api_key": "<key>"}

    within :data:`_FIRST_MESSAGE_AUTH_TIMEOUT` seconds.

    Returns ``True`` if authenticated; closes the socket and returns ``False``
    otherwise.
    """
    settings = get_settings()

    # --- Origin validation (F6) — same check as verify_ws_api_key ---
    if settings.WS_ALLOWED_ORIGINS:
        origin = websocket.headers.get("origin", "")
        if origin:
            allowed = {o.strip().rstrip("/") for o in settings.WS_ALLOWED_ORIGINS.split(",") if o.strip()}
            if origin.rstrip("/") not in allowed:
                logger.warning("WebSocket first-message auth rejected: origin %r not in allowlist", origin)
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Origin not allowed")
                return False

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=_FIRST_MESSAGE_AUTH_TIMEOUT)
    except TimeoutError:
        logger.warning("WebSocket first-message auth timed out")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication timeout")
        return False

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid JSON in auth message")
        return False

    if msg.get("type") != "auth":
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="First message must be auth")
        return False

    key = msg.get("api_key", "")
    if not isinstance(key, str) or not hmac.compare_digest(key, settings.RCFLOW_API_KEY):
        logger.warning("Invalid API key in first-message auth")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")
        return False

    return True


_api_key_header = APIKeyHeader(name="X-API-Key")


async def verify_http_api_key(
    api_key: str = Depends(_api_key_header),
) -> str:
    """Verify the API key from HTTP header.

    Returns the API key if valid, raises HTTPException otherwise.
    """
    settings = get_settings()

    if not hmac.compare_digest(api_key, settings.RCFLOW_API_KEY):
        logger.warning("Invalid API key attempt (HTTP)")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    return api_key
