"""Tests for src/core/agent_prompt.py (format_agent_prompt).

Covers:
- Plain text → structured with Task / Description / Additional Content
- Single-line prompt → only Task section (no Description, no Additional Content)
- Multi-line no-code prompt → Task + Description, no Additional Content header
- Prompt with fenced code blocks → code extracted into Additional Content
- Already-structured prompt (## Task + ## Description present) → returned as-is
- Empty prompt → graceful fallback task title
- Multi-block prompt → all code blocks preserved in Additional Content
- Empty sections are omitted (no bare section headers)
"""

from __future__ import annotations

from src.core.agent_prompt import (
    _CONTENT_MARKER,
    _DESC_MARKER,
    _TASK_MARKER,
    extract_code_blocks,
    format_agent_prompt,
)


class TestFormatAgentPromptPlainText:
    def test_single_line_becomes_task(self) -> None:
        result = format_agent_prompt("Fix the login bug")
        assert result.startswith(f"{_TASK_MARKER}\nFix the login bug")

    def test_single_line_no_description_section(self) -> None:
        result = format_agent_prompt("Fix the login bug")
        assert _DESC_MARKER not in result

    def test_single_line_no_additional_content_section(self) -> None:
        result = format_agent_prompt("Fix the login bug")
        assert _CONTENT_MARKER not in result

    def test_multiline_first_line_is_task(self) -> None:
        raw = "Refactor the parser\nIt currently uses regex; switch to a proper tokeniser."
        result = format_agent_prompt(raw)
        assert f"{_TASK_MARKER}\nRefactor the parser" in result

    def test_multiline_rest_is_description(self) -> None:
        raw = "Refactor the parser\nIt currently uses regex; switch to a proper tokeniser."
        result = format_agent_prompt(raw)
        assert "switch to a proper tokeniser" in result
        assert _DESC_MARKER in result
        assert result.index(_TASK_MARKER) < result.index(_DESC_MARKER)

    def test_multiline_no_code_omits_additional_content(self) -> None:
        result = format_agent_prompt("Do a thing\nMore detail here.")
        assert _TASK_MARKER in result
        assert _DESC_MARKER in result
        assert _CONTENT_MARKER not in result

    def test_sections_in_order_with_code(self) -> None:
        result = format_agent_prompt("Task line\nDesc line.\n\n```js\ncode();\n```")
        task_pos = result.index(_TASK_MARKER)
        desc_pos = result.index(_DESC_MARKER)
        content_pos = result.index(_CONTENT_MARKER)
        assert task_pos < desc_pos < content_pos

    def test_empty_prompt_uses_fallback_task(self) -> None:
        result = format_agent_prompt("")
        assert _TASK_MARKER in result
        assert "Complete the requested task" in result

    def test_empty_prompt_no_description_section(self) -> None:
        result = format_agent_prompt("")
        assert _DESC_MARKER not in result

    def test_whitespace_only_prompt_uses_fallback_task(self) -> None:
        result = format_agent_prompt("   \n  \n  ")
        assert "Complete the requested task" in result

    def test_whitespace_only_no_empty_sections(self) -> None:
        result = format_agent_prompt("   \n  \n  ")
        assert _DESC_MARKER not in result
        assert _CONTENT_MARKER not in result


