# RCFlow — Design Document

## Overview

RCFlow is a background server running on Linux or Windows that provides a WebSocket-based interface for executing actions on the host machine via natural language prompts. Users connect from client applications (Android and Windows desktop), send text or voice prompts, and the server uses an LLM (Anthropic Messages API, AWS Bedrock, or OpenAI Chat Completions API) to interpret those prompts into tool calls. Tools are pluggable and defined via JSON files. Results — both text and audio — stream back to the client in real time.

## Technology Stack

| Component            | Technology                    |
|----------------------|-------------------------------|
| Language             | Python 3.12+                  |
| Package Manager      | uv                            |
| Web Framework        | FastAPI                       |
| ORM                  | SQLAlchemy 2.0 (async)        |
| Database             | SQLite (default) or PostgreSQL |
| LLM                  | Anthropic Messages API, AWS Bedrock, or OpenAI Chat Completions API |
| STT                  | Pluggable (Wispr Flow default)|
| TTS                  | Pluggable (provider TBD)      |
| Audio Format         | Opus/OGG                      |
| Prompt Templates     | Jinja2                        |
| Linting / Formatting | Ruff                          |
| Type Checking        | ty                            |
| Testing              | pytest                        |
| Config               | Environment variables + settings.json       |
| OS                   | Linux, Windows                |
| Client Platforms     | Android, Windows (desktop)    |
| Android Keep-Alive   | flutter_foreground_task       |
| Audio Playback       | audioplayers                  |
| File Picker          | file_picker (Windows custom sounds) |
| Bundling             | PyInstaller (self-contained distributable) |
| Windows GUI          | tkinter (server control window)            |
| Windows Tray         | pystray + Pillow (system tray icon)        |
| Windows Terminal PTY | pywinpty (ConPTY wrapper)                   |
| Windows Installer    | Inno Setup 6 (setup.exe builder)           |

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
│   (Anthropic API,       │       │              │
│    AWS Bedrock, or      │       │              │
│    OpenAI)              │       │              │
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
│   Database               │
│   (SQLite / PostgreSQL)  │
└──────────────────────────┘
```

### Request Lifecycle

```
1. Client connects to /ws/input/text or /ws/input/audio
2. If audio → RCFlow forwards to Wispr Flow STT → receives transcribed text
3. Text prompt is routed to the Prompt Router
4. Prompt Router creates or resumes a Session
5. Prompt + tool definitions + session context sent to LLM provider (Anthropic API, AWS Bedrock, or OpenAI, streaming)
6. LLM responds with text and/or tool_use blocks
7. For each tool_use → Tool Executor runs the tool (shell command or HTTP API call)
8. Tool output fed back to LLM for further reasoning (agentic loop)
9. All LLM text output streams to /ws/output/text chunk-by-chunk
10. All LLM text output also sent to TTS → audio streams to /ws/output/audio
11. When the LLM finishes (no more tool calls), the session remains active.
    If the LLM included [SessionEndAsk], the server pushes a session_end_ask
    message; the client shows a confirmation card. The session only ends
    when the user explicitly confirms or sends POST /api/sessions/{id}/end.
12. Completed sessions are archived from memory to the database
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

### Terminal Sessions (Sidebar Integration)

Terminal panes appear in the session sidebar alongside regular sessions, grouped under their respective worker. Each terminal has a persistent `TerminalSessionInfo` that survives pane close/reopen.

**Data model** (`lib/ui/widgets/terminal_pane.dart`):
- `TerminalSessionInfo`: Holds `terminalId`, `workerId`, `title` (user-renamable, default "Terminal"), `createdAt`, `paneId?` (null when hidden), plus the xterm `Terminal` and `TerminalController` objects. Connection state (`connected`, `ended`) and stream subscriptions are also stored here so the terminal buffer persists across pane lifecycle.

**AppState terminal management** (`lib/state/app_state.dart`):
- `_terminalSessions`: `Map<String, TerminalSessionInfo>` keyed by `terminalId`. Terminals registered here on `openTerminal()`.
- `terminalsByWorker`: Groups terminal sessions by `workerId` for sidebar display.
- `closePane()` for terminal panes: Detaches the pane (`info.paneId = null`) but does NOT kill the server-side PTY. The terminal buffer (xterm `Terminal` object) stays in `_terminalSessions`.
- `showTerminalInPane(terminalId)`: Reattaches a hidden terminal to a new or existing pane. If already visible, focuses the pane.
- `closeTerminalSession(terminalId)`: Actually kills the terminal — sends `close` control message to the server, cancels subscriptions, removes from `_terminalSessions`, and closes the pane.
- `renameTerminal(terminalId, newTitle)`: Updates the title displayed in sidebar and pane header.
- `splitPaneWithTerminal(paneId, zone, terminalId)`: Drag-and-drop support for terminal entries in the sidebar.

