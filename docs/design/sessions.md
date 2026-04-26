---
updated: 2026-04-26
---

# Session Management

Lifecycle, activity state, types, storage, queueing, and token tracking for sessions.

**See also:**
- [WebSocket API](websocket-api.md) — `session_update`, `subscribe`, `list_sessions`, `message_queued`
- [Permissions](permissions.md) — `awaiting_permission` activity state
- [Database](database.md) — `sessions`, `session_pending_messages`, `drafts` tables
- [Telemetry](telemetry.md) — per-turn token/latency capture

---

## Session Lifecycle

```
  ┌──────────┐    prompt    ┌────────────┐   tool done    ┌───────────┐
  │  CREATED  │────────────►│   ACTIVE    │───────────────►│ COMPLETED │
  └──────────┘              └──┬──▲──┬────┘                └─────┬─────┘
                               │  │  │                           │
                         tool  │  │  │ cancel                    │
                         call  │  │  │                           ▼
                               ▼  │  │                     ┌───────────┐
                         ┌─────────────┐                   │ ARCHIVED  │
                         │  EXECUTING  │──── cancel ───┐   │  (in DB)  │
                         └──┬──────────┘               │   └───────────┘
                            │ output               │
                  ┌─────────┘──────────────────┐   │
                  ▼                             ▼   ▼
           ┌────────────┐                 ┌────────────┐
           │   PAUSED   │                 │ CANCELLED  │
           └────────────┘                 └────────────┘
            pause ▲  │ resume
                  │  ▼
            ACTIVE / EXECUTING

           ┌─────────────┐   restore   ┌────────────┐
           │ INTERRUPTED │────────────►│   ACTIVE   │
           └─────────────┘             └────────────┘
             ▲
    backend restart
    (graceful or crash)

  All sessions remain ACTIVE until the user explicitly ends them
  (POST /api/sessions/{id}/end or end_session WebSocket message),
  the underlying process exits, the session is cancelled via
  POST /api/sessions/{session_id}/cancel, or the session is
  auto-ended after 6 hours of inactivity.

  Sessions can be PAUSED from ACTIVE or EXECUTING state via
  POST /api/sessions/{id}/pause. Pausing kills any running
  Claude Code subprocess and cancels its stream task. New prompts
  are rejected. PAUSED sessions are exempt from inactivity reaping.
  Resume via POST /api/sessions/{id}/resume.

  A running subprocess can be killed WITHOUT pausing the session via
  POST /api/sessions/{id}/interrupt. Unlike pause, the session
  remains ACTIVE and immediately accepts new prompts. The subprocess
  executor and stream task are cancelled, AGENT_GROUP_END is pushed,
  and a null subprocess_status ephemeral message is broadcast so the
  client clears its subprocess indicator.

  ARCHIVED sessions (COMPLETED/FAILED/CANCELLED/INTERRUPTED that have
  been written to the database) can be RESTORED back to ACTIVE via
  POST /api/sessions/{id}/restore or the restore_session WebSocket
  message. Restoring loads conversation history and buffer from the
  DB, removes the DB row, and re-creates the in-memory session.
  For Claude Code sessions, the executor is prepared for lazy
  restart using the stored --session-id.
```

## Activity State

Separate from the lifecycle `SessionStatus`, each active session tracks a fine-grained `ActivityState` that answers "what is the session doing right now?":

| State                  | Meaning                                      |
|------------------------|----------------------------------------------|
| `idle`                 | Waiting for user input                       |
| `processing_llm`       | LLM is generating a response / agentic loop  |
| `executing_tool`       | A shell/HTTP tool is running                 |
| `running_subprocess`   | Claude Code subprocess is actively processing|
| `awaiting_permission`  | Blocked waiting for user to approve/deny a tool use |

Activity state is transient (in-memory only — not stored in the database). Archived sessions are always `idle`. The state is included in:
- `session_update` WebSocket messages (`activity_state` field)
- `GET /api/sessions` and `list_sessions` responses
- The Flutter client's `SessionInfo.activityState` field

