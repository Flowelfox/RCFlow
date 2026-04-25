---
updated: 2026-04-26
---

# Architecture

High-level system architecture, request lifecycle, and the Flutter client's multi-platform / multi-pane / multi-worker design.

**See also:**
- [HTTP API](http-api.md) — REST endpoints
- [WebSocket API](websocket-api.md) — streaming protocol
- [Sessions](sessions.md) — session lifecycle and concurrency

---

## High-Level Flow

```
┌─────────────────┐
│  Mobile Client   │
│  (or any client) │
└────┬──────────┬──┘
     │          │
     ▼          ▼
┌──────────┐  ┌───────────┐
│/ws/input │  │/ws/output │
│  /text   │  │  /text    │
└────┬─────┘  └─────▲─────┘
     │               │
     ▼               │
┌─────────────────────────┐
│     Prompt Router       │
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│   LLM Provider          │
│   (Anthropic API,       │
│    AWS Bedrock, or      │
│    OpenAI)              │
│   + Tool Definitions    │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Tool Executor         │
│  ┌───────────────────┐  │
│  │ Shell Executor    │  │
│  │ HTTP API Executor │  │
│  │ Claude Code Exec. │  │
│  └───────────────────┘  │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Session Manager       │───────┐
│   (buffer, history,     │       │
│    subscribe/unsub)     │       ▼
└────────────┬────────────┘  /ws/output/text
             │
             ▼
┌─────────────────────────┐
│   Database               │
│   (SQLite / PostgreSQL)  │
└──────────────────────────┘
```

## Request Lifecycle

```
1. Client connects to /ws/input/text
2. (Optional) Client uploads files via POST /api/uploads → receives attachment_id(s)
3. Text prompt (+ optional attachment IDs) is routed to the Prompt Router
4. Prompt Router creates or resumes a Session; resolves attachment IDs from AttachmentStore
5. Prompt + attachment content blocks + tool definitions + session context sent to LLM provider
   (Anthropic API, AWS Bedrock, or OpenAI, streaming)
6. LLM responds with text and/or tool_use blocks
7. For each tool_use → Tool Executor runs the tool (shell command or HTTP API call)
8. Tool output fed back to LLM for further reasoning (agentic loop)
9. All LLM text output streams to /ws/output/text chunk-by-chunk
10. When the LLM finishes (no more tool calls), the session remains active.
    If the LLM included [SessionEndAsk], the server pushes a session_end_ask
    message; the client shows a confirmation card. The session only ends
    when the user explicitly confirms or sends POST /api/sessions/{id}/end.
11. Completed sessions are archived from memory to the database
```

## Flutter Client — Multi-Platform

The Flutter client (`rcflowclient/`) runs on Android and Windows desktop from a single codebase. Platform-conditional behavior:

- **Foreground service** (`flutter_foreground_task`): Android/iOS only. `ForegroundServiceHelper.init()`, `.start()`, and `.stop()` are no-ops on desktop (guarded by `Platform.isAndroid || Platform.isIOS`). The service is **not** started automatically on connect — see *Mobile Lifecycle Hibernation* below. `.stop()` is still invoked defensively so any legacy build that left it running is torn down.
- **Mobile lifecycle hibernation**: On Android/iOS, `RCFlowApp` installs a `WidgetsBindingObserver`. When the app is `paused`/`hidden`/`detached`, a 30-second grace timer starts; if it fires before the app resumes, `AppState.hibernateForBackground()` tears down every worker's WebSocket (`WorkerConnection.hibernate()`) so the device radio and CPU can sleep. Cached session lists, server info, and the set of subscribed session IDs are preserved so the UI keeps showing last-known state. On `resumed`, `AppState.wakeFromBackground()` reconnects every worker that was hibernating and resubscribes to its previously-subscribed sessions — the backend replays the per-subscriber output buffer on every `subscribe`, so the active pane catches up without any extra endpoint. Manually-disconnected workers stay disconnected across hibernation (no auto-reconnect on resume). Desktop is a no-op; `_isMobile` guards the observer registration. Connection toasts are suppressed when the worker is hibernating so users don't see spurious "Lost Connection" notifications on every app switch.
- **Keyboard input**: On desktop, Enter sends a message and Shift+Enter inserts a newline. On mobile, `TextInputAction.send` + `onSubmitted` is used (standard mobile keyboard behavior).
- **Responsive layout**: At `>700px` width, a persistent 280px session sidebar appears on the left. At narrower widths (mobile), sessions are shown via a modal bottom sheet.
- **Settings**: Multi-section settings menu (`lib/ui/widgets/settings_menu.dart`). On desktop, shown as a two-column dialog (160px sidebar nav + content) via the sidebar's bottom "Settings" button. On mobile, shown as a `DraggableScrollableSheet` bottom sheet with all sections in a scrollable list. Sections: Workers (summary of connected/total count, "Manage Workers" button to open Workers screen), Appearance (theme mode, font size, compact mode), Notifications (toast notification toggles), About (version info + update check UI). Settings persisted via `SharedPreferences` through `SettingsService`.
- **Auto-update**: Client self-update discovery via `UpdateService` (`lib/services/update_service.dart`). Fetches the latest release from the GitHub Releases API (`https://api.github.com/repos/Flowelfox/RCFlow/releases/latest`) using `HttpUpdateFetcher` (`lib/services/update_fetcher.dart`). Version is normalized by stripping any leading `v` and `+build` suffix. The 24-hour cache TTL is enforced via `settings.lastUpdateCheck`; the cached latest version is stored in `settings.cachedLatestVersion`. On each cold start, `main.dart` persists `PackageInfo.version.split('+').first` to `settings.currentVersion`, then calls `appState.initAsync()` (which fires `maybeCheck()` without blocking startup). `AppState` exposes `updateService` as a getter; `restoreCachedState()` is called synchronously in the constructor so the first frame can reflect a known-available update. The **About** section in Settings (now a `StatelessWidget`) reads `settings.currentVersion` directly and wraps the update state area in a `ListenableBuilder` scoped to `updateService` — showing a spinner while checking, an "update available" row with a direct `url_launcher` link when a newer version is found, an error row with retry when the check fails, and an "Up to date / Check again" row otherwise. The session-list sidebar shows an `_UpdateBanner` widget above the settings divider when `updateService.showBanner` is true; tapping the banner opens Settings and a dismiss (✕) button calls `dismissCurrentUpdate()`, which persists the dismissed version to `settings.dismissedUpdateVersion` so the banner stays hidden unless a still-newer release appears.

## Split View (Desktop)

On wide layouts (>700px), the main content area supports multiple simultaneous session panes arranged in a recursive binary split tree.

**Architecture**:
- **SplitNode** (`lib/models/split_tree.dart`): Sealed class — `PaneLeaf` (single pane) or `SplitBranch` (two children with axis + ratio). Pure functions for split/close/query operations.
- **PaneState** (`lib/state/pane_state.dart`): Per-pane `ChangeNotifier` extracted from AppState. Manages session ID, messages, streaming queue, pagination, and session lifecycle for a single pane. References shared state via the `PaneHost` interface.
- **AppState** (`lib/state/app_state.dart`): Keeps shared state (workers, merged session list) plus a `Map<String, PaneState>` and the `SplitNode` tree root. Manages `Map<String, WorkerConnection>` for multi-server connections. Routes incoming WebSocket messages to pane(s) by `session_id`. Manages split/close operations, active pane tracking, and worker CRUD.

**Message routing**: Output handlers receive `(msg, PaneState)` instead of `(msg, AppState)`. AppState extracts `session_id` from incoming messages and dispatches to matching pane(s). `session_list` is handled at AppState level. Ack routing uses a `pendingAck` flag on PaneState.

**UI widgets**:
- `SplitView` — recursively renders the split tree; leaves become `SessionPane` widgets, branches become `Row`/`Column` with `ResizableDivider`.
- `SessionPane` — wraps `OutputDisplay` + `InputArea` with a `PaneHeader` (shown only in multi-pane mode). Tap to set as active pane. Includes a resizable right-panel area with **Todo** and **Project** bookmark tabs.
- `ResizableDivider` — draggable 6px divider with hover/drag highlight and cursor change.
- `PaneHeader` — 32px bar with session title and close button.
- `TodoPanel` — right-side panel showing the active session's `TodoWrite` task list.
- `ProjectPanel` (`lib/ui/widgets/project_panel.dart`) — right-side panel with two modes:
  - **Global** (`main_project_path == null`): prompts the user to attach a project via the `@ProjectName` picker chip above the input field.
  - **Project** (`main_project_path != null`): shows the attached project name, its git worktrees (create / merge / remove / set-active), and a scrollable list of project artifacts fetched from `GET /api/projects/{name}/artifacts`. Both sections load in parallel and auto-refresh when `main_project_path` or `workerId` changes. Sections are **collapsible** (chevron toggle) and **reorderable** (up/down arrows in each section header). Panel key: `"project"`. Icon: `folder_outlined`.