**Sidebar entries** (`lib/ui/widgets/session_panel.dart`):
- Terminal sessions shown after regular sessions in each worker group, via `_TerminalSessionTile`.
- Distinctive terminal icon (`Icons.terminal_rounded`) with green/muted background based on `ended` state.
- No pause/resume buttons (terminals don't support pause).
- Close button (X) kills the terminal with confirmation dialog.
- Right-click context menu: Rename, Close terminal.
- Long-press: Rename dialog.
- Tap: Shows terminal in a pane (reattach if hidden, focus if already visible).
- Draggable with `TerminalDragData` for split-view drop targeting.

**Terminal pane widget** (`lib/ui/widgets/terminal_pane.dart`):
- `TerminalPane` receives `TerminalSessionInfo` from AppState — does NOT create its own `Terminal`/`TerminalController`.
- On `initState`: Sets up output/resize handlers and either connects (first time) or reattaches (re-show).
- On `dispose`: Only cancels stream subscriptions and unregisters from `TerminalService`. Does NOT send close command to server.

**Drop target** (`lib/ui/widgets/session_pane.dart`):
- `DragTarget<Object>` accepts both `SessionDragData` and `TerminalDragData`, dispatching to `splitPaneWithSession` or `splitPaneWithTerminal` respectively.

### Workers (Multi-Server)

The client can connect to multiple RCFlow servers simultaneously. Each server connection is a "Worker". Each backend instance is identified by a unique `RCFLOW_BACKEND_ID` (auto-generated UUID, persisted to `settings.json`). When multiple backends share the same database, sessions are isolated per backend via the `backend_id` column on the `sessions` table — each backend only sees and manages its own sessions.

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
| GET    | `/api/tools`                            | Yes  | List registered tool names and descriptions. Optional `?q=` for case-insensitive substring filter. Returns `{"tools": [{"name": "...", "description": "..."}]}`. |
| GET    | `/api/projects`                         | Yes  | List directory names from all configured project directories (`PROJECTS_DIR`, comma-separated). Optional `?q=` for case-insensitive substring filter. Returns `{"projects": [...]}`. |
| POST   | `/api/sessions/{session_id}/cancel`     | Yes  | Cancel a running session (kills subprocess)      |
| POST   | `/api/sessions/{session_id}/end`        | Yes  | Gracefully end a session (user-confirmed completion) |
| POST   | `/api/sessions/{session_id}/pause`      | Yes  | Pause an active session. Kills any running Claude Code subprocess. New prompts rejected until resumed. |
| POST   | `/api/sessions/{session_id}/resume`     | Yes  | Resume a paused session. Client can subscribe to receive all buffered output. |
| POST   | `/api/sessions/{session_id}/restore`    | Yes  | Restore an archived (completed/failed/cancelled) session back to active state. Rebuilds conversation history, buffer, and Claude Code executor state. |
| PATCH  | `/api/sessions/{session_id}/title`      | Yes  | Set or clear a session title (max 200 chars). Body: `{"title": "..."}` or `{"title": null}`. |
| GET    | `/api/config`                           | Yes  | Get server configuration schema with current values. Secret values are masked. Options grouped by section. |
| PATCH  | `/api/config`                           | Yes  | Update server configuration. Body: `{"updates": {"KEY": "value", ...}}`. Persists to `settings.json`, reloads settings, and hot-reloads LLM/STT/TTS components. Returns updated schema. |
| GET    | `/api/tools/status`                     | Yes  | Get installation status, versions, and update availability for managed CLI tools (Claude Code, Codex). |
| POST   | `/api/tools/update`                     | Yes  | Check for and install updates to RCFlow-managed CLI tools. Only updates tools managed by RCFlow (not user-installed ones). |
| GET    | `/api/tools/{tool_name}/settings`       | Yes  | Get per-tool settings schema and current values for a managed CLI tool. |
| PATCH  | `/api/tools/{tool_name}/settings`       | Yes  | Update per-tool settings. Body: `{"updates": {"key": value, ...}}`. Returns updated schema+values. |
| POST   | `/api/tools/codex/login`                | Yes  | Start Codex ChatGPT login. Optional `?device_code=true` for device-code flow. Streams NDJSON events: `auth_url` or `device_code`, `waiting`, `complete`, `error`. Times out after 5 min. |
| GET    | `/api/tools/codex/login/status`         | Yes  | Check Codex ChatGPT login status. Returns `{"logged_in": bool, "method": "ChatGPT"|null}`. |
| GET    | `/api/tasks`                            | Yes  | List all tasks for the current backend. Optional `?status=` and `?source=` filters. Sorted by `updated_at` desc. |
| GET    | `/api/tasks/{task_id}`                  | Yes  | Get a single task with attached sessions. |
| POST   | `/api/tasks`                            | Yes  | Create a task. Body: `{"title", "description?", "source?", "session_id?"}`. Returns 201. |
| PATCH  | `/api/tasks/{task_id}`                  | Yes  | Update task fields (title, description, status). Status transitions are validated (409 on invalid). |
| DELETE | `/api/tasks/{task_id}`                  | Yes  | Delete a task and all session associations. |
| POST   | `/api/tasks/{task_id}/sessions`         | Yes  | Attach a session to a task. Body: `{"session_id": "..."}`. Returns 201. |
| DELETE | `/api/tasks/{task_id}/sessions/{sid}`   | Yes  | Detach a session from a task. |

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

Send a mid-turn interactive response (plan mode approval, question answers, etc.):

```json
{
  "type": "interactive_response",
  "session_id": "uuid",
  "text": "yes"
}
```

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
  "is_error": false,
  "sequence": 43
}
```

Tool output is emitted for all agent executors:
- **Claude Code**: Captured from `tool_result` events in the stream-json protocol. Content may be plain text or extracted from content blocks. `is_error` reflects the SDK's `is_error` flag.
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
  "created_at": "2025-01-15T10:30:00+00:00",
  "input_tokens": 1234,
  "output_tokens": 567,
  "cache_creation_input_tokens": 100,
  "cache_read_input_tokens": 200,
  "tool_input_tokens": 5000,
  "tool_output_tokens": 3000,
  "tool_cost_usd": 0.05
}
```

Token usage fields are included in every `session_update` broadcast:
- `input_tokens` / `output_tokens`: Tokens used by the global LLM pipeline (Anthropic/Bedrock/OpenAI).
- `cache_creation_input_tokens` / `cache_read_input_tokens`: Anthropic prompt caching breakdown.
- `tool_input_tokens` / `tool_output_tokens`: Tokens used by agent tool sessions (Claude Code, Codex).
- `tool_cost_usd`: Cumulative cost reported by Claude Code (`cost_usd` from result events).

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

**Session metadata updates** (title, status, activity state, and token usage) are automatically streamed to all connected `/ws/output/text` clients without explicit subscription. When any session's title, status, activity state, or token counts change, a `session_update` message is broadcast to all output clients. This enables real-time updates of the session list and token usage display in the client UI without polling.

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
      "title": "List files in directory",
      "input_tokens": 1234,
      "output_tokens": 567,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0,
      "tool_input_tokens": 0,
      "tool_output_tokens": 0,
      "tool_cost_usd": 0.0
    }
  ]
}
```

Sessions are sorted by `created_at` descending (most recent first). The list includes both in-memory active sessions and archived sessions from the database. The `title` field is `null` until auto-generated after the first LLM response.

### Task Messages

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

#### Task Status Transitions

Valid transitions (enforced server-side):

| From         | Allowed To                |
|-------------|---------------------------|
| `todo`       | `in_progress`, `done`     |
| `in_progress`| `todo`, `review`, `done`  |
| `review`     | `in_progress`, `done`     |
| `done`       | `todo`, `in_progress`     |

AI agents (source: `"ai"`) are forbidden from setting status to `done`. Only users can mark tasks as complete.

#### Automatic Task Status Behavior

- **AI-created tasks** start as `in_progress` (since they are created during an active session working on them).
- **Matched existing tasks** in `todo` or `review` status are auto-promoted to `in_progress` when attached to a new session.
- **On session end**, the LLM evaluates each attached task and may advance it to `review` if the work appears complete, or keep it at `in_progress` if more work is needed. The LLM cannot set `done`.
- **Task matching** considers all non-done tasks (`todo`, `in_progress`, `review`) to prevent duplicate creation.

### Artifact Messages

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
      "session_id": "uuid or null"
    }
  ]
}
```

