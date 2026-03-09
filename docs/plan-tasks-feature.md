# Tasks Feature - Implementation Plan

## 1. Feature Overview

Tasks are a standalone entity tracked by the RCFlow backend, representing pieces of work that can be done or are already done. Tasks are typically worked on in sessions. Some sessions create tasks if necessary.

**Key properties:**
- Tasks are a completely separate entity from sessions, with their own DB table and API
- Tasks can be AI-generated (source: `ai`) or user-created (source: `user`) -- user-created tasks are out of scope for this plan
- AI-generated tasks are created by a small LLM agent that runs at session start (alongside the title generation agent)
- AI-generated tasks are always attached to the session that created them
- Tasks have a many-to-many relationship with sessions (one task can be linked to multiple sessions, one session can have multiple tasks)
- When a session ends, a small LLM agent reviews attached tasks and can update their status based on session results
- AI can set status up to `review` but never `done` -- only users can mark tasks as `done`
- Task view opens in a pane (same pattern as chat/terminal panes)

---

## 2. Database / Schema Changes

### 2.1 New Table: `tasks`

```python
# src/models/db.py

class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_backend_id", "backend_id"),
        Index("ix_tasks_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="todo")
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # "ai" or "user"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationship to sessions via association table
    sessions: Mapped[list["Session"]] = relationship(
        secondary="task_sessions", back_populates="tasks"
    )
```

**Status values:** `todo`, `in_progress`, `review`, `done`

**Source values:** `ai`, `user`

### 2.2 New Association Table: `task_sessions`

```python
# src/models/db.py

class TaskSession(Base):
    __tablename__ = "task_sessions"
    __table_args__ = (
        UniqueConstraint("task_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

### 2.3 Add `tasks` Relationship to Session Model

```python
# Addition to existing Session class in src/models/db.py

class Session(Base):
    # ... existing fields ...
    tasks: Mapped[list["Task"]] = relationship(
        secondary="task_sessions", back_populates="sessions"
    )
```

### 2.4 Migration

Create a new Alembic migration following the existing pattern (reference: `d4e5f6a7b8c9_add_token_fields_to_sessions.py`).

**File:** `src/db/migrations/versions/<hash>_add_tasks_table.py`

The migration should:
- Depend on the current head (`fea687bf3218_merge_heads`)
- Create the `tasks` table with all columns and indexes
- Create the `task_sessions` association table with unique constraint
- Downgrade drops both tables

---

## 3. Backend Model & API

### 3.1 Pydantic Request/Response Models

Add to `src/api/http.py` (or a new `src/api/schemas.py` if preferred for organization):

```python
class TaskResponse(BaseModel):
    task_id: str
    title: str
    description: str | None
    status: str
    source: str
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    sessions: list[TaskSessionRef]

class TaskSessionRef(BaseModel):
    session_id: str
    title: str | None
    status: str
    attached_at: str  # ISO 8601

class CreateTaskRequest(BaseModel):
    title: str
    description: str | None = None
    source: str = "user"  # "ai" or "user"
    session_id: str | None = None  # Optional: attach to session on creation

class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None

class AttachSessionRequest(BaseModel):
    session_id: str
```

### 3.2 HTTP API Endpoints

All endpoints added to `src/api/http.py` on the existing `router` (prefix `/api`), protected by `Depends(verify_http_api_key)`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tasks` | List all tasks for the current backend. Optional `?status=` filter, `?source=` filter. Sorted by `updated_at` desc. |
| `GET` | `/api/tasks/{task_id}` | Get a single task with its attached sessions. |
| `POST` | `/api/tasks` | Create a new task. Used by the LLM agent (source: "ai") or future user creation. |
| `PATCH` | `/api/tasks/{task_id}` | Update task fields (title, description, status). Enforces status transition rules. |
| `DELETE` | `/api/tasks/{task_id}` | Delete a task (and its session associations). |
| `POST` | `/api/tasks/{task_id}/sessions` | Attach a session to a task. Body: `{"session_id": "uuid"}`. |
| `DELETE` | `/api/tasks/{task_id}/sessions/{session_id}` | Detach a session from a task. |

**Implementation pattern:** Follow the existing patterns in `src/api/http.py`:
- Use `request.app.state.settings` for `backend_id`
- Use `get_db_session` dependency for database access
- Return proper HTTP status codes (201 for create, 404 for not found, 409 for invalid transitions)