- **Project chip** (`_ProjectChip` in `lib/ui/widgets/input_area.dart`) — displayed above the message input field when a project is attached. Styled like the Worker chip. Shows the folder name and an × to clear. Turns red with a tooltip when `project_name_error` is set. Users attach a project by typing `@ProjectName` in the input — when they confirm (via overlay selection, Space, or Enter after a complete name), the `@name` text is erased and the chip is populated instead.
- **Worktree chip** (`_WorktreeChip` in `lib/ui/widgets/input_area.dart`) — displayed below the project chip, only when `session_id == null` (new chat) and a project is selected. Allows the user to pre-select an active worktree before sending the first message. Shows a "Worktree ▾" button when nothing is selected, or the selected worktree name with an accent color and an × to clear. Tapping opens a `showMenu` dropdown that lists all available worktrees for the selected project (fetched lazily via `GET /api/projects/{name}/worktrees`, cached per `workerId:projectPath`). Includes a "No worktree (default)" option to clear the selection. The selected path is stored as `PaneState._pendingWorktreePath` and sent as `selected_worktree_path` in the first `prompt` WS message; it is cleared when the project chip is cleared or when a new chat starts.
- `StatisticsPane` (`lib/ui/widgets/statistics_pane.dart`) — right-panel bookmark within a session pane for time-series telemetry charts and per-session summaries:
  - **Filter bar**: zoom-level chips (`Minute` / `Hour` / `Day`) and a manual refresh button. Changing zoom resets the time window to the zoom's default duration and triggers an immediate fetch.
  - **Charts section**: six line charts rendered via `fl_chart` — Tokens Sent, Tokens Received, Avg LLM Duration (ms), Avg Tool Duration (ms), Turn Count, Tool Call Count. Interactive tooltips on hover/tap with per-bucket value and timestamp. State managed by `StatisticsPaneState` (`lib/state/statistics_pane_state.dart`).
  - **Session summary card** (visible when a session is selected): stat pills (total turns, tokens, avg/p95 LLM and tool latency, error rate) and a scrollable per-turn table with TTFT, LLM duration, token counts, tool call count, and interrupted flag.
  - Data fetched from `GET /api/telemetry/timeseries` (charts) and `GET /api/telemetry/sessions/{id}/summary` (session card). Models defined in `lib/models/telemetry.dart` (`ZoomLevel`, `BucketPoint`, `TurnSummary`, `SessionTelemetrySummary`). Chart widget factored into `lib/ui/widgets/statistics_panel/telemetry_chart.dart`.
- `WorkerStatsPane` (`lib/ui/widgets/worker_stats_pane.dart`) — worker-level aggregate statistics shown as a full-screen dialog (opened via right-click → Stats on a worker header):
  - Same time-series charts and zoom controls as `StatisticsPane`, using the global rollup timeseries (no `session_id` filter).
  - **Worker summary card**: stat pills for session count, total turns, in/out tokens, tool calls, avg/p95 LLM latency, avg tool latency, and error rate. Top-tools table shows the ten most-called tools with average duration.
  - Data fetched from `GET /api/telemetry/timeseries` (no session_id) and `GET /api/telemetry/worker/summary`. Model: `WorkerTelemetrySummary` in `lib/models/telemetry.dart`. Self-contained widget — takes a `WorkerConnection` directly, no `PaneState` dependency.

**Edge cases**: Last pane close resets to home (tree always has >= 1 leaf). Same session in multiple panes receives messages independently. Reconnection re-subscribes all pane sessions. Mobile layout remains single-pane, using `activePane` with a `ChangeNotifierProvider`.

## Terminal Sessions (Sidebar Integration)

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

## Workers (Multi-Server)