Activity state transitions happen in `PromptRouter` alongside the existing `SessionStatus` transitions. Terminal status transitions (`complete()`, `fail()`, `cancel()`, `pause()`) always reset activity to `idle`.

## Session Types

| Type          | LLM Context    | Example                | Behavior                              |
|---------------|----------------|------------------------|---------------------------------------|
| One-shot      | Stateless      | `ls`, `cat file.txt`   | Runs tool, returns result, ends       |
| Conversational| Session-scoped | All prompt sessions    | Default type. Stays active until user ends. LLM includes `[SessionEndAsk]` when done; client shows confirmation. |
| Long-running  | Session-scoped | `python -i`, Claude Code, Codex| Session persists while process runs   |

All new sessions created by the prompt router use the **conversational** type by default. Sessions stay active until the user explicitly ends them (via the end-session confirmation or `POST /api/sessions/{id}/end`). The LLM includes a `[SessionEndAsk]` tag at the end of its response when it believes the task is complete; the client strips this tag and shows an inline confirmation card.

The tool JSON definition specifies which type a tool uses (see [Tool Definitions](tools.md)).

## Pre-Planning Sessions

Pre-planning sessions are **read-only ONE_SHOT sessions** that generate a Markdown plan for a task before implementation begins. They are triggered by:

- The `start_plan_session` WebSocket message
- `POST /api/tasks/{task_id}/plan` HTTP endpoint

**Setup** (`prepare_plan_session()`):

1. A `ONE_SHOT` session is created and associated with the task in the DB.
2. `session.metadata["session_purpose"] = "plan"` and `session.metadata["task_id"] = task_id` are set so the finalization logic can identify it.
3. The plan output path is `<project_root>/.rcflow/plans/<task_id>.md`.
4. Restrictive permission rules are pre-seeded on the `PermissionManager`:
   - Deny `Bash`, `Edit`, `Agent`, `Write` for the entire session.
   - Allow `Write` for the plan directory only (overrides the deny).
5. The planning prompt is injected via `handle_prompt()` as a background task.

**Finalization** (`_finalize_plan_session()`):

When the session ends (complete, cancel, or fail), a background task runs:
1. Reads the plan file; logs a warning and exits if not found.
2. Upserts an `Artifact` record (handles race condition with `ArtifactScanner` via UniqueConstraint + retry).
3. Sets `task.plan_artifact_id = artifact.id` and updates `task.updated_at`.
4. Broadcasts a `task_update` WebSocket message so clients refresh the task.

**Context injection**:

For implementation sessions (non-plan sessions) with `session.metadata["primary_task_id"]` set, `_build_plan_context()` reads the task's `plan_artifact_id`, loads the plan artifact content, and prepends it as a `<plan_context>` block in the LLM context. Content is truncated to 8 000 characters.

**Task data**:

Tasks carry a `plan_artifact_id` field (UUID string or `null`) in all API responses (`GET /api/tasks`, `GET /api/tasks/{id}`, `list_tasks` WebSocket output, and `task_update` broadcasts). Client renders a plan badge on the task tile and a `_PlanBanner` in the task detail pane.

## Storage

