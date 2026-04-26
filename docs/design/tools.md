---
updated: 2026-04-26
---

# Pluggable Tool Definitions

Tools are defined as JSON files in `tools/`. The tool management service installs/updates managed CLI binaries (Claude Code, Codex, OpenCode), and per-tool settings are isolated under `~/.local/share/rcflow/tools/`.

**See also:**
- [Executors](executors.md) — Claude Code, Codex, Worktree executor internals
- [Direct Tool Mode](direct-tool-mode.md) — `#tool` invocation when no LLM is configured
- [Mentions](mentions.md#mention--tool-preference) — `#mention` autocomplete

---

## File Organization

Tools are defined as individual JSON files in a `tools/` directory:

```
tools/
├── cmd.json            (Windows only)
├── powershell.json     (Windows only)
├── shell_exec.json     (Linux/macOS only)
├── http_request.json
├── python_interactive.json
├── file_read.json
└── system_info.json
```

Each file defines one tool. Drop a `.json` file into `tools/` to register a new tool. The server loads all tool files on startup (and can optionally hot-reload). Tools with an `os` field are only loaded when the server runs on a matching platform; tools without an `os` field load on all platforms.

## Tool JSON Schema

```json
{
  "name": "shell_exec",
  "description": "Execute a shell command on the host machine and return its output.",
  "version": "1.0.0",
  "session_type": "one-shot",
  "llm_context": "stateless",
  "executor": "shell",

  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "The shell command to execute"
      },
      "working_directory": {
        "type": "string",
        "description": "Working directory for the command",
        "default": "/home/user"
      },
      "timeout": {
        "type": "integer",
        "description": "Timeout in seconds",
        "default": 30
      }
    },
    "required": ["command"]
  },

  "executor_config": {
    "shell": {
      "command_template": "{command}",
      "capture_stderr": true,
      "stream_output": true
    }
  }
}
```

> **Note:** When `shell` is omitted, it defaults to `/bin/bash` on Linux and `powershell.exe` on Windows.
>
> **Windows shell handling:** On Windows with a PowerShell shell, `ShellExecutor` uses `create_subprocess_exec` with `-NoProfile -Command` instead of `create_subprocess_shell` (which incorrectly passes `/c` to PowerShell). On Windows with a non-PowerShell shell (e.g. `cmd.exe`), it uses `create_subprocess_shell` without an explicit `executable` to let `COMSPEC` resolve the shell.

## HTTP API Tool Example

```json
{
  "name": "weather_lookup",
  "description": "Look up current weather for a given city using a weather API.",
  "version": "1.0.0",
  "session_type": "one-shot",
  "llm_context": "stateless",
  "executor": "http",

  "parameters": {
    "type": "object",
    "properties": {
      "city": {
        "type": "string",
        "description": "City name to look up weather for"
      }
    },
    "required": ["city"]
  },

  "executor_config": {
    "http": {
      "method": "GET",
      "url_template": "https://api.weather.example.com/v1/current?city={city}&key=${WEATHER_API_KEY}",
      "headers": {
        "Accept": "application/json"
      },
      "timeout": 10,
      "response_path": "$.data.summary"
    }
  }
}
```

## Long-Running Tool Example

```json
{
  "name": "python_interactive",
  "description": "Start an interactive Python session. Keeps running until explicitly exited.",
  "version": "1.0.0",
  "session_type": "long-running",
  "llm_context": "session-scoped",
  "executor": "shell",

  "parameters": {
    "type": "object",
    "properties": {
      "initial_command": {
        "type": "string",
        "description": "Optional initial Python code to run on session start",
        "default": ""
      }
    },
    "required": []
  },

  "executor_config": {
    "shell": {
      "command_template": "python3 -i",
      "shell": "/bin/bash",
      "capture_stderr": true,
      "stream_output": true,
      "interactive": true,
      "stdin_enabled": true
    }
  }
}
```

## Structured Agent Prompt Format

Every prompt dispatched to a coding agent (Claude Code, Codex, OpenCode) is normalised by `src/core/agent_prompt.py:format_agent_prompt()` before the executor is started. The canonical structure is:

```
## Task
<single-line task title — first non-blank line of the raw prompt>

## Description
<remaining plain-text description>

## Additional Content
<fenced code blocks extracted verbatim from the raw prompt>
```

**Behaviour:**
- If the raw prompt already contains both `## Task` and `## Description` headers it is returned unchanged (idempotent), except that a missing `## Additional Content` section is appended.
- Fenced code blocks (` ``` … ``` `) are extracted from the body and placed under `## Additional Content` to preserve raw content; they are stripped from the Task/Description text.
- An empty or whitespace-only prompt falls back to the task title `"Complete the requested task."`.

**Interception point:** `PromptRouter._execute_tool()` in `src/core/prompt_router.py` applies `format_agent_prompt` to `tool_call.tool_input["prompt"]` for all three agent executors (`claude_code`, `codex`, `opencode`) before forwarding to `_start_claude_code / _start_codex / _start_opencode`. This covers both LLM-generated tool calls and direct-mode tool invocations (`#claude_code …`).

## Tool Definition Fields

| Field             | Type   | Required | Description                                           |
|-------------------|--------|----------|-------------------------------------------------------|
| `name`            | string | yes      | Unique tool identifier, sent to LLM                   |
| `display_name`    | string | no       | Human-readable name shown in UI (defaults to `name`)  |
| `description`     | string | yes      | Human/LLM-readable description of what the tool does  |
| `version`         | string | no       | Semantic version of the tool definition                |
| `os`              | list   | no       | OS restriction: subset of `["windows","linux","darwin"]`. Empty = all platforms. Tools are skipped at load time if the current OS is not in the list. |
| `session_type`    | enum   | yes      | `one-shot` or `long-running`                          |
| `llm_context`     | enum   | yes      | `stateless` or `session-scoped`                       |
| `executor`        | enum   | yes      | `shell`, `http`, `claude_code`, `codex`, or `worktree` |
| `parameters`      | object | yes      | JSON Schema describing the tool's input parameters    |
| `executor_config` | object | yes      | Executor-specific configuration                       |

## Tool Management Service

RCFlow automatically manages the installation and updating of external CLI tools (Claude Code, Codex, and OpenCode). The `ToolManager` service (`src/services/tool_manager.py`) handles detection, installation, and periodic updates using **native binary downloads** — no Node.js or npm required.

**How it works:**

1. On server startup, `ToolManager.ensure_tools()` runs in the lifespan and **detects** tools (does not auto-install). Missing tools are reported; installation happens on-demand when the user requests it via the UI.
2. Detection checks the RCFlow managed directory only. External binaries on the system `PATH` are intentionally **not** honoured — every coding-agent invocation must run under the RCFlow-managed copy so the per-tool config (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, …) is the authoritative source. The managed directory is resolved by `get_managed_tools_dir()` in `src/paths.py`: `~/.local/share/rcflow/tools/` (Linux) or `%LOCALAPPDATA%\rcflow\tools\` (Windows), falling back to `<install_dir>/managed-tools/` when the home directory is absent or not writable (e.g. service accounts).
3. Tools in the RCFlow managed directory are marked `managed=True`. When no managed binary is on disk, the tool is reported as not installed and the UI prompts for installation. There is no source switch — `managed=True` is the only operating mode.
4. A background `asyncio.Task` checks for updates every `TOOL_UPDATE_INTERVAL_HOURS` hours (default 6).
5. `PromptRouter` gets binary paths from `ToolManager.get_binary_path()` — no binary path settings needed.

**Installation methods:**

- **Claude Code**: Native binary downloaded from Anthropic's GCS bucket (`storage.googleapis.com/claude-code-dist-.../claude-code-releases`). SHA256 checksum verified against the official manifest. Binary placed at `~/.local/share/rcflow/tools/claude-code/claude` (Linux) or `%LOCALAPPDATA%\rcflow\tools\claude-code\claude.exe` (Windows).
- **Codex**: Native binary downloaded from GitHub Releases (`github.com/openai/codex/releases`). The release tarball contains a single binary named `codex-<target>` (e.g. `codex-x86_64-unknown-linux-gnu`) which is extracted and renamed to `codex`. On Windows, the `.exe` is downloaded directly and renamed to `codex.exe`. The responses API proxy is built into the main binary as a subcommand. Binary placed at `~/.local/share/rcflow/tools/codex/codex` (Linux) or `%LOCALAPPDATA%\rcflow\tools\codex\codex.exe` (Windows).
- **OpenCode**: Native binary downloaded from GitHub Releases (`github.com/sst/opencode/releases`). Linux releases ship as `.tar.gz` archives containing a single `opencode` binary; macOS and Windows releases ship as `.zip` archives. The binary is extracted and placed at `~/.local/share/rcflow/tools/opencode/opencode` (Linux/macOS) or `%LOCALAPPDATA%\rcflow\tools\opencode\opencode.exe` (Windows). On glibc-too-old Linux systems the installer automatically retries with the `-musl` variant. Version is checked via the GitHub Releases API (`api.github.com/repos/sst/opencode/releases/latest`).

**Platform strings:**

| Platform | Claude Code (GCS) | Codex (GitHub) | OpenCode (GitHub) |
|----------|-------------------|----------------|-------------------|
| Linux x64 | `linux-x64` | `x86_64-unknown-linux-gnu` | `opencode-linux-x64` |
| Linux x64 musl | `linux-x64-musl` | `x86_64-unknown-linux-musl` | `opencode-linux-x64-musl` |
| Linux arm64 | `linux-arm64` | `aarch64-unknown-linux-gnu` | `opencode-linux-arm64` |
| macOS arm64 | — | — | `opencode-darwin-arm64` |
| macOS x64 | — | — | `opencode-darwin-x64` |
| Windows x64 | `win32-x64` | `x86_64-pc-windows-msvc` | `opencode-windows-x64` |

**Configuration:**

| Setting                      | Default   | Description |
|------------------------------|-----------|-------------|
| `TOOL_AUTO_UPDATE`           | `true`    | Enable/disable automatic update checks |
| `TOOL_UPDATE_INTERVAL_HOURS` | `6`       | Hours between update checks |

**API endpoints:**

- `GET /api/tools/status` — Returns installed/managed/version/update info for each tool.
- `POST /api/tools/update` — Triggers an immediate update check and install for managed tools.
- `POST /api/tools/{name}/install` — Downloads and installs the managed version, then re-detects so both sources are available.
- `POST /api/tools/{name}/source` — Switch a tool between managed and external source.

**Error handling:** All tool management operations are non-fatal. The server starts and runs even if tool installation fails. Errors are logged but never crash the server.

## Per-Tool Settings Isolation

RCFlow maintains isolated settings files for managed CLI tool instances so they don't share configuration with user-installed versions. The `ToolSettingsManager` service (`src/services/tool_settings.py`) handles reading and writing these settings.

**Settings file locations** (under `~/.local/share/rcflow/tools/` on Linux, `%LOCALAPPDATA%\rcflow\tools\` on Windows):

| Tool         | Settings File                                   | Env Var Injected       |
|--------------|--------------------------------------------------|------------------------|
| Claude Code  | `claude-code/config/settings.json`               | `CLAUDE_CONFIG_DIR`    |
| Codex        | `codex/config/codex.json`                         | `CODEX_HOME`           |

When launching tool subprocesses, `PromptRouter` injects the appropriate environment variable pointing to the tool's isolated config directory. This ensures RCFlow-managed instances use their own settings.

**Startup defaults seeding:** At server startup, `ToolSettingsManager.ensure_defaults("claude_code")` is called in `main.py` immediately after the settings manager is created. It idempotently seeds the managed `settings.json` with two RCFlow-specific constraints if they are not already present:
- `permissions.deny: ["EnterWorktree"]` — blocks the built-in `EnterWorktree` tool; `wt` CLI must be used instead to avoid isolated sub-sessions that reset permission state.
- `permissions.allow: ["Bash(wt:*)"]` — pre-approves the `wt` CLI, which is bundled with RCFlow as a `wtpython` dependency and available in the RCFlow venv.

Existing user values are never overwritten — the method only appends the missing entries.

**`wt` PATH injection:** `_build_claude_code_extra_env()` checks whether `wt` is already resolvable on `PATH`. If not, it prepends `Path(sys.executable).parent` (the RCFlow venv's `bin/` directory) to the subprocess `PATH` so that the bundled `wt` binary is always available inside Claude Code sessions.

**API endpoints:**

- `GET /api/tools/{tool_name}/settings` — Returns `{tool, fields: [{key, label, type, value, default, description, options?, visible_when?}]}`. Secret-type values are masked.
- `PATCH /api/tools/{tool_name}/settings` — Body: `{"updates": {"key": value}}`. Validates keys against the schema, writes atomically (`.tmp` + `rename()`), returns the updated schema+values. Masked secret values sent back are detected and the stored value is preserved.

**Supported field types:** `string`, `boolean`, `select`, `string_list`, `secret`.

**`secret` field type:** Values are masked before being returned to the client — all characters except the last 4 are replaced with `*`. When a masked value is sent back in an update, it is detected and the existing stored value is preserved. The client renders secret fields with a masked display and a "Change" button that reveals an obscured input with a visibility toggle.

**`visible_when` conditional visibility:** Schema fields may include `"visible_when": {"key": "<other_key>", "value": "<expected_value>"}`. The field is only shown in the client UI when the referenced key matches the expected value. The server always returns all applicable fields; visibility filtering is handled client-side.

Schema fields may include `"managed_only": true` — these are only exposed when the tool is using its managed (RCFlow-installed) binary. When the tool is switched to an external (PATH) source, managed-only fields are hidden from the GET endpoint and rejected by the PATCH endpoint. The `managed` status is resolved from `ToolManager` at request time.

Schema fields may include `"coming_soon": true` — the flag is forwarded in the GET endpoint response so the client can render the field as disabled with a "Coming soon" badge. The PATCH endpoint rejects writes to any `coming_soon` key regardless of source.

**Claude Code settings schema:**

| Key                        | Type        | Managed-only | Visible when           | Description                                       |
|----------------------------|-------------|--------------|------------------------|----------------------------------------------------|
| `permissions.allow`        | string_list | no           | —                      | Tool permissions to always allow                   |
| `permissions.deny`         | string_list | no           | —                      | Tool permissions to always deny                    |
| `enableAllProjectMcpServers` | boolean   | no           | —                      | Auto-enable project MCP servers                    |
| `provider`                 | select      | yes          | —                      | API provider: Global / Anthropic Key / Anthropic Login / AWS Bedrock |
| `anthropic_api_key`        | secret      | yes          | provider = anthropic   | API key for Anthropic provider                     |
| `aws_region`               | string      | yes          | provider = bedrock     | AWS region for Bedrock (default us-east-1)         |
| `aws_access_key_id`        | secret      | yes          | provider = bedrock     | AWS access key for Bedrock                         |
| `aws_secret_access_key`    | secret      | yes          | provider = bedrock     | AWS secret access key for Bedrock                  |
| `model`                    | model_select| yes          | —                      | Default model override for sessions. Dynamic dropdown — populated via `GET /api/models?scope=claude_code&provider=…` against the tool's own Anthropic / Bedrock credentials. Falls back to the bundled list on fetch failure. |
| `default_permission_mode`  | select      | yes          | —                      | CLI --permission-mode: interactive (default, enables interactive prompts), bypassPermissions, allowEdits, plan |
| `max_turns`                | string      | yes          | —                      | Maximum agentic turns per session (default 200)    |
| `timeout`                  | string      | yes          | —                      | Process timeout in seconds (default 1800)          |
| `caveman_mode`             | boolean     | yes          | —                      | Inject caveman terse-mode instruction via CLAUDE.md (new sessions only) |
| `undercover`               | boolean     | yes          | —                      | Strip AI attribution from commits and PRs (default false) — **coming soon**, disabled in client and rejected by PATCH |

**Provider env sync:** When `provider` or any credential field is updated, `ToolSettingsManager` automatically rebuilds the `env` section of the Claude Code `settings.json`:

- **Anthropic Key** (`provider=anthropic`): sets `env.ANTHROPIC_API_KEY` from `anthropic_api_key`.
- **Anthropic Login** (`provider=anthropic_login`): clears the `env` section (no API key). Claude Code CLI uses its own OAuth credentials stored in `CLAUDE_CONFIG_DIR`. The UI shows a "Login with Anthropic" button that triggers browser-based OAuth via `POST /api/tools/claude_code/login`.
- **Bedrock** (`provider=bedrock`): sets `env.CLAUDE_CODE_USE_BEDROCK=1`, plus `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` from their respective fields.
- **Global** (`provider=""`): removes the `env` section so that `PromptRouter` injects the server-level `ANTHROPIC_API_KEY` instead.

When the tool has a non-empty `provider`, `PromptRouter._build_claude_code_extra_env` skips injecting the global `ANTHROPIC_API_KEY`, letting the `settings.json` env section take precedence.

**Codex settings schema:**

| Key              | Type   | Managed-only | Description                                |
|------------------|--------|--------------|--------------------------------------------|
| `provider`       | select | yes          | Auth method: Global / OpenAI / ChatGPT (Subscription) |
| `codex_api_key`  | secret | yes          | OpenAI API key (visible when provider=openai) |
| `model`          | model_select | no       | Model name for Codex sessions. Dynamic dropdown — populated via `GET /api/models?scope=codex&provider=openai` against the tool's `codex_api_key`. |
| `approval_mode`  | select | no           | Tool-call approval (full-auto / yolo)      |
| `timeout`        | string | yes          | Process timeout in seconds (default 600)   |
| `caveman_mode`   | boolean| yes          | Inject caveman terse-mode instruction (experimental — hook delivery unverified) |

Provider sync behavior:
- **OpenAI** (`provider=openai`): sets `env.CODEX_API_KEY` from `codex_api_key`. RCFlow injects this into the subprocess environment.
- **ChatGPT** (`provider=chatgpt`): clears the `env` section (no API key). RCFlow symlinks `~/.codex/auth.json` into `CODEX_HOME` so Codex CLI uses cached OAuth tokens. The UI shows a "Login with ChatGPT" button that triggers device-auth flow via `POST /api/tools/codex/login`.
- **Global** (`provider=""`): removes the `env` section so that `PromptRouter` injects the server-level `CODEX_API_KEY` instead.

**Codex ChatGPT login flow:**

- `POST /api/tools/codex/login` — Starts Codex ChatGPT login with managed `CODEX_HOME`. Two modes controlled by `?device_code=true|false` (default false):
  - **Browser OAuth** (default): runs `codex login`, streams `{"step": "auth_url", "url": "..."}` with the OAuth URL (client opens in browser), then waits for completion.
  - **Device code**: runs `codex login --device-auth`, streams `{"step": "device_code", "url": "...", "code": "XXXX-XXXXX"}` for the user to enter in a browser.
  - Both modes stream `{"step": "waiting", ...}` while waiting, `{"step": "complete", ...}` on success, `{"step": "error", ...}` on failure. Times out after 5 minutes. Verifies with `codex login status` after process exit.
- `GET /api/tools/codex/login/status` — Runs `codex login status` with managed `CODEX_HOME`. Returns `{"logged_in": true/false, "method": "ChatGPT"|null}`.

**Claude Code Anthropic login flow:**

Two-step PKCE OAuth flow (no CLI interaction required):

1. `POST /api/tools/claude_code/login` — Generates a PKCE code_verifier/challenge, builds the Anthropic OAuth URL (`https://claude.ai/oauth/authorize`), stores the verifier, and returns `{"auth_url": "https://claude.ai/oauth/..."}`. The client opens this URL in a browser.
2. `POST /api/tools/claude_code/login/code` — Accepts `{"code": "..."}`. Exchanges the authorization code for tokens at `https://platform.claude.com/v1/oauth/token` using the stored PKCE verifier. Writes credentials to `.credentials.json` in the managed config directory. Verifies via `claude auth status --json`. Returns `{"logged_in": true/false, "email": "..."|null, "subscription": "max"|"pro"|null}`.

Supporting endpoints:

- `GET /api/tools/claude_code/login/status` — Runs `claude auth status --json` with managed `CLAUDE_CONFIG_DIR`. Returns `{"logged_in": true/false, "method": "claude.ai"|null, "email": "..."|null, "subscription": "max"|"pro"|null}`.
- `POST /api/tools/claude_code/logout` — Runs `claude auth logout` with managed `CLAUDE_CONFIG_DIR`. Returns `{"logged_out": true}`.

**Config overrides:** When a managed tool has settings configured, `PromptRouter` reads them at executor creation time and passes non-empty values as `config_overrides` to the executor constructor. These overrides are merged on top of the tool definition's `executor_config` when building subprocess commands.

**Dynamic model dropdowns (per-tool):** Each tool's `model` field is a `model_select` schema entry with `dynamic: true` and `fetch_scope` set to the tool name. The Flutter client calls `GET /api/models?provider=<…>&scope=<tool>` to populate the dropdown using the tool's *own* credentials, independent of the global `LLM_PROVIDER`. OpenCode pins `fetch_provider=openrouter` so its dropdown always lists OpenRouter's `provider/model` catalog regardless of the per-tool auth provider. Cached entries are evicted whenever the tool's provider/key fields are updated via `PATCH /api/tools/{tool}/settings`. See [Configuration → Dynamic Model Catalog](configuration.md#dynamic-model-catalog) for the cache mechanics.