The client can connect to multiple RCFlow servers simultaneously. Each server connection is a "Worker". Each backend instance is identified by a unique `RCFLOW_BACKEND_ID` (auto-generated UUID, persisted to `settings.json`). When multiple backends share the same database, sessions are isolated per backend via the `backend_id` column on the `sessions` table — each backend only sees and manages its own sessions.

**Data model**:
- `WorkerConfig` (`lib/models/worker_config.dart`): Client-side configuration with `id` (UUID, generated locally), `name`, `host`, `apiKey`, `useSSL`, `autoConnect`, and `sortOrder`. Serialized to/from JSON. ID generated using `dart:math` Random.secure.
- `SessionInfo.workerId`: Every session is tagged with the worker it belongs to. Set by the client when parsing server responses.

**Persistence** (`SettingsService`):
- `rcflow_workers`: JSON array of `WorkerConfig` objects.
- `rcflow_last_session_per_worker`: JSON map `{workerId: sessionId}`.
- `rcflow_cached_sessions_per_worker`: JSON map `{workerId: jsonEncodedSessionList}`.
- `rcflow_draft_session_{key}` / `rcflow_draft_session_{key}_ts`: Per-session draft text and write timestamp (ms since epoch). Key is a session UUID for real sessions or `new_{workerId}` for the new-session pane. See **Draft persistence** below.
- Legacy single-server keys (`rcflow_host`, `rcflow_api_key`, `rcflow_use_ssl`) kept for migration.
- `rcflow_current_version`: installed version string (e.g. `"1.38.0"`), written at startup.
- `rcflow_last_update_check`: ISO-8601 UTC timestamp of the last successful update check.
- `rcflow_cached_latest_version`: latest version returned by the update server.
- `rcflow_dismissed_update_version`: version the user has dismissed from the banner.

**Draft persistence** (drafts per session):

Each session pane maintains its own unsent message draft. Drafts survive session switches, app restarts, and multiple clients.

- `PaneState._draftProvider`: A `String Function()` callback registered by `InputArea` in `initState` (unregistered in `dispose`). Allows `PaneState` to read the live `TextEditingController` text synchronously at switch time without owning the controller.
- `PaneState._lastLoadedDraft`: The text last *loaded* (not typed) into the input. Guards against multi-pane overwrites: a pane that loaded a draft and never typed will not save back an unchanged snapshot, so a sibling pane's live draft is never clobbered.
- Save triggers: `switchSession()`, `goHome()`, `startNewChat()` call `_saveDraftIfChanged()` synchronously before clearing state. `InputArea._onTextChanged()` starts an 800 ms debounce timer (`_draftTimer`) that calls `PaneState.triggerDraftSave()` when the user pauses.
- Two-phase load on session switch: (1) local `SettingsService` cache — instant, no network; (2) backend `GET /sessions/{id}/draft` — authoritative, wins if `updated_at` is newer than local cache timestamp. Result delivered to `InputArea` via the existing `setPendingInputText` / `consumePendingInputText` mechanism.
- New-session pane drafts are local-only (no session ID to write against the backend). Key: `new_{workerId}`. Cleared in `handleAck()` when the first prompt creates the real session.
- Backend draft is cleared (set to `""`) when a session is deleted (CASCADE on `drafts.session_id → sessions.id`).

**WorkerConnection** (`lib/services/worker_connection.dart`): Wraps one `WebSocketService` instance with per-worker lifecycle. Enum `WorkerConnectionStatus`: `disconnected`, `connecting`, `connected`, `reconnecting`. Manages its own session list (tagged with `workerId`), reconnection loop (3 retries, 10s delay), and session subscriptions. Routes `session_list` and `session_update` messages internally; forwards all other messages to AppState via callbacks.

**AppState refactor**:
- Replaced single `WebSocketService _ws` with `Map<String, WorkerConnection> _workers` keyed by `config.id`.
- Connection state is aggregated: `connected` = any worker connected, `allConnected` = all auto-connect workers connected.
- Session list merges all workers' sessions sorted by `createdAt` desc. `sessionsByWorker` provides grouped access.
- Worker CRUD: `addWorker()`, `updateWorker()`, `removeWorker()`, `connectWorker()`, `disconnectWorker()`.
- `PaneHost` interface: replaced `WebSocketService get ws` with `wsForWorker(String workerId)` and `workerIdForSession(String sessionId)`.
- Foreground service is no longer auto-started on connect; mobile lifecycle hibernation (see *Flutter Client — Multi-Platform*) replaces the always-on wake lock with a connect-on-foreground model. `_onRegistryChanged` still calls `ForegroundServiceHelper.stop()` defensively so legacy installs don't leak the service.

