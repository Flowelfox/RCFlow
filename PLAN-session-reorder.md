# Drag-and-Reorder Sessions — Implementation Plan

## Architecture Summary

Server-side ordering using sparse integers. Move-based API instead of full-list replacement. Per-group `ReorderableListView` in grouped mode. Explicit gesture separation between reorder (long-press) and pane-drop (drag handle).

---

## 1. Backend: Data Model

**File: `src/models/db.py` — `Session` model**

Add column:
```python
sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

**New migration** in `src/db/migrations/versions/`:
- Add nullable `sort_order` integer column to `sessions` table
- No index needed (list sizes are small per-worker)

**Sorting logic change** in `SessionManager.list_all_with_archived()` and `list_all_sessions()`:
- Sort by: `sort_order ASC NULLS LAST`, then `created_at DESC`
- Same logic in the `session_list` WebSocket response builder

**Sparse integer strategy:**
- When assigning order, use gaps of **1000** (e.g., 0, 1000, 2000, ...)
- New sessions get `min(existing) - 1000` or `0` if no sessions exist
- Reorder endpoint re-normalizes all sessions with `i * 1000` gaps

---

## 2. Backend: Move-Based API Endpoint

**New endpoint in `src/api/routes/sessions.py`:**
```
PATCH /api/sessions/{session_id}/reorder
Body: {"after_session_id": str | null}
```

Semantics:
- `after_session_id = null` — move to the very top of the list
- `after_session_id = "uuid"` — place immediately after that session
- Server builds the full ordered list, removes the target, inserts at the new position, then assigns `sort_order = i * 1000` for all sessions
- Persists to DB, then broadcasts `session_reorder` event via WebSocket

**Validation:**
- 404 if `session_id` or `after_session_id` not found (checks both in-memory and DB)
- 400 if `after_session_id == session_id`
- 400 if `after_session_id` is not a valid UUID

**New session handling:**
- On session creation, assign `sort_order = min(existing) - 1000` so new sessions appear at top
- If no sessions exist yet, assign `sort_order = 0`

---

## 3. Backend: WebSocket Event

**New event type: `session_reorder`**
```json
{"type": "session_reorder", "order": ["uuid1", "uuid2", "uuid3", ...]}
```

- Sent after a successful reorder to all connected clients
- Contains just the ordered session IDs (lightweight)
- Client uses this to reorder its local list without a full refresh
- The existing `session_list` full-refresh still works as fallback (reconnection, initial load)

---

## 4. Client: Data Model Changes

**File: `rcflowclient/lib/models/session_info.dart` — `SessionInfo`**
- Add `final int? sortOrder;`
- Wire into `fromJson()`, `toJson()`, `copyWith()` (with `_keep` sentinel pattern)

**File: `rcflowclient/lib/services/worker_connection.dart`**
- `_sortSessions()` helper: sort by `sortOrder` ascending (nulls last → `1 << 62`), then `createdAt` desc
- Handle new `session_reorder` WebSocket message type: map IDs to `sort_order` values, re-sort, cache
- `_handleSessionUpdate` preserves `sortOrder` from server using `containsKey` sentinel
- `reorderSession()` method exposed to UI → delegates to `WebSocketService`

**File: `rcflowclient/lib/services/websocket_service.dart`**
- `reorderSession(sessionId, {afterSessionId})` — HTTP PATCH to `/api/sessions/{id}/reorder`

**File: `rcflowclient/lib/ui/widgets/session_panel/session_list_panel.dart`**
- `compareBySortOrder()` — shared sort comparator (library-level function, also used by `WorkerGroup`)
- `computeFlatVisibleSessionList()` uses `compareBySortOrder` in both grouped and ungrouped paths

---

## 5. Client: Gesture Design — Coexistence

**Gesture mapping:**

| Gesture | Behavior |
|---|---|
| Tap | Select/open session (unchanged) |
| Long-press (on tile body) | Initiates reorder drag (via `ReorderableDragStartListener`) |
| Drag (from grip icon in leading area) | Pane-split drop (via `Draggable<SessionDragData>`) |
| Right-click | Context menu including Rename (unchanged) |
| Ctrl/Cmd+Up/Down | Keyboard reorder of single selected session |

**Changes from original:**
- `onLongPress` rename removed from `ListTile` (rename available via right-click context menu)
- `Draggable<SessionDragData>` moved from wrapping entire tile to a small `Icons.drag_indicator_rounded` grip icon in the leading area
- `_buildDragFeedback()` extracted as shared method for the floating drag card

---

## 6. Client: UI — Ungrouped Mode

**File: `rcflowclient/lib/ui/widgets/session_panel/worker_group.dart`**

- Terminals rendered first as plain widgets (not reorderable), sorted by `createdAt` desc
- Sessions wrapped in `ReorderableListView.builder` with `shrinkWrap: true` + `NeverScrollableScrollPhysics()`
- `buildDefaultDragHandles: false` — reorder initiated via `ReorderableDragStartListener` wrapping each tile
- `proxyDecorator` provides elevated Material appearance during drag
- `onReorder` callback: computes `afterSessionId` from remaining list, calls `widget.onReorder`

---

## 7. Client: UI — Grouped Mode (Per-Project Constraint)

Each project group's session list is its own `ReorderableListView` with `shrinkWrap` + `NeverScrollableScrollPhysics()`. Project headers remain plain widgets outside any reorderable list. Cross-project moves are **structurally impossible** — no boundary detection logic needed.

---

## 8. Reorder Disabled States

Reorder is disabled (falls back to plain non-reorderable tiles) when:
- Search query is active
- Status filters are applied
- Multi-select mode is active (gesture conflicts)
- Only 0 or 1 sessions in the list (nothing to reorder)

Controlled by `reorderEnabled` prop on `WorkerGroup`, computed in `SessionListPanel`.

---

## 9. Keyboard Reorder (Ctrl+Up/Down)

In `SessionListPanel`'s `Focus.onKeyEvent` handler:
- Only when exactly 1 session is selected, no search/filters active
- Ctrl/Cmd+ArrowUp: move before the item above (afterSessionId = item two above, or null for top)
- Ctrl/Cmd+ArrowDown: move after the item below

---

## 10. Accessibility

- `Semantics` widget wraps each reorderable tile with label `'Session: ${title}'`
- `ReorderableListView` provides built-in screen reader support on mobile
- Desktop keyboard reorder via Ctrl+Up/Down (custom, not built into Flutter)

---

## 11. Edge Cases

| Case | Handling |
|---|---|
| New session created | Server assigns `sort_order = min - 1000`, appears at top |
| Session archived/ended | Disappears from list; `sort_order` preserved in DB for restore |
| Session restored | Keeps its original `sort_order` value, reappears in its old position |
| Multiple clients | WebSocket `session_reorder` broadcast syncs all clients |
| Filtered/searched list | Reordering disabled |
| Multi-select active | Reordering disabled |
| Terminal sessions | Not reorderable, always shown at top of worker group |
| Collapsed project group | `ReorderableListView` not rendered when collapsed |
| Empty / single session | Falls back to plain non-reorderable tiles |

---

## 12. Testing

**Backend (9 tests in `tests/test_api/test_routes_sessions_reorder.py`):**
- Move to top, after specific session, to bottom
- Self-reference rejected (400)
- Session not found (404), anchor not found (404), invalid UUID
- `sort_order` assigned on creation
- Multiple sequential reorders maintain consistency

**Client:**
- Existing 188 Flutter tests pass (no regressions)

---

## 13. Implementation Sequence

**Phase 1 — Backend foundation**
1. Add `sort_order` column to `Session` model + migration
2. Add `sort_order` field to `ActiveSession`
3. Update sort logic in `SessionManager.list_all_with_archived()`
4. Update `archive_session()` / `restore_session()` to persist/restore `sort_order`
5. Update `broadcast_session_update()` to include `sort_order`
6. Add `broadcast_session_reorder()` method
7. Assign `sort_order` on `create_session()`
8. Add `PATCH /api/sessions/{id}/reorder` endpoint
9. Add `sort_order` to REST `list_sessions` and WS `list_sessions` responses
10. Backend tests

**Phase 2 — Client data layer**
11. Add `sortOrder` to `SessionInfo` model (field, fromJson, toJson, copyWith)
12. Add `reorderSession()` to `WebSocketService`
13. Update `WorkerConnection`: `_sortSessions()`, `_handleSessionReorder()`, `reorderSession()`
14. Update `_handleSessionUpdate` to handle `sort_order` field
15. Update `computeFlatVisibleSessionList` to use `compareBySortOrder`

**Phase 3 — Gesture restructuring**
16. Remove `onLongPress` rename from `ListTile` in `_buildSessionTile`
17. Extract `_buildDragFeedback()` as shared method
18. Keep `Draggable<SessionDragData>` on original tiles (non-reorderable path)

**Phase 4 — Reorder UI (ungrouped)**
19. Add `reorderEnabled` and `onReorder` props to `WorkerGroup`
20. Separate terminals from sessions in `_buildMergedSessionList`
21. Add `_buildReorderableSessionList` with `ReorderableListView.builder`
22. Add `_buildReorderableSessionTile` with drag handle + `ReorderableDragStartListener`
23. Wire `reorderEnabled` and `onReorder` in `SessionListPanel`

**Phase 5 — Reorder UI (grouped)**
24. Update `_buildProjectGroupedSessionList` to use per-project `ReorderableListView`
25. Use `compareBySortOrder` for project session sorting

**Phase 6 — Polish**
26. Keyboard shortcuts (Ctrl+Up/Down) in `SessionListPanel.onKeyEvent`
27. Accessibility `Semantics` on reorderable tiles
28. Version bumps (backend 0.36.0, client 1.38.0+75)
29. Update `Design.md` (session list example, sorting, new Session Reordering section)

---

## Files Changed

| File | Change |
|---|---|
| `src/models/db.py` | Added `sort_order` column |
| `src/db/migrations/versions/e4f5a6b7c8d9_...py` | New migration |
| `src/core/session.py` | `sort_order` on `ActiveSession`, sorting, broadcast, archive/restore |
| `src/api/routes/sessions.py` | Reorder endpoint, `sort_order` in list response |
| `src/api/ws/output_text.py` | `sort_order` in WS session list |
| `tests/test_api/test_routes_sessions_reorder.py` | 9 endpoint tests |
| `rcflowclient/lib/models/session_info.dart` | `sortOrder` field |
| `rcflowclient/lib/services/websocket_service.dart` | `reorderSession()` HTTP method |
| `rcflowclient/lib/services/worker_connection.dart` | Sorting, reorder event, API call |
| `rcflowclient/lib/ui/widgets/session_panel/session_list_panel.dart` | `compareBySortOrder`, keyboard shortcuts, `reorderEnabled`/`onReorder` |
| `rcflowclient/lib/ui/widgets/session_panel/worker_group.dart` | `ReorderableListView`, gesture restructuring, drag handle |
| `rcflowclient/pubspec.yaml` | Version bump |
| `pyproject.toml` | Version bump |
| `Design.md` | Documentation |
