# Drafts Per Session — Implementation Plan

All 15 issues from the critique are addressed below. Changed decisions are explicitly called out.

---

## Architectural Summary

| Layer | Role |
|---|---|
| Backend DB | `drafts` table, one row per session |
| Backend REST | `PUT /sessions/{id}/draft`, `GET /sessions/{id}/draft` |
| `PaneState` | Owns `_draftProvider` callback (read) and `_lastLoadedDraft` (change-detection guard); fires async save/load; restores via existing `setPendingInputText` |
| `InputArea` | Registers `_draftProvider` in `initState`; 800ms debounce writes to `PaneState`; captures `PaneState` ref before any timer |
| `SettingsService` | Local cache, keyed by session ID or `"new_{workerId}"` |

---

## Step 1 — Backend: Add `Draft` Model

**File:** `src/models/db.py`

Add after the `SessionMessage` class:

```python
class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
```

**Why `updated_at` has no `onupdate=`:** `onupdate` is a SQLAlchemy client-side hook that does not fire on raw SQL upserts (which the endpoint will use for atomicity). The timestamp will be set explicitly in the upsert statement using `datetime.now(UTC)`. See Step 3.

**Alembic autogenerate:** `env.py` line 8 already imports `from src.models.db import Base` and uses `Base.metadata` as `target_metadata`. Adding `Draft` to `db.py` is sufficient — no changes to `env.py` needed.

---

## Step 2 — Backend: Migration

```
just migrate-gen "add drafts table"
```

The generated migration creates the `drafts` table. No data migration needed. Verify the generated migration references `cascade="all, delete-orphan"` or that the FK `ondelete="CASCADE"` is rendered correctly for the target dialect.

---

## Step 3 — Backend: Draft Endpoints

**File:** `src/api/routes/sessions.py`

Add two endpoints and their Pydantic schemas. Follow the existing dependency injection pattern (`request.app.state.db_session_factory`).

**Schemas (inline or in a shared schemas file):**
```python
class DraftUpsertRequest(BaseModel):
    content: str

class DraftResponse(BaseModel):
    content: str
    updated_at: datetime
```

**`PUT /sessions/{session_id}/draft`**

Uses a dialect-appropriate upsert (`INSERT ... ON CONFLICT DO UPDATE`) so the call is safe on first write and on subsequent updates. The `updated_at` timestamp is passed explicitly in the statement — not delegated to `onupdate` — because `onupdate` does not fire on raw SQL upserts:

```python
@router.put(
    "/sessions/{session_id}/draft",
    summary="Save or update session draft",
    response_class=Response,
    status_code=204,
    dependencies=[Depends(verify_http_api_key)],
)
async def upsert_draft(
    session_id: uuid.UUID,
    body: DraftUpsertRequest,
    request: Request,
) -> Response:
    db_factory = request.app.state.db_session_factory
    if db_factory is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    async with db_factory() as db:
        # Verify session exists
        row = await db.get(SessionModel, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        now = datetime.now(UTC)
        stmt = (
            insert(Draft)
            .values(session_id=session_id, content=body.content, updated_at=now)
            .on_conflict_do_update(
                index_elements=["session_id"],
                set_={"content": body.content, "updated_at": now},
            )
        )
        await db.execute(stmt)
        await db.commit()
    return Response(status_code=204)
```

**Why `Response(status_code=204)` explicitly:** FastAPI's default response serialization returns `200 + JSON`. To emit a true 204 with no body, the endpoint must return `Response(status_code=204)` and be annotated with `response_class=Response`. Without this, FastAPI serializes `None` as `"null"` with status 200.

**`GET /sessions/{session_id}/draft`**

```python
@router.get(
    "/sessions/{session_id}/draft",
    summary="Get session draft",
    dependencies=[Depends(verify_http_api_key)],
)
async def get_draft(
    session_id: uuid.UUID,
    request: Request,
) -> DraftResponse:
    db_factory = request.app.state.db_session_factory
    if db_factory is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    async with db_factory() as db:
        result = await db.execute(
            select(Draft).where(Draft.session_id == session_id)
        )
        draft = result.scalar_one_or_none()
        if draft is None:
            return DraftResponse(content="", updated_at=datetime.now(UTC))
        return DraftResponse(content=draft.content, updated_at=draft.updated_at)
```

