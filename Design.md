# RCFlow — Design Document

## Overview

RCFlow is a background server running on Linux or Windows that provides a WebSocket-based interface for executing actions on the host machine via natural language prompts. Users connect from client applications (Android and Windows desktop), send text or voice prompts, and the server uses an LLM (Anthropic Messages API) to interpret those prompts into tool calls. Tools are pluggable and defined via JSON files. Results — both text and audio — stream back to the client in real time.

## Technology Stack

| Component            | Technology                    |
|----------------------|-------------------------------|
| Language             | Python 3.12+                  |
| Package Manager      | uv                            |
| Web Framework        | FastAPI                       |
| ORM                  | SQLAlchemy 2.0 (async)        |
| Database             | PostgreSQL                    |
| LLM                  | Anthropic Messages API or AWS Bedrock |
| STT                  | Pluggable (Wispr Flow default)|
| TTS                  | Pluggable (provider TBD)      |
| Audio Format         | Opus/OGG                      |
| Prompt Templates     | POML (poml Python SDK)        |
| Linting / Formatting | Ruff                          |
| Type Checking        | ty                            |
| Testing              | pytest                        |
| Config               | Environment variables + .env  |
| OS                   | Linux, Windows                |
| Client Platforms     | Android, Windows (desktop)    |
| Android Keep-Alive   | flutter_foreground_task       |
| Audio Playback       | audioplayers                  |
| File Picker          | file_picker (Windows custom sounds) |

---

## Architecture

### High-Level Flow

```
┌─────────────────┐
│  Mobile Client   │
│  (or any client) │
└────┬───┬───┬───┬┘
     │   │   │   │
     │   │   │   └──────────────────────────────────┐
     │   │   └───────────────────────┐              │
     │   └──────────────┐            │              │
     ▼                  ▼            ▼              ▼
┌──────────┐   ┌──────────┐  ┌───────────┐  ┌───────────┐
│/ws/input │   │/ws/input │  │/ws/output │  │/ws/output │
│  /text   │   │  /audio  │  │  /text    │  │  /audio   │
└────┬─────┘   └────┬─────┘  └─────▲─────┘  └─────▲─────┘
     │              │              │              │
     │              ▼              │              │
     │      ┌──────────────┐      │              │
     │      │  Wispr Flow  │      │              │
     │      │  STT Service │      │              │
     │      └──────┬───────┘      │              │
     │             │               │              │
     ▼             ▼               │              │
┌─────────────────────────┐       │              │
│     Prompt Router       │       │              │
│  (text from either      │       │              │
│   input channel)        │       │              │
└────────────┬────────────┘       │              │
             ▼                    │              │
┌─────────────────────────┐       │              │
│   LLM Provider          │       │              │
│   (Anthropic API or     │       │              │
│    AWS Bedrock)         │       │              │
│   + Tool Definitions    │       │              │
└────────────┬────────────┘       │              │
             │                    │              │
             ▼                    │              │
┌─────────────────────────┐       │              │
│   Tool Executor         │       │              │
│  ┌───────────────────┐  │       │              │
│  │ Shell Executor    │  │       │              │
│  │ HTTP API Executor │  │       │              │
│  │ Claude Code Exec. │  │       │              │
│  └───────────────────┘  │       │              │
└────────────┬────────────┘       │              │
             │                    │              │
             ▼                    │              │
┌─────────────────────────┐       │              │
│   Session Manager       │───────┘              │
│   (buffer, history,     │                      │
│    subscribe/unsub)     │                      │
└────────────┬────────────┘                      │
             │                                   │
             ▼                                   │
┌─────────────────────────┐                      │
│   TTS Service           │──────────────────────┘
│   (pluggable provider)  │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   PostgreSQL             │
│   (session archive)      │
└──────────────────────────┘
```

### Request Lifecycle

```
1. Client connects to /ws/input/text or /ws/input/audio
2. If audio → RCFlow forwards to Wispr Flow STT → receives transcribed text
3. Text prompt is routed to the Prompt Router
4. Prompt Router creates or resumes a Session
5. Prompt + tool definitions + session context sent to LLM provider (Anthropic API or AWS Bedrock, streaming)
6. LLM responds with text and/or tool_use blocks
7. For each tool_use → Tool Executor runs the tool (shell command or HTTP API call)
8. Tool output fed back to LLM for further reasoning (agentic loop)
9. All LLM text output streams to /ws/output/text chunk-by-chunk
10. All LLM text output also sent to TTS → audio streams to /ws/output/audio
11. When the LLM finishes (no more tool calls), the session remains active.
    If the LLM included [SessionEndAsk], the server pushes a session_end_ask
    message; the client shows a confirmation card. The session only ends
    when the user explicitly confirms or sends POST /api/sessions/{id}/end.
12. Completed sessions are archived from memory to PostgreSQL
```

### Flutter Client — Multi-Platform

The Flutter client (`rcflowclient/`) runs on Android and Windows desktop from a single codebase. Platform-conditional behavior:

- **Foreground service** (`flutter_foreground_task`): Android/iOS only. On desktop, `ForegroundServiceHelper.init()`, `.start()`, and `.stop()` are no-ops guarded by `Platform.isAndroid || Platform.isIOS`.
- **Keyboard input**: On desktop, Enter sends a message and Shift+Enter inserts a newline. On mobile, `TextInputAction.send` + `onSubmitted` is used (standard mobile keyboard behavior).
- **Responsive layout**: At `>700px` width, a persistent 280px session sidebar appears on the left. At narrower widths (mobile), sessions are shown via a modal bottom sheet.
- **Settings**: Multi-section settings menu (`lib/ui/widgets/settings_menu.dart`). On desktop, shown as a two-column dialog (160px sidebar nav + content) via the sidebar's bottom "Settings" button. On mobile, shown as a `DraggableScrollableSheet` bottom sheet with all sections in a scrollable list. Sections: Workers (summary of connected/total count, "Manage Workers" button to open Workers screen), Appearance (theme mode, font size, compact mode), Notifications (sound toggle, sound selection, vibrate — vibrate hidden on desktop), About (version info). Settings persisted via `SharedPreferences` through `SettingsService`.
- **Notification sounds** (`lib/services/notification_sound_service.dart`): Two independent sound toggles: (1) "Sound when done" (`soundOnCompleteEnabled`, default `true`) — plays when work finishes and the session is waiting for user input (`summary`, `session_end_ask`, `plan_mode_ask`, `plan_review_ask`); (2) "Sound on message" (`soundEnabled`, default `false`) — plays on general message events including errors. Five built-in WAV sounds in `assets/sounds/` (gentle_chime, soft_ping, subtle_pop, bell, digital_tone). On Windows, users can also select a custom `.wav` file (validated to be < 10 seconds) via `file_picker`. Sound selection UI appears in the Notifications settings section when either sound toggle is enabled. Additional settings: `notificationSound` (sound ID string, default `gentle_chime`), `customSoundPath` (file path for custom sound on Windows). Audio playback via `audioplayers` package.