The server broadcasts `artifact_list` after each extraction that discovers new artifacts.

When an artifact is deleted via the HTTP API, the server broadcasts:

```json
{
  "type": "artifact_deleted",
  "artifact_id": "uuid"
}
```

#### HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/artifacts` | List artifacts. Query params: `search`, `limit`, `offset` |
| GET | `/api/artifacts/search` | Search artifacts for autocomplete. Query param: `q` (substring filter). Returns max 10 results with `file_name`, `file_path`, `file_extension`, `file_size`, `mime_type`, `is_text` |
| GET | `/api/artifacts/{id}` | Get artifact metadata |
| GET | `/api/artifacts/{id}/content` | Get raw file content (text/plain) |
| DELETE | `/api/artifacts/{id}` | Delete artifact record (not the file) |
| GET | `/api/artifacts/settings` | Get extraction settings |
| PATCH | `/api/artifacts/settings` | Update extraction settings |

#### Artifact Scanner

The `ArtifactScanner` service extracts file paths from session messages and tracks matching files as artifacts. It uses a regex to find file paths in message content and metadata, then verifies each path exists on disk. Pattern matching is case-insensitive (e.g. `*.md` matches `README.MD`). The scanner:

- Scans the session's `conversation_history` JSON (complete messages, not fragmented streaming chunks) for file paths
- Also reads archived `SessionMessage` rows for tool outputs and metadata
- Extracts file paths using regex (absolute, `~/`, and relative `./`/`../` paths)
- Filters paths against include/exclude patterns
- Verifies files exist on disk before tracking them
- Enforces a configurable max file size (default 5 MB)
- Creates new artifact records or updates existing ones if file metadata changed
- Associates artifacts with the session they were extracted from

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
| `awaiting_permission`  | Blocked waiting for user to approve/deny a tool use |

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
- **Completed sessions**: Automatically archived to the database when a session reaches a terminal state (completed, failed, or cancelled). The prompt router fires a background task after each `session.complete()`, `session.fail()`, or `session.cancel()` call. Stores: session ID, timestamps, all prompts, all LLM responses, tool calls, tool outputs, metadata, `conversation_history` (the raw LLM message list for restoration), and token usage totals (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `tool_input_tokens`, `tool_output_tokens`, `tool_cost_usd`).
- **Restored sessions**: Archived sessions can be restored back to active state via `POST /api/sessions/{id}/restore` or the `restore_session` WebSocket message. The session's conversation history, buffer messages, and metadata are loaded from the DB. For Claude Code sessions, the CC `session_id`, `working_directory`, tool name, and parameters are stored in `metadata_` during archiving and used to reconstruct the executor on restore. The first message sent to a restored CC session triggers a `restart_with_prompt` using the stored `--session-id`, allowing Claude Code to resume its internal conversation context.
- **On server restart**: Active sessions are lost. Archived sessions remain queryable via `GET /api/sessions` and `GET /api/sessions/{session_id}/messages`, and can be restored.
- **Session listing**: `GET /api/sessions` and the WebSocket `list_sessions` command both merge in-memory sessions with archived sessions from the database (excluding duplicates), sorted by `created_at` descending. Each session entry includes a `created_at` ISO 8601 timestamp and `title`. Archived sessions are filtered by `backend_id` so each backend instance only sees its own sessions.

### Session Todos

Each active session tracks an in-memory list of todo items, updated whenever Claude Code calls the `TodoWrite` tool. The todo list is the complete state (replaced wholesale on each update, not incremental). Todo state is:
- Broadcast to subscribed clients via `todo_update` WebSocket messages.
- Queryable via `GET /api/sessions/{session_id}/todos` (returns `{"session_id": "...", "todos": [...]}` or 404 for unknown sessions).
- Included in buffer history, so clients reconstruct todo state on session subscribe replay and archived session history loading.
- Not persisted to a separate database table — stored only in-memory and as buffer messages.

### Session Titles

Sessions receive auto-generated human-readable titles (max 6 words) derived from the first user prompt and LLM response. After the agentic loop completes for the first turn of a session, a background task sends the user prompt and assistant response to the summary model (`SUMMARY_MODEL`) to generate a short title. The title is stored in the `title` column of the `sessions` table and included in all session list responses (HTTP and WebSocket). Title generation failures are logged but never break the session. The `title` field is `null` until generated. Users can also manually rename sessions at any time via `PATCH /api/sessions/{session_id}/title`. Setting the title to `null` clears it.

### Concurrency

Multiple sessions can run simultaneously. Each session is independent with its own:
- LLM conversation context (if session-scoped)
- Tool execution subprocess(es)
- Output buffer
- Subscriber list

### Token Usage Tracking

Each session accumulates token usage counters in real time:

- **`input_tokens` / `output_tokens`**: Tokens consumed by the global LLM pipeline (Anthropic, Bedrock, or OpenAI) during the agentic loop. Updated after each `StreamDone`.
- **`cache_creation_input_tokens` / `cache_read_input_tokens`**: Anthropic prompt-caching breakdown (zero for non-Anthropic providers).
- **`tool_input_tokens` / `tool_output_tokens`**: Tokens consumed by agent tool subprocesses (Claude Code via `result`/`system` events, Codex via `turn.completed` events).
- **`tool_cost_usd`**: USD cost reported by Claude Code's `result` event.

Token counters are broadcast to clients via `session_update` messages whenever they change, persisted to the database on session archive/shutdown, and restored when a session is loaded from the database.