Returns `content: ""` (never 404) so callers don't need to distinguish "no draft" from "empty draft." The `updated_at` field is used by the client for cache-vs-backend conflict resolution (Step 6).

---

## Step 4 — Flutter: `WebSocketService` — Two New HTTP Methods

**File:** `rcflowclient/lib/services/websocket_service.dart`

Add alongside `endSession` (around line 338), following the exact same `io.HttpClient` pattern already used in `fetchSessionMessages`:

```dart
Future<({String content, DateTime updatedAt})> getSessionDraft(
  String sessionId,
) async {
  if (_serverUrl == null) return (content: '', updatedAt: DateTime.now());
  final url = _serverUrl!.http('/api/sessions/$sessionId/draft');
  final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
  try {
    final request = await client.getUrl(url);
    request.headers.set('X-API-Key', _serverUrl!.apiKey);
    final response = await request.close();
    final body = await response.transform(const io.SystemEncoding().decoder).join();
    if (response.statusCode != 200) return (content: '', updatedAt: DateTime.now());
    final map = jsonDecode(body) as Map<String, dynamic>;
    return (
      content: map['content'] as String? ?? '',
      updatedAt: DateTime.parse(map['updated_at'] as String),
    );
  } catch (_) {
    return (content: '', updatedAt: DateTime.now());
  } finally {
    client.close();
  }
}

Future<void> saveSessionDraft(String sessionId, String content) async {
  if (_serverUrl == null) return;
  final url = _serverUrl!.http('/api/sessions/$sessionId/draft');
  final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
  try {
    final request = await client.putUrl(url);
    request.headers.set('X-API-Key', _serverUrl!.apiKey);
    request.headers.contentType = io.ContentType.json;
    request.write(jsonEncode({'content': content}));
    final response = await request.close();
    await response.drain<void>();
  } catch (_) {
    // best-effort; local cache already written
  } finally {
    client.close();
  }
}
```

Both methods swallow errors silently. Draft persistence is best-effort; a network blip must never break the UX. The local SharedPreferences write happens first (Step 5) so the draft is always durable locally even if the backend call fails.

---

## Step 5 — Flutter: `SettingsService` — Local Draft Cache

**File:** `rcflowclient/lib/services/settings_service.dart`

Add three methods using the existing `SharedPreferences` pattern. The key scheme:
- Existing session: `"rcflow_draft_session_{sessionId}"`
- New-session pane: `"rcflow_draft_new_{workerId}"` — **scoped to worker**, not a global singleton. This prevents Worker A's new-session draft from bleeding into Worker B's new-session pane.

```dart
static const _draftSessionPrefix = 'rcflow_draft_session_';
static const _draftNewPrefix = 'rcflow_draft_new_';

Future<({String content, DateTime? cachedAt})> getDraft(String key) async {
  final prefs = await SharedPreferences.getInstance();
  final content = prefs.getString('$_draftSessionPrefix$key') ?? '';
  final cachedAtMs = prefs.getInt('${_draftSessionPrefix}${key}_ts');
  final cachedAt = cachedAtMs != null
      ? DateTime.fromMillisecondsSinceEpoch(cachedAtMs, isUtc: true)
      : null;
  return (content: content, cachedAt: cachedAt);
}

Future<void> saveDraft(String key, String content) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.setString('$_draftSessionPrefix$key', content);
  await prefs.setInt(
    '${_draftSessionPrefix}${key}_ts',
    DateTime.now().millisecondsSinceEpoch,
  );
}

Future<void> clearDraft(String key) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.remove('$_draftSessionPrefix$key');
  await prefs.remove('${_draftSessionPrefix}${key}_ts');
}
```

