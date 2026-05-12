# Session Badge Architecture

## 1. Purpose and Scope

This document designs a **unified badge management system** for RCFlow sessions. A *badge* is any small, contextual indicator displayed in session UI that communicates a discrete property of that session to the user.

The goal is a single, extensible architecture so every badge type — existing and future — follows the same creation, transmission, storage, rendering, and lifecycle patterns. New badge types require zero one-off scaffolding.

---

## 2. Current Badge Landscape

### 2.1 Existing Badges (as of 0.38.0)

| Badge | Display Name | Visual | What It Conveys |
|-------|-------------|--------|-----------------|
| **Status** | Active / Paused / Done / Failed / Ended | Colored chip | Current session lifecycle state |
| **Worker** | Worker name | Indigo chip + DNS icon | Which backend server owns this session |
| **Agent** | `claude_code` / `codex` / `opencode` | *(implied, not yet a chip)* | Which executor subprocess is running |
| **Project** | Folder name | *(input area chip)* | `@mention`-resolved project directory |
| **Worktree** | Branch/path | *(input area chip)* | Active git worktree |
| **Caveman** | "Caveman" | Amber chip | Terse-mode compression is active |

### 2.2 Implementation Spread

Currently badges live in multiple disconnected places:

- **Status badge** — computed locally in the widget from `session.status` string; no dedicated model field
- **Worker badge** — derived from `session.workerId` + `WorkerConfig` lookup; interactive state held in widget local state
- **Caveman badge** — driven by `session.cavemanMode` bool on `SessionInfo`; appears both in `SessionIdentityBar` and `session_panel.dart`
- **Project chip** — rendered separately in `input_area.dart` from `session.mainProjectPath` + `session.projectNameError`; not part of `SessionIdentityBar`
- **Worktree chip** — also in `input_area.dart` from `worktreeInfo` / `selectedWorktreePath`; not in identity bar
- **Agent type** — encoded as `agentType` string in `session_update` but no dedicated chip exists yet

Backend serialization: all badge-relevant fields are flattened as top-level keys in `session_update` JSON, mixed with unrelated fields (token counts, timestamps). No grouping or badge-specific contract.

### 2.3 Pain Points

1. **No shared model.** Each badge reads a different field shape. Adding a badge means touching `SessionInfo`, `ActiveSession`, `broadcast_session_update()`, `session_update` JSON schema, and one or more widget files — independently.
2. **Scattered rendering.** Some badges in `SessionIdentityBar`, others in `input_area.dart`, others duplicated in `session_panel.dart`. No authoritative badge composition layer.
3. **No lifecycle contract.** Badges appear or disappear via ad-hoc conditionals (`if cavemanMode`, `if worktreeInfo != null`). No unified visibility rule.
4. **Interactive badges leak into display widgets.** Worker badge selection logic sits inside `_WorkerBadge`; project chip navigation sits in `input_area.dart`. No separation between badge definition and host context.
5. **Backend field bloat.** `broadcast_session_update()` is a hand-maintained dict growing unboundedly. Badge data mixed with token accounting makes the message hard to evolve.
6. **No badge ordering or priority.** Order is hard-coded at widget build time. Re-ordering or deprioritizing a badge requires widget surgery.

---

## 3. Target Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                     Backend (Python)                     │
│                                                          │
│  BadgeState (per session)                                │
│  ┌────────────────────────────────────────────────┐      │
│  │  { worker, agent, project, worktree, caveman,  │      │
│  │    status, … }  →  List[BadgeSpec]             │      │
│  └───────────────┬────────────────────────────────┘      │
│                  │  session_update.badges array           │
└──────────────────┼───────────────────────────────────────┘
                   │ WebSocket
┌──────────────────┼───────────────────────────────────────┐
│                  ▼     Client (Flutter/Dart)              │
│  SessionInfo.badges: List<BadgeSpec>                     │
│                                                          │
│  BadgeRegistry (singleton)                               │
│  ┌────────────────────────────────────────────────┐      │
│  │  type → BadgeRenderer                          │      │
│  └───────────────┬────────────────────────────────┘      │
│                  │                                        │
│  BadgeBar widget (replaces SessionIdentityBar chips)     │
│  ┌────────────────────────────────────────────────┐      │
│  │  for badge in session.badges                   │      │
│  │    BadgeRegistry.render(badge)                 │      │
│  └────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

