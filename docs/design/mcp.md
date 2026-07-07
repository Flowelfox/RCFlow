---
updated: 2026-07-07
---

# MCP Agent Bridge

Lets nested coding agents (Claude Code, Codex) discover and call RCFlow tools during a session, over the Model Context Protocol. The agent sees the tools as `mcp__rcflow__<name>`; calls route back through RCFlow's executor layer and appear in the session chat.

**See also:**
- [Tools](tools.md) — `expose_to_agents` tool field, `expose_rcflow_tools` settings toggle
- [Executors](executors.md) — per-agent wiring details
- [HTTP API](http-api.md) — `/api/mcp/*` endpoint table

---

## Zero-Touch Contract

**Adding a new agent-exposed tool = dropping a `tools/*.json` file with `"expose_to_agents": true`, then restarting the worker. No code changes — ever.**

Everything the bridge serves derives from the `ToolRegistry` at call time:

- **No tool names in code.** The bridge, the SDK server builder, the HTTP endpoints, and the stdio proxy iterate the registry. A hardcoded tool name anywhere in the MCP path is a defect.
- **Schema pass-through.** `parameters` (already JSON Schema) flows verbatim to MCP `inputSchema`.
- **Dispatch by executor type.** Calls resolve the tool definition and run through the shared one-shot execution path. A new tool on an existing executor type is invisible to the bridge.
- **Fresh list per agent spawn.** Claude Code's in-process server is built at connect from a live registry read; Codex's proxy fetches the list from the worker per request. Worker restart is the delivery mechanism for new tool files (hot-reload deliberately not needed — the worker restarts frequently).
- **Recursion guard.** Tools with an agent executor (`claude_code`, `codex`, `opencode`) are never exposed, regardless of the flag — enforced at load time (loader forces the flag off with a warning) and again in the bridge.

The contract is enforced by test: `tests/test_services/test_mcp_bridge.py::TestSeamlessnessContract` synthesises a tool JSON at test time and asserts it lists and dispatches with no registration anywhere.

## Architecture

```
                         ┌────────────────────────────────────────────┐
                         │                RCFlow worker               │
                         │                                            │
 Claude Code ──(in-proc SDK MCP server)──► McpBridge ──► one-shot     │
                         │                    ▲          executor path │
                         │                    │          (shell/http/  │
 Codex ──► rcflow-mcp ──HTTP──► /api/mcp/* ───┘           worktree)    │
 (child)   stdio proxy   │   (per-session token)                      │
                         └────────────────────────────────────────────┘
```

`McpBridge` (`src/services/mcp_bridge.py`) is the single shared component:

- `list_agent_tools()` — registry tools with `expose_to_agents: true`, minus agent executors, mapped to MCP tool shape.
- `call_tool(session_id, tool_name, arguments)` — resolves the session and tool, dispatches through `PromptRouter.execute_one_shot_tool(..., origin="agent")`, returns `(text, is_error)` as a `ToolCallOutcome`. All failures (unknown session, unexposed tool, executor error, denied permission) come back as `is_error=True` outcomes, never exceptions, so both consumers relay them as MCP tool errors.
- **Worktree gate.** Mutating worktree operations (everything except `list`) always require explicit user approval — the same invariant as the LLM tool loop. The bridge is the single gate for both agents: it runs the interactive permission check itself, and Claude Code's `can_use_tool` waves `mcp__rcflow__*` tools through so the prompt is never doubled.
- `tokens` — the per-session token registry (below).

Constructed in `main.py` after the `PromptRouter` (bidirectional dependency: the bridge dispatches through the router; the router hands executors the bridge at spawn). Available as `app.state.mcp_bridge`; injected into the router via `set_mcp_bridge()`.

Bridge-originated tool calls push `TOOL_START`-path buffer messages with an `origin: "agent"` field so the client can distinguish agent-initiated calls from LLM-loop calls.

## Per-Agent Wiring

### Claude Code — in-process SDK MCP server