### 3.3 Status Transition Validation

Add a helper function in `src/api/http.py` (or a dedicated `src/core/tasks.py` module):

```python
VALID_TRANSITIONS: dict[str, set[str]] = {
    "todo": {"in_progress", "done"},
    "in_progress": {"todo", "review", "done"},
    "review": {"in_progress", "done"},
    "done": {"todo", "in_progress"},
}

AI_FORBIDDEN_STATUSES = {"done"}  # AI cannot set status to "done"

def validate_status_transition(current: str, new: str, source: str | None = None) -> bool:
    """Check if a status transition is valid.

    Args:
        current: Current task status
        new: Desired new status
        source: If "ai", the new status cannot be "done"

    Returns:
        True if valid, raises HTTPException if not
    """
```

The `PATCH /api/tasks/{task_id}` endpoint should:
1. Validate that the transition is allowed per `VALID_TRANSITIONS`
2. If the update is coming from an AI agent (determined by a header or request context), enforce `AI_FORBIDDEN_STATUSES`
3. Update `updated_at` timestamp on any change

### 3.4 WebSocket Integration

Add task-related messages to the WebSocket output protocol.

**New output message type: `task_update`**

When a task is created, updated, or deleted, broadcast to all connected output clients (same pattern as `session_update` in `src/api/ws/output_text.py`).

```json
{
  "type": "task_update",
  "task_id": "uuid",
  "title": "Fix authentication bug",
  "description": "...",
  "status": "in_progress",
  "source": "ai",
  "created_at": "2026-03-08T...",
  "updated_at": "2026-03-08T...",
  "sessions": [{"session_id": "uuid", "title": "...", "status": "..."}]
}
```

**New output message type: `task_deleted`**

```json
{
  "type": "task_deleted",
  "task_id": "uuid"
}
```

**New client request type on `/ws/output/text`: `list_tasks`**

```json
{"type": "list_tasks"}
```

Response:
```json
{
  "type": "task_list",
  "tasks": [...]
}
```

**Implementation:**
- Add a `TaskManager` or extend `SessionManager` to hold task broadcast logic
- Store task update subscribers alongside session update subscribers in `SessionManager` (or a new `TaskBroadcaster`)
- The simplest approach: add task broadcast methods to `SessionManager` since it already has the subscriber infrastructure

### 3.5 New `MessageType` Entry

Add `TASK_UPDATE` to `MessageType` enum in `src/core/buffer.py` if task updates should flow through session buffers (for tasks attached to sessions). However, since tasks are a global entity, it's cleaner to broadcast them via the output WebSocket subscriber pattern (like `session_update`), not through session buffers.

---

## 4. LLM Agent System Changes

### 4.1 Task Creation Agent (Session Start)

**Trigger:** Runs as a background task alongside the title generation agent, after the first agentic loop turn completes in `PromptRouter.handle_prompt()`.

**Location in code:** `src/core/prompt_router.py`, around line 930-942, right after `_fire_title_task()`:

```python
# Existing code (line ~942):
self._fire_title_task(session, text, assistant_text or "")

# New code, added immediately after:
self._fire_task_creation_task(session, text, assistant_text or "")
```

**New method in `PromptRouter`:**

```python
def _fire_task_creation_task(self, session: ActiveSession, user_text: str, assistant_text: str) -> None:
    """Schedule a background task to extract or match tasks from the session."""
    task = asyncio.create_task(
        self._create_tasks_from_session(session, user_text, assistant_text)
    )
    self._pending_task_creation_tasks.add(task)
    task.add_done_callback(self._pending_task_creation_tasks.discard)
```

**New tracking set:** `self._pending_task_creation_tasks: set[asyncio.Task[None]] = set()` in `PromptRouter.__init__`.

**New method in `LLMClient` (`src/core/llm.py`):**

The task creation agent always attempts to match existing tasks before creating new ones. This is the primary method -- it replaces a simpler `extract_tasks()` approach.