- **Active sessions**: Held in memory with full output buffer.
- **Paused sessions**: Remain in memory like active sessions. Any running Claude Code or Codex subprocess is killed on pause. Not reaped by inactivity timer. Archived only after being resumed and reaching a terminal state.
- **Completed sessions**: Automatically archived to the database when a session reaches a terminal state (completed, failed, or cancelled). The prompt router fires a background task after each `session.complete()`, `session.fail()`, or `session.cancel()` call. Stores: session ID, timestamps, all prompts, all LLM responses, tool calls, tool outputs, metadata, `conversation_history` (the raw LLM message list for restoration), and token usage totals (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `tool_input_tokens`, `tool_output_tokens`, `tool_cost_usd`).
- **Restored sessions**: Archived sessions can be restored back to active state via `POST /api/sessions/{id}/restore` or the `restore_session` WebSocket message. The session's conversation history, buffer messages, and metadata are loaded from the DB. For Claude Code sessions, the CC `session_id`, `working_directory`, tool name, and parameters are stored in `metadata_` during archiving and used to reconstruct the executor on restore. The first message sent to a restored CC session triggers a `restart_with_prompt` using the stored `--session-id`, allowing Claude Code to resume its internal conversation context.
- **Interrupted sessions**: On an unexpected backend crash or SIGKILL, in-flight sessions that were still active in the DB are marked `INTERRUPTED` at next startup rather than `FAILED`. `INTERRUPTED` is a non-terminal status: `ended_at` is left unset, the session DB row retains its conversation history and metadata (including `selected_worktree_path`), and the client can restore the session via the normal restore flow. The `metadata_["restart_interrupted"]` flag is set so clients can show a visual indicator. Tasks attached to interrupted sessions remain in their current state until the session is resumed and ends normally.
- **On graceful shutdown**: Active sessions are marked `COMPLETED` as before (state is preserved cleanly).
- **On server crash/restart**: Sessions that were active at crash time are marked `INTERRUPTED` (not `FAILED`). Clients can restore them. Archived sessions remain queryable via `GET /api/sessions` and `GET /api/sessions/{session_id}/messages`.
- **Session listing**: `GET /api/sessions` and the WebSocket `list_sessions` command both merge in-memory sessions with archived sessions from the database (excluding duplicates), sorted by `sort_order` ascending (nulls last) then `created_at` descending. Each session entry includes a `created_at` ISO 8601 timestamp, `title`, and `sort_order`. Archived sessions are filtered by `backend_id` so each backend instance only sees its own sessions.

## Session Todos

Each active session tracks an in-memory list of todo items, updated whenever Claude Code calls the `TodoWrite` tool. The todo list is the complete state (replaced wholesale on each update, not incremental). Todo state is:
- Broadcast to subscribed clients via `todo_update` WebSocket messages.
- Queryable via `GET /api/sessions/{session_id}/todos` (returns `{"session_id": "...", "todos": [...]}` or 404 for unknown sessions).
- Included in buffer history, so clients reconstruct todo state on session subscribe replay and archived session history loading.
- Not persisted to a separate database table — stored only in-memory and as buffer messages.

## Session Titles

Sessions receive auto-generated human-readable titles (max 6 words) derived from the first user prompt and LLM response. After the agentic loop completes for the first turn of a session, a background task sends the user prompt and assistant response to the title model (`TITLE_MODEL`, falls back to main model) to generate a short title. The title is stored in the `title` column of the `sessions` table and included in all session list responses (HTTP and WebSocket). Title generation failures are logged but never break the session. The `title` field is `null` until generated. Users can also manually rename sessions at any time via `PATCH /api/sessions/{session_id}/title`. Setting the title to `null` clears it.

## Concurrency

Multiple sessions can run simultaneously. Each session is independent with its own:
- LLM conversation context (if session-scoped)
- Tool execution subprocess(es)
- Output buffer
- Subscriber list

## Queued User Messages

When a user sends a prompt while the session is busy processing a prior turn, the prompt is enqueued server-side rather than delivered to the agent. The client renders queued messages pinned at the bottom of the chat with a "queued" indicator until the agent actually picks them up, at which point they move into the normal chat history.

**Busy detection** — `ActiveSession.is_busy_for_queue()` returns true when any of the following holds:

- A managed agent executor is attached (`claude_code_executor`, `codex_executor`, or `opencode_executor`) **and** its stream task exists and is not done.
- The per-session `_prompt_lock` is held (LLM path).
- `activity_state` is one of `processing_llm`, `executing_tool`, `running_subprocess`, `awaiting_permission`.

If true, `PromptRouter.handle_prompt()` short-circuits into the enqueue path instead of the routing branches.

**State model** — Each queued message has:

- `queued_id` — UUID, client-visible.
- `position` — dense integer, FIFO order within the session, renumbered on drain/cancel.
- `content` / `display_content` — routing text (may contain `#mentions`) and chat-visible text.
- `attachments_path` — absolute path to a disk directory holding attachment bytes + `meta.json` (all attachments disk-spilled regardless of size).
- `project_name`, `selected_worktree_path`, `task_id` — captured at enqueue time so drain can apply the same context the user intended.
- `submitted_at` / `updated_at` — timestamps.

State is stored in the `session_pending_messages` DB table (see [Database Schema](database.md)) and mirrored in memory on `ActiveSession.pending_user_messages`. All writes go through the DB first, then update the in-memory mirror and broadcast the corresponding WS event.

**Lifecycle**:

1. **Enqueue** — `PromptRouter.handle_prompt()` detects busy, writes a row (+ attachment dir), appends to the in-memory mirror, and emits `message_queued`. The original `ack` to the client carries `queued: true, queued_id`.
2. **Edit** — `edit_queued` input updates the row's `content` / `display_content` / `updated_at`; emits `message_queued_updated`. Only valid while the row exists; after delivery the message is immutable.
3. **Cancel** — `cancel_queued` input deletes the row + attachment dir, renumbers remaining positions, emits `message_dequeued` with `reason: "cancelled"`.
4. **Drain** — At the turn-end boundary in each agent path (Claude Code `result` event, Codex / OpenCode turn end, LLM `_prompt_lock` release), `_drain_pending(session)` pops the head of the queue one entry at a time: delete row + attachments, emit `message_dequeued{reason: "delivered"}`, push `text_chunk(role=user, queued_id=...)` to the buffer, then forward to the executor or append to `conversation_history`.
5. **Clear-on-end** — When a session ends / is cancelled, `clear_pending(reason="session_ended")` deletes all rows and emits `message_dequeued` for each.

**Persistence across backend restart** — Queue rows survive process crashes. When the backend restarts, sessions that were active are marked `INTERRUPTED`. `session_update` broadcasts include `queued_messages` populated from the DB, so clients restore the queued bar immediately on resubscribe. Drain does not run automatically on resume; it only runs at the next turn-end boundary after the user resumes the session.

**Attachment disk spill** — Queued attachments are written to `data/pending_attachments/<session_id>/<queued_id>/` with a `meta.json` manifest. Filenames are sanitized. Directories are removed atomically on drain/cancel/session-delete. A startup task sweeps orphaned directories (dirs with no matching DB row).

**Race handling** — All queue mutations are serialized by `ActiveSession._prompt_lock`. `cancel_queued` / `edit_queued` arriving after drain has removed the row respond with `ok: false, reason: "already_delivered"`. Clients handle this gracefully (cancel → treat as normal delivery, edit → revert + toast).

## Token Usage Tracking

Each session accumulates token usage counters in real time:

- **`input_tokens` / `output_tokens`**: Tokens consumed by the global LLM pipeline (Anthropic, Bedrock, or OpenAI) during the agentic loop. Updated after each `StreamDone`.
- **`cache_creation_input_tokens` / `cache_read_input_tokens`**: Anthropic prompt-caching breakdown (zero for non-Anthropic providers).
- **`tool_input_tokens` / `tool_output_tokens`**: Tokens consumed by agent tool subprocesses (Claude Code via `result`/`system` events, Codex via `turn.completed` events).
- **`tool_cost_usd`**: USD cost reported by Claude Code's `result` event.

Token counters are broadcast to clients via `session_update` messages whenever they change, persisted to the database on session archive/shutdown, and restored when a session is loaded from the database.

**Token limits**: When `SESSION_INPUT_TOKEN_LIMIT` or `SESSION_OUTPUT_TOKEN_LIMIT` is set to a non-zero value, the prompt router checks the session's total tokens (LLM + tool) before processing each new user message. If either limit is exceeded, the session receives an error message and the prompt is rejected. The check compares `input_tokens + tool_input_tokens` against the input limit and `output_tokens + tool_output_tokens` against the output limit.