The `_ts` companion key stores when this cache entry was written, so the client can compare it to the backend's `updated_at` (Step 6) without a separate data structure.

---

## Step 6 — Flutter: Draft State in `PaneState`

**File:** `rcflowclient/lib/state/pane_state.dart`

### 6a. New fields

```dart
// Draft management
String Function()? _draftProvider;     // set by InputArea; reads controller text
String _lastLoadedDraft = '';          // guards multi-pane overwrites (see below)
```

### 6b. `registerDraftProvider` — called by InputArea in `initState`

```dart
void registerDraftProvider(String Function() provider) {
  _draftProvider = provider;
}

void unregisterDraftProvider() {
  _draftProvider = null;
}
```

**Why a callback rather than having PaneState own the controller:** `_InputAreaState` owns `TextEditingController` (input_area.dart line 89). PaneState is a pure state holder with no widget references. The callback is the minimal seam — PaneState reads the value synchronously at the moment it needs it (during `switchSession`/`goHome`) without owning the controller's lifecycle.

### 6c. `_saveDraftIfChanged` — internal, synchronous trigger + async fire-and-forget

```dart
void _saveDraftIfChanged() {
  final text = _draftProvider?.call() ?? '';
  // Multi-pane guard: only write if this pane actually modified the draft.
  // A pane that loaded a draft and never typed should never overwrite the
  // backend with empty string or stale content.
  if (text == _lastLoadedDraft) return;

  final key = _sessionId ?? (_workerId != null ? 'new_$_workerId' : null);
  if (key == null) return;

  // Write local cache synchronously (returns Future but fire-and-forget is fine)
  unawaited(_host.settings.saveDraft(key, text));

  // Write backend for real sessions only (new-session pane is local-only)
  if (_sessionId != null) {
    unawaited(_ws?.saveSessionDraft(_sessionId!, text));
  }
}
```

**Why multi-pane guard works:** When a second pane loads session X it calls `_lastLoadedDraft = loadedDraft`. If the user never types in that pane, `_draftProvider?.call()` returns `loadedDraft` (unchanged controller text), so `text == _lastLoadedDraft` and no save fires. The first pane's live draft is safe.

### 6d. `_loadDraftAsync` — two-phase load

**Backend-wins policy** (replaces the length heuristic from the original plan):

```dart
Future<void> _loadDraftAsync(String sessionId) async {
  final key = sessionId;

  // Phase 1: fast path — local cache, no network
  final local = await _host.settings.getDraft(key);
  if (local.content.isNotEmpty) {
    _lastLoadedDraft = local.content;
    setPendingInputText(local.content);  // existing mechanism, no new wiring
  }

  // Phase 2: authoritative — backend fetch
  try {
    final remote = await _ws?.getSessionDraft(sessionId);
    if (remote == null) return;
    // Backend wins if it has content and its updated_at is newer than local cache.
    final useRemote = remote.content.isNotEmpty &&
        (local.cachedAt == null ||
         remote.updatedAt.isAfter(local.cachedAt!));
    if (useRemote && remote.content != local.content) {
      _lastLoadedDraft = remote.content;
      unawaited(_host.settings.saveDraft(key, remote.content));
      setPendingInputText(remote.content);  // update input if user hasn't typed yet
    }
  } catch (_) {
    // network failure — local cache is the fallback
  }
}
```

**Conflict resolution:** backend wins when its `updated_at` is newer than the local cache timestamp. This replaces the "prefer the longer one" heuristic, which was incorrect (length ≠ recency).

### 6e. Wire into `switchSession`

`switchSession` stays `void` (synchronous contract preserved). Draft save is a synchronous snapshot + fire-and-forget. Draft load is kicked off at the end:

```dart
void switchSession(String sessionId, {bool recordHistory = true}) {
  if (sessionId == _sessionId) return;

  // Snapshot and save BEFORE clearing state — _sessionId still valid here
  _saveDraftIfChanged();

  // ... existing nav history push, finalizeStream, _messages.clear(), etc. ...

  _sessionId = sessionId;
  // ... existing session lookup, subscribe, project chip sync, etc. ...

  notifyListeners();

  // Kick off async draft load — will call setPendingInputText when ready
  unawaited(_loadDraftAsync(sessionId));
}
```