```python
async def extract_or_match_tasks(
    self, user_prompt: str, assistant_response: str, existing_tasks: list[dict]
) -> dict:
    """Extract new tasks or match to existing ones.

    Args:
        user_prompt: The user's first message in the session.
        assistant_response: The assistant's first response.
        existing_tasks: List of existing tasks with 'task_id', 'title',
            'description', and 'status' keys (only todo/in_progress tasks).

    Returns: {
        "new_tasks": [{"title": "...", "description": "..."}],
        "attach_task_ids": ["uuid", ...]
    }
    """
    content = f"User: {user_prompt}\n\nAssistant: {assistant_response}"
    existing_section = ""
    if existing_tasks:
        task_lines = "\n".join(
            f"- [{t['task_id']}] {t['title']} (status: {t['status']}): {t.get('description', '')}"
            for t in existing_tasks
        )
        existing_section = (
            f"\n\nExisting tasks in the system:\n{task_lines}\n\n"
            "If the conversation relates to an existing task, attach it instead of "
            "creating a duplicate. Match by semantic similarity, not exact title match."
        )
    system = (
        "Analyze this conversation and determine if it implies any actionable tasks "
        "or work items. If the user is asking for something to be done (code changes, "
        "bug fixes, feature implementations, investigations, etc.), extract each "
        "distinct task.\n\n"
        f"{existing_section}"
        "Return a JSON object with two keys:\n"
        "- \"new_tasks\": Array of new task objects, each with \"title\" (max 100 chars) "
        "and \"description\" (1-3 sentences). Only create new tasks for work that does NOT "
        "match any existing task.\n"
        "- \"attach_task_ids\": Array of existing task IDs (from the list above) that this "
        "session relates to.\n\n"
        "If the conversation is just a question, greeting, or doesn't imply actionable work, "
        "return: {\"new_tasks\": [], \"attach_task_ids\": []}\n\n"
        "Return ONLY valid JSON, no markdown, no explanation."
    )
    if self._provider == "openai":
        raw = await self._openai_create(system, content, max_tokens=512)
    else:
        raw = await self._anthropic_create(system, content, max_tokens=512)
    # Parse JSON response
    # Handle parsing errors gracefully, return {"new_tasks": [], "attach_task_ids": []} on failure
```

**Implementation in `PromptRouter._create_tasks_from_session()`:**

1. Fetch all existing `todo` and `in_progress` tasks for the current backend from the database
2. Call `self._llm.extract_or_match_tasks(user_text, assistant_text, existing_tasks)`
3. For each task ID in `attach_task_ids`:
   - Verify the task exists in the database
   - Create a `TaskSession` row linking the existing task to the current session
   - Optionally update the task status to `in_progress` if it was `todo`
   - Broadcast `task_update` to all output clients
4. For each new task in `new_tasks`:
   - Create a `Task` row in the database with `source="ai"`, `status="todo"`, `backend_id`
   - Create a `TaskSession` row linking the task to the current session
   - Broadcast `task_update` to all output clients
5. Store all task IDs (both matched and new) on the session object (`session.metadata["attached_task_ids"]`) for the task-update agent to reference later
6. Never raise -- log errors only (same pattern as title generation)

**Session restore behavior:** When a session is restored after a server restart, the task creation agent should re-check attached tasks. On session restore, query the `task_sessions` table to repopulate `session.metadata["attached_task_ids"]` so the task-update agent can function correctly when the session ends.

### 4.2 Task Update Agent (Session End)

**Trigger:** Runs alongside the summary agent when a session is ending. Specifically, inside `_summarize_and_push()` or as a separate parallel task fired from the same locations that fire the summary task.

**Locations where summary is fired:**
- `src/core/prompt_router.py:1350-1355` (Claude Code result event)
- `src/core/prompt_router.py:1700-1703` (Codex completion)

**Approach:** Create a new `_fire_task_update_task()` method, called from the same places that call `_fire_summary_task()`:

```python
# In _relay_claude_code_stream, after the result event handling (~line 1353):
self._fire_summary_task(session, summary_text, push_session_end_ask=True)
self._fire_task_update_task(session, summary_text)  # NEW

# Same pattern for Codex completion
```

**New method in `PromptRouter`:**

```python
def _fire_task_update_task(self, session: ActiveSession, session_result_text: str) -> None:
    """Schedule a background task to update tasks based on session results."""
    task_ids = session.metadata.get("attached_task_ids", [])
    if not task_ids:
        return
    task = asyncio.create_task(
        self._update_tasks_from_session(session, session_result_text, task_ids)
    )
    self._pending_task_update_tasks.add(task)
    task.add_done_callback(self._pending_task_update_tasks.discard)
```

