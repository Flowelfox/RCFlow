---
updated: 2026-04-27
---

# Direct Tool Mode (`LLM_PROVIDER = "none"`)

When `LLM_PROVIDER` is set to `"none"`, the server operates in **direct tool mode**. No LLM client is created, no API keys are required, and all prompts must use `#tool_name` syntax to invoke tools directly.

**See also:**
- [Tools](tools.md) — registered tool definitions
- [Mentions — #Mention Tool Preference](mentions.md#mention--tool-preference) — `#mention` syntax in normal mode
- [Configuration](configuration.md#configurable-groups) — `LLM_PROVIDER` field

---

## Syntax

```
#claude_code @MyProject fix the bug in auth.py
#codex @MyProject implement feature X
#shell_exec ls -la /home/user
#system_info
```

- `#tool_name` (required first token) — which tool to invoke. Matched against tool internal name, mention name, and display name (case-insensitive).
- `@ProjectName` (optional) — resolves to a project directory under `PROJECTS_DIR`. The first valid match is passed as `working_directory` to both agent (`claude_code`/`codex`/`opencode`) and `shell` tools so the command runs in that folder.
- If no `@mention` is given, `working_directory` defaults to the session's selected project (`main_project_path`) when set; otherwise to the server's cwd.
- Remaining text — becomes the tool's primary input parameter (`prompt` for agent tools, `command` for shell tools).

## Behavior Differences

| Aspect | Normal mode | Direct tool mode |
|--------|-------------|------------------|
| LLM required | Yes | No |
| Prompt routing | LLM decides tool | User specifies `#tool_name` |
| Session titles | LLM-generated | Truncated from prompt text |
| Task creation/update | LLM-driven | Skipped |
| Summaries | LLM-generated | Empty `SUMMARY` emitted after each non-agent tool finishes so the client can finalize the tool block |
| Token limits | Enforced | Not applicable |
| Config UI fields | All shown | LLM-specific fields hidden |

## Error Handling

- Prompt without `#tool_name` prefix → error listing available tools
- Unknown tool name → error listing available tools
- Tool with multiple required parameters → error (cannot map single text input)

## What Stays the Same

- Session lifecycle (create, archive, restore, pause, resume)
- Buffer/streaming infrastructure
- Tool execution pipeline (`_execute_tool`, agent streaming)
- Follow-up messages to active agent sessions
- Permission system for interactive mode
- WebSocket endpoints
- All existing LLM provider modes
