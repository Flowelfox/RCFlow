---
updated: 2026-04-26
---

# Executors

Per-executor implementation details for the long-running coding agents (Claude Code, Codex CLI) and the worktree manager.

**See also:**
- [Tools](tools.md) — JSON tool schema, executor selection field, per-tool settings
- [Sessions](sessions.md) — long-running session lifecycle
- [Permissions](permissions.md) — interactive approval relay (Claude Code only)

---

## Claude Code Executor

The `claude_code` executor manages a Claude Code CLI subprocess with bidirectional stream-json communication. It enables delegating complex coding tasks to Claude Code while streaming output back to the client in real time.

**Working directory priority:** The Claude Code executor selects the working directory with the following precedence:
1. `session.metadata["selected_worktree_path"]` — the active worktree path (set via `PATCH /api/sessions/{id}/worktree`, the `attach` worktree action, auto-selected after `new`, or pre-selected by the client in the first `prompt` WS message via `selected_worktree_path`).
2. `session.main_project_path` — the project folder attached via the project chip.
3. `working_directory` from the tool call input (LLM-specified).
4. `"."` (current directory) as final fallback.

**Worktree selection persistence:** `selected_worktree_path` is stored in `session.metadata` and is written to the DB both by the initial `_ensure_session_row_in_db` stub write (on the first prompt) and immediately when set via `PATCH /api/sessions/{id}/worktree` (via `SessionManager.persist_session_metadata`). This ensures the selected worktree survives backend restarts. When the client pre-selects a worktree before the first message (via the worktree chip), `handle_prompt` applies the path to `session.metadata["selected_worktree_path"]` before `_ensure_session_row_in_db`, so the initial DB stub row already contains the selection.

**Working directory validation:** Before spawning the subprocess, the prompt router validates that the specified `working_directory` exists on disk. If it does not, the tool returns an error message to the LLM instead of starting a session. The system prompt also instructs the LLM to verify directory existence via `shell_exec` before calling `claude_code`, and to resolve project names to `~/Projects/<project_name>`.

**How it works:**

1. The outer LLM calls `claude_code(prompt=..., working_directory=...)`.
2. RCFlow validates that `working_directory` exists; returns an error to the LLM if not.
3. RCFlow spawns `claude --input-format stream-json --output-format stream-json` as a long-lived subprocess. On Unix the subprocess is backed by a **PTY** (see below); on Windows it uses standard asyncio pipes.
4. The initial prompt is sent via the PTY master fd (or stdin pipe) in stream-json format.
5. Output events stream to the client session buffer in real time via `PtyLineReader` (PTY) or `asyncio.StreamReader` (pipe).
6. The session enters "Claude Code mode" — subsequent user messages bypass the outer LLM and route directly to the Claude Code subprocess via stdin / PTY master.
7. The process stays alive between turns. Follow-up messages are sent via stdin and responses are read from stdout. If the process unexpectedly crashes, RCFlow restarts it with the same `--session-id` as a fallback.

**PTY-backed execution (Unix):**

By default on Linux and macOS, the Claude Code subprocess is launched with a **pseudoterminal (PTY)** as its stdin and stdout so that `isatty(0)` and `isatty(1)` return `True` inside the child process. This preserves Claude Code's full interactive behaviour:

- **Follow-up questions** (`AskUserQuestion` tool): Claude Code is more likely to ask clarifying questions when it detects a real terminal, rather than silently making assumptions in a headless pipe environment.
- **Plan mode** (`EnterPlanMode` / `ExitPlanMode`): Flows correctly regardless, but the TTY detection ensures Claude Code does not suppress intermediate prompts.
- **Tool permission dialogs**: With `default_permission_mode: interactive`, Claude Code's own permission logic is engaged in addition to RCFlow's `PermissionManager` overlay.

Despite using a PTY, the I/O *protocol* remains `stream-json` (`--output-format stream-json`), so all downstream event translation in `_relay_claude_code_stream` is unchanged. The PTY slave is configured in **raw mode** before the child is spawned:

| Setting | Effect |
|---|---|
| `~ECHO` | Writes to master fd (our JSON input) are not echoed back as output |
| `~OPOST` | `\n` is not translated to `\r\n`; JSON lines arrive with clean endings |
| `~ICANON` | No line buffering; data passes through the discipline immediately |
| `~ISIG` | Signal generation (Ctrl+C → SIGINT) disabled; `kill_process_tree` handles teardown |

`stderr` is kept as a standard asyncio pipe (not on the PTY) so it can be drained separately without mixing into the JSON stream.

**Disabling PTY mode:** Set `"use_pty": false` in `executor_config.claude_code` to fall back to the original pipe-based I/O (required on Windows, optional on Unix).

**Result completion:** When Claude Code emits a `result` event (turn complete), a `session_end_ask` message is pushed to ask the user whether they want to end the session or continue chatting.

**Environment:** The `CLAUDECODE` and `CLAUDE_AVAILABLE_MODELS` environment variables are removed from the subprocess environment to allow nesting.

**Tool Definition Example:**

```json
{
  "name": "claude_code",
  "description": "Start a Claude Code coding agent session. Claude Code can read, write, and execute code autonomously. Use for complex tasks: implementing features, fixing bugs, refactoring, writing tests, etc. The working_directory must be an existing project directory. Search all configured project directories to find the correct path. Always verify the directory exists before calling this tool.",
  "version": "1.0.0",
  "session_type": "long-running",
  "llm_context": "session-scoped",
  "executor": "claude_code",
  "parameters": {
    "type": "object",
    "properties": {
      "prompt": { "type": "string", "description": "Task instructions" },
      "working_directory": { "type": "string", "description": "Project directory" },
      "allowed_tools": { "type": "string", "description": "Space-separated allowed tools" },
      "model": { "type": "string", "description": "Model override" }
    },
    "required": ["prompt", "working_directory"]
  },
  "executor_config": {
    "claude_code": {
      "binary_path": "claude",
      "default_permission_mode": "interactive",
      "max_turns": 50,
      "timeout": 600,
      "use_pty": true
    }
  }
}
```

## Codex CLI Executor

The `codex` executor manages an OpenAI Codex CLI subprocess for delegating coding tasks to OpenAI models. Unlike the `claude_code` executor which keeps a persistent bidirectional process, Codex CLI uses a **one-shot process model**: each turn spawns `codex exec --json --full-auto PROMPT`, reads JSONL from stdout until `turn.completed` or process exit, and then the process naturally terminates.

**Working directory validation:** Same as Claude Code — the prompt router validates `working_directory` exists before spawning the subprocess.

**How it works:**

1. The outer LLM calls `codex(prompt=..., working_directory=...)`.
2. RCFlow validates that `working_directory` exists; returns an error to the LLM if not.
3. RCFlow spawns `codex exec --json --full-auto --skip-git-repo-check --cd WORKDIR` as a subprocess.
4. The prompt is written to stdin, then stdin is closed (one-shot model).
5. The first event `{"type":"thread.started","thread_id":"..."}` provides the session thread ID.
6. Output events (`item.started`, `item.updated`, `item.completed`, `turn.completed`) stream from stdout and are translated into RCFlow buffer messages.
7. After `turn.completed`, the process exits naturally.
8. Follow-up messages spawn a new process: `codex exec --json --full-auto resume THREAD_ID PROMPT`.

**Result summarization:** When Codex emits a `turn.completed` event, the prompt router fires a summary task and pushes a `session_end_ask`, same as Claude Code.

**Authentication:** Codex supports two auth methods, selectable via the per-tool `provider` setting:
- **OpenAI API key** (`provider: "openai"`): `CODEX_API_KEY` is injected into the subprocess environment from the per-tool settings.
- **ChatGPT subscription** (`provider: "chatgpt"`): OAuth tokens from `~/.codex/auth.json` are used. RCFlow symlinks this file into `CODEX_HOME` so the isolated instance can access the user's cached login. The user must run `codex login` on the host machine first.
- **Global** (`provider: ""`): Falls back to the server-level `CODEX_API_KEY` environment variable.