class TestFormatAgentPromptCodeBlocks:
    def test_code_block_extracted_into_additional_content(self) -> None:
        raw = "Fix this function\n\n```python\ndef bad():\n    pass\n```"
        result = format_agent_prompt(raw)
        content_section = result[result.index(_CONTENT_MARKER) :]
        assert "```python" in content_section
        assert "def bad():" in content_section

    def test_code_block_removed_from_task_and_description(self) -> None:
        raw = "Fix this function\n\n```python\ndef bad():\n    pass\n```"
        result = format_agent_prompt(raw)
        task_line = result.splitlines()[1]  # line after ## Task
        assert "```" not in task_line

    def test_multiple_code_blocks_all_preserved(self) -> None:
        raw = "Update two functions\n\n```python\ndef alpha():\n    pass\n```\n\n```python\ndef beta():\n    pass\n```"
        result = format_agent_prompt(raw)
        content_section = result[result.index(_CONTENT_MARKER) :]
        assert "def alpha():" in content_section
        assert "def beta():" in content_section

    def test_description_does_not_contain_code_fence(self) -> None:
        raw = "Rewrite helper\nUse a proper parser.\n```bash\necho hello\n```"
        result = format_agent_prompt(raw)
        desc_start = result.index(_DESC_MARKER) + len(_DESC_MARKER)
        desc_end = result.index(_CONTENT_MARKER)
        description_text = result[desc_start:desc_end]
        assert "```" not in description_text

    def test_plain_text_no_additional_content_section(self) -> None:
        # No code blocks → ## Additional Content must not appear at all
        result = format_agent_prompt("Simple task\nSome description.")
        assert _CONTENT_MARKER not in result

    def test_code_only_prompt_has_task_and_content(self) -> None:
        raw = "```python\nprint('hello')\n```"
        result = format_agent_prompt(raw)
        assert _TASK_MARKER in result
        assert _CONTENT_MARKER in result
        content_section = result[result.index(_CONTENT_MARKER) :]
        assert "print('hello')" in content_section

    def test_code_only_prompt_no_description_section(self) -> None:
        raw = "```python\nprint('hello')\n```"
        result = format_agent_prompt(raw)
        assert _DESC_MARKER not in result


class TestFormatAgentPromptAlreadyStructured:
    def test_already_structured_returned_unchanged(self) -> None:
        raw = "## Task\nMigrate database\n\n## Description\nRun alembic migrations.\n\n## Additional Content\n"
        result = format_agent_prompt(raw)
        assert result == raw

    def test_already_structured_no_content_section_not_appended(self) -> None:
        # Previously appended empty ## Additional Content; now returns as-is
        raw = "## Task\nDo work\n\n## Description\nDetails here.\n"
        result = format_agent_prompt(raw)
        assert result == raw

    def test_already_structured_with_additional_content_untouched(self) -> None:
        raw = "## Task\nFix bug\n\n## Description\nThere is a bug.\n\n## Additional Content\n```python\nprint(1)\n```\n"
        result = format_agent_prompt(raw)
        assert result == raw

    def test_partial_structure_task_only_reformatted(self) -> None:
        # Has ## Task but not ## Description → should reformat
        raw = "## Task\nSome task\nSome description text"
        result = format_agent_prompt(raw)
        assert _DESC_MARKER in result

    def test_partial_structure_desc_only_reformatted(self) -> None:
        # Has ## Description but not ## Task → should reformat
        raw = "## Description\nSome description"
        result = format_agent_prompt(raw)
        assert _TASK_MARKER in result


class TestFormatAgentPromptEdgeCases:
    def test_leading_blank_lines_ignored_for_task(self) -> None:
        raw = "\n\nActual task\nDescription follows."
        result = format_agent_prompt(raw)
        assert f"{_TASK_MARKER}\nActual task" in result

    def test_idempotent_on_already_formatted_output(self) -> None:
        raw = "Do something\nDetails about it.\n\n```js\nconsole.log(1);\n```"
        first_pass = format_agent_prompt(raw)
        second_pass = format_agent_prompt(first_pass)
        assert first_pass == second_pass

    def test_new_session_single_line_only_task(self) -> None:
        # New session with bare task — no description, no code
        result = format_agent_prompt("Implement feature X")
        lines = result.strip().splitlines()
        assert lines[0] == _TASK_MARKER
        assert lines[1] == "Implement feature X"
        assert _DESC_MARKER not in result
        assert _CONTENT_MARKER not in result

    def test_continued_session_populated_sections_render(self) -> None:
        # Continued session with all three populated sections
        raw = (
            "## Task\nAdd retry logic\n\n"
            "## Description\nRetry on transient failures.\n\n"
            "## Additional Content\n```python\nretry(fn)\n```\n"
        )
        result = format_agent_prompt(raw)
        assert _TASK_MARKER in result
        assert _DESC_MARKER in result
        assert _CONTENT_MARKER in result
        assert "retry(fn)" in result


