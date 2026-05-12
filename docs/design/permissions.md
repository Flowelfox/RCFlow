---
updated: 2026-05-12
---

# Interactive Permission Approval

When a Claude Code session is configured with `default_permission_mode: "interactive"`, the server intercepts tool-use events and asks the user before each tool executes.

**See also:**
- [WebSocket API](websocket-api.md#input-text-protocol) — `permission_response` input message and `permission_request` output message
- [Sessions](sessions.md#activity-state) — `awaiting_permission` activity state
- [Executors — Claude Code](executors.md#claude-code-executor) — where the relay intercepts tool-use blocks

---

## How It Works

1. Claude Code emits `tool_use` blocks in its stream-json output (within `assistant` events).
2. `PromptRouter._relay_claude_code_stream()` detects these blocks.
3. The `PermissionManager` checks its in-memory cache of rules. If a cached rule covers this tool/path, the decision is applied silently.
4. If no cached rule matches, a `PERMISSION_REQUEST` message is pushed to the session buffer, the session activity state changes to `awaiting_permission`, and the stream reading coroutine blocks on an `asyncio.Event`.
5. The client displays a `PermissionRequestCard` with the tool name, description, risk level, and scope options (just this once / all uses of this tool / all tools).
6. The user's response arrives as a `permission_response` message on the input WebSocket.
7. `PromptRouter.resolve_permission()` resolves the pending request, optionally stores a rule in the `PermissionManager`, and signals the event.
8. The stream reading coroutine resumes. If denied, a `TOOL_START` message is emitted with `permission_denied: true`. If allowed, the tool proceeds normally.

## Key Components

| Component | File | Purpose |
|-----------|------|---------|
| `PermissionManager` | `src/core/permissions.py` | Per-session permission cache, pending request tracking, rule storage |
| `PermissionDecision` / `PermissionScope` | `src/core/permissions.py` | Enums for allow/deny and scope levels |
| `classify_risk()` | `src/core/permissions.py` | Classifies tool invocations as low/medium/high/critical risk |
| `PERMISSION_REQUEST` | `src/core/buffer.py` | New `MessageType` for permission request messages |
| `AWAITING_PERMISSION` | `src/core/session.py` | New `ActivityState` for blocked-on-approval |
| `PermissionRequestCard` | `rcflowclient/.../permission_request_card.dart` | Flutter widget for the approval UI |

## Permission Scopes

| Scope | Meaning |
|-------|---------|
| `once` | Applies to this single request only |
| `tool_session` | Applies to all uses of this tool for the rest of the session |
| `tool_path` | Applies to this tool for files under a directory prefix (Read/Write/Edit/Glob/Grep) |
| `all_session` | Blanket allow/deny for ALL tools for the rest of the session |

## Risk Classification

Tools are classified by risk level to help the user make informed decisions:

| Risk | Tools | Description |
|------|-------|-------------|
| Low | Read, Glob, Grep, WebFetch | Read-only operations |
| Medium | Write, Edit, NotebookEdit, Agent | File modifications, sub-agent launches |
| High | Bash | Shell command execution |
| Critical | Bash (destructive patterns) | `rm`, `git push --force`, `kill`, etc. |

## Edge Cases

- **Timeout**: If no response arrives within 120 seconds, the request is auto-denied.
- **Client disconnect**: Pending requests stay active. Timeout eventually auto-denies. Reconnecting clients can still respond to unexpired requests.
- **Session pause/cancel**: All pending permission requests are auto-denied via `PermissionManager.cancel_all_pending()`.
- **Session restore**: Permission rules saved in `session.metadata["permission_rules"]` are restored so the user doesn't re-approve previously approved tools.
- **Multiple clients**: Only the first response for a given `request_id` takes effect; subsequent responses are silently ignored.

## AskUserQuestion Gating

When Claude Code emits an `AskUserQuestion` tool_use, the relay does **not**
treat it like a normal tool call. Instead, it pushes a `TOOL_START` so the
client renders the question UI, then **blocks the stream reader on an
`asyncio.Event`** (mirroring the `ExitPlanMode` gate).

Without this gate the relay would keep reading Claude Code's stdout, which
auto-cancels the question and lets the assistant proceed as if the user had
refused to answer — exactly the failure mode the gate is there to prevent.

1. `_relay_claude_code_stream` detects the `AskUserQuestion` tool_use.
2. The relay pushes `TOOL_START` with the question payload and sets the
   session's activity state to `awaiting_permission`.
3. `session._question_event` is created and the relay awaits it (timeout: 1
   hour, matching the plan-mode/plan-review gates).
4. The client renders the `QuestionBlock` widget and the user submits an
   answer (`question_answer` input message on the WebSocket).
5. `send_interactive_response` stores the answer text on
   `session._question_response`, marks the `TOOL_START` history entry as
   `answered`, and sets the event.
6. The relay wakes up, forwards the answer text to Claude Code's stdin as
   a new user turn, and resumes reading the stream.
7. On session pause/cancel, the event is released with `_question_response =
   None` so the relay can exit cleanly instead of timing out an hour later.

## Limitations

- Currently supported for Claude Code sessions only. Codex uses a one-shot process model where stdin is closed after writing the prompt, making interactive approval infeasible without a fundamental I/O change. Codex interactive permissions are planned for a future release.
- When `default_permission_mode` is set to `"interactive"` (or not set), the server does **not** pass `--permission-mode` to Claude Code, letting it use its default behavior. This allows Claude Code to emit interactive events (AskUserQuestion, EnterPlanMode, ExitPlanMode) via stream-json, which the server intercepts and forwards to the client. Mid-turn responses (question answers, plan approval) are sent directly to Claude Code's stdin via the `interactive_response` message type, without creating a new agent group or reading task. For other permission modes (e.g., `bypassPermissions`, `allowEdits`), the value is passed directly to `--permission-mode`.