**New method in `LLMClient` (`src/core/llm.py`):**

```python
async def evaluate_task_status(
    self, task_title: str, task_description: str, current_status: str, session_result: str
) -> dict[str, str]:
    """Evaluate whether a task's status should change based on session results.

    Returns a dict with:
    - "status": new status (or same as current if no change)
    - "description": updated description (or same if no change)
    """
    system = (
        "You are evaluating whether a task's status should be updated based on "
        "the results of a work session.\n\n"
        f"Task: {task_title}\n"
        f"Description: {task_description}\n"
        f"Current status: {current_status}\n\n"
        "Based on the session results below, determine:\n"
        "1. Whether the task status should change. Valid statuses: todo, in_progress, review\n"
        "   - 'review' means the work appears complete and needs user review\n"
        "   - You CANNOT set status to 'done' -- only users can do that\n"
        "2. Whether the description should be updated with new context\n\n"
        "Return JSON with 'status' and 'description' keys.\n"
        "Return ONLY valid JSON, no markdown."
    )
    # ... implementation using _anthropic_create or _openai_create
```

**Implementation in `PromptRouter._update_tasks_from_session()`:**

1. For each task ID in `task_ids`:
   - Fetch the task from the database
   - Call `self._llm.evaluate_task_status(task.title, task.description, task.status, session_result_text)`
   - If status changed (and new status is not "done"), update the task in the database
   - Broadcast `task_update` to all output clients
2. Never raise -- log errors only

### 4.3 Task Matching (Core Behavior)

Task matching is a core part of the task creation agent, not a deferred enhancement. Every time the task creation agent runs, it fetches existing `todo`/`in_progress` tasks and passes them to the LLM alongside the session context. The LLM decides whether to attach existing tasks, create new ones, or both.

This is implemented directly in the `extract_or_match_tasks()` method described in Section 4.1. The matching uses semantic similarity -- the LLM compares the session's intent against existing task titles and descriptions to avoid creating duplicates.

**Key behaviors:**
- The agent always queries existing tasks before creating new ones
- If the session clearly relates to an existing task, the agent attaches it (no new task created)
- If the session involves partially overlapping work, the agent may both attach an existing task and create a new one for the non-overlapping part
- If no existing tasks match, the agent creates new tasks as usual
- Matching is best-effort -- false negatives (creating a duplicate) are acceptable, false positives (attaching an unrelated task) should be avoided via prompt engineering

---

## 5. Status Transition Rules

### 5.1 State Machine

```
              User/AI              User/AI              User only
  ┌──────┐ ──────────► ┌──────────────┐ ──────────► ┌────────┐ ──────────► ┌──────┐
  │ todo │              │ in_progress  │              │ review │              │ done │
  └──┬───┘              └──────┬───────┘              └───┬────┘              └──┬───┘
     │                         │                          │                     │
     │    ◄────────────────────┘  (back to todo)          │                     │
     │                         ◄──────────────────────────┘  (back to          │
     │                                                       in_progress)      │
     ◄─────────────────────────────────────────────────────────────────────────┘
                                        (reopen)
```

### 5.2 Transition Matrix

| From \ To | `todo` | `in_progress` | `review` | `done` |
|-----------|--------|---------------|----------|--------|
| `todo` | - | User, AI | - | User |
| `in_progress` | User, AI | - | User, AI | User |
| `review` | - | User, AI | - | User |
| `done` | User | User | - | - |

### 5.3 AI Restrictions

- AI can set: `todo`, `in_progress`, `review`
- AI **cannot** set: `done`
- The task-update agent's `evaluate_task_status` LLM call is instructed via prompt that `done` is forbidden
- The backend API enforces this: `PATCH /api/tasks/{task_id}` checks if the caller is an AI agent (via an internal flag or request context) and rejects `done` transitions from AI

**Implementation approach for AI vs User distinction:**
- Internal API calls from the task agents use a private helper that passes `source="ai"` to the validation
- HTTP API calls from clients are always treated as user actions (no restriction on `done`)

---

## 6. Frontend Components

