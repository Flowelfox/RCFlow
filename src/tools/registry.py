import logging
from pathlib import Path
from typing import Any

from src.tools.loader import ToolDefinition, load_tools_from_directory

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def load_from_directory(self, tools_dir: Path) -> None:
        tools = load_tools_from_directory(tools_dir)
        for tool in tools:
            if tool.name in self._tools:
                logger.warning("Duplicate tool name '%s', overwriting previous definition", tool.name)
            self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        """Convert all registered tools to Anthropic Messages API tool format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in self._tools.values()
        ]
