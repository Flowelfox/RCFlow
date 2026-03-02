"""Tests for the POML-based PromptBuilder."""

from pathlib import Path

import pytest

from src.prompts.builder import PromptBuilder


@pytest.fixture
def tmp_poml(tmp_path: Path) -> Path:
    """Create a minimal POML template for testing."""
    tpl = tmp_path / "test.poml"
    tpl.write_text(
        "<poml>\n"
        "  <role captionStyle=\"hidden\">Hello, {{ name }}!</role>\n"
        "  <p>Work in {{ directory }}. Be concise.</p>\n"
        "</poml>\n"
    )
    return tpl


class TestPromptBuilder:
    def test_basic_rendering(self, tmp_poml: Path) -> None:
        builder = PromptBuilder(template=tmp_poml)
        result = builder.build(name="Alice", directory="/tmp")
        assert "Hello, Alice!" in result
        assert "Work in /tmp." in result

    def test_variable_substitution(self, tmp_poml: Path) -> None:
        builder = PromptBuilder(template=tmp_poml)
        result = builder.build(name="Bob", directory="/home/bob")
        assert "Hello, Bob!" in result
        assert "Work in /home/bob." in result

    def test_missing_variables_raises(self, tmp_poml: Path) -> None:
        """POML raises RuntimeError when referenced variables are not provided."""
        builder = PromptBuilder(template=tmp_poml)
        with pytest.raises(RuntimeError):
            builder.build(name="Alice")

    def test_missing_template_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.poml"
        with pytest.raises(FileNotFoundError, match="nonexistent.poml"):
            PromptBuilder(template=missing)

    def test_real_template_renders(self) -> None:
        """Integration test: the shipped system_prompt.poml renders correctly."""
        builder = PromptBuilder()
        result = builder.build(projects_dir="/home/user/Projects", os_name="Linux")
        assert "RCFlow" in result
        assert "/home/user/Projects" in result

    def test_real_template_substitution(self) -> None:
        """All {{ projects_dir }} placeholders are replaced in the real template."""
        builder = PromptBuilder()
        result = builder.build(projects_dir="/test/dir", os_name="Linux")
        assert "{{ projects_dir }}" not in result
        assert "/test/dir" in result

    def test_real_template_has_key_sections(self) -> None:
        """The rendered prompt contains expected content sections."""
        builder = PromptBuilder()
        result = builder.build(projects_dir="/home/user/Projects", os_name="Linux")
        assert "text-to-speech" in result
        assert "claude_code" in result
        assert "Project conventions" in result

    def test_real_template_os_name_substitution(self) -> None:
        """The os_name variable is substituted into the role description."""
        builder = PromptBuilder()
        result = builder.build(projects_dir="/home/user/Projects", os_name="Windows")
        assert "Windows machine" in result
        assert "{{ os_name }}" not in result
