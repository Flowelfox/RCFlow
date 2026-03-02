from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from src.tools.loader import ToolDefinition


@dataclass
class ExecutionChunk:
    """A single chunk of output from a tool execution."""

    stream: str  # "stdout", "stderr", or "response"
    content: str


@dataclass
class ExecutionResult:
    """Final result of a tool execution."""

    output: str
    exit_code: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseExecutor(ABC):
    @abstractmethod
    async def execute(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> ExecutionResult:
        """Execute a tool and return the full result."""

    @abstractmethod
    def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Execute a tool and stream output chunks."""
        ...

    @abstractmethod
    async def send_input(self, data: str) -> None:
        """Send input to a running interactive process. Only valid for long-running tools."""

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel a running execution."""
