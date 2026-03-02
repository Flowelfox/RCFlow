import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

# Match ${ENV_VAR_NAME} for environment variable substitution
_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


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