**PaneState routing**:
- Each pane tracks `_workerId` (set on `switchSession()`, `handleAck()`, or `setTargetWorker()`).
- All WS/REST calls (sendPrompt, cancelSession, endSession, etc.) route through `_ws` getter which resolves to `_host.wsForWorker(_workerId ?? defaultWorkerId)`.
- New chats: `setTargetWorker()` called from the worker selector chip in the input area.

**Migration** (`main.dart`): On first launch after upgrade, if `workers` list is empty and legacy `apiKey` is non-empty, creates a single worker from the legacy settings with `autoConnect: true`.

**Workers screen** (`lib/ui/screens/workers_screen.dart`): Desktop dialog / mobile sheet for worker CRUD. Shows each worker as a card with name, host, status dot, auto-connect badge, and Edit/Remove/Connect buttons. Add/Edit sub-dialog with name, host, API key (obscured), SSL toggle, and auto-connect toggle.

**Session tree view** (`lib/ui/widgets/session_panel.dart`): Sessions grouped by worker in expandable sections. Each group has a header with worker name, session count, and colored status dot. Disconnected workers show cached sessions dimmed. Bottom bar has "Workers" and "Settings" links.

**Input area worker selector**: When starting a new chat with multiple connected workers, a chip above the input field shows the target worker name with a dropdown to switch.

## Add-to-Client Deep Link

The worker GUI ("Add to Client" button, settings panel on Windows/macOS and the macOS menu-bar icon) emits a `rcflow://add-worker` URL that the installed Flutter client handles:

```
rcflow://add-worker?host=<host>&port=<port>&token=<urlencoded>&ssl=<0|1>&name=<urlencoded>
```

- `host`, `port`, `token` — required.
- `ssl=1` if the worker's WSS toggle is on.
- `name` — optional suggested label; the worker auto-fills this with `socket.gethostname()` so the client pre-fills a sensible worker name the user can edit.

**URL-scheme registration** (client install):
- Android — `<intent-filter>` on `MainActivity` (`rcflowclient/android/app/src/main/AndroidManifest.xml`).
- iOS / macOS — `CFBundleURLTypes` in each `Info.plist`.
- Windows — Inno Setup `[Registry]` keys under `HKCU\Software\Classes\rcflow` (`scripts/inno_setup_client.iss`), **plus** the Flutter Windows runner writes the same keys on every startup (`rcflowclient/windows/runner/main.cpp` → `RegisterRcflowUrlScheme`). The runner-side write covers dev builds (`flutter run`, `flutter build windows`) that bypass the installer, and always points the scheme at the most recently launched client binary.
- Linux — `.desktop` file with `MimeType=x-scheme-handler/rcflow;` (`rcflowclient/linux/rcflow-client.desktop`, installed to `/usr/share/applications/` by `just bundle-linux-client`).

**Client flow** (`lib/services/deep_link_service.dart`):
1. `DeepLinkService.init()` wraps the `app_links` plugin. On cold start it retrieves the initial URI via `getInitialLink()`; while the app is running, it subscribes to `uriLinkStream`.
2. `AddWorkerLink.tryParse(Uri)` validates the scheme/host and query params and returns a typed record.
3. `_RCFlowAppState._handleAddWorkerLink` calls `AppState.findWorkerByHostPortToken(host, port, token)`. If a match exists, the app shows an "Already added as '<name>'" alert and makes no changes.
4. Otherwise it pre-fills a `WorkerConfig` (new id via `WorkerConfig.generateId()`, name seeded from the URL) and opens `showWorkerEditDialog(prefilled: …)`. The dialog remains in "Add" mode so the user can review fields (e.g. swap `0.0.0.0` for a specific IP) before saving.
5. Desktop platforms call `windowManager.show()` + `focus()` first so the dialog isn't hidden behind the worker GUI.

**Duplicate rule**: host + port + token triple uniqueness. Changing any of the three is treated as a new worker (handles token rotation or relocating the worker to a different port).
