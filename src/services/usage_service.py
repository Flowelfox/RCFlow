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

import hashlib
import json
import logging
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from pathlib import Path

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


def _token_from_json(raw: str) -> str | None:
    """Extract ``claudeAiOauth.accessToken`` from a credentials JSON blob."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    token = data.get("claudeAiOauth", {}).get("accessToken")
    return token if isinstance(token, str) and token else None


def _read_token_from_file(config_dir: Path) -> str | None:
    """Read the token from ``<config_dir>/.credentials.json`` (Linux/Windows)."""
    try:
        raw = (config_dir / ".credentials.json").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return _token_from_json(raw)


def _keychain_service(config_dir: Path) -> str:
    """Service name Claude Code uses in the macOS login Keychain for ``config_dir``.

    Claude Code (macOS) stores credentials in the login Keychain rather than a
    file, under ``Claude Code-credentials-<hash>`` where ``<hash>`` is the first
    8 hex chars of the SHA-256 of the ``CLAUDE_CONFIG_DIR`` path.
    """
    digest = hashlib.sha256(str(config_dir).encode("utf-8")).hexdigest()[:8]
    return f"Claude Code-credentials-{digest}"


def _read_token_from_keychain(config_dir: Path) -> str | None:
    """Read the managed Claude Code token from the macOS login Keychain.

    No-op off macOS. The worker may get a one-time Keychain authorisation prompt
    the first time it reads the item Claude Code created (then "Always Allow"
    caches it). Returns ``None`` on any error.
    """
    if sys.platform != "darwin":
        return None
    service = _keychain_service(config_dir)
    try:
        proc = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    return _token_from_json(raw) if raw else None


def read_oauth_token(config_dir: Path | None) -> str | None:
    """Return the RCFlow-managed Claude Code subscription token, or ``None``.

    ``config_dir`` is RCFlow's **managed** Claude Code config dir
    (``CLAUDE_CONFIG_DIR`` for the managed agent). The token lives either in a
    ``.credentials.json`` file there (Linux/Windows) or in the macOS login
    Keychain keyed by that dir (macOS). Only the managed agent is read — never an
    external ``~/.claude`` login, which may be a different account. Returns
    ``None`` (never raises) when no managed subscription token is present, e.g.
    API-key auth. Re-read every poll so token rotations are picked up.
    """
    if config_dir is None:
        return None
    return _read_token_from_file(config_dir) or _read_token_from_keychain(config_dir)


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