**JSONL event types:**
- `thread.started` — contains `thread_id` for session continuity
- `turn.started` / `turn.completed` / `turn.failed` — turn lifecycle
- `item.started` / `item.updated` / `item.completed` — individual items (agent messages, command executions, file changes, MCP tool calls)

**Tool Definition Example:**

```json
{
  "name": "codex",
  "description": "Start an OpenAI Codex coding agent session...",
  "version": "1.0.0",
  "session_type": "long-running",
  "llm_context": "session-scoped",
  "executor": "codex",
  "parameters": {
    "type": "object",
    "properties": {
      "prompt": { "type": "string", "description": "Task instructions" },
      "working_directory": { "type": "string", "description": "Project directory" },
      "model": { "type": "string", "description": "Model override (e.g. 'o3', 'gpt-5-codex')" }
    },
    "required": ["prompt", "working_directory"]
  },
  "executor_config": {
    "codex": {
      "binary_path": "codex",
      "approval_mode": "full-auto",
      "model": "",
      "timeout": 600
    }
  }
}
```

## Worktree Executor

The `worktree` executor wraps the [`wtpython`](https://github.com/Flowelfox/worktree-manager-python) library's `WorktreeManager` class. Unlike `shell` or `http` executors, it calls Python library code directly rather than spawning a subprocess. All blocking git operations run via `asyncio.to_thread` to avoid blocking the event loop.

### Tool Definition

A single `worktree` tool definition (display name **Worktree**) covers all operations. The required `action` parameter selects the operation at call time:

| `action` value | Operation                                    | Additional parameters                              |
|---------------|----------------------------------------------|-----------------------------------------------------|
| `new`         | Create a new worktree on a new branch        | `branch`, `base` (default `"main"`), `repo_path`   |
| `list`        | List all active worktrees for a repository   | `repo_path`                                         |
| `attach`      | Select an existing worktree as the session's active working directory | `repo_path`, `name` or `path` (one required) |
| `merge`       | Squash-merge a worktree branch and clean up  | `name`, `message`, `repo_path`                      |
| `rm`          | Remove a worktree and its branch             | `name`, `repo_path`                                 |

All five actions share `repo_path` (required) and live in a single `tools/worktree.json`.

`attach` validates that a matching worktree exists, then sets `session.metadata["selected_worktree_path"]` via the prompt router's `_update_session_worktree_meta` hook — the same path that Claude Code and Codex agents use as their `cwd`. Unlike `new`, `attach` never creates anything; it is a pure selection operation.

### Configuration (`WorktreeExecutorConfig`)

| Field                  | Default | Description                                            |
|------------------------|---------|--------------------------------------------------------|
| `default_base_branch`  | `"main"` | Branch to base new worktrees on when `base` is omitted |
| `validate_branch_type` | `true`  | Enforce `type/ticket/description` branch naming        |

### Platform Restriction

The `worktree` tool definition includes `"os": ["linux", "darwin"]`. It is skipped at load time on Windows because the `.worktrees/` directory convention and shell hooks are Unix-only.

### Default Base Branch (`main`)

The executor and all tool definitions explicitly default `base` to `"main"`. This is the upstream default — no assumptions about the current HEAD branch are made.

### Branch Naming Convention

New branches must follow the `type/ticket/description` pattern (e.g. `feature/PROJ-123/add-auth`, `fix/PROJ-456/null-check`). Valid type prefixes are: `feature`, `fix`, `docs`, `hotfix`, `tech-debt`. Validation can be disabled per-tool via `"validate_branch_type": false` in `executor_config.worktree`.

### Auto-commit on Merge

The `merge` action always passes `auto_commit_changes=True` to `WorktreeManager.merge()`. Since RCFlow is a non-interactive server, interactive prompts for uncommitted changes are not feasible; any uncommitted work is committed automatically with the provided merge message.

### HTTP API

The worktree HTTP routes (`src/api/routes/worktrees.py`) provide the same operations over REST for the Flutter client. See [HTTP API](http-api.md) for endpoint details.
