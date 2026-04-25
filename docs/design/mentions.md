---
updated: 2026-04-26
---

# Mention Context Injection

`@ProjectName`, `#ToolName`, `$filename` tokens in user messages trigger context-block injection prepended to the LLM message. Each produces a separate block; all three can appear in the same message.

**See also:**
- [Prompt Templates](prompt-templates.md) — base system prompt
- [Slash Commands](slash-commands.md) — `/`-triggered palette (different trigger semantics)
- [Tools](tools.md) — registered tool list referenced by `#mentions`
- [Direct Tool Mode](direct-tool-mode.md) — `#tool` is mandatory when `LLM_PROVIDER=none`

---

## @Mention — Project Context

When a user message contains `@ProjectName` tokens (e.g. `@RCFlow`), `PromptRouter.handle_prompt()` resolves them against all configured project directories (`PROJECTS_DIR`). Matches produce a context block prepended to the user message:

```
[Context: This message references project "RCFlow" located at /home/user/Projects/RCFlow. All instructions in this message relate to this project.]
```

Key behavior:
- `@` must appear at start of text or after whitespace.
- Only mentions resolving to existing directories under any configured project dir produce context; unresolved silently ignored.
- Original user text preserved — context is additional content block, not replacement.
- Block uses `cache_control: {"type": "ephemeral"}` to avoid polluting prompt cache.
- Client-side buffer receives original text only (no injected context).
- **Session project attachment**: each valid `@ProjectName` mention stores resolved path as `session.main_project_path` on `ActiveSession` and persists to `sessions.main_project_path` DB column. Multiple `@` in one message — *last* resolvable wins. `main_project_path = null` = "Global", no project limitation. Field included in every `session_update` WS broadcast and `GET /api/sessions` response.

## #Mention — Tool Preference

When a user message contains `#ToolName` tokens (e.g. `#claude_code`, `#codex`), resolved against tool registry. Matches produce a tool preference block:

```
[Tool preference: The user has explicitly requested that you use the following tool(s) to accomplish this task:
- "claude_code": Claude Code autonomous coding agent...
Prioritize using these tools. If the task can be accomplished with the mentioned tools, use them rather than alternatives.]
```

Key behavior:
- `#` must appear at start of text or after whitespace.
- Tool name match case-insensitive: `#Claude` resolves to `claude_code`.
- Unresolved mentions silently ignored.
- Duplicates deduplicated.
- Multiple combine with AND logic: `#claude_code #shell_exec` means use both.
- Original text preserved.
- Block uses `cache_control: {"type": "ephemeral"}`.

Client autocomplete via `GET /api/tools?q=<query>`, triggered when user types `#`. Shows tool `display_name` values with descriptions. Each tool definition can include optional `display_name` field for human-readable presentation (e.g. `claude_code` → "Claude Code"); when absent, `name` is used as-is.

## $File Reference

When a user message contains `$filename` tokens (e.g. `$main.py`, `$config.yaml`), resolved against artifact database for the current backend. Matches inject file content or metadata.

For **text files** (extensions in `TEXT_EXTENSIONS`), full file content in fenced code block:
````
[File: main.py (/home/user/project/main.py)]
```py
<file content>
```
````

For **non-text files** (images, binaries, etc.), metadata only:
```
[File: diagram.png (/home/user/project/diagram.png)
  Type: image/png
  Extension: .png
  Size: 245.3 KB
  Modified: 2026-03-09T14:30:00+00:00
  Note: Binary/non-text file -- content not included]
```

Key behavior:
- `$` must appear at start of text or after whitespace.
- Unresolved silently ignored.
- File content capped at 100KB; larger files truncated with note.
- Duplicates deduplicated.
- Original text preserved.
- Block uses `cache_control: {"type": "ephemeral"}`.
- `$` references NOT parsed in executor sessions (Claude Code, Codex) — text sent as-is since those executors have own file reading.

Client autocomplete via `GET /api/artifacts/search?q=<query>`, triggered when user types `$`. Dropdown shows file name + full path on second line, with type-specific icons. Non-text files show indicator that only metadata (not content) will be included.