### 6.1 New Pane Type: `task`

**File:** `rcflowclient/lib/models/split_tree.dart`

```dart
enum PaneType { chat, terminal, task }
```

### 6.2 Task Model

**New file:** `rcflowclient/lib/models/task_info.dart`

```dart
class TaskInfo {
  final String taskId;
  String title;
  String? description;
  String status;       // "todo", "in_progress", "review", "done"
  final String source; // "ai", "user"
  final String workerId;    // Worker/backend that owns this task
  final String workerName;  // Human-readable worker name (for display)
  final DateTime createdAt;
  DateTime updatedAt;
  List<TaskSessionRef> sessions;

  // fromJson, toJson, copyWith
}

class TaskSessionRef {
  final String sessionId;
  final String? title;
  final String status;
  final DateTime attachedAt;

  // fromJson
}
```

### 6.3 Task View Pane Widget

**New file:** `rcflowclient/lib/ui/widgets/task_pane.dart`

The `TaskPane` widget displays a single task's full details. Layout:

```
┌─────────────────────────────────────────┐
│  PaneHeader (task title, close button)  │
├─────────────────────────────────────────┤
│                                         │
│  Status Badge: [in_progress ▼]          │
│                                         │
│  Source: AI                             │
│  Created: Mar 8, 2026                   │
│  Updated: Mar 8, 2026                   │
│                                         │
│  ─────── Description ───────            │
│  Fix the authentication bug that        │
│  causes login failures on mobile...     │
│                                         │
│  ─────── Sessions ──────────            │
│  📎 Fix auth bug (active)     [→]       │
│  📎 Deploy hotfix (completed) [→]       │
│                                         │
└─────────────────────────────────────────┘
```

**Interactions:**
- Status dropdown: User can change status (all transitions including `done`)
- Description: Editable text field
- Session links:
  - **Left-click:** Open session in the same pane (switch pane to chat type, load session)
  - **Middle-mouse-click:** Open session in a new split view
  - **Right-click:** Context menu with "Open" and "Open in Split" options

### 6.4 Task List Panel (Sidebar)

**New file:** `rcflowclient/lib/ui/widgets/task_list_panel.dart`

Tasks appear in the sidebar as a collapsible "Tasks" section at the bottom of the sidebar, below all worker groups.

**Unified view:** The task list shows ALL tasks from all connected workers/backends in a single flat list (not grouped by worker). This gives the user an overview of all work items regardless of which backend they originated from.

Each task tile shows:
- Status icon (color-coded: grey=todo, blue=in_progress, orange=review, green=done)
- Title (truncated)
- Worker/backend name (small label showing which worker the task came from)
- Source badge (small "AI" tag for AI-generated)
- Updated timestamp

In the task detail view (Section 6.3), the full worker/backend name is displayed alongside other metadata so the user knows exactly where the task originated.

**Interactions:**
- **Left-click:** Open task in active pane (switches pane type to `task`)
- **Middle-click:** Open task in new split pane
- **Right-click:** Context menu with "Open", "Open in Split", "Delete"
- **Draggable:** For drag-and-drop into split views (new `TaskDragData` class)

### 6.5 Session Tile Enhancement

In the existing session tiles (`rcflowclient/lib/ui/widgets/session_panel/worker_group.dart`), add a small visual indicator when a session has attached tasks.

### 6.6 Context Menu for Session Links in Task View

**File:** `rcflowclient/lib/ui/widgets/task_pane.dart`

For each session link in the task view:

```dart
GestureDetector(
  onTap: () => _openSessionInPane(sessionRef),       // left-click: same pane
  onTertiaryTapUp: (_) => _openSessionInSplit(sessionRef), // middle-click: split
  onSecondaryTapUp: (details) => _showSessionContextMenu(details, sessionRef),
)
```

Context menu items:
```dart
PopupMenuItem(value: 'open', child: Row(children: [Icon(Icons.open_in_browser), Text('Open')]))
PopupMenuItem(value: 'open_split', child: Row(children: [Icon(Icons.vertical_split), Text('Open in Split')]))
```

---

## 7. Pane Integration

### 7.1 AppState Changes

**File:** `rcflowclient/lib/state/app_state.dart`

