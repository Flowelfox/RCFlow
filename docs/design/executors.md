---
updated: 2026-07-08
---

# Executors

Per-executor implementation details for the long-running coding agents (Claude Code, Codex CLI) and the worktree manager.

**See also:**
- [Tools](tools.md) — JSON tool schema, executor selection field, per-tool settings
- [Sessions](sessions.md) — long-running session lifecycle
- [Permissions](permissions.md) — interactive approval relay (Claude Code only)
- [MCP Agent Bridge](mcp.md) — how both agents list/call RCFlow tools during a session

---

## Claude Code Executor

The `claude_code` executor drives Claude Code through the **Python Agent SDK** (`claude-agent-sdk`) via `ClaudeCodeSdkExecutor` (`src/executors/claude_code_sdk.py`), which spawns the **managed** `claude` binary (`ClaudeAgentOptions.cli_path`) and yields typed messages. The executor's converter (`sdk_message_to_events`) adapts those into the stream-json event dicts that `_relay_claude_code_stream` parses, so all diff/monitor/cwd/artifact handling lives in the relay. It enables delegating complex coding tasks to Claude Code while streaming output back to the client in real time.

**Permissions and AskUserQuestion** are resolved in-process by the SDK `can_use_tool` callback (`ClaudeCodeAgent._make_can_use_tool`) *before* a tool runs — AskUserQuestion is genuinely interactive (the widget is shown, the user's selection is returned as the tool's answer via `updated_input.answers`, and the model continues in the same turn). The relay does not gate questions or permissions; it just records the question's `tool_use_id` to drop the resolved tool_use / tool_result from the chat (the widget shows the answer). `permission_mode="default"` is always used (the callback would be skipped under `bypassPermissions`). Plan mode (`EnterPlanMode` / `ExitPlanMode`) is gated the same way — approve → allow; plan-review feedback → deny-with-message so the model revises.

**AskUserQuestion as the last action of a turn:** Claude Code emits the turn-boundary `result` while the `can_use_tool` gate is still open, so the relay returns before the user answers. `_drain_question_continuation` (called at relay-end) then waits for the answer and streams the model's follow-up turn so the agent resumes on its own — without it the answer was accepted but the continuation sat unread until a manual pause/resume.

**Monitor under the SDK:** the SDK delivers a watch as a "task" — `MONITOR_START` on the tool_use (in-turn), and the **terminal** as a `TaskNotificationMessage` (`status`, `tool_use_id`) *between turns*. The executor uses a single persistent `receive_messages()` reader → queue (so turn streaming and the between-turn drain share one consumer), and `sdk_message_to_events` maps the `TaskNotificationMessage` into a synthesised monitor-terminal `tool_result` → `_process_monitor_event` → `MONITOR_END` (the card clears; no relay changes). The drain yields only monitor `tool_result`s, so the model's per-event wake narration ("Monitor event — no action needed") is suppressed. **Caveat (inherent CC behavior):** each Monitor event *wakes the model* (an extra turn + tokens per event) — RCFlow can't change that. See the Monitor section below for the relay-side handling.

**RCFlow tools over MCP:** when the per-tool `expose_rcflow_tools` setting is on, `_build_options` attaches an in-process SDK MCP server (`mcp_servers={"rcflow": …}`) built live from the [MCP agent bridge](mcp.md)'s registry tool list, so Claude Code can call agent-exposed RCFlow tools as `mcp__rcflow__<name>`. Handlers dispatch straight into the bridge (same process, no token). `can_use_tool` waves `mcp__rcflow__*` through — the bridge gates these calls itself (mutating worktree ops always ask; see [MCP Agent Bridge](mcp.md)).

**Lifecycle:** the SDK client is persistent — one `ClaudeSDKClient` per session, kept alive between turns. Follow-up user messages bypass the outer LLM ("Claude Code mode") and go to the same client via `query()`. If the client dies unexpectedly, the executor reopens it with `resume=<session_id>` so the conversation continues.

> **History:** a raw-CLI executor (bidirectional stream-json over a PTY, `RCFLOW_CC_EXECUTOR=legacy`) predated the SDK migration. It has been removed; the SDK executor is the only implementation.

**Working directory priority:** The Claude Code executor selects the working directory with the following precedence:
1. `session.metadata["selected_worktree_path"]` — the active worktree path (set via `PATCH /api/sessions/{id}/worktree`, the `attach` worktree action, auto-selected after `new`, or pre-selected by the client in the first `prompt` WS message via `selected_worktree_path`).
2. `session.main_project_path` — the project folder attached via the project chip.
3. `working_directory` from the tool call input (LLM-specified).
4. `"."` (current directory) as final fallback.

