---
updated: 2026-04-26
---

# System Prompt Templates

Jinja2-based system prompt construction. Covers base template, global prompt override, and caveman mode injection.

**See also:**
- [Mentions](mentions.md) — `@`/`#`/`$` context blocks prepended to user messages
- [Slash Commands](slash-commands.md) — `/`-triggered command palette
- [Configuration](configuration.md) — `GLOBAL_PROMPT`, `CAVEMAN_MODE`, `CAVEMAN_LEVEL`

---

## File Organization

```
src/prompts/
├── __init__.py              # Exports PromptBuilder
├── builder.py               # PromptBuilder class (uses Jinja2)
└── templates/
    └── system_prompt.j2     # System prompt in Jinja2 format
```

## Template Syntax

[Jinja2](https://jinja.palletsprojects.com/) with `{{ variable }}` syntax. `StrictUndefined` so missing variables raise immediately.

## Integration

`LLMClient.__init__` builds the system prompt via:

```python
PromptBuilder().build(
    projects_dirs=", ".join(str(d) for d in settings.projects_dirs),
    os_name=platform.system(),
)
```

`os_name` injected into `<role>` tag so LLM knows host OS (e.g. "Linux" or "Windows") and can generate appropriate commands.

## Global Prompt

If `GLOBAL_PROMPT` is set (via server configuration), it is appended to the base system prompt for all LLM calls. `LLMClient._system_prompt` property dynamically composes the full prompt by joining base template output with global prompt text separated by a blank line. Allows users to set persistent behavioral guidelines, language preferences, or domain expertise that apply to every session.

## Caveman Mode

Caveman mode is a prompt-injection technique that makes LLMs respond in compressed, token-efficient prose (~65–75% output token reduction while retaining full technical accuracy). Works by appending terse-writing instructions to the system prompt.

**Global toggle (outer LLM):** Two server settings:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `CAVEMAN_MODE` | boolean | `false` | Enable terse caveman-style LLM responses |
| `CAVEMAN_LEVEL` | select | `"full"` | Intensity: `lite` (drop filler only), `full` (drop articles, fragments OK), `ultra` (abbreviations, arrows) |

Both `restart_required: false`. When enabled, caveman instruction block inserted into `LLMClient._system_prompt` **after** base prompt and **before** `GLOBAL_PROMPT`, so user overrides always take final precedence. Order: base → caveman → global_prompt. Changes take effect on next LLM turn of any session (immediate).

**Per-tool toggle (CLI agents):** Each tool settings schema (Claude Code, Codex, OpenCode) exposes a `caveman_mode` boolean (`managed_only: true`). Implementation varies by agent:

- **Claude Code**: Writes a `CLAUDE.md` file containing the always-on caveman snippet to `CLAUDE_CONFIG_DIR`. Claude Code reads this at subprocess spawn time. Takes effect for **new sessions only**.
- **Codex**: No-op pending verification that Codex reads `hooks.json` from `CODEX_HOME`.
- **OpenCode**: No-op pending verification of the config injection mechanism.

`caveman_mode` key stripped from tool's JSON config file before writing (RCFlow UI control, not key CLI tools understand). State derived from filesystem (presence of `CLAUDE.md`) when reading settings back.
