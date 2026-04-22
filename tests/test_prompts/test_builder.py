"""Tests for the Jinja2-based PromptBuilder."""

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from src.prompts.builder import PromptBuilder


@pytest.fixture
def tmp_template(tmp_path: Path) -> Path:
    """Create a minimal Jinja2 template for testing."""
    tpl = tmp_path / "test.j2"
    tpl.write_text("Hello, {{ name }}!\nWork in {{ directory }}. Be concise.\n")
    return tpl


class TestPromptBuilder:
    def test_basic_rendering(self, tmp_template: Path) -> None:
        builder = PromptBuilder(template=tmp_template)
        result = builder.build(name="Alice", directory="/tmp")
        assert "Hello, Alice!" in result
        assert "Work in /tmp." in result

    def test_variable_substitution(self, tmp_template: Path) -> None:
        builder = PromptBuilder(template=tmp_template)
        result = builder.build(name="Bob", directory="/home/bob")
        assert "Hello, Bob!" in result
        assert "Work in /home/bob." in result

    def test_missing_variables_raises(self, tmp_template: Path) -> None:
        """Jinja2 StrictUndefined raises UndefinedError when variables are missing."""
        builder = PromptBuilder(template=tmp_template)
        with pytest.raises(UndefinedError):
            builder.build(name="Alice")

    def test_missing_template_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.j2"
        with pytest.raises(FileNotFoundError, match=r"nonexistent.j2"):
            PromptBuilder(template=missing)

    def test_real_template_renders(self) -> None:
        """Integration test: the shipped system_prompt.j2 renders correctly."""
        builder = PromptBuilder()
        result = builder.build(projects_dirs="/home/user/Projects", os_name="Linux")
        assert "RCFlow" in result
        assert "/home/user/Projects" in result

    def test_real_template_substitution(self) -> None:
        """All {{ projects_dirs }} placeholders are replaced in the real template."""
        builder = PromptBuilder()
        result = builder.build(projects_dirs="/test/dir", os_name="Linux")
        assert "{{ projects_dirs }}" not in result
        assert "/test/dir" in result

    def test_real_template_has_key_sections(self) -> None:
        """The rendered prompt contains expected content sections."""
        builder = PromptBuilder()
        result = builder.build(projects_dirs="/home/user/Projects", os_name="Linux")
        assert "claude_code" in result
        assert "Project resolution" in result
        assert "Task routing" in result

    def test_real_template_os_name_substitution(self) -> None:
        """The os_name variable is substituted into the role description."""
        builder = PromptBuilder()
        result = builder.build(projects_dirs="/home/user/Projects", os_name="Windows")
        assert "Windows machine" in result
        assert "{{ os_name }}" not in result