### Split View (Desktop)

On wide layouts (>700px), the main content area supports multiple simultaneous session panes arranged in a recursive binary split tree.

**Architecture**:
- **SplitNode** (`lib/models/split_tree.dart`): Sealed class — `PaneLeaf` (single pane) or `SplitBranch` (two children with axis + ratio). Pure functions for split/close/query operations.
- **PaneState** (`lib/state/pane_state.dart`): Per-pane `ChangeNotifier` extracted from AppState. Manages session ID, messages, streaming queue, pagination, and session lifecycle for a single pane. References shared state via the `PaneHost` interface.
- **AppState** (`lib/state/app_state.dart`): Keeps shared state (workers, merged session list) plus a `Map<String, PaneState>` and the `SplitNode` tree root. Manages `Map<String, WorkerConnection>` for multi-server connections. Routes incoming WebSocket messages to pane(s) by `session_id`. Manages split/close operations, active pane tracking, and worker CRUD.

**Message routing**: Output handlers receive `(msg, PaneState)` instead of `(msg, AppState)`. AppState extracts `session_id` from incoming messages and dispatches to matching pane(s). `session_list` is handled at AppState level. Ack routing uses a `pendingAck` flag on PaneState.

**UI widgets**:
- `SplitView` — recursively renders the split tree; leaves become `SessionPane` widgets, branches become `Row`/`Column` with `ResizableDivider`.
- `SessionPane` — wraps `OutputDisplay` + `InputArea` with a `PaneHeader` (shown only in multi-pane mode). Tap to set as active pane.
- `ResizableDivider` — draggable 6px divider with hover/drag highlight and cursor change.
- `PaneHeader` — 32px bar with session title and close button.

**Edge cases**: Last pane close resets to home (tree always has >= 1 leaf). Same session in multiple panes receives messages independently. Reconnection re-subscribes all pane sessions. Mobile layout remains single-pane, using `activePane` with a `ChangeNotifierProvider`.

### Workers (Multi-Server)

The client can connect to multiple RCFlow servers simultaneously. Each server connection is a "Worker". Each backend instance is identified by a unique `RCFLOW_BACKEND_ID` (auto-generated UUID, persisted to `.env`). When multiple backends share the same PostgreSQL database, sessions are isolated per backend via the `backend_id` column on the `sessions` table — each backend only sees and manages its own sessions.

**Data model**:
- `WorkerConfig` (`lib/models/worker_config.dart`): Client-side configuration with `id` (UUID, generated locally), `name`, `host`, `apiKey`, `useSSL`, `autoConnect`, and `sortOrder`. Serialized to/from JSON. ID generated using `dart:math` Random.secure.
- `SessionInfo.workerId`: Every session is tagged with the worker it belongs to. Set by the client when parsing server responses.

**Persistence** (`SettingsService`):
- `rcflow_workers`: JSON array of `WorkerConfig` objects.
- `rcflow_last_session_per_worker`: JSON map `{workerId: sessionId}`.
- `rcflow_cached_sessions_per_worker`: JSON map `{workerId: jsonEncodedSessionList}`.
- Legacy single-server keys (`rcflow_host`, `rcflow_api_key`, `rcflow_use_ssl`) kept for migration.

**WorkerConnection** (`lib/services/worker_connection.dart`): Wraps one `WebSocketService` instance with per-worker lifecycle. Enum `WorkerConnectionStatus`: `disconnected`, `connecting`, `connected`, `reconnecting`. Manages its own session list (tagged with `workerId`), reconnection loop (3 retries, 10s delay), and session subscriptions. Routes `session_list` and `session_update` messages internally; forwards all other messages to AppState via callbacks.

**AppState refactor**:
- Replaced single `WebSocketService _ws` with `Map<String, WorkerConnection> _workers` keyed by `config.id`.
- Connection state is aggregated: `connected` = any worker connected, `allConnected` = all auto-connect workers connected.
- Session list merges all workers' sessions sorted by `createdAt` desc. `sessionsByWorker` provides grouped access.
- Worker CRUD: `addWorker()`, `updateWorker()`, `removeWorker()`, `connectWorker()`, `disconnectWorker()`.
- `PaneHost` interface: replaced `WebSocketService get ws` with `wsForWorker(String workerId)` and `workerIdForSession(String sessionId)`.
- Foreground service starts when first worker connects, stops when last disconnects.

**PaneState routing**:
- Each pane tracks `_workerId` (set on `switchSession()`, `handleAck()`, or `setTargetWorker()`).
- All WS/REST calls (sendPrompt, cancelSession, endSession, etc.) route through `_ws` getter which resolves to `_host.wsForWorker(_workerId ?? defaultWorkerId)`.
- New chats: `setTargetWorker()` called from the worker selector chip in the input area.

**Migration** (`main.dart`): On first launch after upgrade, if `workers` list is empty and legacy `apiKey` is non-empty, creates a single worker from the legacy settings with `autoConnect: true`.

**Workers screen** (`lib/ui/screens/workers_screen.dart`): Desktop dialog / mobile sheet for worker CRUD. Shows each worker as a card with name, host, status dot, auto-connect badge, and Edit/Remove/Connect buttons. Add/Edit sub-dialog with name, host, API key (obscured), SSL toggle, and auto-connect toggle.

**Session tree view** (`lib/ui/widgets/session_panel.dart`): Sessions grouped by worker in expandable sections. Each group has a header with worker name, session count, and colored status dot. Disconnected workers show cached sessions dimmed. Bottom bar has "Workers" and "Settings" links.

**Input area worker selector**: When starting a new chat with multiple connected workers, a chip above the input field shows the target worker name with a dropdown to switch.

---

## HTTP API