New state:
```dart
// Task state
final Map<String, TaskInfo> _tasks = {};
Map<String, TaskInfo> get tasks => Map.unmodifiable(_tasks);
List<TaskInfo> get taskList => _tasks.values.toList()
    ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));

// Track which pane shows which task
final Map<String, String> _taskPaneBindings = {};  // paneId -> taskId
```

New methods:
```dart
/// Open a task in the active pane (converts pane type to task)
void openTaskInPane(String taskId) {
  final paneId = _activePaneId;
  _paneTypes[paneId] = PaneType.task;
  _taskPaneBindings[paneId] = taskId;
  notifyListeners();
}

/// Open a task in a new split pane
void splitPaneWithTask(String sourcePaneId, DropZone zone, String taskId) {
  final newPaneId = _createPane();
  _paneTypes[newPaneId] = PaneType.task;
  _taskPaneBindings[newPaneId] = taskId;
  // ... split tree update (same pattern as splitPaneWithSession)
}

/// Update a task from a WebSocket message
void _handleTaskUpdate(Map<String, dynamic> msg) {
  final taskId = msg['task_id'] as String;
  _tasks[taskId] = TaskInfo.fromJson(msg);
  notifyListeners();
}

/// Handle task deletion
void _handleTaskDeleted(Map<String, dynamic> msg) {
  final taskId = msg['task_id'] as String;
  _tasks.remove(taskId);
  // Close any panes showing this task
  for (final entry in _taskPaneBindings.entries.toList()) {
    if (entry.value == taskId) {
      _taskPaneBindings.remove(entry.key);
      _paneTypes[entry.key] = PaneType.chat;  // revert to chat
    }
  }
  notifyListeners();
}

/// Update task status via API
Future<void> updateTaskStatus(String taskId, String workerId, String newStatus) async {
  // PATCH /api/tasks/{taskId} via HTTP
}
```

### 7.2 Output Handler Registration

**File:** `rcflowclient/lib/state/output_handlers.dart`

No per-pane handler needed for tasks since task updates are global. Instead, handle at the AppState level (similar to `session_update` and `session_list`).

In `AppState._handleOutputMessage()`:
```dart
case 'task_update':
  _handleTaskUpdate(msg);
  break;
case 'task_deleted':
  _handleTaskDeleted(msg);
  break;
case 'task_list':
  _handleTaskList(msg);
  break;
```

### 7.3 SessionPane Rendering

**File:** `rcflowclient/lib/ui/widgets/session_pane.dart`

Update the build method to handle the `task` pane type:

```dart
final paneType = appState.getPaneType(widget.pane.paneId);

if (paneType == PaneType.task) {
  final taskId = appState.taskPaneBindings[widget.pane.paneId];
  final task = taskId != null ? appState.tasks[taskId] : null;
  if (task != null) {
    return TaskPane(task: task, paneId: widget.pane.paneId);
  }
}
// ... existing chat/terminal logic
```

### 7.4 Drag-and-Drop Support

**New class in task model or split_tree.dart:**
```dart
class TaskDragData {
  final String taskId;
  TaskDragData(this.taskId);
}
```

Update `SessionPane`'s `DragTarget<Object>` to accept `TaskDragData`:
```dart
if (data is TaskDragData) {
  appState.splitPaneWithTask(paneId, zone, data.taskId);
}
```

### 7.5 PaneHeader for Task Panes

**File:** `rcflowclient/lib/ui/widgets/pane_header.dart`

When pane type is `task`, show:
- Task title (instead of session title)
- Status badge
- Split and close buttons (same as existing)

---

## 8. API Client (Frontend HTTP)

### 8.1 Task API Methods

Add to `WorkerConnection` or a new dedicated service. Since tasks are per-backend and the client connects to multiple workers, task API calls go through the worker's HTTP connection.

**New file:** `rcflowclient/lib/services/task_api.dart`

Or add methods directly to `WorkerConnection`:

```dart
/// Fetch all tasks from a worker
Future<List<TaskInfo>> fetchTasks(String workerId, {String? status}) async {
  // GET /api/tasks?status=...
}

/// Get a single task
Future<TaskInfo> fetchTask(String workerId, String taskId) async {
  // GET /api/tasks/{taskId}
}

/// Update task fields
Future<TaskInfo> updateTask(String workerId, String taskId, {
  String? title,
  String? description,
  String? status,
}) async {
  // PATCH /api/tasks/{taskId}
}

/// Delete a task
Future<void> deleteTask(String workerId, String taskId) async {
  // DELETE /api/tasks/{taskId}
}

/// Attach a session to a task
Future<void> attachSession(String workerId, String taskId, String sessionId) async {
  // POST /api/tasks/{taskId}/sessions
}

/// Detach a session from a task
Future<void> detachSession(String workerId, String taskId, String sessionId) async {
  // DELETE /api/tasks/{taskId}/sessions/{sessionId}
}
```

### 8.2 WebSocket Message Handling

Add `list_tasks` to the WebSocket input types. On connection/reconnection, fetch the task list alongside the session list.

In `WorkerConnection`:
```dart
void _onConnected() {
  // ... existing session list request
  _ws.listTasks();  // NEW: request task list on connect
}
```

In `WebSocketService`:
```dart
void listTasks() {
  _sendOutput({'type': 'list_tasks'});
}
```

---

## 9. File-by-File Breakdown

### Backend Files to Modify

| File | Changes |
|------|---------|
| `src/models/db.py` | Add `Task` model, `TaskSession` association table, add `tasks` relationship to `Session` |
| `src/core/llm.py` | Add `extract_or_match_tasks()` and `evaluate_task_status()` methods to `LLMClient` |
| `src/core/prompt_router.py` | Add `_fire_task_creation_task()`, `_create_tasks_from_session()`, `_fire_task_update_task()`, `_update_tasks_from_session()`, new tracking sets, integrate with session lifecycle, repopulate task IDs on session restore |
| `src/core/session.py` | Add task-related metadata helpers to `ActiveSession` (e.g., `attached_task_ids` property). Add task broadcast methods to `SessionManager` or create separate broadcaster. |
| `src/api/http.py` | Add 7 new task CRUD endpoints, Pydantic models, status validation |
| `src/api/ws/output_text.py` | Handle `list_tasks` client message, broadcast `task_update`/`task_deleted` messages |
| `src/main.py` | No changes needed if task management is self-contained within existing components |
| `Design.md` | Document new Task entity, API endpoints, WebSocket messages, LLM agents |

### Backend Files to Create

| File | Purpose |
|------|---------|
| `src/db/migrations/versions/<hash>_add_tasks_table.py` | Migration for `tasks` and `task_sessions` tables |

### Frontend Files to Modify

| File | Changes |
|------|---------|
| `rcflowclient/lib/models/split_tree.dart` | Add `PaneType.task` to enum |
| `rcflowclient/lib/state/app_state.dart` | Add task state (`_tasks`, `_taskPaneBindings`), task CRUD methods, output message handling for tasks, `openTaskInPane()`, `splitPaneWithTask()` |
| `rcflowclient/lib/state/output_handlers.dart` | No changes (task updates handled at AppState level, not per-pane) |
| `rcflowclient/lib/services/websocket_service.dart` | Add `listTasks()` method |
| `rcflowclient/lib/services/worker_connection.dart` | Add task API HTTP methods, handle `task_list`/`task_update`/`task_deleted` in message routing |
| `rcflowclient/lib/ui/widgets/session_pane.dart` | Add task pane rendering branch, accept `TaskDragData` in drag target |
| `rcflowclient/lib/ui/widgets/pane_header.dart` | Handle task pane type (show task title + status) |
| `rcflowclient/lib/ui/widgets/session_panel.dart` (or session_list_panel.dart) | Add Tasks section to sidebar |
| `rcflowclient/lib/ui/widgets/session_panel/worker_group.dart` | Add visual indicator for sessions with attached tasks |

### Frontend Files to Create

| File | Purpose |
|------|---------|
| `rcflowclient/lib/models/task_info.dart` | `TaskInfo` (with `workerId`/`workerName` fields) and `TaskSessionRef` models |
| `rcflowclient/lib/ui/widgets/task_pane.dart` | Task detail view pane widget |
| `rcflowclient/lib/ui/widgets/task_list_panel.dart` | Sidebar task list section |
| `rcflowclient/lib/ui/widgets/task_tile.dart` | Individual task tile for sidebar |

---

## 10. Implementation Order