---

## 4. Domain Model and Terminology

### 4.1 Core Terms

| Term | Definition |
|------|-----------|
| **Badge** | A typed, renderable indicator attached to a session conveying one discrete property |
| **BadgeSpec** | The serializable value object describing a badge: `{ type, label, payload }` |
| **BadgeType** | Stable string identifier for a badge kind: `"worker"`, `"agent"`, `"project"`, `"worktree"`, `"caveman"`, `"status"` |
| **BadgePayload** | Type-specific data carried by a badge; opaque dict on the wire, typed class on the client |
| **BadgeState** | Per-session server-side collection of all current `BadgeSpec` instances |
| **BadgeRenderer** | Client-side function/widget factory keyed to `BadgeType`; produces the visual chip |
| **BadgeRegistry** | Client-side singleton mapping `BadgeType → BadgeRenderer` |
| **BadgeSlot** | Named position in the UI that can display a filtered subset of badges (identity bar, input area, session list tile) |

### 4.2 Canonical BadgeSpec Shape

```typescript
// Wire format (JSON)
interface BadgeSpec {
  type: string;           // BadgeType identifier
  label: string;          // Human-readable text for the chip
  priority: number;       // Sort order in bar; lower = leftmost
  visible: boolean;       // Server controls display; client honors it
  interactive: boolean;   // Whether tapping the badge does anything
  payload: Record<string, unknown>;  // Type-specific data
}
```

Python equivalent:
```python
@dataclass
class BadgeSpec:
    type: str
    label: str
    priority: int
    visible: bool
    interactive: bool
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

Dart equivalent:
```dart
@immutable
class BadgeSpec {
  final String type;
  final String label;
  final int priority;
  final bool visible;
  final bool interactive;
  final Map<String, dynamic> payload;

  const BadgeSpec({
    required this.type,
    required this.label,
    required this.priority,
    required this.visible,
    required this.interactive,
    this.payload = const {},
  });

  factory BadgeSpec.fromJson(Map<String, dynamic> json) { ... }
  Map<String, dynamic> toJson() { ... }
}
```

### 4.3 Badge Priority Constants

```dart
class BadgePriority {
  static const status   = 0;
  static const worker   = 10;
  static const agent    = 20;
  static const project  = 30;
  static const worktree = 40;
  static const caveman  = 50;
  // Future badges start at 60+
}
```

---

## 5. Data Flow

### 5.1 Badge Creation / Update (Backend)

```
Session state changes (status, metadata, executor)
        │
        ▼
BadgeState.recompute(session: ActiveSession) → List[BadgeSpec]
        │
        ▼
broadcast_session_update(session)
  payload["badges"] = [spec.to_dict() for spec in badge_state.specs]
        │
        ▼ WebSocket
All subscribed clients receive session_update with badges array
```

`BadgeState.recompute()` is a pure function called whenever `broadcast_session_update()` is invoked. It assembles all `BadgeSpec` instances from session fields. No badge logic lives outside this function.

### 5.2 Badge Removal

A badge is removed by setting `visible: false` (or by omitting it from the computed list). The client removes it from display. The server never sends a dedicated "remove badge" message; the badges array in every `session_update` is the full authoritative snapshot.

### 5.3 Draft / Pre-session Badges

Before a session exists (new-chat screen), the client assembles *draft badges* from local state: selected worker config, pre-selected project, pre-selected worktree. These use the same `BadgeSpec` shape but are constructed client-side without `session_id`.

```dart
class DraftBadgeComposer {
  List<BadgeSpec> compose({
    WorkerConfig? worker,
    String? projectPath,
    String? worktreePath,
  }) { ... }
}
```

Draft badges render identically to live badges; they become live once the session is created and the first `session_update` arrives.

### 5.4 Receive / Apply Flow (Client)

```
WebSocket message: session_update
        │
        ▼
SessionInfo.copyWith(badges: BadgeSpec.listFromJson(msg["badges"]))
        │
        ▼
SessionNotifier / Riverpod provider notifies listeners
        │
        ▼
