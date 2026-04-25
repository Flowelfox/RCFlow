---
updated: 2026-04-26
---

# WebSocket API

Streaming protocol for prompts (input) and responses + live state (output). Two endpoints, JSON message framing, and a wide range of message types covering sessions, tasks, artifacts, queued messages, and Linear issues.

**See also:**
- [Sessions](sessions.md) — lifecycle, queueing, token tracking
- [Permissions](permissions.md) — `permission_request` / `permission_response` semantics
- [Linear Integration](linear.md) — Linear-specific WS messages
- [HTTP API](http-api.md) — REST counterparts for many of these operations

---

## Contents

- [Endpoints](#endpoints)
- [Authentication](#authentication)
- **Input messages (client → server)**
  - [Prompt + attachments](#input-text-protocol)
  - [`cancel_queued`, `edit_queued`](#input-text-protocol) — see Queueing
  - [`start_plan_session`](#input-text-protocol)
  - [`end_session`, `pause_session`, `resume_session`, `restore_session`, `dismiss_session_end_ask`](#input-text-protocol)
  - [`question_answer`, `interactive_response`](#input-text-protocol)
  - [`permission_response`](#input-text-protocol)
  - [`subscribe`, `unsubscribe`, `subscribe_all`](#session-subscription)
  - [`list_sessions`, `list_tasks`, `list_artifacts`, `list_linear_issues`](#session-list)
- **Output messages (server → client)**
  - [`text_chunk`, `tool_start`, `tool_output`, `error`](#output-text-protocol)
  - [`session_end_ask`, `session_end`, `summary`, `turn_complete`](#output-text-protocol)
  - [`todo_update`, `thinking`](#output-text-protocol)
  - [`agent_group_start`, `agent_group_end`](#output-text-protocol)
  - [`permission_request`](#output-text-protocol)
  - [`subprocess_status`](#output-text-protocol) (ephemeral)
  - [`session_paused`, `session_resumed`, `session_restored`](#output-text-protocol)
  - [`plan_mode_ask`, `plan_review_ask`](#output-text-protocol)
  - [`session_update`](#output-text-protocol) (incl. queued snapshot)
  - [`message_queued`, `message_dequeued`, `message_queued_updated`, `cancel_ack`, `edit_ack`](#queued-message-events)
  - [`session_list`, `session_reorder`](#session-list)
  - [`task_list`, `task_update`, `task_deleted`](#task-messages)
  - [`artifact_list`, `artifact_deleted`](#artifact-messages)
  - [`linear_issue_*`](linear.md#websocket-messages)
- **Reference**
  - [Error codes](#output-text-protocol) (search `Recognised error codes`)
  - [Task status transitions](#task-status-transitions)
  - [Chat attachments upload flow](#chat-attachments)
  - [Artifact scanner](#artifact-scanner)

---

## Endpoints

| Endpoint            | Direction       | Format         | Purpose                                      |
|---------------------|-----------------|----------------|----------------------------------------------|
| `/ws/input/text`    | Client → Server | JSON           | User sends natural language prompts           |
| `/ws/output/text`   | Server → Client | JSON           | Streaming text responses chunk-by-chunk       |

## Authentication

All WebSocket connections require an API key. Passed as a query parameter:

```
wss://host:port/ws/input/text?api_key=<KEY>
```

## Input Text Protocol

Client sends JSON messages:

```json
{
  "type": "prompt",
  "text": "list all files in the current directory",
  "session_id": null
}
```

To include file attachments, upload each file first via `POST /api/uploads`, then reference the returned `attachment_id`s in the `attachments` field:

```json
{
  "type": "prompt",
  "text": "What is in this image?",
  "session_id": null,
  "attachments": [
    {"id": "<attachment_id>", "name": "photo.jpg", "mime_type": "image/jpeg"}
  ]
}
```

The server resolves each `attachment_id` from the `AttachmentStore`, builds multimodal content blocks (image blocks for JPEG/PNG/GIF/WEBP, inline text blocks for text/code files, metadata placeholders for other binary files), and passes them to the LLM alongside the user's text. Attachment content is adapted to the active LLM provider format (Anthropic base64 image blocks or OpenAI `image_url` blocks). Unused attachment IDs expire after 10 minutes.

- `session_id`: `null` to create a new session, or an existing session ID to send a follow-up prompt to a conversational or long-running session.
- `attachments`: Optional list. Each entry must have `id` (from `POST /api/uploads`) and `name`/`mime_type` for display. Entries with missing or expired IDs are silently skipped.
- `project_name`: Optional folder name (not a full path) of the project to attach to this session. The backend resolves it via `PROJECTS_DIR` and sets `session.main_project_path`. On failure (project not found or unreadable) the backend pushes an `error` message with code `PROJECT_ERROR` and broadcasts a `session_update` with a non-null `project_name_error`. The client sends this field from the project chip (not from `@mention` text).
- `selected_worktree_path`: Optional absolute path of a worktree pre-selected by the client **before the first message is sent**. Applied to `session.metadata["selected_worktree_path"]` only when the session does not already have a worktree selection (i.e., idempotent and non-clobbering). Subsequent worktree changes must use `PATCH /api/sessions/{id}/worktree`. The client sends this field from the worktree chip (visible only when `session_id == null` and a project is selected).
- `task_id`: Optional UUID string. When set on a new-session prompt, `session.metadata["primary_task_id"]` is populated so plan context can be injected into implementation sessions (see [Pre-Planning Sessions](sessions.md#pre-planning-sessions)).

**Queuing behavior**: If the session is currently busy with a prior turn (see [Queued User Messages](sessions.md#queued-user-messages)), the prompt is enqueued rather than delivered. The server responds with `{"type": "ack", "queued": true, "queued_id": "<uuid>"}` and broadcasts a `message_queued` event. When the agent becomes free, queued messages are drained FIFO. Non-busy sessions receive `{"type": "ack", "queued": false}` and process the prompt normally.

Cancel a queued message (only valid while the message is still queued; no-op after delivery):

```json
{
  "type": "cancel_queued",
  "session_id": "uuid",
  "queued_id": "uuid"
}
```

Edit the text of a queued message in place (only valid while queued; same `queued_id` and FIFO position retained):

```json
{
  "type": "edit_queued",
  "session_id": "uuid",
  "queued_id": "uuid",
  "content": "new routing text (may contain #mentions)",
  "display_content": "new chat-visible text (optional; defaults to content with mentions stripped)"
}
```

Only the text fields can be edited. Attachments are fixed at enqueue time.

Start a read-only pre-planning session for a task (ONE_SHOT, write-restricted):

```json
{
  "type": "start_plan_session",
  "task_id": "uuid",
  "project_name": "my-project",
  "selected_worktree_path": "/path/to/worktree"
}
```

The server calls `prepare_plan_session()`, fires the planning prompt as a background task, and immediately sends a `session_update` ack. When the session ends (for any reason) the plan file is upserted as an artifact and linked to the task via `plan_artifact_id`, which triggers a `task_update` broadcast.

End a session (user-confirmed completion):

```json
{
  "type": "end_session",
  "session_id": "uuid"
}
```

Pause a session:

```json
{
  "type": "pause_session",
  "session_id": "uuid"
}
```

Resume a paused session:

```json
{
  "type": "resume_session",
  "session_id": "uuid"
}
```

Restore an archived session (completed/failed/cancelled) back to active:

```json
{
  "type": "restore_session",
  "session_id": "uuid"
}
```

Dismiss the "Task complete. End this chat?" widget (user clicked Continue):

```json
{
  "type": "dismiss_session_end_ask",
  "session_id": "uuid"
}
```

Answer a question from Claude Code (AskUserQuestion):

```json
{
  "type": "question_answer",
  "session_id": "uuid",
  "answers": {"question text": "selected answer"}
}
```

Send a mid-turn interactive response (plan mode approval, question answers, etc.):

```json
{
  "type": "interactive_response",
  "session_id": "uuid",
  "text": "yes",
  "accepted": true
}
```

The `accepted` field is optional (defaults to `true`) and is used for plan review responses: `true` = approve the plan, `false` = provide feedback for revision. It is ignored for all other interactive response types (question answers, plan mode approval).

Respond to a permission request (allow/deny a tool use):

```json
{
  "type": "permission_response",
  "session_id": "uuid",
  "request_id": "uuid",
  "decision": "allow",
  "scope": "tool_session",
  "path_prefix": null
}
```

The `decision` field is `"allow"` or `"deny"`. The `scope` field determines how broadly the decision is cached:
- `"once"` — applies to this single request only
- `"tool_session"` — applies to all uses of this tool for the rest of the session
- `"tool_path"` — applies to this tool for files under `path_prefix` (file tools only)
- `"all_session"` — applies to ALL tools for the rest of the session

## Output Text Protocol

Server sends JSON messages:

```json
{
  "type": "text_chunk",
  "session_id": "uuid",
  "content": "Here are the files",
  "sequence": 42,
  "finished": false
}
```

```json
{
  "type": "tool_start",
  "session_id": "uuid",
  "tool_name": "shell_exec",
  "tool_input": {"command": "ls -la"}
}
```

```json
{
  "type": "tool_output",
  "session_id": "uuid",
  "tool_name": "shell_exec",
  "content": "file1.txt\nfile2.txt\n",
  "stream": "stdout",
  "is_error": false,
  "sequence": 43
}
```

Tool output is emitted for all agent executors:
- **Claude Code**: Captured from `tool_result` content blocks that Claude Code emits inside `{"type":"user", "message":{"content":[{"type":"tool_result",...}]}}` stream-json events. Content may be plain text or extracted from nested content blocks. `is_error` reflects the SDK's `is_error` flag.
- **Codex**: Captured from `item.completed` events for `command_execution` (via `aggregated_output`), `file_change` (via `diff`), and `mcp_tool_call` items. `is_error` is true when `exit_code` is non-zero (commands only).
- **LLM pipeline**: Captured from tool execution results during the agentic loop.

Large tool outputs are truncated to 100,000 characters server-side before delivery.

```json
{
  "type": "error",
  "session_id": "uuid",
  "content": "Permission denied",
  "code": "TOOL_EXEC_ERROR"
}
```

Recognised error codes:

- `TOOL_EXEC_ERROR` — tool execution failed (non-zero exit, permission denied, timeout).
- `PROMPT_PROCESSING_ERROR` — unexpected exception during an LLM turn; `content` carries the raw exception text.
- `LLM_CONFIG_ERROR` — the active `LLM_PROVIDER` has no usable API key. Emitted by the prompt-time preflight check in `PromptRouter.handle_prompt` when `LLM_PROVIDER` is `anthropic`/`openai` and the matching key is blank, and by a runtime fallback that rewrites provider `AuthenticationError`s (covers Bedrock IAM rejections and any case that slipped past preflight). `content` is a user-actionable sentence pointing to worker settings → LLM. The Flutter client also surfaces this state pre-flight as a yellow banner on the new-session pane using config values fetched via `/api/config`.
- `AGENT_CONFIG_ERROR` — a managed coding-agent CLI (Claude Code, Codex, OpenCode) was invoked but has no provider configured. Each coding agent has its own `provider` setting (no inherited "Global" choice — that option was removed because the LLM provider is conceptually independent from the coding-agent CLIs); the preflight in `_start_claude_code` / `_start_codex` / `_start_opencode` blocks the spawn when `provider` is empty or its required key is missing, so the user sees an actionable message instead of a silent PTY-backed login prompt that never produces JSON output. Carries an `agent_type` field (`claude_code` / `codex` / `opencode`) alongside `content`. The Flutter client also calls `/api/tools/auth/preflight` on connect (and after settings changes via `reloadDerivedConfig`, including after a successful tool-settings save) and renders a yellow banner above the chat — mirroring the LLM-key banner — whenever the active agent badge (session `agent_type` or pre-session chip) points at an agent whose `ready` flag is false. The banner's "Configure" button opens the worker edit dialog at the matching tool section; the form auto-flips the provider dropdown to `anthropic_login` when Claude Code's CLI reports an existing OAuth login. OAuth flows (Anthropic Login, ChatGPT) are not preflighted because their credentials live in the CLI's own store.
- `PROJECT_ERROR` — project selection failed (see the `project_name` field docs in the session-creation section).

```json
{
  "type": "session_end_ask",
  "session_id": "uuid"
}
```

```json
{
  "type": "session_end",
  "session_id": "uuid",
  "reason": "completed"
}
```

```json
{
  "type": "summary",
  "session_id": "uuid",
  "content": "A short summary of the Claude Code result."
}
```

`summary` is emitted only when the LLM-generated summary text is non-empty. Background summarization is skipped silently when the LLM returns blank output, so clients never see a `summary` message with empty `content`.

```json
{
  "type": "turn_complete",
  "session_id": "uuid"
}
```

`turn_complete` is an **ephemeral** turn-finalization signal — never archived to the database, never replayed on reconnect. Emitted at the end of a non-managed-executor turn (direct tool / LLM-only turn) so clients can finalize the trailing tool block (switch the spinner to a completed-state icon) and stop the streaming animation. Managed coding-agent executors (Claude Code, Codex, OpenCode) emit their own terminal messages instead.

```json
{
  "type": "todo_update",
  "session_id": "uuid",
  "todos": [
    {"content": "Fix the bug", "status": "in_progress", "activeForm": "Fixing the bug"},
    {"content": "Run tests", "status": "pending", "activeForm": "Running tests"},
    {"content": "Update docs", "status": "completed", "activeForm": "Updating docs"}
  ]
}
```

Emitted whenever Claude Code calls the `TodoWrite` tool. The `todos` array is the complete current task list (not a diff). The server also stores the latest todo state on the in-memory session object, queryable via `GET /api/sessions/{session_id}/todos`.

```json
{
  "type": "thinking",
  "session_id": "uuid",
  "content": "Let me analyze this problem step by step..."
}
```

Emitted when Claude Code produces `thinking` content blocks in `assistant` events (extended thinking / chain-of-thought). Multiple thinking messages for the same turn are aggregated client-side into a single collapsible block. The client renders thinking blocks as collapsed-by-default cards with a brain icon.

```json
{
  "type": "agent_group_start",
  "session_id": "uuid",
  "tool_name": "claude_code",
  "tool_input": {"prompt": "...", "working_directory": "..."}
}
```

```json
{
  "type": "agent_group_end",
  "session_id": "uuid"
}
```

```json
{
  "type": "permission_request",
  "session_id": "uuid",
  "request_id": "uuid",
  "tool_name": "Bash",
  "tool_input": {"command": "npm install"},
  "description": "Execute command: npm install",
  "risk_level": "high",
  "scope_options": ["once", "tool_session", "all_session"]
}
```

```json
{
  "type": "subprocess_status",
  "session_id": "uuid",
  "subprocess_type": "claude_code",
  "display_name": "Claude Code",
  "working_directory": "/home/user/project",
  "current_tool": "Bash",
  "started_at": "2026-03-20T12:00:00+00:00"
}
```

`subprocess_status` is **ephemeral** — it is broadcast to live subscribers only and is never archived to `text_history` or replayed on reconnect. It signals that a subprocess (Claude Code or Codex) has started, updated its active tool, or finished. When the subprocess ends, a null-type variant is sent: `{"type": "subprocess_status", "session_id": "uuid", "subprocess_type": null}`. The client uses this to show/hide the subprocess status bar. `current_tool` is optional and may be absent or `null`.

```json
{
  "type": "session_paused",
  "session_id": "uuid",
  "paused_at": "2025-01-15T10:30:00+00:00",
  "reason": "max_turns",
  "claude_code_interrupted": false
}
```

The `reason` field is optional. `"max_turns"` means Claude Code hit its configured `--max-turns` limit and the session was automatically paused. `null` (or absent) indicates a manual pause triggered by the user. The Flutter client renders a distinct `MaxTurnsPauseCard` widget in the message stream when `reason == "max_turns"`.

```json
{
  "type": "session_resumed",
  "session_id": "uuid"
}
```

```json
{
  "type": "session_restored",
  "session_id": "uuid"
}
```

```json
{
  "type": "plan_mode_ask",
  "session_id": "uuid"
}
```

The relay blocks the Claude Code stream while this message is pending. The client must send an `interactive_response` with `text: "yes"` (allow) or `text: "no"` (deny). On denial the session is ended with a `PLAN_MODE_DENIED` error. The message gains an `"accepted": true/false` field once resolved.

```json
{
  "type": "plan_review_ask",
  "session_id": "uuid",
  "plan_input": {"plan": "1. Step one\n2. Step two"}
}
```

`plan_input` contains the raw `input` dict from Claude Code's `ExitPlanMode` tool call. The `plan` key (or `content` as fallback) holds the plan text to display.

The relay blocks the Claude Code stream while this message is pending. The client must send an `interactive_response` with an `accepted` field:
- `accepted: true` — the user approves the plan. The relay forwards the text to Claude Code's stdin and execution proceeds.
- `accepted: false` — the user provides feedback. The relay forwards the feedback text to Claude Code's stdin; Claude Code revises the plan and will call `ExitPlanMode` again.

The message gains an `"accepted": true/false` field once resolved. Session cancel or pause auto-denies the pending gate.

```json
{
  "type": "session_update",
  "session_id": "uuid",
  "status": "active",
  "activity_state": "processing_llm",
  "title": "Some title",
  "session_type": "conversational",
  "created_at": "2025-01-15T10:30:00+00:00",
  "input_tokens": 1234,
  "output_tokens": 567,
  "cache_creation_input_tokens": 100,
  "cache_read_input_tokens": 200,
  "tool_input_tokens": 5000,
  "tool_output_tokens": 3000,
  "tool_cost_usd": 0.05,
  "paused_reason": "max_turns",
  "worktree": null,
  "selected_worktree_path": null,
  "main_project_path": "/home/user/Projects/RCFlow",
  "project_name_error": null,
  "agent_type": "claude_code",
  "queued_messages": []
}
```

The `paused_reason` field is only present when `status == "paused"`. `"max_turns"` means the session was automatically paused because Claude Code reached its `--max-turns` limit. `null` (absent) means a manual pause.

The `agent_type` field identifies the managed coding agent driving the session: `"claude_code"`, `"codex"`, or `null` for pure-LLM sessions. Only live (in-memory) sessions populate this field; archived sessions always return `null`. The client uses this to determine which tool to open when the user invokes `/plugins`.

Token usage fields are included in every `session_update` broadcast:
- `input_tokens` / `output_tokens`: Tokens used by the global LLM pipeline (Anthropic/Bedrock/OpenAI).
- `cache_creation_input_tokens` / `cache_read_input_tokens`: Anthropic prompt caching breakdown.
- `tool_input_tokens` / `tool_output_tokens`: Tokens used by agent tool sessions (Claude Code, Codex).
- `tool_cost_usd`: Cumulative cost reported by Claude Code (`cost_usd` from result events).

**Project name error field** (`project_name_error`): transient string field set when the backend cannot resolve or access the project folder sent via the WS prompt `project_name` field. Cleared on the next successful resolution. The client renders the project chip in error state (red, with tooltip) when this field is non-null. The field is NOT persisted to the database.

**Queued messages field** (`queued_messages`): authoritative snapshot of the session's pending message queue (see [Queued User Messages](sessions.md#queued-user-messages)). Each entry:

```json
{
  "queued_id": "uuid",
  "position": 0,
  "display_content": "tell it to also update docs",
  "submitted_at": "2026-04-22T10:30:00+00:00",
  "updated_at": "2026-04-22T10:30:00+00:00"
}
```

Entries are ordered by `position` ascending. Clients fully reconcile their local queue state from this list on every `session_update` receipt (reconnect-safe). Attachment metadata is intentionally omitted from this snapshot to keep it light; attachments are only streamed in the initial `message_queued` event and persisted server-side until drain/cancel.

## Queued Message Events

```json
{
  "type": "message_queued",
  "session_id": "uuid",
  "queued_id": "uuid",
  "position": 0,
  "content": "the full routing text including #mentions",
  "display_content": "the chat-visible text",
  "attachments": [
    {"name": "photo.jpg", "mime_type": "image/jpeg", "size": 12345}
  ],
  "submitted_at": "2026-04-22T10:30:00+00:00"
}
```

Ephemeral. Emitted when a user prompt is enqueued (because the agent is busy). Clients add the entry to their pinned "queued" bar.

```json
{
  "type": "message_dequeued",
  "session_id": "uuid",
  "queued_id": "uuid",
  "reason": "delivered"
}
```

Ephemeral. Emitted immediately before the buffer push that delivers a queued message to the agent. `reason` is `"delivered"` (drained into agent), `"cancelled"` (user cancelled), or `"session_ended"` (session ended while queued). Clients remove the entry from the queued bar. For `delivered`, a `text_chunk(role=user)` with a matching `queued_id` follows.

```json
{
  "type": "message_queued_updated",
  "session_id": "uuid",
  "queued_id": "uuid",
  "content": "new routing text",
  "display_content": "new chat-visible text",
  "updated_at": "2026-04-22T10:31:00+00:00"
}
```

Ephemeral. Emitted when a queued message is edited (server-acked). Clients with the queue open update the entry's text.

```json
{
  "type": "cancel_ack",
  "session_id": "uuid",
  "queued_id": "uuid",
  "ok": true
}
```

Direct response to a `cancel_queued` input. When `ok` is `false`, a `reason` field is present (`"already_delivered"` or `"not_found"`).

```json
{
  "type": "edit_ack",
  "session_id": "uuid",
  "queued_id": "uuid",
  "ok": true
}
```

Direct response to an `edit_queued` input. When `ok` is `false`, a `reason` field is present (`"already_delivered"`, `"empty"`, or `"not_found"`).

When a queued message is drained, the corresponding `text_chunk` echo gains an optional `queued_id` field so clients can correlate it with the pinned entry they just removed:

```json
{
  "type": "text_chunk",
  "session_id": "uuid",
  "role": "user",
  "content": "the chat-visible text",
  "sequence": 42,
  "queued_id": "uuid"
}
```

## Session Subscription

Clients control which sessions they receive output for by sending subscribe/unsubscribe messages on the output connections:

```json
{
  "type": "subscribe",
  "session_id": "uuid"
}
```

```json
{
  "type": "unsubscribe",
  "session_id": "uuid"
}
```

```json
{
  "type": "subscribe_all"
}
```

When subscribing to an existing session, the server sends the **full buffered history** for that session, then continues with live streaming. This allows pause/resume and session switching without data loss.

**Ephemeral messages** are broadcast to live subscribers only via `SessionBuffer.push_ephemeral()`. They are never appended to `text_history` and are never replayed on reconnect. The sequence counter is still incremented so ordering is preserved for live subscribers. `subprocess_status` is the only current ephemeral message type.

**Session metadata updates** (title, status, activity state, and token usage) are automatically streamed to all connected `/ws/output/text` clients without explicit subscription. When any session's title, status, activity state, or token counts change, a `session_update` message is broadcast to all output clients. This enables real-time updates of the session list and token usage display in the client UI without polling.

## Session List

Clients can request the full session list (in-memory + archived) via:

```json
{
  "type": "list_sessions"
}
```

Server responds with:

```json
{
  "type": "session_list",
  "sessions": [
    {
      "session_id": "uuid",
      "status": "completed",
      "session_type": "one-shot",
      "created_at": "2025-01-15T10:30:00+00:00",
      "title": "List files in directory",
      "input_tokens": 1234,
      "output_tokens": 567,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0,
      "tool_input_tokens": 0,
      "tool_output_tokens": 0,
      "tool_cost_usd": 0.0,
      "worktree": null,
      "selected_worktree_path": null,
      "main_project_path": null,
      "sort_order": 0
    }
  ]
}
```

Sessions are sorted by `sort_order` ascending (nulls last), then `created_at` descending as a tiebreaker. New sessions are automatically assigned a `sort_order` value that places them at the top of the list. The list includes both in-memory active sessions and archived sessions from the database. The `title` field is `null` until auto-generated after the first LLM response. The `worktree` field is `null` for sessions that have never used a worktree tool, or a dict with `repo_path`, `last_action`, `branch?`, and `base?` for sessions that have. The `selected_worktree_path` field is `null` by default and is set via `PATCH /api/sessions/{id}/worktree`; when non-null it overrides the agent working directory for Claude Code and Codex runs in that session. The `main_project_path` field is `null` until the user types `@ProjectName` in a message and the name resolves to a directory under `PROJECTS_DIR`; once set it persists across the session lifetime and is updated to reflect the latest `@` mention used. It is also included in `session_update` WebSocket broadcasts.

## Session Reordering

Sessions can be reordered via drag-and-drop in the client or keyboard shortcuts (Ctrl+Up/Down). The backend provides a move-based reorder endpoint:

```
PATCH /api/sessions/{session_id}/reorder
Body: {"after_session_id": "uuid" | null}
```

- `after_session_id = null` moves the session to the top of the list.
- `after_session_id = "uuid"` places it immediately after the specified session.

The server computes sparse integer `sort_order` values (gaps of 1000) and re-normalizes all sessions when gaps collapse. After a successful reorder, a lightweight `session_reorder` event is broadcast to all connected WebSocket clients:

```json
{
  "type": "session_reorder",
  "order": ["uuid1", "uuid2", "uuid3"]
}
```

When grouping by project is enabled, the client uses separate `ReorderableListView` widgets per project group, making cross-project drag structurally impossible. Reordering is disabled when search filters, status filters, or multi-select mode are active.

## Task Messages

Clients can request the full task list via:

```json
{
  "type": "list_tasks"
}
```

Server responds with:

```json
{
  "type": "task_list",
  "tasks": [
    {
      "task_id": "uuid",
      "title": "Implement login feature",
      "description": "Add OAuth2 login with Google provider",
      "status": "in_progress",
      "source": "ai",
      "created_at": "2025-01-15T10:30:00+00:00",
      "updated_at": "2025-01-15T11:00:00+00:00",
      "sessions": [
        {
          "session_id": "uuid",
          "title": "Session title",
          "status": "active",
          "attached_at": "2025-01-15T10:30:00+00:00"
        }
      ]
    }
  ]
}
```

The server broadcasts `task_update` messages when tasks are created or modified:

```json
{
  "type": "task_update",
  "task_id": "uuid",
  "title": "...",
  "status": "...",
  "sessions": [...]
}
```

And `task_deleted` when a task is removed:

```json
{
  "type": "task_deleted",
  "task_id": "uuid"
}
```

### Task Status Transitions

Valid transitions (enforced server-side):

| From         | Allowed To                |
|-------------|---------------------------|
| `todo`       | `in_progress`, `done`     |
| `in_progress`| `todo`, `review`, `done`  |
| `review`     | `in_progress`, `done`     |
| `done`       | `todo`, `in_progress`     |

AI agents (source: `"ai"`) are forbidden from setting status to `done`. Only users can mark tasks as complete.

### Automatic Task Status Behavior

- **AI-created tasks** start as `in_progress` (since they are created during an active session working on them).
- **Matched existing tasks** in `todo` or `review` status are auto-promoted to `in_progress` when attached to a new session.
- **On session end**, the LLM evaluates each attached task and may advance it to `review` if the work appears complete, or keep it at `in_progress` if more work is needed. The LLM cannot set `done`.
- **Task matching** considers all non-done tasks (`todo`, `in_progress`, `review`) to prevent duplicate creation.

### Task Description Format

Task descriptions generated by the LLM use **markdown formatting** for readability. Every task description includes:

1. A brief summary paragraph explaining what needs to be done.
2. A **Review Checklist** section with `- [ ]` items specifying what reviewers should focus on when the task enters `review` status. The checklist is task-specific (e.g., logic correctness, edge cases, tests, documentation).

When a task's status is updated (especially to `review`), the LLM refreshes the description to reflect the actual work performed and updates the review checklist accordingly.

## Artifact Messages

Artifacts are files discovered by parsing session conversation messages for file paths. When `ARTIFACT_AUTO_SCAN` is enabled, the scanner runs in real time after each assistant message and tool result during session execution, as well as after session archival. It extracts file paths from message content, verifies they exist on disk, and tracks files matching the configured include/exclude patterns.

Clients can request the full artifact list via:

```json
{
  "type": "list_artifacts"
}
```

Server responds with:

```json
{
  "type": "artifact_list",
  "artifacts": [
    {
      "artifact_id": "uuid",
      "file_path": "/home/user/Projects/repo/README.md",
      "file_name": "README.md",
      "file_extension": ".md",
      "file_size": 4096,
      "mime_type": "text/markdown",
      "discovered_at": "2025-01-15T10:30:00+00:00",
      "modified_at": "2025-01-15T11:00:00+00:00",
      "session_id": "uuid or null",
      "project_name": "repo or null"
    }
  ]
}
```

Each artifact includes a `project_name` field derived from its `file_path` relative to the configured `PROJECTS_DIR` directories. If the artifact's path falls under a subdirectory of any projects directory, the first path component is used as the project name. Artifacts outside any project directory have `project_name: null`.

The client groups artifacts by worker, then by project name. Artifacts with no project are shown under an "Other" category.

The server broadcasts `artifact_list` after each extraction that discovers new artifacts.

When an artifact is deleted via the HTTP API, the server broadcasts:

```json
{
  "type": "artifact_deleted",
  "artifact_id": "uuid"
}
```

### Artifact HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/artifacts` | List artifacts. Query params: `search`, `limit`, `offset` |
| GET | `/api/artifacts/search` | Search artifacts for autocomplete. Query param: `q` (substring filter). Returns max 10 results with `file_name`, `file_path`, `file_extension`, `file_size`, `mime_type`, `is_text` |
| GET | `/api/artifacts/{id}` | Get artifact metadata |
| GET | `/api/artifacts/{id}/content` | Get raw file content (text/plain) |
| DELETE | `/api/artifacts/{id}` | Delete artifact record (not the file) |
| GET | `/api/artifacts/settings` | Get extraction settings |
| PATCH | `/api/artifacts/settings` | Update extraction settings |

### Chat Attachments

Chat attachments are user-uploaded files sent alongside a prompt message (images, code files, PDFs, etc.). They are distinct from **Artifacts** (server-side files discovered during execution).

**Upload flow:**
1. Client calls `POST /api/uploads` with `multipart/form-data` containing the file.
2. Server stores the file bytes in `AttachmentStore` (in-memory, 10-minute TTL) and returns `{attachment_id, file_name, mime_type, size, is_image}`.
3. Client includes the returned `attachment_id`s in the WebSocket `prompt` message's `attachments` list.
4. Server resolves IDs from `AttachmentStore` (consuming each entry) and passes `ResolvedAttachment` objects to `PromptRouter.handle_prompt`.
5. Prompt router converts each attachment to an LLM content block:
   - **Images** (JPEG, PNG, GIF, WEBP): provider-specific image block (Anthropic `type: image / source.type: base64` or OpenAI `type: image_url`).
   - **Text files** (any `text/*` MIME, or known text extensions: `.py`, `.dart`, `.json`, `.md`, `.yaml`, etc.): `type: text` block with filename header and decoded UTF-8 content.
   - **Other binary**: `type: text` placeholder noting the filename and byte size.
6. Attachment blocks are prepended to the prompt text in the conversation history turn.
7. Server pushes the user's text to the buffer with `attachments: [{name, mime_type, size}]` metadata so the client can display file chips in the chat.

**Implementation:**
- `src/core/attachment_store.py` — `AttachmentStore` (store/get/pop with TTL eviction) and `ResolvedAttachment` dataclass.
- `src/api/routes/uploads.py` — `POST /api/uploads` endpoint.
- `src/core/prompt_router.py` — `_build_attachment_blocks()` helper; `handle_prompt(attachments=...)`.
- `src/api/ws/input_text.py` — resolves `attachments` list from `AttachmentStore` before dispatching to `handle_prompt`.
- **Client**: `WebSocketService.uploadAttachment()` HTTP POST helper; `sendPrompt(attachments: ...)` extended; `InputArea` shows paperclip button and pending-attachment chips above the text field.

**Constraints:** Max file size 20 MB per file. Attachments expire in 10 minutes if the prompt is never sent. Missing or expired IDs in the `attachments` list are silently skipped. No attachment data is stored in the database (only the text echo is logged).

### Artifact Scanner

The `ArtifactScanner` service extracts file paths from session messages and tracks matching files as artifacts. It uses a regex to find file paths in message content and metadata, then verifies each path exists on disk. Pattern matching is case-insensitive (e.g. `*.md` matches `README.MD`). The scanner:

- Scans the session's `conversation_history` JSON (complete messages, not fragmented streaming chunks) for file paths
- Also reads archived `SessionMessage` rows for tool outputs and metadata
- Extracts file paths using regex (absolute, `~/`, and relative `./`/`../` paths)
- Filters paths against include/exclude patterns
- Verifies files exist on disk before tracking them
- Enforces a configurable max file size (default 5 MB)
- Creates new artifact records or updates existing ones if file metadata changed
- Associates artifacts with the session they were extracted from
