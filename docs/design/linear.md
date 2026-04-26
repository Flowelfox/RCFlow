---
updated: 2026-04-26
---

# Linear Integration

RCFlow integrates with the [Linear](https://linear.app) project management API to sync issues into the local database and expose them in the Flutter client's sidebar.

**See also:**
- [Database](database.md) — `linear_issues` table
- [WebSocket API](websocket-api.md#task-messages) — task message types (Linear issues link to tasks)
- [Configuration](configuration.md) — `LINEAR_API_KEY`, `LINEAR_TEAM_ID`, `LINEAR_SYNC_ON_STARTUP`

---

## Overview

- Issues are fetched via the **Linear GraphQL API** using a personal API token (`LINEAR_API_KEY`).
- Synced issues are stored in the `linear_issues` table and survive server restarts.
- The Flutter client surfaces Linear issues **inside the Tasks tab** rather than a separate sidebar tab. Unlinked issues appear in a collapsible "Unlinked Issues" section at the bottom of the task list; linked issues appear in the task detail pane.
- Issues can be **linked to tasks** (sets `task_id` on `LinearIssue`), enabling cross-referencing between tasks and issues.
- The **"Create Task"** button in the `LinearIssuePane` atomically creates an RCFlow task from the issue title/description (`source: 'linear'`, `status: 'todo'`) and links them in one API call (`POST /api/integrations/linear/issues/{id}/create-task`).

## Backend Service — `LinearService`

`src/services/linear_service.py`

An async HTTP client wrapper around the Linear GraphQL API. Uses `httpx.AsyncClient` with bearer-token auth.

| Method | Description |
|--------|-------------|
| `fetch_teams()` | Query all teams accessible to the API key |
| `fetch_issues(team_id)` | Query all issues for a specific team (paginated) |
| `fetch_all_issues()` | Query all issues across all accessible teams (paginated) |
| `get_issue(linear_id)` | Fetch a single issue by its Linear ID |
| `create_issue(team_id, title, description, priority)` | Create a new issue in Linear |
| `update_issue(linear_id, title, description, state_id, priority)` | Update an existing issue |
| `aclose()` | Close the underlying HTTP client |

Raises `LinearServiceError` on API errors. All methods are `async`.

## HTTP Endpoints

All endpoints are under `/api/integrations/linear/` and require bearer-token auth (`RCFLOW_API_KEY`). After mutating the database, each endpoint broadcasts a `linear_issue_update` or `linear_issue_deleted` WebSocket message to all connected output clients.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/integrations/linear/test` | Validate an API key and return accessible teams — no prior config required |
| `GET`  | `/api/integrations/linear/teams` | List teams accessible via the configured `LINEAR_API_KEY` |
| `GET`  | `/api/integrations/linear/issues` | List all cached issues for this backend |
| `GET`  | `/api/integrations/linear/issues/{id}` | Get a single cached issue by UUID |
| `POST` | `/api/integrations/linear/sync` | Sync issues from Linear API; uses `LINEAR_TEAM_ID` if set, otherwise syncs all teams |
| `POST` | `/api/integrations/linear/issues` | Create an issue in Linear; uses `LINEAR_TEAM_ID` or `team_id` from request body |
| `PATCH`| `/api/integrations/linear/issues/{id}` | Update an issue (local cache + Linear API) |
| `POST` | `/api/integrations/linear/issues/{id}/link` | Link an issue to a task (`task_id`) |
| `DELETE`| `/api/integrations/linear/issues/{id}/link` | Unlink an issue from a task |
| `POST` | `/api/integrations/linear/issues/{id}/create-task` | Create a new RCFlow task from the issue (title + description), link them atomically; returns `{"task": {...}, "issue": {...}}`; 409 if already linked |

## WebSocket Messages

### Outbound (server → client)

**`linear_issue_list`** — sent in response to a `list_linear_issues` request. Delivers all cached issues for the backend.

```json
{
  "type": "linear_issue_list",
  "issues": [
    {
      "id": "uuid",
      "linear_id": "...",
      "identifier": "ENG-123",
      "title": "...",
      "description": "...",
      "priority": 2,
      "state_name": "In Progress",
      "state_type": "started",
      "assignee_id": "...",
      "assignee_name": "...",
      "team_id": "...",
      "team_name": "...",
      "url": "https://linear.app/...",
      "labels": ["bug", "frontend"],
      "created_at": "...",
      "updated_at": "...",
      "synced_at": "...",
      "task_id": "uuid or null"
    }
  ]
}
```

**`linear_issue_update`** — broadcast when an issue is created, synced, or modified. Contains the same issue dict as above.

**`linear_issue_deleted`** — broadcast when an issue is removed from the cache.

```json
{ "type": "linear_issue_deleted", "id": "uuid" }
```

### Inbound (client → server)

**`list_linear_issues`** — request the full list of cached issues for this backend.

```json
{ "type": "list_linear_issues" }
```

## Flutter Client

### Model — `LinearIssueInfo`

`rcflowclient/lib/models/linear_issue_info.dart`

Dart model mirroring the backend `linear_issues` table. Includes `workerId`, `priorityLabel` getter, and `isTerminal` getter. Constructed via `LinearIssueInfo.fromJson()`.

### State — `AppState`

- `_linearIssues: Map<String, LinearIssueInfo>` — all cached issues keyed by UUID.
- `linearIssues` — sorted list (by `updatedAt` desc).
- `linearIssuesByWorker` — issues grouped by `workerId`.
- `getLinearIssue(id)` — lookup by UUID.
- `linearIssuesForTask(taskId)` — all issues linked to a given task (sorted by `updatedAt` desc).
- `unlinkedLinearIssues` — all issues with `taskId == null` (sorted by `updatedAt` desc).
- `openLinearIssueInPane(id)` — open issue in active pane (or new pane), pushing nav history.
- `_handleLinearIssueList/Update/Deleted` — WebSocket message handlers that update `_linearIssues`.

### Pane — `PaneType.linearIssue`

`PaneType` enum includes `linearIssue`. `PaneState` has a `linearIssueId` field. `SessionPane` dispatches to `LinearIssuePane` when `paneType == PaneType.linearIssue`.

### Pane — `PaneType.workerSettings`

`PaneType` enum includes `workerSettings`. `PaneState` has `workerSettingsTool` (`String?`) and `workerSettingsSection` (`String?`) fields, plus `setWorkerSettings(toolName, {section})` and `clearWorkerSettings()` methods. `SessionPane` dispatches to `WorkerSettingsPane` when `paneType == PaneType.workerSettings`.

`AppState` provides:
- `openWorkerSettingsInPane(String toolName, {String section = 'plugins'})` — converts the active pane to `workerSettings` (pushing current view to nav history), or creates a new pane when none exist.
- `closeWorkerSettingsView(String paneId)` — reverts the pane to chat mode.

`PaneNavEntry` includes `workerSettingsTool` and `workerSettingsSection` fields so that back-navigation correctly restores the settings view.

### `WorkerSettingsPane`

`rcflowclient/lib/ui/widgets/worker_settings_pane.dart`

Full-pane plugin management UI for a managed coding agent tool. Layout:
- **Header** (32 px): back button (when nav history exists), active-pane dot, extension icon, title (`"<Tool> — Plugins"`), split/close buttons.
- **Install bar**: text field for plugin path/URL + "Install" button. Inline error on failure.
- **Plugin list**: `ListView` of `_PluginTile` entries. Each tile shows:
  - Plugin name + "disabled" badge when disabled
  - Command chips (`/name` in accent color) for all contributed commands
  - Enable/disable `Switch`
  - Delete icon (with confirmation dialog)

Data is fetched from `WebSocketService.fetchToolPlugins(toolName)` on load. Mutations call `installToolPlugin`, `uninstallToolPlugin`, and `setToolPluginEnabled`, then reload.

### `LinearIssuePane`

`rcflowclient/lib/ui/widgets/linear_issue_pane.dart`

Full-pane detail view showing:
- Header: identifier badge, title, back button, close button (multi-pane)
- Priority + state metadata chips
- Assignee and team chips
- Labels
- Description (selectable text)
- Timestamps (created, updated, synced)
- "Copy URL" button
- "Link to Task" / "Unlink Task" button (calls backend link/unlink endpoints)

### Sidebar — Tasks Tab (consolidated)

The sidebar `SessionListPanel` has a **3-tab layout**: **Workers**, **Tasks**, **Artifacts**. The Linear tab has been removed; Linear issues are now surfaced within the Tasks tab.

**`TaskListPanel`** (`rcflowclient/lib/ui/widgets/session_panel/task_list_panel.dart`):
- Search bar, status filter chips, source filter chips
- Tasks grouped by status with collapsible sections
- **Sync button** (⟳) in the filter bar — calls `worker.ws.syncLinearIssues()` then `listLinearIssues()`
- **"Unlinked Issues" section** at the bottom — collapsible list of `LinearIssueTile` for all issues where `taskId == null`
- **Multi-select**: Shift+click selects a range, Ctrl/Meta+click toggles individual tasks, plain click while a selection exists toggles the clicked task; plain click with no selection opens the task in a pane (unchanged). Escape clears the selection.
- **Selection toolbar**: thin bar shown below the filter bar when ≥1 task is selected, displaying the count and a clear button.
- **Bulk right-click context menu**: when tasks are selected and the user right-clicks any tile (adding the clicked tile to the selection if not already in it), a bulk menu appears with: *Mark all → In Progress / To Do / Review / Done*, *Delete N tasks…* (with confirmation dialog), and *Clear selection*. When no selection is active the per-tile single-task menu is used instead.
- `computeFlatVisibleList` is a library-level pure function that builds the ordered flat task list respecting the current grouping and collapse state; used for Shift+click range-index resolution and exercised in unit tests.

**`TaskTile`** (`rcflowclient/lib/ui/widgets/session_panel/task_tile.dart`):
- Existing session count badge
- **Linear issue count badge** (purple, shows link icon + count) when the task has linked issues
- `isSelected` parameter: when `true`, renders a checkbox icon in the leading area and an accent-tinted row background.
- `onTapOverride` / `onSecondaryTapOverride` parameters: when set by the parent (`TaskListPanel`), replace the tile's built-in open-in-pane and context-menu behaviours respectively.

**`LinearIssueTile`** (`rcflowclient/lib/ui/widgets/session_panel/linear_issue_tile.dart`):
- Priority icon + colored state background
- Identifier badge, title, state/time subtitle
- Link indicator icon when `taskId` is set
- Active/viewed highlight via pane state

### Task Detail Pane — Linked Issues

`TaskPane` (`rcflowclient/lib/ui/widgets/task_pane.dart`) now includes a **"Linked Issues"** section below "Linked Sessions":
- Lists all `LinearIssueInfo` where `taskId == task.taskId` (via `appState.linearIssuesForTask`)
- Each issue shown as a `_LinkedIssueTile`: identifier badge, title, state name; tap → open `LinearIssuePane`
- Right-click context menu per tile: **Open issue** / **Unlink from task** (calls `worker.ws.unlinkLinearIssueFromTask`)
- **"Link Issue"** button opens `_LinkIssueDialog` — searchable list of unlinked issues; selecting one calls `worker.ws.linkLinearIssueToTask(issueId, taskId)`