| Method | Endpoint                                | Auth | Description                                      |
|--------|-----------------------------------------|------|--------------------------------------------------|
| GET    | `/api/health`                           | No   | Health check — returns `{"status": "ok"}`        |
| GET    | `/api/info`                             | Yes  | Server metadata — returns `{"os", "os_version", "architecture", "hostname"}` |
| GET    | `/api/sessions`                         | Yes  | List all sessions (in-memory + archived) sorted by `created_at` descending. Includes `title`. |
| GET    | `/api/sessions/{session_id}/messages`   | Yes  | Get message history for a session (in-memory buffer or archived DB messages). Supports cursor-based pagination via `?limit=N` and `?before=SEQ` query params. Response includes `pagination: {total_count, has_more, next_cursor}`. When `limit` is omitted, returns all messages (backward compatible). |
| GET    | `/api/tools`                            | Yes  | List registered tool definitions                 |
| GET    | `/api/projects`                         | Yes  | List directory names under `PROJECTS_DIR`. Optional `?q=` for case-insensitive substring filter. Returns `{"projects": [...]}`. |
| POST   | `/api/sessions/{session_id}/cancel`     | Yes  | Cancel a running session (kills subprocess)      |
| POST   | `/api/sessions/{session_id}/end`        | Yes  | Gracefully end a session (user-confirmed completion) |
| POST   | `/api/sessions/{session_id}/pause`      | Yes  | Pause an active session. Kills any running Claude Code subprocess. New prompts rejected until resumed. |
| POST   | `/api/sessions/{session_id}/resume`     | Yes  | Resume a paused session. Client can subscribe to receive all buffered output. |
| POST   | `/api/sessions/{session_id}/restore`    | Yes  | Restore an archived (completed/failed/cancelled) session back to active state. Rebuilds conversation history, buffer, and Claude Code executor state. |
| PATCH  | `/api/sessions/{session_id}/title`      | Yes  | Set or clear a session title (max 200 chars). Body: `{"title": "..."}` or `{"title": null}`. |
| GET    | `/api/config`                           | Yes  | Get server configuration schema with current values. Secret values are masked. Options grouped by section. |
| PATCH  | `/api/config`                           | Yes  | Update server configuration. Body: `{"updates": {"KEY": "value", ...}}`. Persists to `.env`, reloads settings, and hot-reloads LLM/STT/TTS components. Returns updated schema. |
| GET    | `/api/tools/status`                     | Yes  | Get installation status, versions, and update availability for managed CLI tools (Claude Code, Codex). |
| POST   | `/api/tools/update`                     | Yes  | Check for and install updates to RCFlow-managed CLI tools. Only updates tools managed by RCFlow (not user-installed ones). |
| GET    | `/api/tools/{tool_name}/settings`       | Yes  | Get per-tool settings schema and current values for a managed CLI tool. |
| PATCH  | `/api/tools/{tool_name}/settings`       | Yes  | Update per-tool settings. Body: `{"updates": {"key": value, ...}}`. Returns updated schema+values. |

Authentication for HTTP endpoints uses the `X-API-Key` header with the same key as `RCFLOW_API_KEY`.

---

## WebSocket API

### Endpoints

| Endpoint            | Direction       | Format         | Purpose                                      |
|---------------------|-----------------|----------------|----------------------------------------------|
| `/ws/input/text`    | Client → Server | JSON           | User sends natural language prompts           |
| `/ws/input/audio`   | Client → Server | Binary (PCM)   | User sends voice audio for STT               |
| `/ws/output/text`   | Server → Client | JSON           | Streaming text responses chunk-by-chunk       |
| `/ws/output/audio`  | Server → Client | Binary (Opus)  | Streaming TTS audio responses                |

### Authentication

All WebSocket connections require an API key. Passed as a query parameter:

```
wss://host:port/ws/input/text?api_key=<KEY>
```

### Input Text Protocol

Client sends JSON messages:

```json
{
  "type": "prompt",
  "text": "list all files in the current directory",
  "session_id": null
}
```

- `session_id`: `null` to create a new session, or an existing session ID to send a follow-up prompt to a conversational or long-running session.

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

### Input Audio Protocol

Client sends audio following the Wispr Flow format:

1. Initial auth/config message (JSON)
2. Sequential audio chunks (base64-encoded 16-bit PCM, 16kHz, mono)
3. Commit message to signal end of utterance

The server handles the full Wispr Flow protocol internally.

### Output Text Protocol

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
  "sequence": 43
}
```

```json
{
  "type": "error",
  "session_id": "uuid",
  "content": "Permission denied",
  "code": "TOOL_EXEC_ERROR"
}
```

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
  "content": "A short TTS-friendly summary of the Claude Code result."
}
```

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
  "type": "session_paused",
  "session_id": "uuid",
  "paused_at": "2025-01-15T10:30:00+00:00"
}
```

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

```json
{
  "type": "plan_review_ask",
  "session_id": "uuid"
}
```

```json
{
  "type": "session_update",
  "session_id": "uuid",
  "status": "active",
  "activity_state": "processing_llm",
  "title": "Some title",
  "session_type": "conversational",
  "created_at": "2025-01-15T10:30:00+00:00"
}
```

### Output Audio Protocol

Server sends binary Opus/OGG frames. Each frame is prefixed with a small binary header:

```
[session_id: 16 bytes UUID][sequence: 4 bytes uint32][opus frame data]
```

This allows the client to demultiplex audio from multiple sessions if subscribed to more than one.

### Session Subscription

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

**Session metadata updates** (title, status, and activity state changes) are automatically streamed to all connected `/ws/output/text` clients without explicit subscription. When any session's title, status, or activity state changes, a `session_update` message is broadcast to all output clients. This enables real-time updates of the session list in the client UI without polling.

### Session List

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
      "title": "List files in directory"
    }
  ]
}
```

Sessions are sorted by `created_at` descending (most recent first). The list includes both in-memory active sessions and archived sessions from the database. The `title` field is `null` until auto-generated after the first LLM response.

---

## Timing Synchronization (Text ↔ Audio)

### The Problem

Text and audio streams are produced at different rates. Text chunks arrive as the LLM generates tokens (fast), while audio chunks arrive after TTS processing (slower, with latency). A client displaying text and playing audio simultaneously will see them drift out of sync.

### Solutions

1. **Sequence-based correlation**: Both text and audio messages carry `sequence` numbers. The client can use these to correlate which audio corresponds to which text chunk and pace display accordingly.

2. **Timestamp-based sync**: Each output message includes a `timestamp_ms` field (milliseconds since session start). The client uses this as a shared timeline.

3. **Text pacing**: Instead of displaying text instantly, the client buffers text and reveals it in sync with audio playback progress. The audio stream drives the display rate.

4. **Independent streams (simplest)**: Accept that text and audio are not perfectly synced. Text appears first, audio follows. Many voice assistant UIs work this way.

**Recommended approach for v1**: Start with option 4 (independent streams) with sequence numbers for loose correlation. Move to timestamp-based sync in a later version if needed.

---

## Session Management