`ClaudeCodeSdkExecutor` builds an in-process server (`create_sdk_mcp_server`) from `McpBridge.list_agent_tools()` when the `expose_rcflow_tools` setting is on, and passes it via `ClaudeAgentOptions.mcp_servers={"rcflow": …}`. Tool handlers call `bridge.call_tool()` directly — same process, no IPC, no token needed. The bridge handle is plumbed from `PromptRouter` at both construction sites (fresh spawn and resume/restore).

Permissions: `can_use_tool` short-circuits `mcp__rcflow__*` tools with an allow — the bridge gates them itself (see the worktree gate above), so gating in both places would double-prompt.


### Codex — stdio proxy + HTTP

Codex only speaks external MCP servers, so RCFlow ships `rcflow-mcp` (`src/mcp_proxy.py`, console script in `[project.scripts]`): a stdlib-only, newline-delimited JSON-RPC stdio server handling `initialize` / `tools/list` / `tools/call` (+ `ping`) and proxying the tool methods to the worker's `/api/mcp/*` endpoints.

At Codex spawn (`CodexAgent._configure_mcp_bridge`):

1. The managed `CODEX_HOME/config.toml` gets a marker-delimited, machine-owned `[mcp_servers.rcflow]` block pointing at the venv's `rcflow-mcp` binary — added when the toggle is on, removed when off (`ensure_codex_mcp_registration` in `src/services/tool_settings.py`). User-added entries — including a hand-written `[mcp_servers.rcflow]` — are never touched. All failures are non-fatal.
2. A per-session token is issued and injected into the Codex subprocess env as `RCFLOW_MCP_TOKEN`, plus `RCFLOW_MCP_URL` (loopback + `RCFLOW_PORT`). Codex spawns the proxy as its own child, which inherits that env. The config block is static and shared across sessions; all per-session data travels via env.

> **Verification caveat:** env inheritance from the Codex process to its MCP server children is assumed (standard child-process behaviour) but not yet verified against a live Codex run. If Codex sanitises the env, token delivery needs a fallback (worker-lifetime token in the config `env` map).

## Token Model

Per-session bearer tokens for the HTTP path (`McpSessionTokenRegistry`):

- Issued at Codex spawn (`secrets.token_urlsafe(32)`), one live token per session (re-issue replaces).
- In-memory only — never persisted; a worker restart kills the subprocesses holding them.
- Revoked in `_end_codex_session`.
- Scope: unlocks only `/api/mcp/tools` and `/api/mcp/call`, bound to the issuing session.

The worker-wide `RCFLOW_API_KEY` is deliberately **not** accepted on these endpoints and must never be handed to an agent subprocess. The worker binds `0.0.0.0`; endpoint security rests on the token, not the binding — the proxy targets loopback but the token is mandatory regardless.

## Settings

Per-tool boolean `expose_rcflow_tools` (default off, managed-only) in both the Claude Code and Codex settings schemas. Takes effect for new sessions.

## Initial Exposure

Two tools ship with `expose_to_agents: true`:

- **`system_info`** — safe read-only demonstrator of the pipeline.
- **`worktree`** — worktree operations as a *worker-managed* tool. Agents could already shell out to the `wt` CLI, but the bridge route runs through RCFlow's worktree executor, so the session's worktree state stays in sync: `_update_session_worktree_meta` fires on mutating calls (selected worktree, badge broadcast, auto-select after `new`), which raw `wt` invocations bypass. Mutating actions hit the always-ask permission gate; `list` is exempt. The `wt` CLI remains available on the agent PATH, but the MCP tool is the preferred route precisely because the worker sees it. Unix-only (the tool definition carries `"os": ["linux", "darwin"]`).

`shell_exec` stays unexposed (agents have their own shell). The bridge's value grows further with RCFlow-native tools (notify user, register artifact, session status) — planned as a `python` callable executor type so they ride the same registry pipeline (see Future Considerations in [README](README.md)).
