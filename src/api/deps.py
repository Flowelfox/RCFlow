import hashlib
import hmac
import logging

from fastapi import Depends, HTTPException, Query, WebSocketException, status
from fastapi.security import APIKeyHeader

from src.config import get_settings

logger = logging.getLogger(__name__)


def hash_api_key(key: str) -> str:
    """Hash an API key for storage/comparison."""
    return hashlib.sha256(key.encode()).hexdigest()


async def verify_ws_api_key(
    api_key: str = Query(..., alias="api_key"),
) -> str:
    """Verify the API key from WebSocket query parameters.

    Returns the API key if valid, raises WebSocketException otherwise.
    """
    settings = get_settings()

    if not hmac.compare_digest(api_key, settings.RCFLOW_API_KEY):
        logger.warning("Invalid API key attempt")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")

    return api_key


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
