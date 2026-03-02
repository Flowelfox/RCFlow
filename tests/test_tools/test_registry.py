from pathlib import Path

from src.tools.registry import ToolRegistry


class TestToolRegistry:
    def test_load_and_list(self):
        registry = ToolRegistry()
        tools_dir = Path(__file__).parent.parent.parent / "tools"
        registry.load_from_directory(tools_dir)

        tools = registry.list_tools()
        assert len(tools) >= 1

    def test_get_tool(self):
        registry = ToolRegistry()
        tools_dir = Path(__file__).parent.parent.parent / "tools"
        registry.load_from_directory(tools_dir)

        tool = registry.get("shell_exec")
        assert tool is not None
        assert tool.name == "shell_exec"

    def test_get_nonexistent_tool(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_to_anthropic_tools(self):
        registry = ToolRegistry()
        tools_dir = Path(__file__).parent.parent.parent / "tools"
        registry.load_from_directory(tools_dir)

        anthropic_tools = registry.to_anthropic_tools()
        assert len(anthropic_tools) >= 1
        tool_names = [t["name"] for t in anthropic_tools]
        assert "shell_exec" in tool_names
        shell_tool = next(t for t in anthropic_tools if t["name"] == "shell_exec")
        assert "input_schema" in shell_tool
        assert "description" in shell_tool