### Session Lifecycle

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

  ARCHIVED sessions (COMPLETED/FAILED/CANCELLED that have been
  written to the database) can be RESTORED back to ACTIVE via
  POST /api/sessions/{id}/restore or the restore_session WebSocket
  message. Restoring loads conversation history and buffer from the
  DB, removes the DB row, and re-creates the in-memory session.
  For Claude Code sessions, the executor is prepared for lazy
  restart using the stored --session-id.
```

### Activity State

Separate from the lifecycle `SessionStatus`, each active session tracks a fine-grained `ActivityState` that answers "what is the session doing right now?":

| State                  | Meaning                                      |
|------------------------|----------------------------------------------|
| `idle`                 | Waiting for user input                       |
| `processing_llm`       | LLM is generating a response / agentic loop  |
| `executing_tool`       | A shell/HTTP tool is running                 |
| `running_subprocess`   | Claude Code subprocess is actively processing|

Activity state is transient (in-memory only — not stored in the database). Archived sessions are always `idle`. The state is included in:
- `session_update` WebSocket messages (`activity_state` field)
- `GET /api/sessions` and `list_sessions` responses
- The Flutter client's `SessionInfo.activityState` field

Activity state transitions happen in `PromptRouter` alongside the existing `SessionStatus` transitions. Terminal status transitions (`complete()`, `fail()`, `cancel()`, `pause()`) always reset activity to `idle`.

### Session Types

| Type          | LLM Context    | Example                | Behavior                              |
|---------------|----------------|------------------------|---------------------------------------|
| One-shot      | Stateless      | `ls`, `cat file.txt`   | Runs tool, returns result, ends       |
| Conversational| Session-scoped | All prompt sessions    | Default type. Stays active until user ends. LLM includes `[SessionEndAsk]` when done; client shows confirmation. |
| Long-running  | Session-scoped | `python -i`, Claude Code, Codex| Session persists while process runs   |

All new sessions created by the prompt router use the **conversational** type by default. Sessions stay active until the user explicitly ends them (via the end-session confirmation or `POST /api/sessions/{id}/end`). The LLM includes a `[SessionEndAsk]` tag at the end of its response when it believes the task is complete; the client strips this tag and shows an inline confirmation card.

The tool JSON definition specifies which type a tool uses (see Tool Definitions below).

### Storage

- **Active sessions**: Held in memory with full output buffer (text + audio references).
- **Paused sessions**: Remain in memory like active sessions. Any running Claude Code or Codex subprocess is killed on pause. Not reaped by inactivity timer. Archived only after being resumed and reaching a terminal state.
- **Completed sessions**: Automatically archived to PostgreSQL when a session reaches a terminal state (completed, failed, or cancelled). The prompt router fires a background task after each `session.complete()`, `session.fail()`, or `session.cancel()` call. Stores: session ID, timestamps, all prompts, all LLM responses, tool calls, tool outputs, metadata, and `conversation_history` (the raw LLM message list for restoration).
- **Restored sessions**: Archived sessions can be restored back to active state via `POST /api/sessions/{id}/restore` or the `restore_session` WebSocket message. The session's conversation history, buffer messages, and metadata are loaded from the DB. For Claude Code sessions, the CC `session_id`, `working_directory`, tool name, and parameters are stored in `metadata_` during archiving and used to reconstruct the executor on restore. The first message sent to a restored CC session triggers a `restart_with_prompt` using the stored `--session-id`, allowing Claude Code to resume its internal conversation context.
- **On server restart**: Active sessions are lost. Archived sessions remain queryable via `GET /api/sessions` and `GET /api/sessions/{session_id}/messages`, and can be restored.
- **Session listing**: `GET /api/sessions` and the WebSocket `list_sessions` command both merge in-memory sessions with archived sessions from PostgreSQL (excluding duplicates), sorted by `created_at` descending. Each session entry includes a `created_at` ISO 8601 timestamp and `title`. Archived sessions are filtered by `backend_id` so each backend instance only sees its own sessions.

### Session Titles

Sessions receive auto-generated human-readable titles (max 6 words) derived from the first user prompt and LLM response. After the agentic loop completes for the first turn of a session, a background task sends the user prompt and assistant response to the summary model (`SUMMARY_MODEL`) to generate a short title. The title is stored in the `title` column of the `sessions` table and included in all session list responses (HTTP and WebSocket). Title generation failures are logged but never break the session. The `title` field is `null` until generated. Users can also manually rename sessions at any time via `PATCH /api/sessions/{session_id}/title`. Setting the title to `null` clears it.

### Concurrency

Multiple sessions can run simultaneously. Each session is independent with its own:
- LLM conversation context (if session-scoped)
- Tool execution subprocess(es)
- Output buffer
- Subscriber list

---

## System Prompt Templates

The system prompt sent to the LLM is defined in a POML (Prompt Orchestration Markup Language) template file rather than inline Python strings. This separates prompt content from code, provides semantic structure via tags like `<role>` and `<output-format>`, and supports variable substitution.

### File Organization

```
src/prompts/
├── __init__.py              # Exports PromptBuilder
├── builder.py               # PromptBuilder class (wraps poml Python SDK)
└── templates/
    └── system_prompt.poml   # System prompt in POML format
