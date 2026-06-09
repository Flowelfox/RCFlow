"""Claude subscription usage (rate-limit quota) client.

Fetches the account-level subscription quota windows — the rolling **5-hour** and
**7-day** "used %" plus their reset times, and per-model 7-day windows where the
API reports them — that the Claude Agent SDK does *not* expose to headless
consumers.  Tools like *claudewatch* surface the same data via the Claude Code
``statusLine`` JSON, but ``statusLine`` never fires under the headless SDK, so we
read it directly from the (undocumented, OAuth-gated) usage endpoint instead::

    GET https://api.anthropic.com/api/oauth/usage
    Authorization: Bearer <subscription accessToken>
    anthropic-beta: oauth-2025-04-20

The bearer token is the Claude.ai subscription OAuth access token that Claude
Code stores in ``~/.claude/.credentials.json`` (``claudeAiOauth.accessToken``)
and keeps refreshed; we re-read it on every poll so refreshes are picked up.

This is **subscription-only**: workers authenticated with a plain API key have no
such token, so :func:`read_oauth_token` returns ``None`` and the poller reports
the quota as unavailable.  The endpoint is undocumented, so every field is parsed
defensively and treated as optional.

Usage::

    token = read_oauth_token()
    if token:
        async with UsageService(token) as svc:
            data = await svc.fetch_usage()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"
# Beta flag the OAuth usage endpoint requires; pinned so the response stays
# stable across rolling API changes.
OAUTH_BETA = "oauth-2025-04-20"

# Quota windows the endpoint reports, in display order.  ``five_hour`` /
# ``seven_day`` are the headline windows; the rest are per-model / promotional
# buckets that are frequently ``null`` and only surfaced when present.
USAGE_WINDOWS: tuple[str, ...] = (
    "five_hour",
    "seven_day",
    "seven_day_opus",
    "seven_day_sonnet",
)


class UsageServiceError(Exception):
    """Raised when the usage endpoint returns an error or is unreachable.

    ``retry_after`` carries the server's ``Retry-After`` hint (seconds) on a 429
    so the caller can back off for at least that long instead of re-polling on
    its fixed schedule.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _credentials_path() -> Path:
    """Path to Claude Code's credentials file, honouring ``CLAUDE_CONFIG_DIR``."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return Path(config_dir).expanduser() / ".credentials.json"


def read_oauth_token() -> str | None:
    """Return the current subscription OAuth access token, or ``None``.

    Reads ``claudeAiOauth.accessToken`` from Claude Code's credentials file
    (``CLAUDE_CONFIG_DIR`` or ``~/.claude``).  Returns ``None`` — never raises —
    when the file is missing, unreadable, malformed, or the worker is on API-key
    auth (no subscription token).  Re-read every poll so token refreshes written
    by Claude Code are picked up automatically.
    """
    path = _credentials_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    token = data.get("claudeAiOauth", {}).get("accessToken")
    return token if isinstance(token, str) and token else None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form) into a float, or None.

    Only the numeric delta-seconds form is honoured; an HTTP-date form is
    ignored (the caller falls back to its own backoff), since the endpoint
    returns delta-seconds in practice.
    """
    if not value:
        return None
    try:
        secs = float(value.strip())
    except ValueError:
        return None
    return secs if secs >= 0 else None


def _parse_window(value: Any) -> dict[str, Any] | None:
    """Normalise one quota window to ``{utilization, resets_at}`` or ``None``.

    ``utilization`` is the 0-100 "used %" the endpoint returns; ``resets_at`` is
    an ISO-8601 timestamp (may be ``null``).  Returns ``None`` for absent or
    malformed windows so callers can simply omit them.
    """
    if not isinstance(value, dict):
        return None
    util = value.get("utilization")
    if not isinstance(util, (int, float)):
        return None
    resets_at = value.get("resets_at")
    return {
        "utilization": float(util),
        "resets_at": resets_at if isinstance(resets_at, str) else None,
    }


def parse_usage(body: Any) -> dict[str, dict[str, Any] | None]:
    """Parse the raw endpoint body into the known quota windows.

    Defensive by design — the endpoint is undocumented, so unknown fields are
    ignored and any missing/malformed window becomes ``None``.
    """
    if not isinstance(body, dict):
        return {window: None for window in USAGE_WINDOWS}
    return {window: _parse_window(body.get(window)) for window in USAGE_WINDOWS}


class UsageService:
    """Async client for the Claude subscription usage endpoint.

    Usable as an async context manager or standalone (call :meth:`aclose` when
    done).  Authentication is the subscription OAuth bearer token from
    :func:`read_oauth_token`.
    """

    def __init__(self, token: str) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": OAUTH_BETA,
            },
            timeout=15.0,
        )

    async def fetch_usage(self) -> dict[str, dict[str, Any] | None]:
        """Fetch and parse the current subscription quota windows.

        Returns the parsed windows (see :func:`parse_usage`).  Raises
        :class:`UsageServiceError` on transport errors or a non-2xx response.
        """
        try:
            resp = await self._client.get(USAGE_API_URL)
        except httpx.TimeoutException as exc:
            raise UsageServiceError("Usage API request timed out") from exc
        except httpx.RequestError as exc:
            raise UsageServiceError(f"Usage API request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise UsageServiceError(
                f"Usage API returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                retry_after=_parse_retry_after(resp.headers.get("Retry-After")),
            )
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise UsageServiceError("Usage API returned non-JSON body") from exc
        return parse_usage(body)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> UsageService:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()
