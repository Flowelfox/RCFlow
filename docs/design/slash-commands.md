---
updated: 2026-04-26
---

# Slash Command System

`/`-triggered command palette in the input area. Combines RCFlow built-in commands and Claude Code skill commands in a single grouped dropdown.

**See also:**
- [Mentions](mentions.md) — `@`/`#`/`$` triggers (different rules)
- [Prompt Templates](prompt-templates.md) — system prompt construction
- [Tools](tools.md) — managed CLI tools that contribute commands

---

## Trigger Rule

`/` trigger fires **only when `/` is the first character of the text field** (`triggerPos == 0`). A `/` appearing later is treated as normal character. Differs from `@`/`#`/`$` mentions, which trigger when preceded by whitespace.

## Command Sources

| Source | Description |
|--------|-------------|
| `rcflow` | RCFlow built-in client-side commands (hardcoded) |
| `claude_code_builtin` | Claude Code's own built-in slash commands — descriptions sourced live from Claude via `claude -p`, cached on disk; hard-coded fallback when binary unavailable |
| `claude_code_user` | User-level skills from `~/.claude/commands/*.md` |
| `claude_code_project` | Project-level skills from `<projects_dir>/.claude/commands/*.md` |
| `claude_code_plugin` | Commands contributed by installed and enabled Claude Code plugins (see below) |
| `rcflow_plugin` | Commands from RCFlow-managed plugins in `<managed_tools_dir>/claude-code/plugins/` (see below) |

## RCFlow Built-in Commands

| Command | Action |
|---------|--------|
| `/clear` | Clear displayed messages in current pane (client-side only; server session unaffected) |
| `/new` | Start a new session (equivalent to "New Chat") |
| `/help` | Display RCFlow tips + available commands as system message in pane |
| `/pause` | Pause current active session |
| `/resume` | Resume current paused session |
| `/plugins` | Open plugin settings for active coding agent (see below) |

RCFlow commands intercepted in `_send()` before text reaches WebSocket layer. Unknown `/foo` commands not intercepted — fall through to `sendPrompt()`.

## /plugins Command

`/plugins` is an RCFlow built-in that navigates the active pane to the **Worker Settings** pane for the current session's coding agent. If the session has an `agentType` (e.g. `"claude_code"` or `"codex"`), that tool is used; otherwise `"claude_code"` is the default. The `WorkerSettingsPane` widget shows the `"plugins"` section where the user can install, uninstall, enable, and disable plugins interactively.

Replaces old subcommand-based text interface (`/plugins list`, `/plugins install`, `/plugins remove`). Handler is `_dispatchPluginsCommand()` in `input_area.dart`.

## Plugin Management API

Two API layers:

1. **Canonical (v2)** — tool-scoped endpoints under `/api/tools/{tool_name}/plugins`
2. **Deprecated aliases** — old `/api/rcflow-plugins` routes, preserved for backwards compat; carry `X-RCFlow-Deprecated` response header

Only `claude_code` fully supported for plugin operations. `codex` is known tool name but returns 422 (not yet supported) for all operations.

### Canonical Endpoints

**`GET /api/tools/{tool_name}/plugins`** — list plugins for a managed tool.

Response:
```json
{
  "plugins": [
    {
      "name": "my-plugin",
      "description": "Does things",
      "commands": [{"name": "do-thing", "description": "Do the thing"}],
      "path": "/abs/path",
      "enabled": true
    }
  ]
}
```

**`POST /api/tools/{tool_name}/plugins`** — install a plugin. Body: `{"source": "<git-url-or-local-path>", "name": "<optional>"}`.

- If source is existing local directory, copied via `shutil.copytree`.
- Otherwise `git clone --depth 1 <source> <dest>` runs (requires `git` on PATH; returns 503 if absent).
- Returns 201 with `{"plugin": {...}}`, 409 if name exists, 504 on clone timeout.

**`DELETE /api/tools/{tool_name}/plugins/{name}`** — uninstall by name (removes directory). Returns 200 on success, 404 if not found.

**`PATCH /api/tools/{tool_name}/plugins/{name}`** — enable or disable. Body: `{"enabled": bool}`. Returns 200. Disabled state persisted to `plugins_state.json` inside tool's plugins directory.

### Plugin State Persistence

`PluginStateManager` class (in `src/api/routes/rcflow_plugins.py`) handles reading + writing `plugins_state.json` atomically. File lives at `<tool_plugins_dir>/plugins_state.json` and contains object with `"disabled"` key (list of disabled plugin names):

```json
{"disabled": ["old-plugin"]}
```

Disabled plugins excluded from slash command palette. `plugins_state.json` separate from Claude Code's own `settings.json` to avoid polluting user's Claude Code configuration.

### Deprecated Aliases

| Old Endpoint | Maps To |
|---|---|
| `GET /api/rcflow-plugins` | `GET /api/tools/claude_code/plugins` |
| `POST /api/rcflow-plugins` | `POST /api/tools/claude_code/plugins` |
| `DELETE /api/rcflow-plugins/{name}` | `DELETE /api/tools/claude_code/plugins/{name}` |

All return `X-RCFlow-Deprecated: true` in response headers.

Client methods: `fetchToolPlugins()`, `installToolPlugin()`, `uninstallToolPlugin()`, `setToolPluginEnabled()` on `WebSocketService`.

## Claude Code Commands