### 6f. Wire into `goHome`

**`goHome()` was entirely missing from the original plan.** It is a separate code path (pane_state.dart lines 630–659) that also clears `_sessionId`. The same save must fire:

```dart
void goHome() {
  // Snapshot and save BEFORE clearing — same as switchSession
  _saveDraftIfChanged();

  // ... existing goHome body unchanged ...

  // Load new-session draft (worker-scoped local-only key)
  if (_workerId != null) {
    unawaited(_loadNewSessionDraftAsync(_workerId!));
  }

  notifyListeners();
}
```

`_loadNewSessionDraftAsync` is a simplified variant of `_loadDraftAsync` that reads from the local-only key `"new_{workerId}"` and never fetches the backend (new-session pane has no session ID):

```dart
Future<void> _loadNewSessionDraftAsync(String workerId) async {
  final local = await _host.settings.getDraft('new_$workerId');
  if (local.content.isNotEmpty) {
    _lastLoadedDraft = local.content;
    setPendingInputText(local.content);
  }
}
```

### 6g. `startNewChat`

`startNewChat()` (pane_state.dart line 661) is structurally identical to `goHome()` — it also clears `_sessionId`. Apply the same `_saveDraftIfChanged()` + `_loadNewSessionDraftAsync` treatment there as well.

---

## Step 7 — Flutter: Wire `InputArea`

**File:** `rcflowclient/lib/ui/widgets/input_area.dart`

### 7a. Register draft provider in `initState`

```dart
late final PaneState _pane;   // captured once; safe to use in timers

@override
void initState() {
  super.initState();
  _pane = context.read<PaneState>();
  _pane.registerDraftProvider(() => _controller.text);
  _controller.addListener(_onTextChanged);
  // ... existing focusRequestNotifier setup ...
  WidgetsBinding.instance.addPostFrameCallback((_) {
    _consumePendingInput();
  });
}
```

**Why capture `_pane` in `initState`:** The `context.read` is done once, synchronously, while the element is active. Timer callbacks and other async closures then reference `_pane` directly — no `context.read` inside timers, which is the unsafe pattern from the original plan.

### 7b. Unregister in `dispose`

```dart
@override
void dispose() {
  _pane.unregisterDraftProvider();
  _draftTimer?.cancel();
  _focusRequestNotifier.removeListener(_onFocusRequest);
  _debounceTimer?.cancel();
  _removeOverlay();
  _controller.dispose();
  _focusNode.dispose();
  super.dispose();
}
```

### 7c. Draft autosave — second debounce timer

Add `Timer? _draftTimer` alongside the existing `_debounceTimer` (line 218). Extend `_onTextChanged`:

```dart
Timer? _draftTimer;

void _onTextChanged() {
  // Existing 300ms debounce for mention suggestions — unchanged
  _debounceTimer?.cancel();
  _debounceTimer = Timer(const Duration(milliseconds: 300), _updateMentionSuggestions);

  // 800ms debounce for draft persistence
  _draftTimer?.cancel();
  _draftTimer = Timer(const Duration(milliseconds: 800), () {
    // Uses pre-captured _pane — no context.read here
    _pane.triggerDraftSave();
  });

  // ... existing _hasText update ...
}
```

**Why two timers:** The 300ms timer drives mention suggestion UI — it must fire quickly or suggestions feel laggy. The 800ms timer drives persistence — firing at 300ms would generate excessive network calls on every keypress. They serve different SLOs and are intentionally separate. The captured `_pane` reference means neither timer accesses `context` after potential widget disposal.

**`triggerDraftSave()` in PaneState:**
```dart
void triggerDraftSave() {
  _saveDraftIfChanged();
}
```

### 7d. No changes needed for draft restoration

