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
        """Look up a tool by internal name, mention name, or display name (case-insensitive)."""
        tool = self._tools.get(name)
        if tool is not None:
            return tool
        name_lower = name.lower()
        for t in self._tools.values():
            if t.mention_name.lower() == name_lower or t.display_name.lower() == name_lower:
                return t
        return None

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

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Convert all registered tools to OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]