Claude Code commands shown in palette only when active session's executor is `claude_code` (`pane_state.isClaudeCodeSession == true`). When selected, sent as-is through `sendPrompt()` to Claude Code subprocess, which handles them natively.

Registered as `claude_code_builtin`: `help`, `clear`, `compact`, `cost`, `resume`, `init`, `bug`, `pr-comments`, `permissions`, `doctor`, `vim`, `btw`.

Descriptions for `claude_code_builtin` commands obtained by invoking `claude -p --no-session-persistence --output-format text --max-budget-usd 0.05` with prompt asking Claude to output JSON object mapping command names to descriptions. Result cached to disk (`<managed_tools_dir>/cc_builtins_cache.json`) keyed by installed Claude Code version string so one-time API call not repeated unless binary is updated. Hard-coded fallback list used when binary absent, subprocess times out, or response can't be parsed.

Resolution order for `claude_code_builtin` descriptions:
1. In-process memory cache (populated after first resolution within server process).
2. On-disk cache (`cc_builtins_cache.json`) if cached Claude Code version matches installed version.
3. Live fetch via `claude -p` (one API call per new Claude Code version).
4. Hard-coded fallback.

Claude Code skill `.md` files must have YAML frontmatter block with `description` field:

```markdown
---
description: Commit and push changes to remote
allowed-tools: Bash(git add:*), Bash(git commit:*)
---
...
```

Backend parses `description` field via regex at request time (no caching). Files that can't be read or lack description field included with empty description.

## Claude Code Plugin Commands

Claude Code supports installable plugins (managed via `claude install`). Each plugin cached under `~/.claude/plugins/cache/<marketplace>/<plugin-name>/<version>/` and can contribute slash commands by placing `.md` files in `commands/` subdirectory of plugin root.

Enumeration logic:
1. Read `~/.claude/settings.json` → `enabledPlugins` map (`{ "name@marketplace": true/false }`) — only plugins with value `true` included.
2. Read `~/.claude/plugins/installed_plugins.json` → `plugins` map — resolves each plugin key to its `installPath` on disk. When multiple versions exist, last entry (most recently installed) used.
3. For each enabled plugin, enumerate `<installPath>/commands/*.md` using `_parse_plugin_command`.
4. Files whose YAML frontmatter contains `hide-from-slash-command-tool: "true"` excluded — Claude Code uses this field to suppress internal helper commands (e.g., loop-control scripts) from autocomplete palette.
5. Description values stripped of surrounding quotes so both `description: My skill` and `description: "My skill"` render identically.
6. Command names deduplicated across plugins (first occurrence wins, plugins sorted alphabetically by key).

Plugin commands returned with `"source": "claude_code_plugin"` and additional `"plugin"` field containing short plugin name (portion of key before `@` separator). In client UI shown in dedicated **"Plugins"** group, separate from **"Claude Code"** group used for built-in and user/project commands.

## RCFlow-Managed Plugin Commands

RCFlow maintains own plugin directory at `<managed_tools_dir>/claude-code/plugins/` (e.g. `~/.local/share/rcflow/tools/claude-code/plugins/` on Linux, `%LOCALAPPDATA%/rcflow/tools/claude-code/plugins/` on Windows). Separate from user's global Claude Code plugin registry (`~/.claude/plugins/`). Intended for plugins curated or deployed by RCFlow itself.

Each subdirectory under this path is a plugin. A plugin contributes slash commands by placing `.md` files inside `commands/` subfolder:

```
<managed_tools_dir>/claude-code/plugins/
    my-plugin/
        commands/
            do-thing.md
    another-plugin/
        commands/
            other-skill.md
```

Same `.md` frontmatter format applies (`description`, `hide-from-slash-command-tool`). Commands returned with `"source": "rcflow_plugin"` and `"plugin"` field set to plugin directory name.

**Lifecycle:** Plugins directory created automatically during managed Claude Code installation (`ToolManager._install_claude_code` and `_install_claude_code_streaming`) via `get_managed_cc_plugins_dir()`, so exists on every machine where RCFlow manages Claude Code binary — not just machine where endpoint was first called. Also created on first access if directory missing.

## Backend API

**`GET /api/slash-commands?q=<query>`**

Returns unified list of all slash commands. Optionally filters by case-insensitive substring match on command name.

Response:
```json
{
  "commands": [
    {"name": "clear",       "description": "Clear chat messages in this pane", "source": "rcflow"},
    {"name": "compact",     "description": "Compact conversation to save context", "source": "claude_code_builtin"},
    {"name": "commit-push", "description": "Commit and push changes to remote", "source": "claude_code_user"},
    {"name": "code-review", "description": "Code review a pull request", "source": "claude_code_plugin", "plugin": "code-review"},
    {"name": "do-thing",    "description": "Do the thing",               "source": "rcflow_plugin",      "plugin": "my-plugin"}
  ]
}
```

## Client UI

Suggestion overlay renders commands in three visual groups separated by dividers + group header labels ("RCFLOW", "CLAUDE CODE", "PLUGINS"). Both `claude_code_plugin` and `rcflow_plugin` commands appear under "PLUGINS". Commands shown with:
- Bolt icon (⚡) for RCFlow commands in accent color
- Terminal icon for Claude Code and plugin commands in muted color
- Command name with `/` prefix (query match highlighted)
- Description on second line

Keyboard navigation (up/down arrows), Enter/Tab to select, Escape to dismiss work same as `@`/`#`/`$` mention overlays. Overlay 360px wide with 380px max height.
