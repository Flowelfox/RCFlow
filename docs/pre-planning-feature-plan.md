# Pre-Planning Feature — Implementation Plan

**Feature:** "Make plan" button on tasks that runs a read-only planning session, saves the result as a Markdown artifact, links it to the task, and includes it as context in subsequent implementation sessions.

> **Revision note:** This document was reviewed after initial drafting. All critical and major issues from that review have been incorporated — specifically around the async execution model, use of real API entry points, permission rule schema and ordering, `_pending_*` task set management, finalization call sites, Pydantic field patterns, and PaneState wiring.

---

## 1. Overview and Goals

| Goal | Description |
|------|-------------|
| Planning session | A restricted session that explores the codebase and produces a structured Markdown implementation plan for a task |
| Read-only by default | The planning session cannot Write, Edit, Bash, or run agent subprocesses — it may only write to a single designated plan file path |
| Artifact linkage | The generated plan is saved as a Markdown file, registered as an `Artifact`, and linked to the task via a new `plan_artifact_id` FK |
| Quick-open | When a task already has a plan, the task tile and task pane show a one-click button to open that artifact |
| Context injection | When starting a normal implementation session for a task that already has a plan, the plan content is automatically injected into the session context |

---

## 2. Architecture Summary

```
User clicks "Make plan" on a task
        │
        ▼
[Client] PaneState.startPlanSession() sends:
  WS message: {type: "start_plan_session", task_id, project_name}
        │
        ▼
[Backend] input_text.py handler:
  → await prompt_router.prepare_plan_session(task_id, project_name)
      Creates session, sets metadata, pre-seeds permissions, attaches session to task
      Returns (session_id, planning_prompt)
  → send ack {type: "ack", session_id, purpose: "plan"}
  → asyncio.create_task(prompt_router.handle_prompt(
        planning_prompt, session_id, task_id=task_id, project_name=...
    ))  ← fires in background, does NOT block WS
        │
        ▼
handle_prompt() sets session.metadata["primary_task_id"] = task_id
Session runs — LLM explores codebase (Read/Grep/Glob/WebFetch allowed)
  LLM writes plan to .rcflow/plans/<task_id>.md
        │
        ▼
[Backend] session end / cancel / fail — session_lifecycle.py:
  _fire_task_update_on_session_end()   ← existing
  _fire_plan_finalization_task()        ← NEW, called here
  _fire_archive_task()                  ← existing
        │
        ▼
_finalize_plan_session() (background coroutine):
  - Reads plan file from disk
  - Upserts Artifact record (handles ArtifactScanner race via UniqueConstraint)
  - Updates task.plan_artifact_id = artifact.id
  - Broadcasts task_update with plan_artifact_id
        │
        ▼
[Client] receives task_update → TaskInfo.planArtifactId populated
  - Task tile shows plan quick-open badge
  - Task pane shows plan section with "Open plan" button
```

---

## 3. Data Model Changes

### 3.1 `Task` table — new column

**File:** `src/models/db.py`

Add `plan_artifact_id` as a nullable FK to `artifacts`:

```python
class Task(Base):
    __tablename__ = "tasks"
    # ... existing columns ...

    plan_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    plan_artifact: Mapped["Artifact | None"] = relationship(
        "Artifact",
        foreign_keys=[plan_artifact_id],
        lazy="select",
    )
```

`ondelete="SET NULL"` means deleting the artifact record clears the task's plan reference gracefully.

> **SQLite FK note:** SQLite FK enforcement is already enabled in `src/db/engine.py` via `PRAGMA foreign_keys=ON` on every connection (using a SQLAlchemy `connect` event listener). No additional work is needed.

### 3.2 Migration

**Note:** `plan_artifact_id` is part of the squashed initial migration (`0001_initial_schema.py`).

The snippet below shows the original pre-squash migration for reference only:

```python
"""Add plan_artifact_id to tasks table

Revision ID: c3d4e5f6a7b8
Revises: a2b3c4d5e6f7  (pre-squash head — now superseded by 0001)
"""
import sqlalchemy as sa
from alembic import op

revision = 'c3d4e5f6a7b8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('plan_artifact_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_tasks_plan_artifact_id',
        'tasks', 'artifacts',
        ['plan_artifact_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_tasks_plan_artifact_id', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'plan_artifact_id')
```

---

## 4. Backend Changes

### 4.1 `_task_to_dict_full()` in `src/api/routes/tasks.py`

The helper that serializes a `Task` row to a dict must include `plan_artifact_id`:

```python
def _task_to_dict_full(task: TaskModel, ...) -> dict:
    return {
        ...
        "plan_artifact_id": str(task.plan_artifact_id) if task.plan_artifact_id else None,
    }
```

All callers (`create_task`, `update_task`, `attach_session_to_task`, `get_task`) already go through this helper and will propagate the field automatically.

### 4.2 `list_tasks` handler in `src/api/ws/output_text.py` (lines ~208–251)

The `list_tasks` WS output handler builds task dicts manually (does not use `_task_to_dict_full`). Update the per-task loop to include `plan_artifact_id`:

```python
tasks_out.append({
    "task_id": str(t.id),
    ...
    "plan_artifact_id": str(t.plan_artifact_id) if t.plan_artifact_id else None,
})
```

### 4.3 `broadcast_task_update()` in `src/core/session_manager.py`

Verify that the payload passed to `broadcast_task_update` includes `plan_artifact_id`. This is automatically true if the broadcast uses `_task_to_dict_full()` — confirm the call site.

### 4.4 New input message type: `start_plan_session`

**File:** `src/api/ws/input_text.py`

The handler mirrors the existing `prompt` handler pattern exactly: call a setup method, send `ack` immediately, then fire the agentic loop as a background task. **Do not await the agentic loop in the handler** — doing so would block the WebSocket for the full session duration.