BadgeBar widget rebuilds
  session.badges
    .where((b) => b.visible)
    .sortedBy((b) => b.priority)
    .map((b) => BadgeRegistry.instance.render(context, b))
```

---

## 6. Storage Model and Ownership Boundaries

### 6.1 Ownership

| Layer | Owns What |
|-------|----------|
| **Server `BadgeState`** | Authoritative badge specs; derived from `ActiveSession` fields |
| **DB `sessions.metadata_`** | Persists badge-relevant raw fields (`caveman_mode`, `worktree`, `selected_worktree_path`); does **not** store serialized `BadgeSpec` objects — they are recomputed on load |
| **Client `SessionInfo.badges`** | Cache of last-received badge specs; rebuilt on every `session_update` |
| **Client `DraftBadgeComposer`** | Ephemeral, never persisted |

Rationale: storing computed `BadgeSpec` objects in the DB adds redundancy. Badge specs are cheap to recompute from session fields. DB stores the source-of-truth fields only.

### 6.2 Session Restore

On `POST /api/sessions/{id}/restore`, the backend loads the session from DB, reconstructs `ActiveSession`, and immediately calls `BadgeState.recompute()`. The first `session_update` broadcast after restore carries a full badge array — no stale state.

### 6.3 Schema Changes

`BadgeSpec` is versioned via an optional `schema_version` field (default `1`). Client ignores unknown badge types gracefully (renders a generic "unknown" chip or hides it). This enables badge additions without coordinated client deploys.

---

## 7. Backend Implementation

### 7.1 BadgeState Class

```python
# src/core/badges.py

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.session import ActiveSession


@dataclass
class BadgeSpec:
    type: str
    label: str
    priority: int
    visible: bool
    interactive: bool
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BadgeState:
    """Computes the authoritative badge list from an ActiveSession."""

    def compute(self, session: ActiveSession) -> list[BadgeSpec]:
        badges: list[BadgeSpec] = []
        badges.append(self._status_badge(session))
        badges.append(self._worker_badge(session))
        if b := self._agent_badge(session):
            badges.append(b)
        if b := self._project_badge(session):
            badges.append(b)
        if b := self._worktree_badge(session):
            badges.append(b)
        if b := self._caveman_badge(session):
            badges.append(b)
        return badges

    def _status_badge(self, session: ActiveSession) -> BadgeSpec:
        return BadgeSpec(
            type="status",
            label=session.status.value,
            priority=0,
            visible=True,
            interactive=False,
            payload={"activity_state": session.activity_state.value},
        )

    def _worker_badge(self, session: ActiveSession) -> BadgeSpec:
        return BadgeSpec(
            type="worker",
            label=session.worker_name or session.worker_id or "unknown",
            priority=10,
            visible=True,
            interactive=False,  # read-only for existing sessions
            payload={"worker_id": session.worker_id},
        )

    def _agent_badge(self, session: ActiveSession) -> BadgeSpec | None:
        agent = session.agent_type
        if not agent:
            return None
        return BadgeSpec(
            type="agent",
            label=agent,
            priority=20,
            visible=True,
            interactive=False,
            payload={"agent_type": agent},
        )

    def _project_badge(self, session: ActiveSession) -> BadgeSpec | None:
        path = session.main_project_path
        if not path:
            return None
        error = session.project_name_error
        return BadgeSpec(
            type="project",
            label=path.rstrip("/").split("/")[-1],
            priority=30,
            visible=True,
            interactive=False,
            payload={"path": path, "error": error},
        )

    def _worktree_badge(self, session: ActiveSession) -> BadgeSpec | None:
        wt = session.metadata.get("worktree")
        if not wt:
            return None
        return BadgeSpec(
            type="worktree",
            label=wt.get("branch") or wt.get("repo_path", "worktree"),
            priority=40,
            visible=True,
            interactive=False,
            payload=wt,
        )

    def _caveman_badge(self, session: ActiveSession) -> BadgeSpec | None:
        if not session.metadata.get("caveman_mode", False):
            return None
        return BadgeSpec(
            type="caveman",
            label="Caveman",
            priority=50,
            visible=True,
            interactive=False,
            payload={"level": session.metadata.get("caveman_level", "full")},
        )