Draft restoration uses `setPendingInputText` (existing mechanism). InputArea already watches `pendingInputText` via `context.select` (build method lines 1078–1084) and calls `_consumePendingInput()` which sets `_controller.text` and moves the cursor (lines 1069–1074). **No new wiring in InputArea's build method.**

### 7e. Clear draft on send — timing fix

The original plan cleared the draft when `sendPrompt` was called. This is wrong: the session does not exist yet at that moment.

**Correct location: `handleAck()` in `pane_state.dart` (line 717).**

When `handleAck` fires, `_sessionId` transitions from `null` to the new session ID:

```dart
void handleAck(String sessionId, {String? workerId}) {
  final wasNewSession = _sessionId == null;  // flag before overwrite
  // ... existing handleAck body ...
  _sessionId = sessionId;  // line 723 — existing
  // ...
  if (wasNewSession && _workerId != null) {
    // Clear the new-session local draft now that a real session exists
    unawaited(_host.settings.clearDraft('new_$_workerId'));
    _lastLoadedDraft = '';
  }
  notifyListeners();
}
```

**Why here and not in `sendPrompt`:** At `sendPrompt` time, the session ID is still unknown. At `handleAck` time, we have the worker-scoped key (`new_{workerId}`) to clear. Clearing here is semantically correct: the draft has been "sent."

---

## Step 8 — Update `Design.md`

Per CLAUDE.md rule 3, document:
- New `drafts` table in the Data Model section
- Two new REST endpoints in the API section
- PaneState draft management (`_draftProvider` callback, `_lastLoadedDraft` guard) in the client architecture section
- Explicit statement that new-session pane drafts are local-only

---

## Step 9 — Version Bumps

- **`pyproject.toml`:** patch bump — new endpoints, backward-compatible
- **`rcflowclient/pubspec.yaml`:** minor bump — new user-visible behavior

---

## Data Flow

```
User types in InputArea
  → _onTextChanged
  → 300ms: update mention suggestions  (existing)
  → 800ms: _pane.triggerDraftSave()    (new, via pre-captured _pane ref)
      → _saveDraftIfChanged()
          → _draftProvider() snapshots _controller.text
          → guard: text == _lastLoadedDraft → skip (no overwrite)
          → SettingsService.saveDraft(key, text)         [local, fire-and-forget]
          → _ws.saveSessionDraft(sessionId, text)        [backend, fire-and-forget]

User triggers switchSession / goHome / startNewChat
  → _saveDraftIfChanged() synchronously snapshots and saves
  → state cleared, new sessionId set
  → notifyListeners()
  → _loadDraftAsync(newSessionId) kicked off
      → Phase 1: local cache hit → _lastLoadedDraft = local; setPendingInputText(local)
      → Phase 2: backend fetch
          → if backend.updatedAt > local.cachedAt → _lastLoadedDraft = remote
                                                   → saveDraft(local cache update)
                                                   → setPendingInputText(remote)
  → InputArea.build: context.select detects pendingInputText != null
      → addPostFrameCallback → _consumePendingInput()
          → _controller.text = draft; cursor at end; focusNode.requestFocus()
          → pane.consumePendingInputText()

User sends prompt from new-session pane
  → sendPrompt() fires; _sessionId still null
  → backend creates session, sends 'ack'
  → AppState routes to pane.handleAck(newSessionId)
      → wasNewSession = true
      → _sessionId = newSessionId
      → clearDraft('new_{workerId}')   [local key cleared]
      → _lastLoadedDraft = ''
```

---

## Edge Cases

| Case | Handling |
|---|---|
| Two panes with the same session — one pane never typed | `_lastLoadedDraft == currentDraft` in the idle pane → save is skipped; active pane's draft survives |
| Session deleted while client has draft | Backend `CASCADE` removes draft row; next `GET` returns `""` |
| Network offline during autosave | Local SharedPreferences write already done; backend write silently dropped |
| `goHome()` called mid-typing (before 800ms timer fires) | `_saveDraftIfChanged()` at top of `goHome()` snapshots live controller text synchronously |
| InputArea not yet built when `switchSession` fires | `_draftProvider == null`; `_saveDraftIfChanged` returns early; load still fires and calls `setPendingInputText` for when InputArea builds |
| App restart — same client | Local cache hit in Phase 1 of `_loadDraftAsync`; draft appears instantly |
| App opened on a second client | No local cache; Phase 1 is empty; Phase 2 fetches from backend |
| `handleAck` fires but `_workerId` is null | Guard `if (wasNewSession && _workerId != null)` prevents `clearDraft('new_null')` |
| New-session pane draft on a different client | Local-only; documented limitation. No session ID exists to store against the backend. |