```python
if msg_type == "start_plan_session":
    task_id_str = message.get("task_id")
    project_name = message.get("project_name")
    worktree_path = message.get("selected_worktree_path")
    if not task_id_str:
        await websocket.send_json({
            "type": "error",
            "content": "Missing task_id",
            "code": "MISSING_TASK_ID",
        })
        continue
    try:
        # prepare_plan_session() is fast: DB lookup + session creation only.
        # It does NOT start the agentic loop.
        plan_session_id, planning_prompt = await prompt_router.prepare_plan_session(
            task_id=task_id_str,
            project_name=project_name,
            selected_worktree_path=worktree_path,
        )
        # Ack immediately so the client can subscribe before chunks arrive.
        await websocket.send_json({
            "type": "ack",
            "session_id": plan_session_id,
            "purpose": "plan",
        })
        # Fire the agentic loop as a background task (same pattern as "prompt").
        task = asyncio.create_task(
            prompt_router.handle_prompt(
                planning_prompt,
                plan_session_id,
                project_name=project_name,
                selected_worktree_path=worktree_path,
                task_id=task_id_str,
            )
        )
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
    except (ValueError, RuntimeError) as e:
        await websocket.send_json({
            "type": "error",
            "content": str(e),
            "code": "PLAN_SESSION_ERROR",
        })
    continue
```

### 4.5 `PromptRouter.prepare_plan_session()` (new method)

**File:** `src/core/prompt_router.py`

This method is **fast and async**: it does DB work (look up task, create session row, attach to task), sets session metadata and permission rules, and returns. It does NOT start the LLM loop.

```python
async def prepare_plan_session(
    self,
    task_id: str,
    project_name: str | None = None,
    selected_worktree_path: str | None = None,
) -> tuple[str, str]:
    """Set up a read-only planning session for a task.

    Returns (session_id, planning_prompt). The caller is responsible for
    firing handle_prompt(planning_prompt, session_id, ...) as a background task.
    Does NOT start the agentic loop.
    """
    # 1. Resolve task from DB to get title + description
    if self._db_session_factory is None:
        raise RuntimeError("Database not configured")
    task_uuid = uuid.UUID(task_id)
    async with self._db_session_factory() as db:
        task = await db.get(TaskModel, task_uuid)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        task_title = task.title
        task_description = task.description or ""

    # 2. Create a new session (ONE_SHOT: one request → plan → auto-ends)
    session = self._session_manager.create_session(SessionType.ONE_SHOT)

    # 3. Apply project context if provided (resolves project_name → path)
    if project_name:
        await self._apply_project_name(session, project_name)
    if selected_worktree_path:
        session.metadata["selected_worktree_path"] = selected_worktree_path

    # 4. Determine plan output path
    project_root = session.main_project_path
    if not project_root and self._settings and self._settings.projects_dirs:
        project_root = self._settings.projects_dirs[0]
    if not project_root:
        raise RuntimeError("No project configured — cannot determine plan output path")

    plan_dir = Path(project_root) / ".rcflow" / "plans"
    plan_path = plan_dir / f"{task_id}.md"
    session.metadata["session_purpose"] = "plan"
    session.metadata["task_id"] = task_id
    session.metadata["plan_output_path"] = str(plan_path)

    # 5. Pre-seed restrictive permission rules.
    #
    # IMPORTANT — rule ordering: PermissionManager.check_cached() iterates
    # self._rules in REVERSE (most-recently-added rules are checked first).
    # To deny all Writes except one specific path, add the catch-all deny
    # FIRST (checked last, acts as fallback), then add the specific allow
    # LAST (checked first, takes priority for that path).
    #
    # The key in each dict is "tool_name" (not "tool"), matching what
    # restore_rules() reads (permissions.py line 352).
    session.metadata["permission_rules"] = [
        # Fallback denies (added first → checked last)
        {"tool_name": "Bash",  "decision": "deny", "scope": "all_session", "path_prefix": None},
        {"tool_name": "Edit",  "decision": "deny", "scope": "all_session", "path_prefix": None},
        {"tool_name": "Agent", "decision": "deny", "scope": "all_session", "path_prefix": None},
        {"tool_name": "Write", "decision": "deny", "scope": "all_session", "path_prefix": None},
        # Specific allow (added last → checked first, overrides Write deny for plan dir)
        {"tool_name": "Write", "decision": "allow", "scope": "tool_path",
         "path_prefix": str(plan_dir)},
    ]

    # 6. Attach session to task in DB
    backend_id = self._settings.RCFLOW_BACKEND_ID
    async with self._db_session_factory() as db:
        session_uuid = uuid.UUID(session.id)
        # Ensure Session row exists (may not be archived yet)
        existing = await db.get(SessionModel, session_uuid)
        if existing is None:
            db.add(SessionModel(
                id=session_uuid,
                backend_id=backend_id,
                created_at=session.created_at,
                ended_at=session.ended_at,
                session_type=session.session_type.value,
            ))
            await db.flush()
        # Create task_sessions link
        link = TaskSessionModel(task_id=task_uuid, session_id=session_uuid)
        db.add(link)
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            # Link may already exist; non-fatal
            pass

    # 7. Build and return the planning prompt
    planning_prompt = _build_planning_prompt(task_title, task_description, plan_path)
    return session.id, planning_prompt
```

> **Why `SessionType.ONE_SHOT`?** A planning session is a single self-contained operation: one prompt in, one plan file out, session ends automatically. `CONVERSATIONAL` would leave the session alive and awaiting further user input, which is not the intent.

> **Why not `prepare_plan_session` await the agentic loop?** The WS input handler (and the background `background_tasks` set at `input_text.py:57`) owns the lifecycle of agentic tasks. Keeping the fire-and-forget inside the handler is consistent with how every other `prompt` message works (`input_text.py:331-341`).

### 4.6 `_build_planning_prompt()` helper

```python
def _build_planning_prompt(title: str, description: str, plan_path: Path) -> str:
    """Build the initial user message for a planning session."""
    lines = [
        "You are a software planning assistant. Your job is to produce a detailed",
        "implementation plan — not to implement anything.",
        "",
        "## Task",
        f"**Title:** {title}",
    ]
    if description:
        lines += ["", "**Description:**", description]
    lines += [
        "",
        "## Instructions",
        "1. Explore the codebase thoroughly using Read, Grep, and Glob tools.",
        "2. Identify all files, components, APIs, and data models that need to change.",
        "3. Write a detailed step-by-step implementation plan in Markdown.",
        "4. Include: files to change, data model implications, API changes, UI changes,",
        "   edge cases, testing strategy, and rollout notes.",
        "5. Do NOT implement anything. Do NOT run shell commands or modify any files",
        f"   except to write your final plan to exactly: `{plan_path}`",
        "6. Create the directory if it does not exist, then write the plan file.",
    ]
    return "\n".join(lines)
```