```

### 7.2 Integration into broadcast_session_update

```python
# src/core/session.py

from src.core.badges import BadgeState

_badge_state = BadgeState()

def broadcast_session_update(self, session: ActiveSession) -> None:
    badges = _badge_state.compute(session)
    update: dict[str, Any] = {
        "type": "session_update",
        "session_id": session.id,
        # --- existing top-level fields preserved for backward compat ---
        "status": session.status.value,
        "activity_state": session.activity_state.value,
        "title": session.title,
        # ... (all existing fields remain) ...
        # --- new unified badges array ---
        "badges": [b.to_dict() for b in badges],
    }
    for queue in self._update_subscribers.values():
        queue.put_nowait(update)
```

The existing flat fields remain in `session_update` during the migration period (see Section 12).

---

## 8. Client Implementation

### 8.1 BadgeSpec Dart Model

```dart
// rcflowclient/lib/models/badge_spec.dart

@immutable
class BadgeSpec {
  final String type;
  final String label;
  final int priority;
  final bool visible;
  final bool interactive;
  final Map<String, dynamic> payload;

  const BadgeSpec({
    required this.type,
    required this.label,
    required this.priority,
    required this.visible,
    required this.interactive,
    this.payload = const {},
  });

  factory BadgeSpec.fromJson(Map<String, dynamic> json) => BadgeSpec(
        type: json['type'] as String,
        label: json['label'] as String,
        priority: (json['priority'] as num).toInt(),
        visible: json['visible'] as bool,
        interactive: json['interactive'] as bool,
        payload: (json['payload'] as Map<String, dynamic>?) ?? const {},
      );

  Map<String, dynamic> toJson() => {
        'type': type,
        'label': label,
        'priority': priority,
        'visible': visible,
        'interactive': interactive,
        'payload': payload,
      };

  static List<BadgeSpec> listFromJson(List<dynamic>? list) =>
      list?.map((e) => BadgeSpec.fromJson(e as Map<String, dynamic>)).toList() ?? [];
}
```

### 8.2 SessionInfo Integration

```dart
// rcflowclient/lib/models/session_info.dart — add field

class SessionInfo {
  // ... existing fields ...
  final List<BadgeSpec> badges;

  // In fromJson:
  badges: BadgeSpec.listFromJson(json['badges'] as List?),

  // In copyWith:
  badges: badges ?? this.badges,
}
```

### 8.3 BadgeRegistry

```dart
// rcflowclient/lib/ui/badges/badge_registry.dart

typedef BadgeRenderer = Widget Function(BuildContext context, BadgeSpec badge);

class BadgeRegistry {
  BadgeRegistry._();
  static final BadgeRegistry instance = BadgeRegistry._();

  final Map<String, BadgeRenderer> _renderers = {};

  void register(String type, BadgeRenderer renderer) {
    _renderers[type] = renderer;
  }

  Widget render(BuildContext context, BadgeSpec badge) {
    final renderer = _renderers[badge.type];
    if (renderer == null) {
      // Unknown badge type: render generic chip
      return _unknownBadge(context, badge);
    }
    return renderer(context, badge);
  }

  Widget _unknownBadge(BuildContext context, BadgeSpec badge) =>
      _GenericBadge(label: badge.label, color: const Color(0xFF6B7280));
}
```

### 8.4 Badge Renderers (one file per type)

```dart
// rcflowclient/lib/ui/badges/renderers/status_badge_renderer.dart
void registerStatusBadge(BadgeRegistry registry) {
  registry.register('status', (context, badge) => _StatusBadge(badge: badge));
}

// rcflowclient/lib/ui/badges/renderers/worker_badge_renderer.dart
void registerWorkerBadge(BadgeRegistry registry) {
  registry.register('worker', (context, badge) => _WorkerBadge(badge: badge));
}

// ... one file per badge type ...
```

### 8.5 Registration at App Startup

```dart
// rcflowclient/lib/main.dart