```

### Template Syntax

The template uses [POML](https://microsoft.github.io/poml/latest/) markup with semantic components:

- `<role>` — LLM identity and behavior instructions
- `<output-format>` — Response format constraints (TTS-friendly rules)
- `<section>` / `<h>` / `<p>` — Structured content sections

Variable substitution uses POML's built-in template engine with `{{ variable }}` syntax.

### Integration

`LLMClient.__init__` builds the system prompt via:

```python
PromptBuilder().build(
    projects_dir=str(settings.PROJECTS_DIR.expanduser().resolve()),
    os_name=platform.system(),
)
```

The `os_name` variable is injected into the `<role>` tag so the LLM knows the host OS (e.g. "Linux" or "Windows") and can generate appropriate commands.

### @Mention Project Context Injection

When a user message contains `@ProjectName` tokens (e.g. `@RCFlow`), `PromptRouter.handle_prompt()` detects the mentions and resolves them against `PROJECTS_DIR`. If a mentioned name matches an actual project directory, a context block is prepended to the user message content sent to the LLM:

```
[Context: This message references project "RCFlow" located at /home/user/Projects/RCFlow. All instructions in this message relate to this project.]
```

Key behavior:
- The `@` must appear at the start of the text or after whitespace.
- Only mentions that resolve to existing directories under `PROJECTS_DIR` produce context; unresolved mentions are silently ignored.
- The original user text is preserved — the context is an additional content block, not a replacement.
- The injected context block uses `cache_control: {"type": "ephemeral"}` to avoid polluting prompt caching.
- The client-side buffer receives the original text only (no injected context).

---

## Pluggable Tool Definitions

### File Organization

Tools are defined as individual JSON files in a `tools/` directory:

```
tools/
├── shell_exec.json
├── http_request.json
├── python_interactive.json
├── file_read.json
└── system_info.json
```

Each file defines one tool. Drop a `.json` file into `tools/` to register a new tool. The server loads all tool files on startup (and can optionally hot-reload).

### Tool JSON Schema

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

### HTTP API Tool Example

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

### Long-Running Tool Example

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

### Claude Code Executor

The `claude_code` executor manages a Claude Code CLI subprocess with bidirectional stream-json communication. It enables delegating complex coding tasks to Claude Code while streaming output back to the client in real time.

**Working directory validation:** Before spawning the subprocess, the prompt router validates that the specified `working_directory` exists on disk. If it does not, the tool returns an error message to the LLM instead of starting a session. The system prompt also instructs the LLM to verify directory existence via `shell_exec` before calling `claude_code`, and to resolve project names to `~/Projects/<project_name>`.

**How it works:**

1. The outer LLM calls `claude_code(prompt=..., working_directory=...)`.
2. RCFlow validates that `working_directory` exists; returns an error to the LLM if not.
3. RCFlow spawns `claude --input-format stream-json --output-format stream-json` as a long-lived subprocess.
4. The initial prompt is sent via stdin in stream-json format.
5. Output events stream from stdout to the client session buffer in real time.
6. The session enters "Claude Code mode" — subsequent user messages bypass the outer LLM and route directly to the Claude Code subprocess via stdin.
7. The process stays alive between turns. Follow-up messages are sent via stdin and responses are read from stdout. If the process unexpectedly crashes, RCFlow restarts it with the same `--session-id` as a fallback.

**Result summarization:** When Claude Code emits a `result` event (turn complete), the prompt router fires a background task that sends the result text to a fast model (configured via `SUMMARY_MODEL`) to produce a 2-3 sentence TTS-friendly summary. The summary is pushed to the session buffer as a `summary` message type, arriving after the `text_chunk(finished=true)` for the result. Summary failures are logged but never break the session. A `session_end_ask` message is also pushed immediately after the result to ask the user whether they want to end the session or continue chatting.

**Environment:** The `CLAUDECODE` and `CLAUDE_AVAILABLE_MODELS` environment variables are removed from the subprocess environment to allow nesting.

**Tool Definition Example:**

```json
{
  "name": "claude_code",
  "description": "Start a Claude Code coding agent session. Claude Code can read, write, and execute code autonomously. Use for complex tasks: implementing features, fixing bugs, refactoring, writing tests, etc. The working_directory must be an existing project directory. User projects live under ~/Projects/ so when the user mentions a project by name, use ~/Projects/<project_name> as the working_directory. Always verify the directory exists before calling this tool.",
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
      "default_permission_mode": "bypassPermissions",
      "max_turns": 50,
      "timeout": 600
    }
  }
}
```

### Codex CLI Executor

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

**Authentication:** The `CODEX_API_KEY` environment variable is injected into the subprocess environment from the server config.

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

### Tool Management Service

RCFlow automatically manages the installation and updating of external CLI tools (Claude Code and Codex). The `ToolManager` service (`src/services/tool_manager.py`) handles detection, installation, and periodic updates using **native binary downloads** — no Node.js or npm required.

**How it works:**

1. On server startup, `ToolManager.ensure_tools()` runs in the lifespan and **detects** tools (does not auto-install). Missing tools are reported; installation happens on-demand when the user requests it via the UI.
2. Detection checks: RCFlow managed directory (`~/.local/share/rcflow/tools/` on Linux, `%LOCALAPPDATA%\rcflow\tools\` on Windows) → system `PATH` → report as not found.
3. Tools in the RCFlow managed directory are marked `managed=True`. Tools found on `PATH` are marked `managed=False` (external).
4. A background `asyncio.Task` checks for updates every `TOOL_UPDATE_INTERVAL_HOURS` hours (default 6). Only RCFlow-managed tools are auto-updated.
5. `PromptRouter` gets binary paths from `ToolManager.get_binary_path()` — no binary path settings needed.
6. The UI allows switching between managed and external sources when both are available via `POST /api/tools/{name}/source`.

**Installation methods:**

- **Claude Code**: Native binary downloaded from Anthropic's GCS bucket (`storage.googleapis.com/claude-code-dist-.../claude-code-releases`). SHA256 checksum verified against the official manifest. Binary placed at `~/.local/share/rcflow/tools/claude-code/claude` (Linux) or `%LOCALAPPDATA%\rcflow\tools\claude-code\claude.exe` (Windows).
- **Codex**: Native binary downloaded from GitHub Releases (`github.com/openai/codex/releases`). Tarball extracted on Linux; bare `.exe` downloaded directly on Windows. Binary placed at `~/.local/share/rcflow/tools/codex/codex` (Linux) or `%LOCALAPPDATA%\rcflow\tools\codex\codex.exe` (Windows).

**Platform strings:**

| Platform | Claude Code (GCS) | Codex (GitHub) |
|----------|-------------------|----------------|
| Linux x64 | `linux-x64` | `x86_64-unknown-linux-gnu` |
| Linux x64 musl | `linux-x64-musl` | `x86_64-unknown-linux-musl` |
| Linux arm64 | `linux-arm64` | `aarch64-unknown-linux-gnu` |
| Windows x64 | `win32-x64` | `x86_64-pc-windows-msvc` |

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

### Per-Tool Settings Isolation

RCFlow maintains isolated settings files for managed CLI tool instances so they don't share configuration with user-installed versions. The `ToolSettingsManager` service (`src/services/tool_settings.py`) handles reading and writing these settings.

**Settings file locations** (under `~/.local/share/rcflow/tools/` on Linux, `%LOCALAPPDATA%\rcflow\tools\` on Windows):

| Tool         | Settings File                                   | Env Var Injected       |
|--------------|--------------------------------------------------|------------------------|
| Claude Code  | `claude-code/config/settings.json`               | `CLAUDE_CONFIG_DIR`    |
| Codex        | `codex/config/codex.json`                         | `CODEX_HOME`           |

When launching tool subprocesses, `PromptRouter` injects the appropriate environment variable pointing to the tool's isolated config directory. This ensures RCFlow-managed instances use their own settings.

**API endpoints:**

- `GET /api/tools/{tool_name}/settings` — Returns `{tool, fields: [{key, label, type, value, default, description, options?, visible_when?}]}`. Secret-type values are masked.
- `PATCH /api/tools/{tool_name}/settings` — Body: `{"updates": {"key": value}}`. Validates keys against the schema, writes atomically (`.tmp` + `rename()`), returns the updated schema+values. Masked secret values sent back are detected and the stored value is preserved.

**Supported field types:** `string`, `boolean`, `select`, `string_list`, `secret`.

**`secret` field type:** Values are masked before being returned to the client — all characters except the last 4 are replaced with `*`. When a masked value is sent back in an update, it is detected and the existing stored value is preserved. The client renders secret fields with a masked display and a "Change" button that reveals an obscured input with a visibility toggle.

**`visible_when` conditional visibility:** Schema fields may include `"visible_when": {"key": "<other_key>", "value": "<expected_value>"}`. The field is only shown in the client UI when the referenced key matches the expected value. The server always returns all applicable fields; visibility filtering is handled client-side.

Schema fields may include `"managed_only": true` — these are only exposed when the tool is using its managed (RCFlow-installed) binary. When the tool is switched to an external (PATH) source, managed-only fields are hidden from the GET endpoint and rejected by the PATCH endpoint. The `managed` status is resolved from `ToolManager` at request time.

**Claude Code settings schema:**

| Key                        | Type        | Managed-only | Visible when           | Description                                       |
|----------------------------|-------------|--------------|------------------------|----------------------------------------------------|
| `permissions.allow`        | string_list | no           | —                      | Tool permissions to always allow                   |
| `permissions.deny`         | string_list | no           | —                      | Tool permissions to always deny                    |
| `enableAllProjectMcpServers` | boolean   | no           | —                      | Auto-enable project MCP servers                    |
| `provider`                 | select      | yes          | —                      | API provider: Global / Anthropic / AWS Bedrock     |
| `anthropic_api_key`        | secret      | yes          | provider = anthropic   | API key for Anthropic provider                     |
| `aws_region`               | string      | yes          | provider = bedrock     | AWS region for Bedrock (default us-east-1)         |
| `aws_access_key_id`        | secret      | yes          | provider = bedrock     | AWS access key for Bedrock                         |
| `aws_secret_access_key`    | secret      | yes          | provider = bedrock     | AWS secret access key for Bedrock                  |
| `model`                    | string      | yes          | —                      | Default model override for sessions                |
| `default_permission_mode`  | select      | yes          | —                      | CLI --permission-mode (bypassPermissions/allowEdits) |
| `max_turns`                | string      | yes          | —                      | Maximum agentic turns per session (default 200)    |
| `timeout`                  | string      | yes          | —                      | Process timeout in seconds (default 1800)          |

**Provider env sync:** When `provider` or any credential field is updated, `ToolSettingsManager` automatically rebuilds the `env` section of the Claude Code `settings.json`:

- **Anthropic** (`provider=anthropic`): sets `env.ANTHROPIC_API_KEY` from `anthropic_api_key`.
- **Bedrock** (`provider=bedrock`): sets `env.CLAUDE_CODE_USE_BEDROCK=1`, plus `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` from their respective fields.
- **Global** (`provider=""`): removes the `env` section so that `PromptRouter` injects the server-level `ANTHROPIC_API_KEY` instead.

When the tool has a non-empty `provider`, `PromptRouter._build_claude_code_extra_env` skips injecting the global `ANTHROPIC_API_KEY`, letting the `settings.json` env section take precedence.

**Codex settings schema:**

| Key              | Type   | Managed-only | Description                                |
|------------------|--------|--------------|--------------------------------------------|
| `model`          | string | no           | Model name for Codex sessions              |
| `approval_mode`  | select | no           | Tool-call approval (full-auto / yolo)      |
| `timeout`        | string | yes          | Process timeout in seconds (default 600)   |

**Config overrides:** When a managed tool has settings configured, `PromptRouter` reads them at executor creation time and passes non-empty values as `config_overrides` to the executor constructor. These overrides are merged on top of the tool definition's `executor_config` when building subprocess commands.

### Tool Definition Fields

| Field             | Type   | Required | Description                                           |
|-------------------|--------|----------|-------------------------------------------------------|
| `name`            | string | yes      | Unique tool identifier, sent to LLM                   |
| `description`     | string | yes      | Human/LLM-readable description of what the tool does  |
| `version`         | string | no       | Semantic version of the tool definition                |
| `session_type`    | enum   | yes      | `one-shot` or `long-running`                          |
| `llm_context`     | enum   | yes      | `stateless` or `session-scoped`                       |
| `executor`        | enum   | yes      | `shell`, `http`, `claude_code`, or `codex`            |
| `parameters`      | object | yes      | JSON Schema describing the tool's input parameters    |
| `executor_config` | object | yes      | Executor-specific configuration                       |

---

## STT Integration (Pluggable)

### Interface

STT is abstracted behind a provider interface, same pattern as TTS. The server does not depend on a specific STT service.

```
Protocol:
  Input:  streaming audio chunks (binary)
  Output: transcribed text (partial and final results)