**Token limits**: When `SESSION_INPUT_TOKEN_LIMIT` or `SESSION_OUTPUT_TOKEN_LIMIT` is set to a non-zero value, the prompt router checks the session's total tokens (LLM + tool) before processing each new user message. If either limit is exceeded, the session receives an error message and the prompt is rejected. The check compares `input_tokens + tool_input_tokens` against the input limit and `output_tokens + tool_output_tokens` against the output limit.

---

## Interactive Permission Approval

When a Claude Code session is configured with `default_permission_mode: "interactive"` in tool settings, the server intercepts tool-use events from the subprocess and asks the user for approval before each tool executes.

### How It Works

1. Claude Code emits `tool_use` blocks in its stream-json output (within `assistant` events).
2. `PromptRouter._relay_claude_code_stream()` detects these blocks.
3. The `PermissionManager` checks its in-memory cache of rules. If a cached rule covers this tool/path, the decision is applied silently.
4. If no cached rule matches, a `PERMISSION_REQUEST` message is pushed to the session buffer, the session activity state changes to `awaiting_permission`, and the stream reading coroutine blocks on an `asyncio.Event`.
5. The client displays a `PermissionRequestCard` with the tool name, description, risk level, and scope options (just this once / all uses of this tool / all tools).
6. The user's response arrives as a `permission_response` message on the input WebSocket.
7. `PromptRouter.resolve_permission()` resolves the pending request, optionally stores a rule in the `PermissionManager`, and signals the event.
8. The stream reading coroutine resumes. If denied, a `TOOL_START` message is emitted with `permission_denied: true`. If allowed, the tool proceeds normally.

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| `PermissionManager` | `src/core/permissions.py` | Per-session permission cache, pending request tracking, rule storage |
| `PermissionDecision` / `PermissionScope` | `src/core/permissions.py` | Enums for allow/deny and scope levels |
| `classify_risk()` | `src/core/permissions.py` | Classifies tool invocations as low/medium/high/critical risk |
| `PERMISSION_REQUEST` | `src/core/buffer.py` | New `MessageType` for permission request messages |
| `AWAITING_PERMISSION` | `src/core/session.py` | New `ActivityState` for blocked-on-approval |
| `PermissionRequestCard` | `rcflowclient/.../permission_request_card.dart` | Flutter widget for the approval UI |

### Permission Scopes

| Scope | Meaning |
|-------|---------|
| `once` | Applies to this single request only |
| `tool_session` | Applies to all uses of this tool for the rest of the session |
| `tool_path` | Applies to this tool for files under a directory prefix (Read/Write/Edit/Glob/Grep) |
| `all_session` | Blanket allow/deny for ALL tools for the rest of the session |

### Risk Classification

Tools are classified by risk level to help the user make informed decisions:

| Risk | Tools | Description |
|------|-------|-------------|
| Low | Read, Glob, Grep, WebFetch | Read-only operations |
| Medium | Write, Edit, NotebookEdit, Agent | File modifications, sub-agent launches |
| High | Bash | Shell command execution |
| Critical | Bash (destructive patterns) | `rm`, `git push --force`, `kill`, etc. |

### Edge Cases

- **Timeout**: If no response arrives within 120 seconds, the request is auto-denied.
- **Client disconnect**: Pending requests stay active. Timeout eventually auto-denies. Reconnecting clients can still respond to unexpired requests.
- **Session pause/cancel**: All pending permission requests are auto-denied via `PermissionManager.cancel_all_pending()`.
- **Session restore**: Permission rules saved in `session.metadata["permission_rules"]` are restored so the user doesn't re-approve previously approved tools.
- **Multiple clients**: Only the first response for a given `request_id` takes effect; subsequent responses are silently ignored.

### Limitations

- Currently supported for Claude Code sessions only. Codex uses a one-shot process model where stdin is closed after writing the prompt, making interactive approval infeasible without a fundamental I/O change. Codex interactive permissions are planned for a future release.
- When `default_permission_mode` is set to `"interactive"` (or not set), the server does **not** pass `--permission-mode` to Claude Code, letting it use its default behavior. This allows Claude Code to emit interactive events (AskUserQuestion, EnterPlanMode, ExitPlanMode) via stream-json, which the server intercepts and forwards to the client. Mid-turn responses (question answers, plan approval) are sent directly to Claude Code's stdin via the `interactive_response` message type, without creating a new agent group or reading task. For other permission modes (e.g., `bypassPermissions`, `allowEdits`), the value is passed directly to `--permission-mode`.

---

## System Prompt Templates

The system prompt sent to the LLM is defined in a Jinja2 template file rather than inline Python strings. This separates prompt content from code and supports variable substitution.

### File Organization

```
src/prompts/
├── __init__.py              # Exports PromptBuilder
├── builder.py               # PromptBuilder class (uses Jinja2)
└── templates/
    └── system_prompt.j2     # System prompt in Jinja2 format
```

### Template Syntax

