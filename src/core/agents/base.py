"""Shared constants and helpers for the managed-agent paths."""

from __future__ import annotations

MAX_TOOL_OUTPUT_CHARS = 100_000


def truncate_tool_output(content: str) -> str:
    """Truncate tool output that exceeds the size limit for client delivery.

    Used by all three managed agents (Claude Code, Codex, OpenCode) so a
    single agent turn cannot flood the WebSocket buffer with megabytes of
    grep / find / build output.
    """
    if len(content) > MAX_TOOL_OUTPUT_CHARS:
        return content[:MAX_TOOL_OUTPUT_CHARS] + f"\n\n... (truncated, {len(content):,} total chars)"
    return content