Provider interface:
  - connect()
  - transcribe(audio_chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptionResult]
  - close()

TranscriptionResult:
  - text: str
  - is_final: bool
```

The STT provider is configured via environment variable `STT_PROVIDER`.

### Wispr Flow Provider (default)

```
Mobile Client                  RCFlow Server              Wispr Flow
     │                              │                          │
     │── audio chunks ─────────────►│                          │
     │   (via /ws/input/audio)      │── auth message ────────►│
     │                              │── audio chunks ────────►│
     │                              │── commit ──────────────►│
     │                              │                          │
     │                              │◄── transcription ────────│
     │                              │    (text result)         │
     │                              │                          │
     │                              │── route text to ────►[Prompt Router]
     │                              │   LLM pipeline           │
```

#### Wispr Flow Connection Details

- **URL**: `wss://platform-api.wisprflow.ai/api/v1/dash/ws?api_key=Bearer%20<KEY>`
- **Audio format**: Base64-encoded 16-bit PCM, 16kHz, mono, ~1 second chunks
- **Protocol**: auth message → sequential append messages → commit message
- **Response**: JSON with `status: "text"` and `final: true/false`
- **Optimization**: Binary mode available via MessagePack serialization

---

## TTS Integration (Pluggable)

### Interface

TTS is abstracted behind a provider interface. The server does not depend on a specific TTS service.

```
Protocol:
  Input:  text string (or streaming text chunks)
  Output: streaming Opus/OGG audio frames

Provider interface:
  - connect()
  - synthesize(text: str) -> AsyncIterator[bytes]
  - close()
```

### Provider Selection

The TTS provider is configured via environment variable. Candidate providers (to be evaluated):