The template uses [Jinja2](https://jinja.palletsprojects.com/) with `{{ variable }}` syntax for variable substitution. `StrictUndefined` is used so that missing variables raise errors immediately.

### Integration

`LLMClient.__init__` builds the system prompt via:

```python
PromptBuilder().build(
    projects_dirs=", ".join(str(d) for d in settings.projects_dirs),
    os_name=platform.system(),
)
```

The `os_name` variable is injected into the `<role>` tag so the LLM knows the host OS (e.g. "Linux" or "Windows") and can generate appropriate commands.

### Global Prompt

If `GLOBAL_PROMPT` is set (via server configuration), it is appended to the base system prompt for all LLM calls. The `LLMClient._system_prompt` property dynamically composes the full prompt by joining the base template output with the global prompt text separated by a blank line. This allows users to set persistent behavioral guidelines, language preferences, or domain expertise that apply to every session.

### @Mention Project Context Injection

When a user message contains `@ProjectName` tokens (e.g. `@RCFlow`), `PromptRouter.handle_prompt()` detects the mentions and resolves them against all configured project directories (`PROJECTS_DIR`). If a mentioned name matches an actual project directory, a context block is prepended to the user message content sent to the LLM:

```
[Context: This message references project "RCFlow" located at /home/user/Projects/RCFlow. All instructions in this message relate to this project.]
```

Key behavior:
- The `@` must appear at the start of the text or after whitespace.
- Only mentions that resolve to existing directories under any configured project directory produce context; unresolved mentions are silently ignored.
- The original user text is preserved — the context is an additional content block, not a replacement.
- The injected context block uses `cache_control: {"type": "ephemeral"}` to avoid polluting prompt caching.
- The client-side buffer receives the original text only (no injected context).

### #Mention Tool Preference Injection

When a user message contains `#ToolName` tokens (e.g. `#claude_code`, `#codex`), `PromptRouter.handle_prompt()` detects the mentions and resolves them against the tool registry. If a mentioned name matches a registered tool (case-insensitive), a tool preference context block is prepended to the user message content sent to the LLM:

```
[Tool preference: The user has explicitly requested that you use the following tool(s) to accomplish this task:
- "claude_code": Claude Code autonomous coding agent...
Prioritize using these tools. If the task can be accomplished with the mentioned tools, use them rather than alternatives.]
```

Key behavior:
- The `#` must appear at the start of the text or after whitespace.
- Tool name matching is case-insensitive: `#Claude` resolves to `claude_code`.
- Only mentions that resolve to registered tools produce context; unresolved mentions are silently ignored.
- Duplicate tool mentions are deduplicated — each tool appears at most once in the context.
- Multiple tool mentions combine with AND logic: `#claude_code #shell_exec` means use both tools.
- The original user text is preserved — the tool context is an additional content block, not a replacement.
- The injected context block uses `cache_control: {"type": "ephemeral"}` to avoid polluting prompt caching.
- Both `@` project and `#` tool mentions can appear in the same message; each produces a separate context block.

The client provides autocomplete suggestions via `GET /api/tools?q=<query>`, triggered when the user types `#` in the input area. The autocomplete shows tool `display_name` values with descriptions to help users identify the right tool. Each tool definition can include an optional `display_name` field for human-readable presentation (e.g. `claude_code` → "Claude Code"); when absent, the `name` field is used as-is.

### $File Reference Context Injection

When a user message contains `$filename` tokens (e.g. `$main.py`, `$config.yaml`), `PromptRouter.handle_prompt()` detects the references and resolves them against the artifact database for the current backend. If a referenced file name matches an artifact (case-insensitive), the file's content or metadata is included as a context block prepended to the user message content sent to the LLM.

For **text files** (extensions in `TEXT_EXTENSIONS`), the full file content is included in a fenced code block:
```
[File: main.py (/home/user/project/main.py)]
```py
<file content>
```
```

For **non-text files** (images, binaries, etc.), metadata is included instead:
```
[File: diagram.png (/home/user/project/diagram.png)
  Type: image/png
  Extension: .png
  Size: 245.3 KB
  Modified: 2026-03-09T14:30:00+00:00
  Note: Binary/non-text file -- content not included]
```

Key behavior:
- The `$` must appear at the start of the text or after whitespace.
- Only references that resolve to existing artifacts produce context; unresolved references are silently ignored.
- File content is capped at 100KB; larger files are truncated with a note.
- Duplicate file references are deduplicated.
- The original user text is preserved -- the file context is an additional content block, not a replacement.
- The injected context block uses `cache_control: {"type": "ephemeral"}` to avoid polluting prompt caching.
- `$` file references are NOT parsed in executor sessions (Claude Code, Codex) -- the text is sent as-is since those executors have their own file reading capabilities.
- `@` project, `#` tool, and `$` file mentions can all appear in the same message; each produces a separate context block.

The client provides autocomplete suggestions via `GET /api/artifacts/search?q=<query>`, triggered when the user types `$` in the input area. The suggestion dropdown shows the file name on the first line and the full file path on the second line, with type-specific icons. Non-text files display a small indicator to show that only metadata (not content) will be included.

---

## Pluggable Tool Definitions

### File Organization

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

### Tool Management Service

RCFlow automatically manages the installation and updating of external CLI tools (Claude Code and Codex). The `ToolManager` service (`src/services/tool_manager.py`) handles detection, installation, and periodic updates using **native binary downloads** — no Node.js or npm required.

**How it works:**

1. On server startup, `ToolManager.ensure_tools()` runs in the lifespan and **detects** tools (does not auto-install). Missing tools are reported; installation happens on-demand when the user requests it via the UI.
2. Detection checks: RCFlow managed directory → system `PATH` → report as not found. The managed directory is resolved by `get_managed_tools_dir()` in `src/paths.py`: `~/.local/share/rcflow/tools/` (Linux) or `%LOCALAPPDATA%\rcflow\tools\` (Windows), falling back to `<install_dir>/managed-tools/` when the home directory is absent or not writable (e.g. service accounts).
3. Tools in the RCFlow managed directory are marked `managed=True`. Tools found on `PATH` are marked `managed=False` (external).
4. A background `asyncio.Task` checks for updates every `TOOL_UPDATE_INTERVAL_HOURS` hours (default 6). Only RCFlow-managed tools are auto-updated.
5. `PromptRouter` gets binary paths from `ToolManager.get_binary_path()` — no binary path settings needed.
6. The UI allows switching between managed and external sources when both are available via `POST /api/tools/{name}/source`.

**Installation methods:**

- **Claude Code**: Native binary downloaded from Anthropic's GCS bucket (`storage.googleapis.com/claude-code-dist-.../claude-code-releases`). SHA256 checksum verified against the official manifest. Binary placed at `~/.local/share/rcflow/tools/claude-code/claude` (Linux) or `%LOCALAPPDATA%\rcflow\tools\claude-code\claude.exe` (Windows).
- **Codex**: Native binary downloaded from GitHub Releases (`github.com/openai/codex/releases`). The release tarball contains a single binary named `codex-<target>` (e.g. `codex-x86_64-unknown-linux-gnu`) which is extracted and renamed to `codex`. On Windows, the `.exe` is downloaded directly and renamed to `codex.exe`. The responses API proxy is built into the main binary as a subcommand. Binary placed at `~/.local/share/rcflow/tools/codex/codex` (Linux) or `%LOCALAPPDATA%\rcflow\tools\codex\codex.exe` (Windows).

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
| `default_permission_mode`  | select      | yes          | —                      | CLI --permission-mode: interactive (default, enables interactive prompts), bypassPermissions, allowEdits, plan |
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
| `provider`       | select | yes          | Auth method: Global / OpenAI / ChatGPT (Subscription) |
| `codex_api_key`  | secret | yes          | OpenAI API key (visible when provider=openai) |
| `model`          | string | no           | Model name for Codex sessions              |
| `approval_mode`  | select | no           | Tool-call approval (full-auto / yolo)      |
| `timeout`        | string | yes          | Process timeout in seconds (default 600)   |

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

**Config overrides:** When a managed tool has settings configured, `PromptRouter` reads them at executor creation time and passes non-empty values as `config_overrides` to the executor constructor. These overrides are merged on top of the tool definition's `executor_config` when building subprocess commands.

### Tool Definition Fields

| Field             | Type   | Required | Description                                           |
|-------------------|--------|----------|-------------------------------------------------------|
| `name`            | string | yes      | Unique tool identifier, sent to LLM                   |
| `display_name`    | string | no       | Human-readable name shown in UI (defaults to `name`)  |
| `description`     | string | yes      | Human/LLM-readable description of what the tool does  |
| `version`         | string | no       | Semantic version of the tool definition                |
| `os`              | list   | no       | OS restriction: subset of `["windows","linux","darwin"]`. Empty = all platforms. Tools are skipped at load time if the current OS is not in the list. |
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

Both SQLite and PostgreSQL are supported. The ORM uses `sa.JSON` columns (maps to JSONB on PostgreSQL, TEXT with JSON serialization on SQLite). UUIDs are stored as CHAR(32) on SQLite. Timestamps are stored as ISO 8601 strings on SQLite.

### Tables

```sql
-- Logical schema (PostgreSQL syntax for illustration; SQLAlchemy ORM handles dialect differences)
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

-- Tasks (persistent, cross-session work items)
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backend_id VARCHAR(36) NOT NULL DEFAULT '',
    title VARCHAR(300) NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'todo',  -- 'todo', 'in_progress', 'review', 'done'
    source VARCHAR(20) NOT NULL DEFAULT 'user',  -- 'user' or 'ai'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_tasks_backend_id ON tasks(backend_id);
CREATE INDEX ix_tasks_status ON tasks(status);

-- Many-to-many: tasks ↔ sessions
CREATE TABLE task_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    attached_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(task_id, session_id)
);
CREATE INDEX ix_task_sessions_task_id ON task_sessions(task_id);
CREATE INDEX ix_task_sessions_session_id ON task_sessions(session_id);