class TestExtractCodeBlocks:
    def test_finds_single_block(self) -> None:
        blocks = extract_code_blocks("Here:\n```python\ndef f(): pass\n```\n")
        assert blocks == ["```python\ndef f(): pass\n```"]

    def test_finds_multiple_blocks(self) -> None:
        raw = "First:\n```js\na;\n```\n\nSecond:\n```py\nb\n```"
        blocks = extract_code_blocks(raw)
        assert len(blocks) == 2
        assert "a;" in blocks[0]
        assert "b" in blocks[1]

    def test_no_blocks_returns_empty(self) -> None:
        assert extract_code_blocks("Plain text only") == []


class TestFormatAgentPromptExtraCodeBlocks:
    def test_extra_blocks_added_to_additional_content(self) -> None:
        block = "```python\ndef helper():\n    return 1\n```"
        result = format_agent_prompt(
            "Refactor the helper",
            extra_code_blocks=[block],
        )
        assert _CONTENT_MARKER in result
        content_section = result[result.index(_CONTENT_MARKER) :]
        assert "def helper():" in content_section

    def test_extra_blocks_merged_with_existing_blocks(self) -> None:
        existing = "```js\nconsole.log(1);\n```"
        extra = "```python\nprint(2)\n```"
        result = format_agent_prompt(f"Update both\n\n{existing}", extra_code_blocks=[extra])
        content_section = result[result.index(_CONTENT_MARKER) :]
        assert "console.log(1);" in content_section
        assert "print(2)" in content_section

    def test_extra_blocks_not_duplicated(self) -> None:
        block = "```python\ndef foo(): pass\n```"
        result = format_agent_prompt(
            f"Fix this\n\n{block}",
            extra_code_blocks=[block],
        )
        # Block must appear exactly once.
        assert result.count("def foo(): pass") == 1

    def test_extra_blocks_appended_to_already_structured_prompt(self) -> None:
        raw = "## Task\nFix bug\n\n## Description\nA description.\n"
        block = "```python\ndef extra(): pass\n```"
        result = format_agent_prompt(raw, extra_code_blocks=[block])
        assert _CONTENT_MARKER in result
        content_section = result[result.index(_CONTENT_MARKER) :]
        assert "def extra():" in content_section

    def test_extra_blocks_appended_to_existing_additional_content(self) -> None:
        raw = "## Task\nFix bug\n\n## Description\nA description.\n\n## Additional Content\n```js\noriginal();\n```\n"
        block = "```python\ndef appended(): pass\n```"
        result = format_agent_prompt(raw, extra_code_blocks=[block])
        # Both the original block and the appended block are present, and
        # there is still only a single Additional Content section header.
        assert result.count(_CONTENT_MARKER) == 1
        assert "original();" in result
        assert "def appended():" in result

    def test_extra_blocks_already_present_in_structured_prompt_not_duplicated(self) -> None:
        block = "```python\ndef once(): pass\n```"
        raw = f"## Task\nFix bug\n\n## Description\nA description.\n\n## Additional Content\n{block}\n"
        result = format_agent_prompt(raw, extra_code_blocks=[block])
        assert result == raw

    def test_extra_blocks_only_no_raw_prompt(self) -> None:
        block = "```python\nprint('only-extra')\n```"
        result = format_agent_prompt("", extra_code_blocks=[block])
        assert _TASK_MARKER in result
        assert _CONTENT_MARKER in result
        assert "print('only-extra')" in result