### 4.7 `handle_prompt()` — add `task_id` parameter

**File:** `src/core/prompt_router.py`, method at line 455.

Add `task_id: str | None = None` to the signature and store it in session metadata immediately after `ensure_session`, before the first LLM call:

```python
async def handle_prompt(
    self,
    text: str,
    session_id: str | None = None,
    attachments: list[ResolvedAttachment] | None = None,
    project_name: str | None = None,
    selected_worktree_path: str | None = None,
    task_id: str | None = None,          # ← NEW
) -> str:
    session_id = self.ensure_session(session_id)
    session = self._session_manager.get_session(session_id)
    ...
    # NEW: store task_id so plan context can be injected before first LLM call
    if task_id and "primary_task_id" not in session.metadata:
        session.metadata["primary_task_id"] = task_id
    ...
```

This is the only place `primary_task_id` is written. It must be set before `_build_plan_context()` is called.

### 4.8 `_build_plan_context()` — plan injection for implementation sessions

**File:** `src/core/context.py` (or `src/core/prompt_router.py` if context building lives there).

Called during the context-assembly phase before the first LLM turn in `handle_prompt`. It reads `primary_task_id` from session metadata, looks up the task's plan artifact, and prepends the plan text:

```python
async def _build_plan_context(self, session: ActiveSession) -> str | None:
    """If the task attached to this session has a plan, return it for injection."""
    task_id_str = session.metadata.get("primary_task_id")
    if not task_id_str or self._db_session_factory is None:
        return None

    async with self._db_session_factory() as db:
        task = await db.get(TaskModel, uuid.UUID(task_id_str))
        if task is None or task.plan_artifact_id is None:
            return None
        artifact = await db.get(ArtifactModel, task.plan_artifact_id)
        if artifact is None or not artifact.file_exists:
            return None
        # Capture values before leaving the session context
        file_path = artifact.file_path

    plan_path = Path(file_path)
    if not plan_path.exists():
        return None

    plan_text = plan_path.read_text(encoding="utf-8")
    return (
        "## Implementation Plan\n\n"
        "The following plan was generated for this task. "
        "Use it as your primary guide for implementation.\n\n"
        f"{plan_text}\n"
    )
```

**Important:** For planning sessions themselves (`session.metadata.get("session_purpose") == "plan"`), skip plan injection — the LLM should explore freely without being pre-biased by a prior plan.

### 4.9 Plan finalization background task

**File:** `src/core/background_tasks.py`

Add two methods and a new pending-task set.

**New set in `PromptRouter.__init__`** (alongside the existing sets at lines 97–102):

```python
self._pending_plan_finalization_tasks: set[asyncio.Task[None]] = set()
```

**Register the set in `wait_for_pending_tasks()`** in `src/core/session_lifecycle.py` (lines 52-60, the tuple of sets to iterate):

```python
for task_set in (
    self._pending_log_tasks,
    self._pending_title_tasks,
    self._pending_archive_tasks,
    self._pending_summary_tasks,
    self._pending_task_creation_tasks,
    self._pending_task_update_tasks,
    self._pending_plan_finalization_tasks,   # ← ADD
):
```

**New methods in `BackgroundTasksMixin`:**

```python
def _fire_plan_finalization_task(self, session: ActiveSession) -> None:
    """Schedule plan artifact registration after a plan session ends (any reason)."""
    if session.metadata.get("session_purpose") != "plan":
        return
    task = asyncio.create_task(self._finalize_plan_session(session))
    self._pending_plan_finalization_tasks.add(task)  # ty:ignore[unresolved-attribute]
    task.add_done_callback(self._pending_plan_finalization_tasks.discard)  # ty:ignore[unresolved-attribute]

async def _finalize_plan_session(self, session: ActiveSession) -> None:
    """Register the plan file as an artifact and link it to the task. Never raises."""
    try:
        plan_path_str = session.metadata.get("plan_output_path")
        task_id_str = session.metadata.get("task_id")
        if not plan_path_str or not task_id_str:
            return

        plan_path = Path(plan_path_str)
        if not plan_path.exists():
            logger.warning(
                "Plan session %s ended but plan file not found at %s",
                session.id, plan_path,
            )
            return

        backend_id = self._settings.RCFLOW_BACKEND_ID  # ty:ignore[unresolved-attribute]
        task_uuid = uuid.UUID(task_id_str)
        stat = plan_path.stat()
        now = datetime.now(UTC)

        async with self._db_session_factory() as db:  # ty:ignore[unresolved-attribute]
            # Upsert the artifact record.
            # Race condition note: ArtifactScanner may have already inserted
            # this file (if the LLM printed the path in its output). Handle
            # the (backend_id, file_path) UniqueConstraint by attempting a
            # select first; if absent, insert; if IntegrityError on insert,
            # select again.
            stmt = select(ArtifactModel).where(
                ArtifactModel.backend_id == backend_id,
                ArtifactModel.file_path == str(plan_path),
            )
            result = await db.execute(stmt)
            artifact = result.scalar_one_or_none()

            if artifact is None:
                artifact = ArtifactModel(
                    backend_id=backend_id,
                    file_path=str(plan_path),
                    file_name=plan_path.name,
                    file_extension=plan_path.suffix,
                    file_size=stat.st_size,
                    mime_type="text/markdown",
                    file_exists=True,
                    discovered_at=now,
                    modified_at=now,
                    session_id=uuid.UUID(session.id),
                )
                db.add(artifact)
                try:
                    await db.flush()  # generates artifact.id before commit
                except Exception:
                    # Another writer (ArtifactScanner) beat us — re-fetch
                    await db.rollback()
                    result2 = await db.execute(stmt)
                    artifact = result2.scalar_one()
            else:
                artifact.file_size = stat.st_size
                artifact.file_exists = True
                artifact.modified_at = now
                artifact.session_id = uuid.UUID(session.id)

            # Link to task — build broadcast dict while still in session
            task = await db.get(TaskModel, task_uuid)
            if task is not None:
                task.plan_artifact_id = artifact.id
                task.updated_at = now

            await db.commit()

            # Build broadcast payload while objects are still bound to session
            if task is not None:
                task_dict = {
                    "task_id": str(task.id),
                    "title": task.title,
                    "description": task.description,
                    "status": task.status,
                    "source": task.source,
                    "created_at": task.created_at.isoformat() if task.created_at else "",
                    "updated_at": task.updated_at.isoformat() if task.updated_at else "",
                    "plan_artifact_id": str(task.plan_artifact_id) if task.plan_artifact_id else None,
                    "sessions": [],  # sessions list omitted here; client refreshes on demand
                }

        # Broadcast outside the DB session
        if task is not None:
            self._session_manager.broadcast_task_update(task_dict)  # ty:ignore[unresolved-attribute]

        logger.info(
            "Plan artifact saved and linked: task=%s artifact=%s",
            task_id_str, artifact.id,
        )

    except Exception:
        logger.exception("Failed to finalize plan session %s", session.id)
```