-- Discovered file artifacts (markdown, text files, etc.)
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backend_id VARCHAR(36) NOT NULL DEFAULT '',
    file_path TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_extension VARCHAR(20),
    file_size BIGINT NOT NULL DEFAULT 0,
    mime_type VARCHAR(100),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    modified_at TIMESTAMPTZ NOT NULL,
    session_id UUID REFERENCES sessions(id),
    UNIQUE(backend_id, file_path)
);
CREATE INDEX ix_artifacts_backend_id ON artifacts(backend_id);
CREATE INDEX ix_artifacts_session_id ON artifacts(session_id);
```

---

## Configuration

All configuration is via environment variables, loaded from a `settings.json` file. Environment variables set in the shell take precedence over values in `settings.json`. On first run, if a legacy `.env` file exists it is automatically migrated to `settings.json`.

| Variable                | Required | Default         | Description                          |
|-------------------------|----------|-----------------|--------------------------------------|
| `RCFLOW_HOST`           | no       | `0.0.0.0`       | Server bind address                  |
| `RCFLOW_PORT`           | no       | `53890` (Linux) / `53891` (Windows) | Server port                          |
| `RCFLOW_API_KEY`        | yes      |                 | API key for WebSocket auth           |
| `RCFLOW_BACKEND_ID`     | no       | auto-generated  | Unique backend instance ID (UUID). Auto-generated and persisted to `settings.json` on first run. Used to isolate sessions per backend when multiple backends share one database. |
| `SSL_CERTFILE`          | no       |                 | Path to TLS certificate (enables WSS when both cert+key set) |
| `SSL_KEYFILE`           | no       |                 | Path to TLS private key (enables WSS when both cert+key set) |
| `DATABASE_URL`          | no       | `sqlite+aiosqlite:///./data/rcflow.db` | Database connection string (SQLite or PostgreSQL) |
| `LLM_PROVIDER`          | no       | `anthropic`     | LLM provider: `anthropic`, `bedrock`, or `openai` |
| `ANTHROPIC_API_KEY`     | cond.    |                 | Anthropic API key (required when `LLM_PROVIDER=anthropic`) |
| `ANTHROPIC_MODEL`       | no       | `claude-sonnet-4-20250514`| Anthropic model ID (use Bedrock model IDs when `LLM_PROVIDER=bedrock`) |
| `AWS_REGION`            | no       | `us-east-1`     | AWS region (used when `LLM_PROVIDER=bedrock`) |
| `AWS_ACCESS_KEY_ID`     | no       |                 | AWS access key ID (optional if using IAM roles/instance profiles) |
| `AWS_SECRET_ACCESS_KEY` | no       |                 | AWS secret access key (optional if using IAM roles/instance profiles) |
| `OPENAI_API_KEY`        | cond.    |                 | OpenAI API key (required when `LLM_PROVIDER=openai`) |
| `OPENAI_MODEL`          | no       | `gpt-4o`        | OpenAI model ID (e.g. gpt-4o, gpt-4.1, o3) |
| `STT_PROVIDER`          | no       | `wispr_flow`    | STT provider name                    |
| `STT_API_KEY`           | yes      |                 | STT provider API key (Wispr Flow)    |
| `TTS_PROVIDER`          | no       | `none`          | TTS provider name                    |
| `TTS_API_KEY`           | no       |                 | TTS provider API key                 |
| `PROJECTS_DIR`          | no       | `~/Projects`    | Comma-separated list of project directories (used in system prompt, path resolution, and `/api/projects` endpoint) |
| `TOOLS_DIR`             | no       | `./tools`       | Path to tool definitions directory   |
| `CODEX_API_KEY`         | no       |                 | OpenAI API key for Codex CLI         |
| `SUMMARY_MODEL`         | no       | auto (per provider) | Fast model for summaries/titles. Defaults to `claude-haiku-4-5-20251001` (Anthropic) or `{region}.anthropic.claude-haiku-4-5-v1:0` (Bedrock, region prefix derived from `AWS_REGION`) |
| `GLOBAL_PROMPT`         | no       |                 | Custom instructions appended to the system prompt for every session |
| `SESSION_INPUT_TOKEN_LIMIT` | no   | `0` (unlimited) | Max total input tokens (LLM + tool) per session. `0` = no limit. |
| `SESSION_OUTPUT_TOKEN_LIMIT`| no   | `0` (unlimited) | Max total output tokens (LLM + tool) per session. `0` = no limit. |
| `ARTIFACT_INCLUDE_PATTERN` | no    | `*.md`          | Glob pattern for files to include in artifact extraction (case-insensitive) |
| `ARTIFACT_EXCLUDE_PATTERN` | no    | `node_modules/**,...` | Comma-separated glob patterns to exclude from extraction |
| `ARTIFACT_AUTO_SCAN`    | no       | `true`          | Auto-extract artifacts from messages in real time during session execution |
| `ARTIFACT_MAX_FILE_SIZE`| no       | `5242880`       | Max file size in bytes for artifact content viewing (default 5 MB) |
| `LOG_LEVEL`             | no       | `INFO`          | Logging level                        |

