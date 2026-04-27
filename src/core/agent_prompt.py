"""Structured prompt formatting for coding agent invocations.

Every prompt dispatched to a coding agent (Claude Code, Codex, OpenCode) is
normalised into three sections so the agent receives a consistent contract:

    ## Task
    <single-line task title>

    ## Description
    <full description of what needs to be done>

    ## Additional Content
    <fenced code blocks and other raw content extracted from the prompt>
"""

from __future__ import annotations

import re

_CODE_FENCE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)

_TASK_MARKER = "## Task"
_DESC_MARKER = "## Description"
_CONTENT_MARKER = "## Additional Content"


def extract_code_blocks(text: str) -> list[str]:
    """Return all fenced code blocks (`` ``` … ``` ``) found in *text*.

    Used by the prompt router to capture verbatim code blocks from the user's
    original message so they can be merged into an agent tool call's
    ``Additional Content`` section even when the LLM omits them when
    constructing the tool's ``prompt`` argument.
    """
    return _CODE_FENCE_RE.findall(text)


def format_agent_prompt(raw: str, *, extra_code_blocks: list[str] | None = None) -> str:
    """Return *raw* normalised as a structured agent prompt.

    Sections produced (only when non-empty):

    - ``## Task`` — single-line task title (first non-blank text line); always present
    - ``## Description`` — remaining plain-text description; omitted when empty
    - ``## Additional Content`` — fenced code blocks extracted from *raw*; omitted when empty

    Already-structured prompts that contain both ``## Task`` and
    ``## Description`` are returned unchanged unless *extra_code_blocks*
    contains blocks not already present in the prompt — those are appended
    to (or used to create) the ``## Additional Content`` section.

    *extra_code_blocks* is an optional list of fenced code blocks captured from
    the user's original message that the caller wants preserved in the agent's
    ``Additional Content`` section regardless of what the LLM chose to put in
    *raw*. Blocks already present in *raw* are not duplicated.
    """
    extra = list(extra_code_blocks or [])

    if _TASK_MARKER in raw and _DESC_MARKER in raw:
        if not extra:
            return raw
        existing = set(_CODE_FENCE_RE.findall(raw))
        new_blocks = [b for b in extra if b not in existing]
        if not new_blocks:
            return raw
        joined = "\n\n".join(new_blocks)
        body = raw.rstrip("\n")
        if _CONTENT_MARKER in body:
            return f"{body}\n\n{joined}\n"
        return f"{body}\n\n{_CONTENT_MARKER}\n{joined}\n"

    code_blocks = _CODE_FENCE_RE.findall(raw)
    for block in extra:
        if block not in code_blocks:
            code_blocks.append(block)
    plain = _CODE_FENCE_RE.sub("", raw)

    plain_lines = [ln for ln in plain.splitlines() if ln.strip()]
    task = plain_lines[0].strip() if plain_lines else "Complete the requested task."
    description = "\n".join(plain_lines[1:]).strip() if len(plain_lines) > 1 else ""

    additional = "\n\n".join(code_blocks)

    parts = [f"{_TASK_MARKER}\n{task}"]
    if description:
        parts.append(f"{_DESC_MARKER}\n{description}")
    if additional:
        parts.append(f"{_CONTENT_MARKER}\n{additional}")
    return "\n\n".join(parts)