### 4.10 Exact call sites for `_fire_plan_finalization_task()`

**File:** `src/core/session_lifecycle.py`

Add the call in **three places**, immediately after `_fire_task_update_on_session_end()` and before `_fire_archive_task()`:

**`cancel_session()` (around line 178–181):**
```python
self._fire_task_update_on_session_end(session)
self._fire_plan_finalization_task(session)   # ← ADD HERE
self._fire_archive_task(session_id)
```

**`end_session()` (around line 275–278):**
```python
self._fire_task_update_on_session_end(session)
self._fire_plan_finalization_task(session)   # ← ADD HERE
self._fire_archive_task(session_id)
```

**`handle_prompt()` failure path** (around line 809–810 in `src/core/prompt_router.py`):
```python
session.fail(str(e))
self._fire_plan_finalization_task(session)   # ← ADD HERE
self._fire_archive_task(session.id)
```

This covers all three terminal session states: normal user-confirmed end, cancellation, and unhandled failure.

### 4.11 Permission enforcement details

The `PermissionManager.check_cached()` method iterates `reversed(self._rules)` and returns on the **first match** (last-added rule wins). The pre-seeded rules in §4.5 are ordered deliberately:

| Order added | `tool_name` | `decision` | `scope` | `path_prefix` | When checked |
|-------------|-------------|------------|---------|----------------|--------------|
| 1st (first) | `"Bash"` | `"deny"` | `"all_session"` | `None` | Last (fallback) |
| 2nd | `"Edit"` | `"deny"` | `"all_session"` | `None` | Last (fallback) |
| 3rd | `"Agent"` | `"deny"` | `"all_session"` | `None` | Last (fallback) |
| 4th | `"Write"` | `"deny"` | `"all_session"` | `None` | 2nd-to-last (fallback) |
| 5th (last) | `"Write"` | `"allow"` | `"tool_path"` | `str(plan_dir)` | **First** (overrides) |

The `tool_path`-scoped allow rule is checked first. If the Write target path starts with `plan_dir`, it returns `ALLOW` immediately. If not, iteration continues until the blanket `"deny"` rule is reached.

These rules are loaded at session restore time via `PermissionManager.restore_rules()` (called in `session_lifecycle.py:752-756`). The key in each dict **must be `"tool_name"`** (not `"tool"`), matching what `restore_rules()` reads at `permissions.py:352`.

---

## 5. HTTP API Changes

### 5.1 `GET /api/tasks` and `GET /api/tasks/{id}`

Both return task dicts via `_task_to_dict_full()`. After §4.1 the `plan_artifact_id` field is included automatically.

### 5.2 New endpoint: `POST /api/tasks/{id}/plan`

**File:** `src/api/routes/tasks.py`

HTTP endpoint to trigger a plan session from non-WebSocket callers (automation, CLI):

```python
class StartPlanRequest(BaseModel):
    project_name: str | None = None
    selected_worktree_path: str | None = None


@router.post(
    "/tasks/{task_id}/plan",
    summary="Start a planning session for a task",
    description=(
        "Creates a new read-only planning session for the task. "
        "The session explores the project and saves a Markdown plan to "
        ".rcflow/plans/<task_id>.md, then links it as an artifact. "
        "Returns immediately with the new session_id; the planning session "
        "runs asynchronously."
    ),
    tags=["tasks"],
    dependencies=[Depends(verify_http_api_key)],
)
async def start_task_plan(
    task_id: str,
    body: StartPlanRequest,
    request: Request,
) -> dict[str, str]:
    """Trigger a planning session for a task."""
    prompt_router: PromptRouter = request.app.state.prompt_router
    plan_session_id, planning_prompt = await prompt_router.prepare_plan_session(
        task_id=task_id,
        project_name=body.project_name,
        selected_worktree_path=body.selected_worktree_path,
    )
    # Fire agentic loop as a true background task (not tied to the HTTP request lifetime)
    asyncio.create_task(
        prompt_router.handle_prompt(
            planning_prompt,
            plan_session_id,
            project_name=body.project_name,
            selected_worktree_path=body.selected_worktree_path,
            task_id=task_id,
        )
    )
    return {"session_id": plan_session_id}
```

### 5.3 Update `UpdateTaskRequest` — clearable `plan_artifact_id`

The correct Pydantic pattern for an optional field that can also be **explicitly cleared** (set to `null`) is to use `str | None = None` and then check whether the field was actually supplied using `model_fields_set`:

```python
class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    plan_artifact_id: str | None = None   # None means "not provided" by default


# In the update_task endpoint handler:
if "plan_artifact_id" in body.model_fields_set:
    # Field was explicitly sent (even if as null — clears the plan link)
    if body.plan_artifact_id is None:
        task.plan_artifact_id = None
    else:
        try:
            task.plan_artifact_id = uuid.UUID(body.plan_artifact_id)
            changed = True
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid plan_artifact_id")
```

Using `Field(default=...)` with Ellipsis is **wrong** — in Pydantic v2 that makes the field *required*, which is the opposite of the intent.

---

## 6. Flutter Client Changes