void _registerBadges() {
  final registry = BadgeRegistry.instance;
  registerStatusBadge(registry);
  registerWorkerBadge(registry);
  registerAgentBadge(registry);
  registerProjectBadge(registry);
  registerWorktreeBadge(registry);
  registerCavemanBadge(registry);
}
```

### 8.6 BadgeBar Widget

```dart
// rcflowclient/lib/ui/widgets/badge_bar.dart

class BadgeBar extends StatelessWidget {
  final List<BadgeSpec> badges;
  final Set<String>? slotFilter; // null = show all visible

  const BadgeBar({required this.badges, this.slotFilter, super.key});

  @override
  Widget build(BuildContext context) {
    final visible = badges
        .where((b) => b.visible)
        .where((b) => slotFilter == null || slotFilter!.contains(b.type))
        .toList()
      ..sort((a, b) => a.priority.compareTo(b.priority));

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: visible
          .map((b) => Padding(
                padding: const EdgeInsets.only(right: 4),
                child: BadgeRegistry.instance.render(context, b),
              ))
          .toList(),
    );
  }
}
```

Usage:
```dart
// Identity bar — show all badges
BadgeBar(badges: session.badges)

// Session list tile — show only status + caveman
BadgeBar(badges: session.badges, slotFilter: {'status', 'caveman'})

// Input area — show only project + worktree
BadgeBar(badges: session.badges, slotFilter: {'project', 'worktree'})
```

---

## 9. Rendering Architecture

### 9.1 Widget Hierarchy

```
SessionIdentityBar
└── BadgeBar (slot: identity)
    ├── BadgeRegistry.render → _StatusBadge
    ├── BadgeRegistry.render → _WorkerBadge
    ├── BadgeRegistry.render → _AgentBadge
    └── BadgeRegistry.render → _CavemanBadge

SessionListTile
└── BadgeBar (slot: {status, caveman})

InputArea
└── BadgeBar (slot: {project, worktree}) OR DraftBadgeBar
```

### 9.2 Interactive Badges

Badges marked `interactive: true` receive a tap callback. Interaction behavior is defined in the renderer, not in `BadgeBar`. Example: a future `worker` badge could be interactive in new-chat context (dropdown) — the renderer checks `badge.interactive` and wraps accordingly.

```dart
class _WorkerBadge extends StatelessWidget {
  final BadgeSpec badge;

  @override
  Widget build(BuildContext context) {
    final chip = _buildChip();
    if (!badge.interactive) return chip;
    return GestureDetector(
      onTap: () => _showWorkerPicker(context, badge),
      child: chip,
    );
  }
}
```

### 9.3 Error States

Project badge carries `payload["error"]`. The renderer checks this field and applies error styling (red border, red text) without any special-casing in `BadgeBar`.

---

## 10. Extensibility Model

### 10.1 Adding a New Badge Type

**Backend (one change):**
```python
# src/core/badges.py — add method to BadgeState
def _my_new_badge(self, session: ActiveSession) -> BadgeSpec | None:
    if not session.metadata.get("my_flag"):
        return None
    return BadgeSpec(
        type="my_new_type",
        label="My Badge",
        priority=60,
        visible=True,
        interactive=False,
        payload={"detail": session.metadata["my_detail"]},
    )

# Then call it in compute():
if b := self._my_new_badge(session):
    badges.append(b)