**Worktree selection persistence:** `selected_worktree_path` is stored in `session.metadata` and is written to the DB both by the initial `_ensure_session_row_in_db` stub write (on the first prompt) and immediately when set via `PATCH /api/sessions/{id}/worktree` (via `SessionManager.persist_session_metadata`). This ensures the selected worktree survives backend restarts. When the client pre-selects a worktree before the first message (via the worktree chip), `handle_prompt` applies the path to `session.metadata["selected_worktree_path"]` before `_ensure_session_row_in_db`, so the initial DB stub row already contains the selection.

**Working directory validation:** Before spawning the subprocess, the prompt router validates that the specified `working_directory` exists on disk. If it does not, the tool returns an error message to the LLM instead of starting a session. The system prompt also instructs the LLM to verify directory existence via `shell_exec` before calling `claude_code`, and to resolve project names to `~/Projects/<project_name>`.

**Result completion:** When Claude Code emits a `result` event (turn complete), a `session_end_ask` message is pushed to ask the user whether they want to end the session or continue chatting.

**Environment:** The `CLAUDECODE` and `CLAUDE_AVAILABLE_MODELS` environment variables are removed from the subprocess environment to allow nesting.

**Monitor tool (long-running watches):** Claude Code's deferred `Monitor` tool starts a background script and emits one `tool_result` block per stdout-line batch (using the same `tool_use_id` as the original `tool_use`). Unlike normal tools, Monitor invocations stay open across many turns until the script exits, times out, or is stopped via `TaskStop`.

The Claude Code agent mixin recognises `Monitor` tool calls and:

1. Tracks each invocation in `ActiveSession._active_monitors` keyed by `tool_use_id` (`MonitorState`: description, command, timeout, persistent, started_at, event_count).
2. Emits `MONITOR_START` instead of `TOOL_START`.
3. Diverts subsequent `tool_result` blocks for that id to `_process_monitor_event`, which emits `MONITOR_EVENT` for each stdout batch and `MONITOR_END` when a terminal payload is detected (heuristic: `is_error=True` or content prefix matches `"Monitor exited"`, `"Monitor timed out"`, `"Monitor stopped"`).
4. Skips the `_pending_snapshots` stack entirely so snapshot/diff alignment for interleaved Edit/Write tools is preserved.
5. Skips `subprocess_current_tool` updates so a long-running monitor does not hijack the input-area subprocess indicator while normal tools execute.
6. On `_end_claude_code_session`, `pause_session`, `interrupt_subprocess`, and `max_turns` pause, `_terminate_active_monitors(reason="session_end" | "cancelled")` flushes any remaining live monitors so the UI never sees a perpetually-live block.
7. After every turn-end (`result` event), if any monitor is still tracked, `_drain_monitor_events(session, executor)` keeps the stdout reader alive across user turns by repeatedly calling `executor.read_more_events()`. Without this drain, Claude Code's between-turn `tool_result` blocks for the deferred Monitor (including the terminal "Monitor exited/timed out/stopped" payload) would sit unread in the OS pipe buffer until the next user input, causing `MONITOR_END` to never reach the client and the live-monitor strip to never clear on its own. The drain runs inline in the encompassing `_claude_code_stream_task` so `_forward_to_claude_code` stays the cancel target when the user sends a new message.

User-initiated cancel from the client sends a `cancel_monitor` ws-input message; `cancel_monitor(session_id, monitor_id)` on the mixin emits `MONITOR_END(reason="cancelled")` immediately for instant UI feedback and (when Claude Code is still running) injects a stdin instruction asking it to call `TaskStop` on the matching watcher.

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
      "timeout": 600
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

**RCFlow tools over MCP:** when the per-tool `expose_rcflow_tools` setting is on, Codex spawn syncs a machine-owned `[mcp_servers.rcflow]` block into the managed `CODEX_HOME/config.toml` (pointing at the bundled `rcflow-mcp` stdio proxy) and injects a per-session `RCFLOW_MCP_TOKEN` + `RCFLOW_MCP_URL` into the subprocess env. The proxy forwards `tools/list` / `tools/call` to the worker's `/api/mcp/*` endpoints. When the setting is off the block is removed. See [MCP Agent Bridge](mcp.md).

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

## ACP Executor

