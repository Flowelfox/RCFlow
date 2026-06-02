---
updated: 2026-06-02
---

# Refactoring Log

Tracking the multi-phase code-structure improvement plan kicked off in
mid-2026. Each section records what landed, what was deferred, and why.

## Coverage Ratchet Rule

Every PR must keep the Python coverage floor
(`[tool.coverage.report] fail_under` in `pyproject.toml`) and the
Flutter coverage floor (`rcflowclient/coverage_threshold.txt`) at or
above their previous values. PRs that add ≥10 lines under `src/` or
`rcflowclient/lib/` must additionally raise the relevant gate by the
new code's measured coverage.

Current floors: Python **54%**, Flutter **14%**.

## Phase 1 — Tooling & Dead Code

| Slice | Status | Notes |
|-------|--------|-------|
| 1a — Delete dead GUI shadows | ✅ done | `src/gui_macos.py`, `src/gui_core.py`, `src/theme.py` removed; `[tool.ty.src].exclude` trimmed |
| 1b — Coverage gates + just/CI/pre-commit | ✅ done | Python `fail_under=52`, Flutter `coverage_threshold.txt=14`, `just check` runs pytest + flutter test; pre-commit `pytest-fast` hook added |
| 1c — Expand ruff rules | ✅ done | C901, ASYNC, S, RET, LOG enabled with config-level subrule ignores for inherent CLI/server patterns; ~30 sites annotated with line-scoped `noqa` |

## Phase 2 — Backend Composition Refactor

| Slice | Status | Notes |
|-------|--------|-------|
| 2a — Shared agent base | ✅ done | `src/core/agents/base.py` houses `MAX_TOOL_OUTPUT_CHARS` + `truncate_tool_output`; three agent modules + `prompt_router` now import the shared helper instead of defining it locally |
| 2b — ToolManager split | ✅ done | `tool_manager.py` is now a back-compat shim re-exporting from the new `src/services/tools/` package: `constants.py`, `models.py` (`ManagedTool`), `platform_detect.py` (`_is_musl`/`_glibc_too_old`/`_detect_*`/`_parse_version`), `binary_install.py` (`_atomic_install_binary`/`_verify_binary`/checksum + archive helpers), `manager.py` (`ToolManager`). Test patch targets repointed to the new module paths; all 47 tool-manager tests pass |
| 2c — ActiveSession partition | ✅ done | The four concerns now live on composed sub-objects in `src/core/session_state.py` (`SessionTokenAccumulator`, `SessionSubprocessTracker`, `SessionPendingState`, `SessionWakeMirror`), along with the `MonitorState`/`PendingMessage`/`ScheduledWake` dataclasses (re-exported from `session.py` for compatibility). `ActiveSession` re-exposes every historical flat attribute (`input_tokens`, `subprocess_*`, `pending_user_messages`, `scheduled_wakes`, …) and the `mirror_*` helpers via delegating properties/methods. Full suite (1599) passes |
| 2d — PromptRouter mixins → composition | ✅ done | All six mixins are now composed collaborators reached via a typed `self._r` back-reference: `ContextBuilder` (`self._context`), `BackgroundTasks` (`self._background`), `ClaudeCodeAgent`/`CodexAgent`/`OpenCodeAgent` (`self._claude`/`self._codex`/`self._opencode`), and `SessionLifecycle` (`self._lifecycle`). `PromptRouter` has **no base classes** and delegates every collaborator's public entry point, so WS/route handlers, main.py, the agentic loop, and tests call `router.<method>` unchanged. All 231 `ty:ignore[unresolved-attribute]` annotations are gone and no `*Mixin` classes remain; full suite (1599) passes |

## Phase 3 — Backend Test Gaps

| Slice | Status | Notes |
|-------|--------|-------|
| Route tests for dashboard.py + projects.py | ✅ done | Coverage 52.99 → 53.12% |
| Direct agent unit tests | ⏳ partial | The agent collaborators are now isolated classes (`ClaudeCodeAgent`/`CodexAgent`/`OpenCodeAgent`); `test_agent_monitor.py` and `test_agent_diff.py` cover the Claude relay/monitor/diff paths against a bare `ClaudeCodeAgent`. Broader Codex/OpenCode unit coverage can follow incrementally |
| Real implementations for `*_plan.py` files | n/a | Audit was misled by filename — those files are real tests for plan-mode infrastructure, not "to-do" stubs |
| Tests for `auth`, `config`, `models`, `slash_commands`, `tools`, `uploads`, `telemetry`, `rcflow_plugins` routes | ✅ done | `test_routes_tools.py` (read endpoints + not-installed / unknown-tool error paths; tools.py 62%) and `test_routes_telemetry.py` (all four read endpoints against an empty in-memory DB + UUID/param guards; telemetry.py 51%) landed. Python coverage floor raised 52 → 54% |

## Phase 4 — Flutter File Splits