```

**Client (two changes):**
1. Create `rcflowclient/lib/ui/badges/renderers/my_new_badge_renderer.dart`
2. Call `registerMyNewBadge(registry)` in `_registerBadges()`

No changes needed to: `SessionInfo`, `BadgeBar`, `BadgeRegistry`, `broadcast_session_update`, any existing badge widget, or any DB migration.

### 10.2 Schema Compatibility

- Unknown `type` values on the client are silently rendered as a gray generic chip.
- Clients on old versions that do not have the `badges` array key fall back gracefully to computing badges from legacy flat fields (see Section 12.2).
- Payload fields are always optional dicts — no required fields beyond `type`, `label`, `priority`, `visible`, `interactive`.

---

## 11. Concrete Examples Under New Model

### 11.1 Worker Badge

```python
# Backend
BadgeSpec(
    type="worker",
    label="HomeServer",
    priority=10,
    visible=True,
    interactive=False,
    payload={"worker_id": "wkr-abc123"},
)
```

```dart
// Renderer
void registerWorkerBadge(BadgeRegistry registry) {
  registry.register('worker', (context, badge) {
    const color = Color(0xFF6366F1);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(25),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withAlpha(70), width: 0.5),
      ),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.dns_outlined, size: 10, color: color.withAlpha(180)),
        const SizedBox(width: 4),
        Text(badge.label,
            style: const TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.w600)),
      ]),
    );
  });
}
```

### 11.2 Agent Badge

```python
BadgeSpec(
    type="agent",
    label="claude_code",
    priority=20,
    visible=True,
    interactive=False,
    payload={"agent_type": "claude_code"},
)
```

### 11.3 Project Badge

```python
BadgeSpec(
    type="project",
    label="RCFlow",
    priority=30,
    visible=True,
    interactive=False,
    payload={"path": "/home/user/Projects/RCFlow", "error": None},
)
```

Error state (red styling):
```python
BadgeSpec(
    type="project",
    label="UnknownProject",
    priority=30,
    visible=True,
    interactive=False,
    payload={"path": None, "error": "Project 'UnknownProject' not found"},
)
```

### 11.4 Worktree Badge

```python
BadgeSpec(
    type="worktree",
    label="feature/badges",
    priority=40,
    visible=True,
    interactive=False,
    payload={
        "repo_path": "/home/user/Projects/RCFlow",
        "branch": "feature/badges",
        "base": "main",
        "last_action": "new",
    },
)
```

### 11.5 Caveman Badge

```python
BadgeSpec(
    type="caveman",
    label="Caveman",
    priority=50,
    visible=True,
    interactive=False,
    payload={"level": "full"},
)
```

### 11.6 Status Badge

```python
BadgeSpec(
    type="status",
    label="active",
    priority=0,
    visible=True,
    interactive=False,
    payload={"activity_state": "processing_llm"},
)
```

Client renderer maps `label` to display text and color:
```dart
final (display, color) = switch (badge.label) {
  'active' || 'executing' => ('Active', Color(0xFF3B82F6)),
  'paused' => ('Paused', Color(0xFFF59E0B)),
  'completed' => ('Done', Color(0xFF10B981)),
  'failed' => ('Failed', Color(0xFFEF4444)),
  'cancelled' => ('Ended', Color(0xFF6B7280)),
  _ => (badge.label, Color(0xFF6B7280)),
};
```

---

## 12. Migration Plan

### 12.1 Phases

#### Phase 1 — Backend: add `badges` array alongside existing fields (non-breaking)

- Create `src/core/badges.py` with `BadgeSpec` and `BadgeState`.
- Add `"badges"` key to `broadcast_session_update()` output.
- All existing flat fields (`caveman_mode`, `agent_type`, `worktree`, etc.) remain in `session_update` untouched.
- Tests: unit-test `BadgeState.compute()` for every badge type; verify `session_update` payload shape.

**Risk:** None. Purely additive.

#### Phase 2 — Client: add `BadgeSpec` model, registry, `BadgeBar`, renderers

- Add `badge_spec.dart`, `badge_registry.dart`, `badge_bar.dart`.
- Add per-type renderer files.
- Register all renderers at startup.
- `SessionInfo` gains `badges: List<BadgeSpec>` field parsed from `session_update["badges"]`.
- `BadgeBar` used in new locations; old widget classes kept intact.
- Tests: unit-test `BadgeSpec.fromJson`, each renderer, `BadgeBar` slot filtering.

**Risk:** Low. New code path; old rendering still active.

#### Phase 3 — Client: replace old badge widgets with `BadgeBar`

- Replace `_WorkerBadge`, `_CavemanBadge`, `_StatusBadge` in `session_identity_bar.dart` with `BadgeBar(badges: session.badges)`.
- Replace project chip in `input_area.dart` with `BadgeBar(badges: session.badges, slotFilter: {'project', 'worktree'})` or keep draft-badge path for new-chat.
- Replace session list tile badge rendering with `BadgeBar`.
- Delete dead widget classes.

**Risk:** Medium. Visual regression possible. Requires thorough UI testing (golden tests recommended).

#### Phase 4 — Backend: deprecate flat badge fields from `session_update`

- After all clients are confirmed on Phase 3+, remove individual badge fields from `session_update` JSON.
- Keep non-badge fields: `status`, `activity_state`, `title`, `session_type`, token counts, timestamps.
- Version `session_update` with `"protocol_version": 2` field.

**Risk:** Breaking for old clients. Coordinate with version bump.

#### Phase 5 — Agent badge (new, no migration needed)

- Add `_agent_badge` to `BadgeState` (already specified above).
- Register `AgentBadgeRenderer` on the client.
- No legacy field to remove — agent badge is new.

### 12.2 Fallback for Old Clients

Old clients (pre-Phase 2) that do not parse `badges` continue to work because all flat fields remain during Phases 1–3. No action required.

New clients connecting to old servers (pre-Phase 1) that do not emit `badges` receive an empty array (`[]`). They fall back to computing display from flat fields using a `LegacyBadgeAdapter`:

```dart
class LegacyBadgeAdapter {
  static List<BadgeSpec> adapt(Map<String, dynamic> sessionUpdate) {
    // Construct BadgeSpec list from flat fields for backward compat
    final badges = <BadgeSpec>[];
    // status
    badges.add(BadgeSpec(type: 'status', label: sessionUpdate['status'] ?? '', ...));
    // caveman
    if (sessionUpdate['caveman_mode'] == true) {
      badges.add(BadgeSpec(type: 'caveman', label: 'Caveman', ...));
    }
    // ... etc
    return badges;
  }
}
```

---

## 13. Backward Compatibility Concerns

1. **`session_update` flat fields** — must remain through Phase 3. Remove only after all deployed clients consume `badges`.
2. **`SessionInfo` existing fields** (`cavemanMode`, `agentType`, `worktreeInfo`, etc.) — keep them; they serve code beyond badge rendering (business logic, conditions, etc.). Do not remove.
3. **`_WorkerBadge`, `_CavemanBadge`, `_StatusBadge`** widget classes — mark `@Deprecated` in Phase 3, delete in Phase 4.
4. **DB schema** — no migration needed. Badge specs are recomputed; source fields in `metadata_` stay.
5. **`project_name_error`** — currently a transient field on `ActiveSession`. Carry it in project `BadgeSpec.payload["error"]`; keep the field on `ActiveSession` for non-badge uses.

---

## 14. Suggested APIs / Types / Events

### 14.1 New Python Types

```python
# src/core/badges.py
BadgeSpec           # dataclass
BadgeState          # stateless compute class
```

### 14.2 New Dart Types/Files

```
rcflowclient/lib/models/badge_spec.dart
rcflowclient/lib/ui/badges/badge_registry.dart
rcflowclient/lib/ui/badges/badge_bar.dart
rcflowclient/lib/ui/badges/draft_badge_composer.dart
rcflowclient/lib/ui/badges/renderers/status_badge_renderer.dart
rcflowclient/lib/ui/badges/renderers/worker_badge_renderer.dart
rcflowclient/lib/ui/badges/renderers/agent_badge_renderer.dart
rcflowclient/lib/ui/badges/renderers/project_badge_renderer.dart
rcflowclient/lib/ui/badges/renderers/worktree_badge_renderer.dart
rcflowclient/lib/ui/badges/renderers/caveman_badge_renderer.dart
rcflowclient/lib/ui/badges/legacy_badge_adapter.dart
```

### 14.3 session_update JSON Shape (Phase 1+)

```json
{
  "type": "session_update",
  "session_id": "uuid",
  "status": "active",
  "activity_state": "processing_llm",
  "title": "My Session",
  "session_type": "conversational",
  "created_at": "2025-01-15T10:30:00+00:00",
  "input_tokens": 1234,
  "output_tokens": 567,
  "cache_creation_input_tokens": 100,
  "cache_read_input_tokens": 200,
  "tool_input_tokens": 5000,
  "tool_output_tokens": 3000,
  "tool_cost_usd": 0.05,
  "paused_reason": null,
  "sort_order": 0,
  "badges": [
    { "type": "status",   "label": "active",        "priority": 0,  "visible": true, "interactive": false, "payload": {"activity_state": "processing_llm"} },
    { "type": "worker",   "label": "HomeServer",     "priority": 10, "visible": true, "interactive": false, "payload": {"worker_id": "wkr-abc"} },
    { "type": "agent",    "label": "claude_code",    "priority": 20, "visible": true, "interactive": false, "payload": {"agent_type": "claude_code"} },
    { "type": "project",  "label": "RCFlow",         "priority": 30, "visible": true, "interactive": false, "payload": {"path": "/home/.../RCFlow", "error": null} },
    { "type": "worktree", "label": "feature/badges", "priority": 40, "visible": true, "interactive": false, "payload": {"branch": "feature/badges", "base": "main"} },
    { "type": "caveman",  "label": "Caveman",        "priority": 50, "visible": true, "interactive": false, "payload": {"level": "full"} }
  ]
}
```

---

## 15. Testing Strategy

### 15.1 Backend Unit Tests

```
tests/test_core/test_badges.py
```

- `test_status_badge_active` / `test_status_badge_paused` / …
- `test_caveman_badge_hidden_when_false`
- `test_caveman_badge_visible_when_true`
- `test_agent_badge_none_for_pure_llm_session`
- `test_agent_badge_claude_code`
- `test_worktree_badge_none_without_metadata`
- `test_worktree_badge_with_branch`
- `test_project_badge_with_error`
- `test_all_badges_priority_ordering`
- `test_compute_returns_serializable_dicts` (validate `to_dict()` on all types)
- `test_broadcast_includes_badges_key`

### 15.2 Client Unit Tests

```
rcflowclient/test/widgets/badge_bar_test.dart
rcflowclient/test/models/badge_spec_test.dart
```

- `test_badge_spec_from_json_roundtrip`
- `test_unknown_badge_type_renders_generic`
- `test_badge_bar_respects_visible_false`
- `test_badge_bar_slot_filter`
- `test_badge_bar_priority_ordering`
- `test_legacy_badge_adapter_produces_status_from_flat_fields`

### 15.3 Widget Golden Tests (Phase 3)

- `badge_bar_all_badges.png` — all 6 badge types rendered
- `badge_bar_status_only.png`
- `badge_bar_project_error.png` — red project badge
- `session_identity_bar_caveman_on.png`
- `session_identity_bar_caveman_off.png`

### 15.4 Integration / E2E Tests

- Start session with caveman mode → verify `badges` array in first `session_update` contains caveman badge with `visible: true`
- Attach agent → verify agent badge appears
- `@mention` project → verify project badge appears with correct label
- Resolve project error → verify project badge `payload.error` clears

---

## 16. Rollout Plan and Risks

### 16.1 Rollout Order

```
Phase 1 (backend: +badges array)      → merge to main, deploy server
Phase 2 (client: new models + widgets) → merge; both old and new code active
Phase 3 (client: swap to BadgeBar)     → merge; old widget classes removed from UI
Phase 4 (backend: remove flat fields)  → after all clients confirmed ≥ Phase 3
Phase 5 (agent badge: new feature)     → any time after Phase 2
```

### 16.2 Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Badge ordering regression (wrong priority) | Low | Priority constants codified; unit tests verify ordering |
| Unknown badge type crash | Low | Registry returns generic chip; client never throws |
| Visual regression during Phase 3 swap | Medium | Golden tests before merge; side-by-side manual review |
| Old clients connecting after Phase 4 | High | Keep `session_update` flat fields through at least one minor version cycle; advertise removal in CHANGELOG |
| New badge type needs payload field not in schema | None | Payload is `dict[str, Any]` / `Map<String, dynamic>` — no schema enforcement; fully open |
| `BadgeState.compute()` throws on malformed session | Medium | Wrap each `_*_badge` call in try/except; return empty list on failure; log warning |

---

## 17. Future Badge Ideas

These require zero architectural changes — just add a `_*_badge` method and a renderer:

| Badge Type | Purpose |
|-----------|---------|
| `model` | Show active LLM model name (e.g. `claude-sonnet-4-6`) |
| `permission_level` | Indicate current tool permission mode |
| `context_window` | Token usage / context window fill level |
| `task_count` | Number of active TODO tasks in session |
| `cost` | Cumulative session cost |

---

*Document version: 1.0 — April 2026*