### 6.1 `TaskInfo` model — `rcflowclient/lib/models/task_info.dart`

Add `planArtifactId`. For `copyWith`, use a dedicated sentinel constant rather than `Object?` — this avoids unsafe casts and is clearer:

```dart
// Private sentinel used by copyWith to distinguish "not provided" from null.
const _unset = Object();

class TaskInfo {
  // ... existing fields ...
  final String? planArtifactId;

  TaskInfo({
    // ...
    this.planArtifactId,
  });

  factory TaskInfo.fromJson(
    Map<String, dynamic> json, {
    String workerId = '',
    String workerName = '',
  }) {
    return TaskInfo(
      // ...
      planArtifactId: json['plan_artifact_id'] as String?,
    );
  }

  TaskInfo copyWith({
    // ...
    Object? planArtifactId = _unset,
  }) {
    return TaskInfo(
      // ...
      planArtifactId: identical(planArtifactId, _unset)
          ? this.planArtifactId
          : planArtifactId as String?,
    );
  }
}
```

### 6.2 `WebSocketService` — extend `sendPrompt` and add `startPlanSession`

**File:** `rcflowclient/lib/services/websocket_service.dart`

**Extend `sendPrompt`** (current signature at lines 136–154) to accept an optional `taskId`:

```dart
void sendPrompt(
  String text,
  String? sessionId, {
  List<Map<String, dynamic>>? attachments,
  String? projectName,
  String? selectedWorktreePath,
  String? taskId,              // ← NEW
}) {
  final msg = <String, dynamic>{
    'type': 'prompt',
    'text': text,
    'session_id': sessionId,
    if (attachments != null && attachments.isNotEmpty) 'attachments': attachments,
    'project_name': projectName,
    'selected_worktree_path': selectedWorktreePath,
    if (taskId != null) 'task_id': taskId,    // ← NEW
  };
  _inputChannel?.sink.add(jsonEncode(msg));
}
```

**New `startPlanSession` method:**

```dart
/// Send a start_plan_session message and return the server's ack.
Future<Map<String, dynamic>> startPlanSession(
  String taskId, {
  String? projectName,
  String? selectedWorktreePath,
}) {
  final msg = <String, dynamic>{
    'type': 'start_plan_session',
    'task_id': taskId,
    if (projectName != null) 'project_name': projectName,
    if (selectedWorktreePath != null) 'selected_worktree_path': selectedWorktreePath,
  };
  return _sendAndAwaitAck(msg);
}
```

### 6.3 `PaneState` — add `_pendingTaskId`

**File:** `rcflowclient/lib/state/pane_state.dart`

Follow the identical pattern of `_pendingWorktreePath` (lines 139–145):

```dart
// Inside PaneState:
String? _pendingTaskId;
String? get pendingTaskId => _pendingTaskId;

void setPendingTaskId(String? taskId) {
  _pendingTaskId = taskId;
  notifyListeners();
}

void _clearPendingTaskId() {
  _pendingTaskId = null;
}
```

Clear it in `startNewChat()` (wherever the other pending fields are cleared) so it doesn't bleed into unrelated subsequent sessions.

In `sendPrompt()` inside `PaneState` (line ~517), pass `_pendingTaskId` to `_ws?.sendPrompt(...)`:

```dart
_ws?.sendPrompt(
  text,
  _sessionId,
  attachments: attachments,
  projectName: _selectedProjectName,
  selectedWorktreePath: _pendingWorktreePath,
  taskId: _pendingTaskId,     // ← NEW
);
_clearPendingTaskId();        // ← clear after first send
```

### 6.4 `AppState` — `startPlanSession()`

**File:** `rcflowclient/lib/state/app_state.dart`

```dart
/// Start a planning session for the given task.
/// Opens a session pane and fires the plan session in the background.
void startPlanSession(String paneId, TaskInfo task) {
  final pane = _panes[paneId];
  if (pane == null) return;

  final worker = _workers[task.workerId];
  if (worker == null || !worker.isConnected) {
    addSystemMessage('Worker not connected', isError: true);
    return;
  }

  // Switch pane from task view to chat (so the streaming output is visible).
  closeTaskView(paneId);
  pane.startNewChat();
  pane.setTargetWorker(task.workerId);

  // project_name comes from the pane's current project selection.
  // This is the same source used by regular sendPrompt() calls.
  final projectName = pane.selectedProjectName;

  worker.ws.startPlanSession(
    task.taskId,
    projectName: projectName,
    selectedWorktreePath: pane.pendingWorktreePath,
  ).then((ack) {
    final sessionId = ack['session_id'] as String?;
    if (sessionId != null) {
      pane.setSessionId(sessionId);
    }
  }).catchError((Object e) {
    addSystemMessage('Failed to start plan session: $e', isError: true);
  });
}
```

> **Why `pane.selectedProjectName`?** `PaneState._selectedProjectName` (line 108) is the project picker selection already used by every `sendPrompt()` call. It's the correct and only source of truth for project context on a pane. There is no `worker.projectName` property.

### 6.5 Extend `startSessionFromTask()` to pass `task_id`

**File:** `rcflowclient/lib/state/app_state.dart` — `startSessionFromTask()` at line 774

```dart
void startSessionFromTask(String paneId, TaskInfo task) {
  final pane = _panes[paneId];
  if (pane == null) return;

  closeTaskView(paneId);
  pane.startNewChat();
  pane.setTargetWorker(task.workerId);

  pane.setNewSessionCallback((sessionId) {
    final worker = _workers[task.workerId];
    if (worker != null && worker.isConnected) {
      worker.ws.attachSessionToTask(task.taskId, sessionId);
    }
  });

  // Store task_id so sendPrompt() sends it with the first message.
  // The backend sets session.metadata["primary_task_id"] from this,
  // enabling plan context injection before the first LLM call.
  pane.setPendingTaskId(task.taskId);    // ← NEW

  final buffer = StringBuffer('Task: ${task.title}');
  if (task.description != null && task.description!.isNotEmpty) {
    buffer.write('\n\n${task.description}');
  }

  pane.setPendingInputText(buffer.toString());
  requestInputFocus();
}
```

### 6.6 `TaskTile` — plan quick-open badge and context menu