### Remote Configuration (Client-Side Editing)

The server exposes `GET /api/config` and `PATCH /api/config` endpoints that allow connected clients to view and edit a subset of server settings remotely. This enables users to configure API keys, provider selection, model IDs, and other options from the Flutter client without manual `settings.json` file editing.

**Config option metadata schema** (returned by `GET /api/config`):

| Field              | Type   | Description                                        |
|--------------------|--------|----------------------------------------------------|
| `key`              | string | Setting name (e.g. `LLM_PROVIDER`)                 |
| `label`            | string | Human-readable label                               |
| `type`             | string | `"string"`, `"textarea"`, `"select"`, `"boolean"`, or `"secret"` |
| `value`            | any    | Current value (masked for secrets — last 4 chars)   |
| `options`          | list   | Available choices (for `select` type only)          |
| `group`            | string | Grouping category (LLM, STT, TTS, Executors, etc.) |
| `description`      | string | Help text                                          |
| `required`         | bool   | Whether the field is required                      |
| `restart_required` | bool   | Whether changing requires a server restart          |

**Configurable groups**: LLM, Prompt, STT, TTS, Claude Code, Codex, Paths, Session Limits, Logging. Groups are rendered as collapsible sections in the client UI.

**Excluded from remote config** (for security): `RCFLOW_API_KEY`, `RCFLOW_HOST`, `RCFLOW_PORT`, `SSL_CERTFILE`, `SSL_KEYFILE`, `DATABASE_URL`.

**Hot-reload**: When config is updated via `PATCH /api/config`, the server persists changes to `settings.json` and recreates the LLM client, STT provider, and TTS provider with the new settings. The old LLM client is gracefully closed.

**Client UI**: The Flutter client shows a "Settings" button on each connected worker card. Tapping it opens a dialog (desktop) or bottom sheet (mobile) that renders a dynamic form based on the server's config schema. Fields are grouped by section and rendered as text fields, multi-line text areas, dropdowns, switches, or password fields depending on type.

---

## Project Structure

```
RCFlow/
├── CLAUDE.md                    # Claude Code instructions
├── Design.md                    # This file — project design document
├── pyproject.toml               # Project metadata and dependencies (uv)
├── settings.json                 # Server configuration (JSON, auto-created on first run)
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
│   │   ├── llm.py               # LLM client (Anthropic, Bedrock, OpenAI)
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
│   │   ├── builder.py           # PromptBuilder class (uses Jinja2)
│   │   └── templates/
│   │       └── system_prompt.j2    # System prompt in Jinja2 format
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
| Background mode | systemd service | GUI window (`rcflow gui`) or system tray (`rcflow tray`) |
| Auto-start | systemd enable | Registry `HKCU\...\Run` key |

### Database

SQLite is the default database — no external server required. The database file is created automatically at the path specified in `DATABASE_URL` (default: `./data/rcflow.db`). SQLite WAL mode and foreign keys are enabled automatically.

For heavier workloads or multi-backend deployments, PostgreSQL is supported. Install the `postgres` extra (`pip install rcflow[postgres]` or `uv pip install rcflow[postgres]`) and set `DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/rcflow`.

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

### Production (Windows — GUI + System Tray)

`rcflow gui` (or `rcflow tray`, which delegates to it) launches a combined tkinter window and system tray application (`src/gui.py`). This is the default mode for frozen Windows builds. The server runs as a subprocess — closing the window minimizes to the system tray; double-clicking the tray icon restores the window. "Quit" from the tray stops the server and exits.

**Features:**
- **Server settings** — IP address and port text fields, pre-populated from `settings.json` configuration.
- **Start/Stop button** — Starts the server as a child subprocess (`rcflow run` with `CREATE_NO_WINDOW`). Settings fields are disabled while the server is running.
- **Status indicator** — Shows "Running" (green), "Stopped" (gray), "Starting..."/"Stopping..." (yellow), or error messages (red).
- **Instance details panel** — Displays bound address, uptime (HH:MM:SS), active session count, and backend ID. Session count and backend ID are fetched from the `/api/info` endpoint every 5 seconds.
- **Log output** — Scrollable dark-themed text area with real-time display of the server subprocess stdout. ERROR/CRITICAL lines are highlighted red, WARNING lines orange. Auto-scrolls when at the bottom; capped at 5,000 lines.
- **System tray icon** — Shows server status. Right-click menu: status line, "Open" (restores window), "Start with Windows" toggle (Windows registry autostart), "Quit".

**Architecture:**
- The GUI process spawns `rcflow run` as a child subprocess with stdout/stderr piped. A reader thread consumes subprocess output and feeds it into a `queue.Queue` that the tkinter main loop drains into the text widget.
- `pystray` runs in a background daemon thread; tkinter runs in the main thread.
- Closing the window (X button) calls `root.withdraw()` to hide the window — the tray icon remains and the server keeps running.
- Double-clicking the tray icon calls `root.deiconify()` to restore the window.
- "Quit" from the tray menu terminates the server subprocess (graceful with 10s timeout, then kill), stops the tray icon, and destroys the tkinter window.
- If `pystray`/`Pillow` are not installed, the GUI still works but without a tray icon — closing the window exits the application.
- Port availability is checked before starting (same socket-bind check as `rcflow run`).
- Environment variables `RCFLOW_HOST` and `RCFLOW_PORT` are set in the subprocess environment so the server picks up the GUI-configured values.
- The server auto-starts on application launch.

**Autostart:** Registry key `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\RCFlow` stores `"<exe>" gui`. The `rcflow tray` command is kept for backwards compatibility but delegates to `run_gui()`.

**Icon:** `assets/tray_icon.ico` (generated by `scripts/generate_icon.py`, same design as the client app icon). Copied into the bundle root as `tray_icon.ico`. The frozen build loads it from `{install_dir}/tray_icon.ico`; dev builds look in `{project_root}/assets/tray_icon.ico`. Fallback: generates a blue rounded square with "RC" text.

### Production (systemd — Linux)

```ini
# systemd/rcflow.service
[Unit]
Description=RCFlow Action Server
After=network.target