| Slice | Status | Notes |
|-------|--------|-------|
| `lib/theme/spacing.dart` shared tokens | ✅ done | `kSpace1..6`, `kPadCompact/Default/Comfortable`, `kGapInline/Tight/Relaxed`, `kRadiusSmall/Medium/Large`. Replaces ad-hoc literals; widget rewrites consume these in follow-ups |
| `server_config_screen.dart` (3959) split | ✅ done | Field/layout widgets extracted into `part` files: `config_layout`, `config_fields_text`, `config_fields_model`, `config_fields_misc`, `config_fields_tool`, `config_field_wrapper`. Main keeps `showServerConfigScreen` + `ServerConfigContent(State)` (the large stateful controller, → 2451 lines). `part`/`part of` keeps private widgets private with no external import changes |
| `input_area.dart` (2649) split | ✅ done | Chips, mention items, status bars (subprocess + monitor/wake strip), and slash items extracted into `input_chips`/`input_mention_items`/`input_status_bars`/`input_slash_items` parts; main keeps `InputArea`/`_InputAreaState` (→ 1487 lines) |
| `settings_menu.dart` (1778) split | ✅ done | Each settings section extracted into a `settings_sections_*` part (workers/appearance/notifications/hotkeys/about) plus `settings_shared`; main keeps `showSettingsMenu`, `AndroidSettingsBody`, and the dialog/sheet/page shells (→ 385 lines) |
| `task_pane.dart` (1821) split | ✅ done | Header, detail content, tiles, link-issue dialog, and plan banner extracted into parts; main keeps `TaskPane`/`_TaskPaneState` (→ 99 lines) |
| Codemod replacing hardcoded EdgeInsets with spacing tokens | ⏳ deferred | Still a separate mechanical sweep — only literals that exactly match a `spacing.dart` token value should be rewritten, so it is done as its own focused pass rather than bundled with the structural splits |

## Phase 5 — Flutter State / Transport Split

🔄 in progress. `AppState`, `PaneState`, `WebSocketService` get carved into
per-feature `ChangeNotifier`s and a `WebSocketTransport` /
`MessageDispatcher` / `lib/services/rest/*` trio.

**Step 1 (REST extraction) — ✅ done.** The 63 `Future`-returning HTTP methods
(plus the `_escapeFilename` / `_extractDetail` static helpers and the private
`_streamToolOperation`) moved into an injectable `RestClient` at
`lib/services/rest/rest_client.dart`. `RestClient` owns its own
`_serverUrl` / `_allowSelfSigned`; `WebSocketService.connect` syncs it via
`configure()`. `WebSocketService` keeps 63 thin **virtual delegators**
(`fetchProjects(...) => _rest.fetchProjects(...)`) so existing call sites and —
critically — the `FakeWebSocketService extends WebSocketService` test overrides
keep working (an `extension` was rejected because its methods are not virtual).
`websocket_service.dart` 2239 → 640 lines; flutter analyze clean, all 479
client tests pass.

**Step 2 (transport extraction) — ✅ done.** `WebSocketTransport`
(`lib/services/web_socket_transport.dart`, 162 lines) now owns the two raw
channels, the broadcast controllers + subscriptions, ping keepalive,
connect/disconnect/dispose, the inbound frame→stream decode (the
message-dispatch leg), and `sendInput`/`sendOutput`. `WebSocketService`
composes it (`self._transport`), delegates `connect`/`disconnect`/`dispose` /
the three streams / `isConnected`, and its `void` command methods build their
JSON map and call `_transport.sendInput`/`sendOutput`. `connect` also missed
six worktree/project-artifact HTTP methods in step 1 — those moved into
`RestClient` here too, so `WebSocketService` no longer holds any connection
state. `websocket_service.dart` 767 → 521 lines; flutter analyze clean, all
479 client tests pass.

**Step 3 (AppState / PaneState → feature stores) — in progress.** The three
entity collections are carved into owned stores under `lib/state/stores/`:
`LinearIssueStore`, `TaskStore`, `ArtifactStore` (each holds its map + the
read-only query projections + list/upsert/remove mutations). `AppState` owns
one of each and delegates its getters/handlers, keeping the notify, toast, and
pane-management responsibilities, so the read-sites and Provider tree are
unchanged. All 479 client tests pass. Remaining for step 3: the pane/session/
clipboard/notification clusters on `AppState` and the `PaneState` (2073-line)
carve — same owned-store-with-delegation pattern.

## Phase 6 — Flutter Tests + Final Lint

| Slice | Status | Notes |
|-------|--------|-------|
| Tests for the new transport / dispatcher / REST / sub-state seams | ⏳ deferred | Gated on Phase 5 |
| Ruff `D` (docstrings) | ⏳ deferred | Thousands of violations on existing code; needs a docstring sweep PR or per-module incremental enablement |
| Ruff `COM` (trailing commas) | ❌ not enabled | The `ruff format` step already handles trailing commas and the ruff docs flag `COM812` / `COM819` as conflicting with the formatter — enabling both produces oscillating fixes. Comment in `pyproject.toml` records the decision so future contributors don't reintroduce the conflict |