**File:** `rcflowclient/lib/ui/widgets/session_panel/task_tile.dart`

**In the `trailing` row** (around line 126), add a plan badge before the session-count badge:

```dart
if (task.planArtifactId != null)
  Tooltip(
    message: 'Open plan',
    child: GestureDetector(
      onTap: () => state.openArtifactInPane(
        state.activePaneId, task.planArtifactId!),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
        margin: const EdgeInsets.only(right: 4),
        decoration: BoxDecoration(
          color: const Color(0xFF10B981).withAlpha(30),
          borderRadius: BorderRadius.circular(8),
        ),
        child: const Icon(
          Icons.description_outlined,
          size: 11,
          color: Color(0xFF10B981),
        ),
      ),
    ),
  ),
```

**In `_showContextMenu()`**, add items after `start_session`:

```dart
PopupMenuItem(
  value: 'make_plan',
  child: Row(children: [
    Icon(Icons.auto_awesome_outlined,
        color: context.appColors.textSecondary, size: 18),
    const SizedBox(width: 8),
    Text('Make plan',
        style: TextStyle(color: context.appColors.textPrimary)),
  ]),
),
if (task.planArtifactId != null)
  PopupMenuItem(
    value: 'open_plan',
    child: Row(children: [
      const Icon(Icons.description_outlined,
          color: Color(0xFF10B981), size: 18),
      const SizedBox(width: 8),
      Text('Open plan',
          style: TextStyle(color: context.appColors.textPrimary)),
    ]),
  ),
```

Handle in `.then()`:

```dart
} else if (value == 'make_plan') {
  _startPlanSession(context);
} else if (value == 'open_plan') {
  if (task.planArtifactId != null) {
    state.openArtifactInPane(state.activePaneId, task.planArtifactId!);
  }
}
```

```dart
void _startPlanSession(BuildContext context) {
  state.startPlanSession(state.activePaneId, task);
  onTaskSelected?.call();
}
```

### 6.7 `TaskPane` — "Make plan" / "Open plan" header buttons and plan banner

**File:** `rcflowclient/lib/ui/widgets/task_pane.dart`

**In `_TaskPaneHeader.build()`** (after the existing edit/delete buttons):

```dart
if (task case final t?) ...[
  // ... existing edit + delete buttons ...

  if (t.planArtifactId == null)
    SizedBox(
      width: 26, height: 26,
      child: IconButton(
        padding: EdgeInsets.zero,
        icon: Icon(Icons.auto_awesome_outlined,
            color: context.appColors.textMuted, size: 14),
        tooltip: 'Make plan',
        onPressed: () => appState.startPlanSession(paneId, t),
      ),
    ),

  if (t.planArtifactId != null)
    SizedBox(
      width: 26, height: 26,
      child: IconButton(
        padding: EdgeInsets.zero,
        icon: const Icon(Icons.description_outlined,
            color: Color(0xFF10B981), size: 14),
        tooltip: 'Open plan',
        onPressed: () =>
            appState.openArtifactInPane(paneId, t.planArtifactId!),
      ),
    ),
],
```

**In `_TaskDetailContent`** (the scrollable body), add a `_PlanBanner` when a plan exists:

```dart
if (task.planArtifactId case final planId?) ...[
  const SizedBox(height: 8),
  _PlanBanner(planArtifactId: planId, paneId: widget.paneId, appState: appState),
],
```

`_PlanBanner` is a small private widget displaying:
- A green "Plan available" chip with a document icon
- "Open in pane" → `appState.openArtifactInPane(paneId, planArtifactId)`
- "Re-plan" → `appState.startPlanSession(paneId, task)` (replaces the old plan file in place)

---

## 7. WebSocket Protocol Changes

### 7.1 New input message: `start_plan_session`

```json
{
  "type": "start_plan_session",
  "task_id": "uuid",
  "project_name": "ProjectName",
  "selected_worktree_path": "/abs/path"
}
```

Server responds with `ack` before the session starts streaming:

```json
{
  "type": "ack",
  "session_id": "uuid",
  "purpose": "plan"
}
```

### 7.2 Extended `prompt` input message

```json
{
  "type": "prompt",
  "text": "...",
  "session_id": null,
  "task_id": "uuid",
  "project_name": "...",
  "selected_worktree_path": "..."
}
```

`task_id` is optional and backward-compatible (omitting it causes no change in behaviour for existing sessions).

### 7.3 Updated `task_update` and `task_list` output messages

Each task object gains `plan_artifact_id`:

```json
{
  "task_id": "uuid",
  "title": "...",
  "status": "...",
  "plan_artifact_id": "uuid or null",
  "sessions": [...]
}
```

---

## 8. Plan File Storage Convention

Plans are stored at:
```
<project_root>/.rcflow/plans/<task_id>.md
```

- `<project_root>` = `session.main_project_path` (resolved from `project_name`). Falls back to `settings.projects_dirs[0]` if no project is selected.
- If neither is available, `prepare_plan_session()` raises `RuntimeError` and the WS handler returns an error to the client — preventing a silent half-started session.
- File names use the raw `task_id` UUID to avoid path-escaping issues with task titles.
- The directory `.rcflow/plans/` is created on-demand by the LLM (the prompt instructs it). The directory may also be created proactively by `_finalize_plan_session()` via `plan_path.parent.mkdir(parents=True, exist_ok=True)` before the `Artifact` insert.
- Consider adding `.rcflow/plans/` to the project's `.gitignore` unless the user wants plans versioned.

---

## 9. "Re-plan" Flow

If a task already has a plan and the user clicks "Make plan" again:
1. Client sends `start_plan_session` as normal.
2. Backend creates a new session via `prepare_plan_session()`.
3. LLM overwrites the existing `.rcflow/plans/<task_id>.md`.
4. `_finalize_plan_session()` finds the existing `Artifact` row (by the unique `(backend_id, file_path)` constraint) and updates it in-place (`file_size`, `modified_at`, `session_id`).
5. `task.plan_artifact_id` already points to this artifact — it stays unchanged.
6. No duplicate `Artifact` record is created.

---

## 10. Edge Cases