[Service]
Type=simple
User=rcflow
WorkingDirectory=/opt/rcflow
# Settings loaded from /opt/rcflow/settings.json by the application
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

## Bundling & Distribution

RCFlow is distributed as a self-contained package built with PyInstaller. End users download a single archive, run an install script, and get RCFlow running as a system service.

### Build

| Target | Command | Output |
|--------|---------|--------|
| Current platform (backend) | `just bundle` | `dist/rcflow-{version}-{platform}-{arch}.tar.gz` (or `.zip`) |
| Linux backend (.deb) | `just bundle-linux-backend` | `dist/rcflow_{version}_{deb_arch}.deb` |
| Linux client | `just bundle-linux-client` | `dist/rcflowclient-linux-{arch}.tar.gz` |
| Windows client | `just bundle-windows-client` | `dist/rcflowclient-windows-x64.zip` |
| Windows backend (installer) | `just bundle-windows-backend` | `dist/rcflow-{version}-x64-setup.exe` |

Backend build script: `scripts/bundle.py`. Requires PyInstaller (`uv add --dev pyinstaller`). Cross-compilation is not supported — build on the target platform. Client targets build the Flutter desktop app (`rcflowclient`) for the respective platform.

The `bundle-linux-backend` target builds a `.deb` package that installs RCFlow to `/opt/rcflow` with a systemd service. Requires `dpkg-deb` (standard on Debian/Ubuntu). Install with `sudo dpkg -i dist/rcflow_*.deb`.

The `bundle-windows-backend` target builds a windowed (no console) executable with GUI + system tray support and compiles a `setup.exe` installer using Inno Setup 6. Requires Inno Setup 6 installed on the build machine (`iscc.exe` on PATH or in default location).

### Bundle Contents

The archive contains: the PyInstaller executable + runtime (`_internal/`), tool JSON definitions (`tools/`), alembic migrations (`migrations/`), prompt templates (`templates/`), install/uninstall scripts, systemd service template (Linux), tray icon (Windows), and a `VERSION` file.

### Installation

**Linux (.deb):** `sudo dpkg -i rcflow_*.deb` — installs to `/opt/rcflow/`, creates `rcflow` system user, sets up systemd service, generates `settings.json` on first server start. Remove with `sudo apt remove rcflow` (or `--purge` to also delete data).

**Linux (tar.gz/manual):** `sudo ./install.sh` — installs to `/opt/rcflow/`, creates `rcflow` system user, sets up systemd service, generates `settings.json` with random API key, runs migrations.

**Windows (zip/manual):** `.\install.ps1` (as Administrator) — installs to `C:\RCFlow\`, downloads NSSM, registers Windows Service, generates `settings.json` with random API key, runs migrations, creates firewall rule.

**Windows (setup.exe):** Run the Inno Setup installer — installs to `%PROGRAMFILES%\RCFlow\` (user-level, no admin required), runs migrations, optionally registers "Start with Windows" autostart, and optionally launches the GUI. `settings.json` is generated automatically on first server start. The GUI runs the server as a background subprocess and provides a window with server controls, live logs, and a system tray icon.

Both scripts are idempotent — safe to run again for upgrades. Existing `settings.json` and `data/` are preserved.

### Path Resolution

The `src/paths.py` module provides functions that resolve paths correctly in both development (source) and frozen (PyInstaller) environments:

- `get_bundle_dir()` — `sys._MEIPASS` when frozen, project root otherwise
- `get_install_dir()` — directory containing the executable
- `get_default_tools_dir()` — `{install_dir}/tools`
- `get_migrations_dir()` — `{install_dir}/migrations` when frozen
- `get_templates_dir()` — `{_MEIPASS}/templates` when frozen
- `get_alembic_ini()` — `{install_dir}/alembic.ini` when frozen

### CLI Commands

The `rcflow` entry point supports subcommands relevant to bundled operation:

- `rcflow` / `rcflow run` — Start the server (headless)
- `rcflow gui` — Run with GUI window + system tray (default on frozen Windows builds)
- `rcflow tray` — Alias for `rcflow gui` (backwards compatibility)
- `rcflow migrate [revision]` — Run database migrations (default: `head`)
- `rcflow version` — Print version
- `rcflow info` — Print server configuration (bind address, port, WSS status)
- `rcflow api-key` — Print the current API key
- `rcflow set-api-key <value>` — Save a new API key

On frozen Windows builds, the default command (no subcommand) launches `gui` mode.

---

## Future Considerations
- **Sandboxed execution**: Docker-based tool execution for untrusted tools
- **Python callable tools**: Another executor type for direct Python function invocation
- **Hot-reload tools**: Watch the `tools/` directory for changes without restart
- **Multi-user support**: JWT-based auth with user accounts and per-user sessions
- **Voice input on output channels**: Bidirectional audio for full voice conversation
- **Audio format negotiation**: Let client specify preferred audio codec on connect