The `acp` executor drives any agent that speaks the [Agent Client Protocol](https://agentclientprotocol.com) as a persistent stdio subprocess — one executor for every ACP agent. RCFlow is the ACP *client*; per-agent differences live entirely in the tool definition's `executor_config.acp` (binary + args) and the per-agent env builders — never in code branches.

**Current agents:** OpenCode (native, `opencode acp`) and Codex (via the `codex-acp` adapter binary). Claude Code deliberately stays on the SDK executor — it migrates only when the PyPI ACP adapter reaches production parity (cwd handling, resume, usage, AskUserQuestion fidelity).

**ACP is the default.** With the rollback flags (`RCFLOW_OPENCODE_EXECUTOR` / `RCFLOW_CODEX_EXECUTOR`, values `acp` | `legacy`) unset, an agent tool runs over ACP whenever its definition carries an `executor_config.acp` block (both `tools/opencode.json` and `tools/codex.json` do) **and** the adapter binary is resolvable (managed ToolManager copy or `PATH`); otherwise it degrades gracefully to the legacy executor — e.g. Codex keeps its legacy JSONL path until the `codex-acp` adapter is installed. An explicit `acp` forces the ACP path (skipping the availability probe, so a missing adapter surfaces in-session); an explicit `legacy` always opts out — same rollback pattern the Claude Code SDK migration used.

**How it works:**

1. `AcpExecutor` (`src/executors/acp.py`) spawns the agent binary and negotiates `initialize` (capability exchange — `load_session` support is recorded for resume).
2. `session/new` is issued with the working directory and — when the per-tool `expose_rcflow_tools` setting is on — the RCFlow MCP bridge as a client-provided MCP server: the `rcflow-mcp` proxy command plus a per-session token passed as **explicit protocol data** in the `env` entries (no process-env inheritance, no agent config file blocks).
3. A prompt turn is one `session/prompt` call; `session/update` notifications stream concurrently and are translated by the pure `acp_update_to_event()` into normalised event dicts (`text`, `thought`, `tool_call`/`tool_call_update` with locations + diffs, `plan`, `usage`, `available_commands`, `turn_end`, `error`), which `AcpAgent._relay_acp_stream` (`src/core/agent_acp.py`) maps onto the standard buffer messages (`TEXT_CHUNK`, `THINKING`, `TOOL_START`/`TOOL_OUTPUT`, `TODO_UPDATE`, …). Tool-call `locations` feed the worktree-badge cwd tracking (`infer_cwd_from_tool_paths`).
4. Follow-up user messages are further `session/prompt` calls on the same live process ("agent mode", mirroring Claude Code). If the process died and the agent advertised `loadSession`, the next turn respawns and resumes via `session/load` (the agent replays the conversation).
5. `session/request_permission` is relayed to RCFlow's interactive permission flow (`PERMISSION_REQUEST` widget): ALLOW selects the agent's allow-once option — rule caching stays on RCFlow's side — DENY selects reject-once (or cancels the call when the agent offered no reject option). fs and terminal capabilities are declined in this phase; agents use their own filesystem/shell access exactly as on the legacy paths.
6. Turn completion carries an ACP stop reason: `end_turn` fires the summary/task-update pipeline; `cancelled` ends quietly; `max_tokens` / `max_turn_requests` / `refusal` surface as errors. `usage_update` totals (tokens, context size, cost) land in `session.metadata["acp_usage"]`.

**Resume across pause/restart:** the agent-issued ACP session id is persisted in `session.metadata["acp_session_id"]`; resume reconstructs the executor with `set_resume_target()` so the next turn issues `session/load`.

**Configuration (`AcpExecutorConfig`):**

| Field | Default | Description |
|---|---|---|
| `binary_path` | — | ACP agent/adapter binary. Managed ToolManager copies win: resolution keys off the *binary* name (`opencode` → managed opencode, `codex-acp` → managed codex_acp) |
| `args` | `[]` | Arguments (e.g. `["acp"]` for OpenCode's server mode) |
| `timeout` | `1800` | Per-turn wall-clock timeout in seconds |

**Known limitations:**
- OpenCode's default policy auto-allows edits, so it rarely asks for permission — matching the legacy path's behaviour. The relay is fully wired; agents that ask (codex-acp does by default) get the interactive widget. Seeding ask-mode into OpenCode's own config needs a verified delivery mechanism first.
- **Per-tool `model` / `approval_mode` settings are not applied on the ACP path.** ACP's `session/new` has no standard model parameter, and `AcpExecutor` only forwards `timeout` from the managed config overrides. A user who configured a specific model for OpenCode/Codex gets the agent's own default when the tool runs over ACP (the default mode). Wiring model selection needs the agent's `set_session_mode` / model-enumeration surface; pin the agent to the legacy executor (`RCFLOW_*_EXECUTOR=legacy`) if a specific model is required meanwhile.

## Worktree Executor

The `worktree` executor wraps the [`wtpython`](https://github.com/Flowelfox/worktree-manager-python) library's `WorktreeManager` class. Unlike `shell` or `http` executors, it calls Python library code directly rather than spawning a subprocess. All blocking git operations run via `asyncio.to_thread` to avoid blocking the event loop.

### Tool Definition

A single `worktree` tool definition (display name **Worktree**) covers all operations. The required `action` parameter selects the operation at call time:

| `action` value | Operation                                    | Additional parameters                              |
|---------------|----------------------------------------------|-----------------------------------------------------|
| `new`         | Create a new worktree on a new branch        | `branch`, `base` (default `"main"`), `repo_path`   |
| `list`        | List all active worktrees for a repository   | `repo_path`                                         |
| `attach`      | Select an existing worktree as the session's active working directory | `repo_path`, `name` or `path` (one required) |
| `detach`      | Return the session to the main repo (deselect the worktree) | `repo_path`                          |
| `get`         | Return details for a single worktree by name | `name`, `repo_path`                                 |
| `init`        | Initialise the repo's `.worktrees` convention and return its config | `repo_path`                          |
| `merge`       | Squash-merge a worktree branch and clean up  | `name`, `message`, `repo_path`                      |
| `rm`          | Remove a worktree and its branch             | `name`, `repo_path`                                 |

All actions share `repo_path` (required) and live in a single `tools/worktree.json`; each maps to the matching `wtpython.WorktreeManager` method. Read-only `list` and `get` skip the always-ask approval gate.

`attach` validates that a matching worktree exists, then sets `session.metadata["selected_worktree_path"]` via the prompt router's `_update_session_worktree_meta` hook — the same path that Claude Code and Codex agents use as their `cwd`. Unlike `new`, `attach` never creates anything; it is a pure selection operation.

### Configuration (`WorktreeExecutorConfig`)

| Field                  | Default | Description                                            |
|------------------------|---------|--------------------------------------------------------|
| `default_base_branch`  | `"main"` | Branch to base new worktrees on when `base` is omitted |
| `validate_branch_type` | `true`  | Respect the repo's naming convention from `.worktrees/.wt-config` (no-op when none is configured); `false` skips validation even when configured |

### Platform Restriction

The `worktree` tool definition includes `"os": ["linux", "darwin"]`. It is skipped at load time on Windows because the `.worktrees/` directory convention and shell hooks are Unix-only.

### Default Base Branch (`main`)

The executor and all tool definitions explicitly default `base` to `"main"`. This is the upstream default — no assumptions about the current HEAD branch are made.

### Branch Naming Convention

By default any branch name is accepted — no naming convention is enforced. A repository can opt into a `<type>/...` convention by listing allowed types in its `.worktrees/.wt-config` file (wtpython ≥ 1.2.0), e.g. `{"valid_branch_types": ["feature", "fix"]}`; branch names must then start with one of the configured types, and the prefix is stripped from the worktree directory name. Validation can additionally be disabled per-tool via `"validate_branch_type": false` in `executor_config.worktree` (skips the convention even when configured).

### Auto-commit on Merge

The `merge` action always passes `auto_commit_changes=True` to `WorktreeManager.merge()`. Since RCFlow is a non-interactive server, interactive prompts for uncommitted changes are not feasible; any uncommitted work is committed automatically with the provided merge message.

### HTTP API

The worktree HTTP routes (`src/api/routes/worktrees.py`) provide the same operations over REST for the Flutter client. See [HTTP API](http-api.md) for endpoint details.

### Agent Exposure (MCP)

The `worktree` tool ships with `"expose_to_agents": true`, so nested agents can call it as `mcp__rcflow__worktree` via the [MCP agent bridge](mcp.md). This is the preferred route over the `wt` CLI (which stays on the agent PATH) because calls run through this executor — session worktree metadata, the client badge, and auto-selection after `new` stay in sync, which raw `wt` invocations bypass. Mutating actions require explicit user approval (the same always-ask rule as the LLM tool loop); `list` is exempt.