- **ElevenLabs** — WebSocket streaming, high quality voices, native Opus support
- **OpenAI TTS** — HTTP streaming, simple integration, Opus support
- **Cartesia** — WebSocket streaming, ultra-low latency, designed for real-time

The chosen provider will be implemented behind the pluggable interface.

---

## Database Schema

### Tables

```sql
-- API keys for WebSocket authentication
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

-- Archived sessions
CREATE TABLE sessions (
    id UUID PRIMARY KEY,
    backend_id VARCHAR(36) NOT NULL DEFAULT '',  -- owning backend instance ID (for multi-backend isolation)
    created_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    session_type VARCHAR(20) NOT NULL,       -- 'one-shot', 'conversational', 'long-running'
    status VARCHAR(20) NOT NULL,             -- 'completed', 'failed', 'cancelled'
    title VARCHAR(200),                      -- auto-generated human-readable title
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX ix_sessions_backend_id ON sessions(backend_id);

-- All messages within a session (prompts, responses, tool calls, tool output)
CREATE TABLE session_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id),
    sequence INTEGER NOT NULL,
    message_type VARCHAR(30) NOT NULL,       -- 'user_prompt', 'llm_text', 'tool_call', 'tool_output', 'error', 'session_end_ask'
    content TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, sequence)
);

-- LLM API call log (per-turn, no FK to sessions)
CREATE TABLE llm_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,                    -- indexed, no FK (sessions are in-memory until archival)
    message_id VARCHAR(255) NOT NULL,            -- Anthropic message ID "msg_..."
    model VARCHAR(255) NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    stop_reason VARCHAR(50) NOT NULL,
    has_tool_calls BOOLEAN NOT NULL DEFAULT false,
    request_messages JSONB NOT NULL,             -- full messages array sent to the LLM
    response_text TEXT,                          -- generated text only (nullable)
    service_tier VARCHAR(50),
    inference_geo VARCHAR(100)
);
CREATE INDEX ix_llm_calls_session_id ON llm_calls(session_id);
CREATE INDEX ix_llm_calls_started_at ON llm_calls(started_at);

-- Tool execution log
CREATE TABLE tool_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id),
    tool_name VARCHAR(255) NOT NULL,
    tool_input JSONB NOT NULL,
    tool_output TEXT,
    exit_code INTEGER,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL              -- 'running', 'completed', 'failed', 'timeout'
);
```

---

## Configuration

All configuration is via environment variables, loaded from a `.env` file in development.

| Variable                | Required | Default         | Description                          |
|-------------------------|----------|-----------------|--------------------------------------|
| `RCFLOW_HOST`           | no       | `0.0.0.0`       | Server bind address                  |
| `RCFLOW_PORT`           | no       | `8765`          | Server port                          |
| `RCFLOW_API_KEY`        | yes      |                 | API key for WebSocket auth           |
| `RCFLOW_BACKEND_ID`     | no       | auto-generated  | Unique backend instance ID (UUID). Auto-generated and persisted to `.env` on first run. Used to isolate sessions per backend when multiple backends share one database. |
| `SSL_CERTFILE`          | no       |                 | Path to TLS certificate (enables WSS when both cert+key set) |
| `SSL_KEYFILE`           | no       |                 | Path to TLS private key (enables WSS when both cert+key set) |
| `DATABASE_URL`          | yes      |                 | PostgreSQL connection string         |
| `LLM_PROVIDER`          | no       | `anthropic`     | LLM provider: `anthropic` or `bedrock` |
| `ANTHROPIC_API_KEY`     | cond.    |                 | Anthropic API key (required when `LLM_PROVIDER=anthropic`) |
| `ANTHROPIC_MODEL`       | no       | `claude-sonnet-4-20250514`| Model to use (use Bedrock model IDs when `LLM_PROVIDER=bedrock`) |
| `AWS_REGION`            | no       | `us-east-1`     | AWS region (used when `LLM_PROVIDER=bedrock`) |
| `AWS_ACCESS_KEY_ID`     | no       |                 | AWS access key ID (optional if using IAM roles/instance profiles) |
| `AWS_SECRET_ACCESS_KEY` | no       |                 | AWS secret access key (optional if using IAM roles/instance profiles) |
| `STT_PROVIDER`          | no       | `wispr_flow`    | STT provider name                    |
| `STT_API_KEY`           | yes      |                 | STT provider API key (Wispr Flow)    |
| `TTS_PROVIDER`          | no       | `none`          | TTS provider name                    |
| `TTS_API_KEY`           | no       |                 | TTS provider API key                 |
| `PROJECTS_DIR`          | no       | `~/Projects`    | Absolute path to user projects directory (used in system prompt and path resolution) |
| `TOOLS_DIR`             | no       | `./tools`       | Path to tool definitions directory   |
| `CODEX_API_KEY`         | no       |                 | OpenAI API key for Codex CLI         |
| `SUMMARY_MODEL`         | no       | auto (per provider) | Fast model for summaries/titles. Defaults to `claude-haiku-4-5-20251001` (Anthropic) or `{region}.anthropic.claude-haiku-4-5-v1:0` (Bedrock, region prefix derived from `AWS_REGION`) |
| `LOG_LEVEL`             | no       | `INFO`          | Logging level                        |

### Remote Configuration (Client-Side Editing)

The server exposes `GET /api/config` and `PATCH /api/config` endpoints that allow connected clients to view and edit a subset of server settings remotely. This enables users to configure API keys, provider selection, model IDs, and other options from the Flutter client without manual `.env` file editing.

**Config option metadata schema** (returned by `GET /api/config`):

| Field              | Type   | Description                                        |
|--------------------|--------|----------------------------------------------------|
| `key`              | string | Setting name (e.g. `LLM_PROVIDER`)                 |
| `label`            | string | Human-readable label                               |
| `type`             | string | `"string"`, `"select"`, `"boolean"`, or `"secret"` |
| `value`            | any    | Current value (masked for secrets — last 4 chars)   |
| `options`          | list   | Available choices (for `select` type only)          |
| `group`            | string | Grouping category (LLM, STT, TTS, Executors, etc.) |
| `description`      | string | Help text                                          |
| `required`         | bool   | Whether the field is required                      |
| `restart_required` | bool   | Whether changing requires a server restart          |

**Configurable groups**: LLM, STT, TTS, Claude Code, Codex, Paths, Logging. Groups are rendered as collapsible sections in the client UI.

**Excluded from remote config** (for security): `RCFLOW_API_KEY`, `RCFLOW_HOST`, `RCFLOW_PORT`, `SSL_CERTFILE`, `SSL_KEYFILE`, `DATABASE_URL`.

