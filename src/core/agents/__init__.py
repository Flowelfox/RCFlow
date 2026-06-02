"""Per-agent helpers shared by the three managed-agent paths.

Claude Code, Codex, and OpenCode all wrap a CLI subprocess and stream
structured events back to the session buffer. The thin pieces of logic
that they share — output truncation, the maximum-output constant —
live here so each agent module imports them instead of duplicating.

The richer per-agent classes (``ClaudeCodeAgent``, ``CodexAgent``,
``OpenCodeAgent``) remain in :mod:`src.core.agent_claude_code`,
:mod:`src.core.agent_codex`, and :mod:`src.core.agent_opencode` for
now. Phase 2 of the refactor plan moves them into this package.
"""

from src.core.agents.base import MAX_TOOL_OUTPUT_CHARS, truncate_tool_output

__all__ = ["MAX_TOOL_OUTPUT_CHARS", "truncate_tool_output"]
