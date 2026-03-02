import json
import tempfile
from pathlib import Path

import pytest

from src.tools.loader import load_tool_file, load_tools_from_directory


@pytest.fixture
def sample_tool_json() -> dict:
    return {
        "name": "test_tool",
        "description": "A test tool",
        "version": "1.0.0",
        "session_type": "one-shot",
        "llm_context": "stateless",
        "executor": "shell",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run"},
            },
            "required": ["command"],
        },
        "executor_config": {
            "shell": {
                "command_template": "{command}",
                "shell": "/bin/bash",
                "capture_stderr": True,
                "stream_output": True,
            }
        },
    }


class TestLoadToolFile:
    def test_load_valid_tool(self, sample_tool_json: dict):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_tool_json, f)
            f.flush()
            tool = load_tool_file(Path(f.name))

        assert tool.name == "test_tool"
        assert tool.executor == "shell"
        assert tool.session_type == "one-shot"

    def test_invalid_executor_raises(self, sample_tool_json: dict):
        sample_tool_json["executor"] = "invalid"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(sample_tool_json, f)
            f.flush()
            with pytest.raises(ValueError, match="invalid executor"):
                load_tool_file(Path(f.name))


class TestLoadToolsFromDirectory:
    def test_load_from_project_tools_dir(self):
        tools_dir = Path(__file__).parent.parent.parent / "tools"
        tools = load_tools_from_directory(tools_dir)
        assert len(tools) >= 1
        assert any(t.name == "shell_exec" for t in tools)

    def test_load_from_nonexistent_dir(self):
        tools = load_tools_from_directory(Path("/nonexistent/path"))
        assert tools == []
