import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse

import httpx

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

# Match ${ENV_VAR_NAME} for environment variable substitution
_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")

# ---------------------------------------------------------------------------
# SSRF protection (F2)
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # RFC-1918 private ranges
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Link-local / cloud instance-metadata (169.254.169.254, etc.)
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # Unspecified / broadcast
    ipaddress.ip_network("0.0.0.0/8"),
    # Unique-local IPv6 (fc00::/7 covers fd00::/8 too)
    ipaddress.ip_network("fc00::/7"),
]


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if *ip_str* falls in a private or reserved address range."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        return True  # Fail closed on unparseable addresses


async def _validate_url_no_ssrf(url: str) -> None:
    """Raise ValueError if *url* resolves to a private or reserved address.

    Protects against Server-Side Request Forgery (SSRF) by blocking HTTP
    tool requests to RFC-1918 private ranges, loopback, link-local (including
    cloud metadata endpoints such as 169.254.169.254), and IPv6 private
    ranges.  DNS is resolved here and each returned address is validated so
    that DNS-rebinding attacks are also mitigated.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ValueError(f"Invalid URL: {url!r}") from exc

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL has no hostname: {url!r}")

    port = parsed.port or (443 if parsed.scheme in ("https", "wss") else 80)

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM),
        )
    except OSError as exc:
        raise ValueError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    for info in infos:
        ip: str = str(info[4][0])
        if _is_blocked_ip(ip):
            raise ValueError(f"HTTP tool request blocked: {hostname!r} resolves to private/reserved address {ip!r}")


def _substitute_env_vars(text: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variable values."""

    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name, "")
        if not value:
            logger.warning("Environment variable '%s' not set, using empty string", var_name)
        return value

    return _ENV_VAR_PATTERN.sub(replace, text)


class HttpExecutor(BaseExecutor):
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def execute(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> ExecutionResult:
        config = tool.get_http_config()

        url = _substitute_env_vars(config.url_template.format(**parameters))
        await _validate_url_no_ssrf(url)
        headers = {k: _substitute_env_vars(v) for k, v in config.headers.items()}

        body = None
        if config.body_template:
            body = _substitute_env_vars(config.body_template.format(**parameters))

        try:
            async with httpx.AsyncClient(timeout=config.timeout) as client:
                self._client = client
                response = await client.request(
                    method=config.method,
                    url=url,
                    headers=headers,
                    content=body,
                )

            response_text = response.text

            if config.response_path:
                try:
                    data = json.loads(response_text)
                    response_text = str(_extract_json_path(data, config.response_path))
                except Exception:
                    logger.warning(
                        "Failed to extract response_path '%s', returning full response",
                        config.response_path,
                    )

            return ExecutionResult(
                output=response_text,
                exit_code=0 if response.is_success else response.status_code,
                error=None if response.is_success else f"HTTP {response.status_code}",
                metadata={"status_code": response.status_code, "url": url},
            )
        except httpx.TimeoutException:
            return ExecutionResult(
                output="",
                exit_code=-1,
                error=f"HTTP request timed out after {config.timeout} seconds",
            )
        except Exception as e:
            return ExecutionResult(
                output="",
                exit_code=-1,
                error=str(e),
            )
        finally:
            self._client = None

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        config = tool.get_http_config()

        url = _substitute_env_vars(config.url_template.format(**parameters))
        await _validate_url_no_ssrf(url)
        headers = {k: _substitute_env_vars(v) for k, v in config.headers.items()}

        body = None
        if config.body_template:
            body = _substitute_env_vars(config.body_template.format(**parameters))

        async with httpx.AsyncClient(timeout=config.timeout) as client:
            self._client = client
            async with client.stream(config.method, url, headers=headers, content=body) as response:
                async for chunk in response.aiter_text():
                    yield ExecutionChunk(stream="response", content=chunk)

        self._client = None

    async def send_input(self, data: str) -> None:
        raise RuntimeError("HTTP executor does not support interactive input")

    async def cancel(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


def _extract_json_path(data: Any, path: str) -> Any:
    """Simple JSON path extraction supporting dot notation (e.g., '$.data.summary')."""
    parts = path.lstrip("$").lstrip(".").split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(f"Cannot traverse into {type(current)} with key '{part}'")
    return current