**Hot-reload**: When config is updated via `PATCH /api/config`, the server persists changes to the `.env` file and recreates the LLM client, STT provider, and TTS provider with the new settings. The old LLM client is gracefully closed.

**Client UI**: The Flutter client shows a "Settings" button on each connected worker card. Tapping it opens a dialog (desktop) or bottom sheet (mobile) that renders a dynamic form based on the server's config schema. Fields are grouped by section and rendered as text fields, dropdowns, switches, or password fields depending on type.

---

## Project Structure

```
RCFlow/
├── CLAUDE.md                    # Claude Code instructions
├── Design.md                    # This file — project design document
├── pyproject.toml               # Project metadata and dependencies (uv)
├── .env.example                 # Example environment variables
├── .python-version              # Python version pin
├── ruff.toml                    # Ruff linter/formatter config
│
├── src/
│   ├── __init__.py
│   ├── main.py                  # Entry point, FastAPI app, lifespan
│   ├── config.py                # Settings loaded from env vars
│   │
│   ├── cli/
│   │   └── __init__.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py              # Shared dependencies (auth, db session)
│   │   └── ws/
│   │       ├── __init__.py
│   │       ├── input_text.py    # /ws/input/text handler
│   │       ├── input_audio.py   # /ws/input/audio handler
│   │       ├── output_text.py   # /ws/output/text handler
│   │       └── output_audio.py  # /ws/output/audio handler
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── session.py           # Session manager and session state
│   │   ├── prompt_router.py     # Routes text to LLM pipeline
│   │   ├── llm.py               # Anthropic Messages API client
│   │   └── buffer.py            # Output buffer for session history
│   │
│   ├── executors/
│   │   ├── __init__.py
│   │   ├── base.py              # Base executor interface
│   │   ├── shell.py             # Shell command executor
│   │   ├── http.py              # HTTP API executor
│   │   ├── claude_code.py       # Claude Code CLI executor
│   │   └── codex.py             # Codex CLI executor (OpenAI)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   └── tool_manager.py      # Auto-install/update for Claude Code & Codex CLIs
│   │
│   ├── speech/
│   │   ├── __init__.py
│   │   ├── stt/
│   │   │   ├── __init__.py
│   │   │   ├── base.py          # Abstract STT provider interface
│   │   │   └── wispr_flow.py    # Wispr Flow STT provider
│   │   └── tts/
│   │       ├── __init__.py
│   │       ├── base.py          # TTS provider interface
│   │       └── providers/       # TTS provider implementations
│   │           └── __init__.py
│   │
│   ├── prompts/
│   │   ├── __init__.py          # Exports PromptBuilder
│   │   ├── builder.py           # PromptBuilder class (wraps poml SDK)
│   │   └── templates/
│   │       └── system_prompt.poml  # System prompt in POML format
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── db.py                # SQLAlchemy models
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py            # Async engine and session factory
│   │   └── migrations/          # Alembic migrations
│   │
│   └── tools/
│       ├── __init__.py
│       ├── loader.py            # Load and validate tool JSON files
│       └── registry.py          # Tool registry for LLM integration
│
├── tools/                       # Pluggable tool definition JSON files
│   ├── shell_exec.json
│   ├── codex.json               # OpenAI Codex CLI agent tool
│   └── ...
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared fixtures
│   ├── test_api/
│   │   └── test_ws/
│   │       ├── test_input_text.py
│   │       ├── test_input_audio.py
│   │       ├── test_output_text.py
│   │       └── test_output_audio.py
│   ├── test_core/
│   │   ├── test_session.py
│   │   ├── test_prompt_router.py
│   │   ├── test_llm.py
│   │   └── test_buffer.py
│   ├── test_executors/
│   │   ├── test_shell.py
│   │   ├── test_http.py
│   │   ├── test_claude_code.py
│   │   └── test_codex.py
│   ├── test_services/
│   │   └── test_tool_manager.py
│   ├── test_prompts/
│   │   └── test_builder.py
│   ├── test_speech/
│   │   ├── test_stt/
│   │   │   └── test_wispr_flow.py
│   │   └── test_tts/
│   └── test_tools/
│       ├── test_loader.py
│       └── test_registry.py
│
└── systemd/
    └── rcflow.service           # Systemd unit file
```

---

## Platform Support

RCFlow supports **Linux (x64, arm64)** and **Windows (x64)**.

### Platform-Specific Behavior

| Feature | Linux | Windows |
|---------|-------|---------|
| Managed tools directory | `~/.local/share/rcflow/tools/` | `%LOCALAPPDATA%\rcflow\tools\` |
| Default shell | `/bin/bash` | `powershell.exe` |
| Claude Code binary | `claude` | `claude.exe` |
| Codex binary | `codex` | `codex.exe` |
| Codex archive format | `.tar.gz` | `.zip` |
| Process isolation | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP` |
| Process tree kill | `os.killpg(SIGKILL)` | `taskkill /T /F /PID` |

### Database

PostgreSQL is required on all platforms. On Windows, install PostgreSQL natively or via Docker Desktop. The `DATABASE_URL` format is identical: `postgresql+asyncpg://user:pass@localhost:5432/rcflow`.

### Cross-Platform Process Management

Process creation and termination are abstracted in `src/utils/process.py`:

- `new_session_kwargs()` — returns the correct kwargs to isolate child process trees (`start_new_session` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows).
- `kill_process_tree()` — kills a process and all its children (`os.killpg` on POSIX, `taskkill /T /F` on Windows).

Both `ClaudeCodeExecutor` and `CodexExecutor` use these helpers.

---

## Deployment

### Development

```bash
uv run rcflow                    # or: uv run rcflow run — starts the server
```

### Production (systemd — Linux)

```ini
# systemd/rcflow.service
[Unit]
Description=RCFlow Action Server
After=network.target postgresql.service

[Service]
Type=simple
User=rcflow
WorkingDirectory=/opt/rcflow
EnvironmentFile=/opt/rcflow/.env
ExecStart=/opt/rcflow/.venv/bin/python -m rcflow
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable rcflow
sudo systemctl start rcflow
sudo journalctl -u rcflow -f     # View logs
```

---

## Future Considerations
- **Sandboxed execution**: Docker-based tool execution for untrusted tools
- **Python callable tools**: Another executor type for direct Python function invocation
- **Hot-reload tools**: Watch the `tools/` directory for changes without restart
- **Multi-user support**: JWT-based auth with user accounts and per-user sessions
- **Voice input on output channels**: Bidirectional audio for full voice conversation
- **Audio format negotiation**: Let client specify preferred audio codec on connect