| Case | Handling |
|------|----------|
| LLM writes plan to wrong path or never writes | `_finalize_plan_session()` checks `plan_output_path` from metadata; logs warning and returns without DB writes. Task retains any existing plan link. |
| Plan session cancelled before LLM finishes | `cancel_session()` triggers `_fire_plan_finalization_task()`. File check guards: if no file, nothing happens. |
| Plan session fails (unhandled exception) | `handle_prompt()` failure path (line ~809) also calls `_fire_plan_finalization_task()`. Same file-check guard applies. |
| Plan file deleted externally | `ArtifactScanner` sets `file_exists = false`. Client should check `artifact.fileExists` before showing "Open plan" and instead show a "Plan missing" indicator with a "Re-plan" option. |
| Task deleted while plan session is running | `_finalize_plan_session()` fetches the task; if `None`, skips DB writes. Plan file may still exist on disk (orphaned). |
| Two concurrent plan sessions for same task | Both write to the same path; last one wins on disk. Both call `_finalize_plan_session()`; the second update wins in DB. Mitigate by disabling the "Make plan" button while any session with `session_purpose == "plan"` for that task is active (check task's session list for an active plan session). |
| No project configured for the worker/pane | `prepare_plan_session()` raises `RuntimeError`. WS handler returns `{type: "error", ...}` to client. |
| `ArtifactScanner` race on plan file insert | Handled by the select-then-insert-with-rollback pattern in `_finalize_plan_session()` (see §4.9). The `(backend_id, file_path)` `UniqueConstraint` is the safety net. |
| Plan injected into its own planning session | Guarded in `_build_plan_context()` by checking `session.metadata.get("session_purpose") == "plan"` and returning `None` in that case. |
| Plan file exceeds LLM context window on injection | Truncate plan text to a maximum (e.g., 8,000 characters) before injecting, with a note that the full plan is available at the file path. |

---

## 11. Design.md Updates Required

The following sections of `Design.md` must be updated **as part of the same PR** (per CLAUDE.md project rules).

### WebSocket Protocol — Input Messages

Add new entry:

> **`start_plan_session`**
> Triggers a read-only planning session for a task. The server responds with an `ack` immediately and streams session output as with any other session.
>
> ```json
> { "type": "start_plan_session", "task_id": "uuid", "project_name": "string|null", "selected_worktree_path": "string|null" }
> ```
>
> Response: `{ "type": "ack", "session_id": "uuid", "purpose": "plan" }`

Add `task_id` to the `prompt` message schema:

> `task_id` *(optional)* — UUID of the task being implemented. When provided, the backend stores it in `session.metadata["primary_task_id"]` before the first LLM call, enabling automatic plan context injection if the task has a plan artifact.

### WebSocket Protocol — Output Messages

Add `plan_artifact_id` to `task_update` and `task_list` schemas:

> `plan_artifact_id` — UUID of the plan `Artifact` record linked to this task, or `null` if no plan has been generated yet.

### HTTP API Endpoints

Add entry:

> **`POST /api/tasks/{task_id}/plan`** — Triggers a planning session for the task. Body: `{ "project_name": "string|null", "selected_worktree_path": "string|null" }`. Returns `{ "session_id": "uuid" }`. The session runs asynchronously.

### Database Models — Task

Add column documentation:

> **`plan_artifact_id`** *(UUID, nullable, FK → `artifacts.id` ON DELETE SET NULL)* — Points to the most recently generated plan `Artifact` for this task. `null` means no plan has been created.

### Session Lifecycle

Add a new subsection:

> **Planning Sessions** — Sessions with `session.metadata["session_purpose"] == "plan"` and `SessionType.ONE_SHOT`. These sessions have pre-seeded permission rules (in `session.metadata["permission_rules"]`) restricting writes to a single path (`.rcflow/plans/<task_id>.md`). On session termination (any reason: end, cancel, or failure), `_fire_plan_finalization_task()` is called to register the plan file as an `Artifact` and link it to the task.

---

## 12. Testing Strategy

### 12.1 Backend unit tests (`pytest`)

| Test | File |
|------|------|
| `test_prepare_plan_session_creates_session_with_plan_metadata` | `tests/core/test_prompt_router.py` |
| `test_prepare_plan_session_raises_if_task_not_found` | `tests/core/test_prompt_router.py` |
| `test_prepare_plan_session_raises_if_no_project` | `tests/core/test_prompt_router.py` |
| `test_planning_prompt_includes_task_title_and_description` | `tests/core/test_prompt_router.py` |
| `test_handle_prompt_stores_primary_task_id_in_metadata` | `tests/core/test_prompt_router.py` |
| `test_finalize_plan_session_creates_artifact_and_links_task` | `tests/core/test_background_tasks.py` |
| `test_finalize_plan_session_skips_if_file_missing` | `tests/core/test_background_tasks.py` |
| `test_finalize_plan_session_updates_existing_artifact_on_replan` | `tests/core/test_background_tasks.py` |
| `test_finalize_plan_session_handles_scanner_race_condition` | `tests/core/test_background_tasks.py` |
| `test_build_plan_context_injects_plan_when_present` | `tests/core/test_context.py` |
| `test_build_plan_context_skips_for_plan_sessions` | `tests/core/test_context.py` |
| `test_build_plan_context_skips_when_no_plan` | `tests/core/test_context.py` |
| `test_task_to_dict_includes_plan_artifact_id` | `tests/api/test_tasks.py` |
| `test_list_tasks_ws_includes_plan_artifact_id` | `tests/api/test_ws_output.py` |
| `test_permission_rules_deny_write_outside_plan_dir` | `tests/core/test_permissions.py` |
| `test_permission_rules_allow_write_inside_plan_dir` | `tests/core/test_permissions.py` |
| `test_update_task_clears_plan_artifact_id_when_null_sent` | `tests/api/test_tasks.py` |
| `test_update_task_ignores_plan_artifact_id_when_not_in_fields_set` | `tests/api/test_tasks.py` |
| `test_pending_plan_finalization_tasks_awaited_on_shutdown` | `tests/core/test_session_lifecycle.py` |

### 12.2 Integration tests

- Full flow: create task → trigger plan session via WS → verify `.rcflow/plans/<task_id>.md` written → verify `Artifact` record created → verify `task.plan_artifact_id` set → verify `task_update` broadcast contains `plan_artifact_id`.
- Re-plan flow: trigger a second plan session → verify `Artifact` record updated (not duplicated) → verify same UUID in `task.plan_artifact_id`.
- Interrupted plan session: cancel mid-run → verify no crash → verify task `plan_artifact_id` unchanged.
- Implementation session with plan: create task + plan → start regular session with `task_id` → confirm `_build_plan_context` appended to LLM context.

### 12.3 Flutter widget tests

| Test | Widget |
|------|--------|
| `TaskTile` shows plan badge when `planArtifactId != null` | `task_tile_test.dart` |
| `TaskTile` plan badge tap calls `openArtifactInPane` | `task_tile_test.dart` |
| `TaskTile` context menu shows "Make plan" item | `task_tile_test.dart` |
| `TaskTile` context menu shows "Open plan" only when plan exists | `task_tile_test.dart` |
| `TaskPane` header shows "Make plan" button when no plan | `task_pane_test.dart` |
| `TaskPane` header shows "Open plan" button when plan exists | `task_pane_test.dart` |
| `TaskPane` body shows `_PlanBanner` when plan exists | `task_pane_test.dart` |
| `AppState.startPlanSession()` sends `start_plan_session` WS message | `app_state_test.dart` |
| `AppState.startSessionFromTask()` sets `pendingTaskId` on pane | `app_state_test.dart` |
| `PaneState.sendPrompt()` includes `task_id` when `_pendingTaskId` set | `pane_state_test.dart` |
| `PaneState.sendPrompt()` clears `_pendingTaskId` after first send | `pane_state_test.dart` |

### 12.4 Manual QA checklist

- [ ] Click "Make plan" on a task without a project selected → error toast shown
- [ ] Click "Make plan" on a task → session pane opens, streaming output visible
- [ ] After plan session completes → task tile shows green plan badge
- [ ] Click plan badge in task tile → artifact pane opens with plan Markdown content
- [ ] Open task pane → `_PlanBanner` visible with "Open in pane" and "Re-plan" buttons
- [ ] Start implementation session for task with plan → plan text appears in session context (confirm via backend logs)
- [ ] Click "Make plan" again on task with existing plan → plan file updated, no duplicate artifacts in sidebar
- [ ] Delete plan file externally → "Plan missing" indicator shown instead of "Open plan"
- [ ] Cancel a plan session mid-run → no crash, no partial artifact record created

---

## 13. Versioning

Per project conventions:
- This is a **MINOR** version bump (new feature, backward-compatible).
- **Backend** (`pyproject.toml`): bump `version` field.
- **Client** (`rcflowclient/pubspec.yaml`): bump `version` field.

Both bumps must be in the same PR as the feature implementation.

---

## 14. Rollout Notes

1. **Database migration must run before deploying the new backend.** The `plan_artifact_id` column is nullable with no default — safe to apply to live data without downtime.
2. **Client and backend can be briefly out of sync.** The client handles `plan_artifact_id: null` gracefully; no crash if the field is missing. Update both simultaneously when possible.
3. **`.rcflow/plans/` is created on demand.** No pre-provisioning required.
4. **Existing tasks** receive `plan_artifact_id: null` after migration. No backfill required.
5. **The permission pre-seeding relies on `restore_rules()`** being called at session creation. This is already done in `session_lifecycle.py:752–756` when `session.metadata["permission_rules"]` is non-empty — the mechanism is in place.
6. **Plan files are not ephemeral.** They persist on disk after session archival, as regular files in the project directory.

---

## 15. Files to Change — Summary

### Backend

| File | Change |
|------|--------|
| `src/models/db.py` | Add `plan_artifact_id` FK to `Task` |
| `src/db/migrations/versions/<id>_add_plan_artifact_id_to_tasks.py` | New Alembic migration |
| `src/api/routes/tasks.py` | `_task_to_dict_full`: add field; new `POST /tasks/{id}/plan`; fix `UpdateTaskRequest` with `model_fields_set` |
| `src/api/ws/input_text.py` | Handle `start_plan_session`; extract `task_id` from `prompt` message |
| `src/api/ws/output_text.py` | Include `plan_artifact_id` in `list_tasks` output |
| `src/core/prompt_router.py` | New `prepare_plan_session()` + `_build_planning_prompt()`; add `task_id` param to `handle_prompt()`; set `primary_task_id` in metadata; new `_pending_plan_finalization_tasks` set in `__init__` |
| `src/core/background_tasks.py` | New `_fire_plan_finalization_task()` and `_finalize_plan_session()` |
| `src/core/session_lifecycle.py` | Call `_fire_plan_finalization_task()` in `end_session()` (after line 275) and `cancel_session()` (after line 178); add `_pending_plan_finalization_tasks` to `wait_for_pending_tasks()` loop |
| `src/core/context.py` | New `_build_plan_context()`; call it during context assembly; skip for plan sessions |
| `Design.md` | Update WS protocol (new input message, `task_id` on `prompt`, `plan_artifact_id` on outputs), HTTP API, DB model, session lifecycle sections (see §11 for draft text) |

### Flutter Client

| File | Change |
|------|--------|
| `rcflowclient/lib/models/task_info.dart` | Add `planArtifactId` field with `copyWith` sentinel |
| `rcflowclient/lib/services/websocket_service.dart` | Add `taskId` param to `sendPrompt()`; add `startPlanSession()` method |
| `rcflowclient/lib/state/pane_state.dart` | Add `_pendingTaskId` field, getter, setter, clear; pass to `sendPrompt()`; clear in `startNewChat()` |
| `rcflowclient/lib/state/app_state.dart` | Add `startPlanSession()`; update `startSessionFromTask()` to call `pane.setPendingTaskId()` |
| `rcflowclient/lib/ui/widgets/session_panel/task_tile.dart` | Plan badge in trailing; "Make plan"/"Open plan" in context menu |
| `rcflowclient/lib/ui/widgets/task_pane.dart` | "Make plan"/"Open plan" header buttons; `_PlanBanner` private widget in body |
| `rcflowclient/pubspec.yaml` | Version bump |
| `pyproject.toml` | Version bump |
