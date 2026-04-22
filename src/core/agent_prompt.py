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


def format_agent_prompt(raw: str) -> str:
    """Return *raw* normalised as a structured agent prompt.

    Sections produced (only when non-empty):

    - ``## Task`` — single-line task title (first non-blank text line); always present
    - ``## Description`` — remaining plain-text description; omitted when empty
    - ``## Additional Content`` — fenced code blocks extracted from *raw*; omitted when empty

    Already-structured prompts that contain both ``## Task`` and
    ``## Description`` are returned unchanged.
    """
    if _TASK_MARKER in raw and _DESC_MARKER in raw:
        return raw

    code_blocks = _CODE_FENCE_RE.findall(raw)
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