---

## Testing Strategy

### Backend (pytest) — `tests/test_drafts.py`

1. `PUT` creates row; second `PUT` updates (upsert idempotency)
2. `GET` returns `content: ""` for session with no draft
3. `GET` returns saved content and `updated_at` after `PUT`
4. `PUT` with `content: ""` clears the draft (empty string round-trip)
5. `PUT` on nonexistent `session_id` returns 404
6. Deleting session cascades to delete draft
7. Unicode, newlines, and whitespace-only content round-trips
8. Migration: `alembic upgrade head` runs cleanly on a fresh DB

### Flutter — State Tests — `test/state/pane_state_draft_test.dart`

1. `_saveDraftIfChanged` skips write when `currentDraft == _lastLoadedDraft` (multi-pane guard)
2. `_saveDraftIfChanged` writes when draft changed
3. `switchSession` calls save before clearing state
4. `goHome` calls save before clearing state
5. `startNewChat` calls save before clearing state
6. `handleAck` with `wasNewSession == true` clears the new-session local draft key
7. `_loadDraftAsync`: local cache hit sets `setPendingInputText` immediately
8. `_loadDraftAsync`: backend newer than cache → second `setPendingInputText` call

### Flutter — Widget Tests — `test/widgets/input_area_draft_test.dart`

1. `initState` registers `_draftProvider` on PaneState
2. `dispose` unregisters `_draftProvider`
3. Typing triggers `_draftTimer` → `pane.triggerDraftSave()` after 800ms
4. `setPendingInputText` call on PaneState is consumed and populates `_controller.text`
5. `_pane` reference captured in `initState` — no `context.read` inside timer callbacks

### Flutter — Service Tests — `test/services/settings_service_draft_test.dart`

1. `saveDraft` / `getDraft` round-trip including `cachedAt`
2. `getDraft` returns `(content: '', cachedAt: null)` for unknown key
3. `clearDraft` removes both content and `_ts` companion key
4. Worker-scoped new-session keys don't collide: `"new_worker_a"` ≠ `"new_worker_b"`

---

## File Change Summary

| File | Change |
|---|---|
| `src/models/db.py` | Add `Draft` model |
| `src/api/routes/sessions.py` | Add `PUT` and `GET` draft endpoints; import `Draft`, `insert` |
| `alembic/versions/XXXX_add_drafts_table.py` | Generated migration |
| `rcflowclient/lib/services/websocket_service.dart` | Add `getSessionDraft`, `saveSessionDraft` |
| `rcflowclient/lib/services/settings_service.dart` | Add `getDraft`, `saveDraft`, `clearDraft` with `_ts` companion |
| `rcflowclient/lib/state/pane_state.dart` | Add `_draftProvider`, `_lastLoadedDraft`, `_saveDraftIfChanged`, `_loadDraftAsync`, `_loadNewSessionDraftAsync`, `triggerDraftSave`, `registerDraftProvider`, `unregisterDraftProvider`; wire into `switchSession`, `goHome`, `startNewChat`, `handleAck` |
| `rcflowclient/lib/ui/widgets/input_area.dart` | Capture `_pane` in `initState`; register/unregister provider; add `_draftTimer` with 800ms debounce in `_onTextChanged`; unregister in `dispose` |
| `pyproject.toml` | Patch version bump |
| `rcflowclient/pubspec.yaml` | Minor version bump |
| `Design.md` | Document new table, endpoints, and draft state pattern |