### Phase 1: Database & Backend Foundation
1. **Add models to `src/models/db.py`** -- `Task`, `TaskSession`, relationship on `Session`
2. **Create Alembic migration** -- `src/db/migrations/versions/<hash>_add_tasks_table.py`
3. **Add HTTP API endpoints** -- CRUD for tasks in `src/api/http.py`
4. **Add status transition validation** -- Helper function with rules matrix
5. **Test the API** -- Verify CRUD operations, status transitions, session attach/detach

### Phase 2: WebSocket & Real-time Updates
6. **Add task broadcast infrastructure** -- Subscriber pattern for task updates
7. **Add `task_update`/`task_deleted`/`task_list` WebSocket messages** -- In `src/api/ws/output_text.py`
8. **Wire up API endpoints to broadcast** -- Every create/update/delete triggers broadcast

### Phase 3: LLM Agent Integration
9. **Add `extract_or_match_tasks()` to `LLMClient`** -- Task extraction and matching against existing tasks from session context
10. **Add `_fire_task_creation_task()` to `PromptRouter`** -- Fire alongside title generation; fetches existing tasks and passes them to the LLM for matching
11. **Add `evaluate_task_status()` to `LLMClient`** -- Task status evaluation
12. **Add `_fire_task_update_task()` to `PromptRouter`** -- Fire alongside summary generation
13. **Add session restore task re-check** -- On session restore, repopulate `attached_task_ids` from `task_sessions` table
14. **Test agent integration** -- Verify tasks are created, matched, and updated correctly; test that duplicate tasks are avoided when matching works

### Phase 4: Frontend - Models & State
15. **Create `TaskInfo` model** -- `rcflowclient/lib/models/task_info.dart` (includes `workerId` and `workerName` fields)
16. **Add `PaneType.task`** -- Update `split_tree.dart` enum
17. **Add task state to `AppState`** -- `_tasks`, bindings, handlers
18. **Add WebSocket message handling** -- `listTasks()`, handle `task_update`/`task_list`
19. **Add HTTP API client methods** -- Task CRUD via worker connection

### Phase 5: Frontend - UI Components
20. **Create `TaskPane` widget** -- Full task detail view (shows worker/backend origin)
21. **Create `TaskListPanel` widget** -- Unified sidebar task section showing all tasks across workers, with worker label on each tile
22. **Create `TaskTile` widget** -- Individual task tile with worker name
23. **Integrate task pane into `SessionPane`** -- Rendering branch for task type
24. **Update `PaneHeader`** -- Task pane header with title and status
25. **Add drag-and-drop support** -- `TaskDragData`, drop handling
26. **Add context menus** -- Session links in task view (open, open in split)
27. **Add session attachment indicator** -- Visual cue on session tiles

### Phase 6: Polish & Documentation
28. **Update `Design.md`** -- Document all new components, endpoints, protocols
29. **Bump versions** -- Backend in `pyproject.toml`, client in `pubspec.yaml`
30. **Manual testing** -- End-to-end: create session -> AI matches/creates task -> session ends -> AI updates task -> user marks done; test session restore repopulates task IDs

---

## 11. Resolved Decisions

1. **Task persistence during server restart:** YES -- the task creation agent re-checks tasks when a session is restored after a server restart. On session restore, the system queries the `task_sessions` table to repopulate `session.metadata["attached_task_ids"]` so the task-update agent functions correctly when the restored session ends. See Section 4.1 for implementation details.

2. **Multi-worker task display:** The sidebar shows ALL tasks in a unified flat list, not grouped by worker. Each task tile includes a small worker/backend label so the user knows where it came from. The task detail view (Section 6.3) shows the full worker/backend name in the metadata section. See Section 6.4 and the `TaskInfo` model (Section 6.2) which includes `workerId` and `workerName` fields.

3. **Task deduplication:** The AI tries to match existing tasks before creating new ones. The `extract_or_match_tasks()` method (Section 4.1) is the primary task creation method -- it always receives existing `todo`/`in_progress` tasks and uses semantic matching to decide whether to attach an existing task or create a new one. This is core behavior, not a deferred enhancement. See Section 4.3 for matching details.

4. **Task deletion cascade:** Only the `task_sessions` links are removed when a task is deleted. Sessions remain independent and unaffected.

5. **Notification sounds:** NO -- task status changes do not trigger notification sounds.
