---
updated: 2026-07-23
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

Current floors: Python **64%**, Flutter **14%**.

## Consolidation refactors (in progress)

- **Executor teardown** ✅ — the four lifecycle kill paths (cancel / end / pause / interrupt) now share one `SessionLifecycle._teardown_executors(session) -> bool` instead of hand-copying the 4-executor cancel+stream-cancel block, which had drifted.
- **PR/issue listing** ✅ — the backend-scoped PR and Linear-issue list query + serializer lived inline in four places (both WS channels × 2). Consolidated into `list_backend_prs` / `list_backend_issues` in the integration modules; the WS handlers call them (no more importing route-module private serializers).
- **Managed-agent base class** 🟡 (in progress) — `core/agents/base.py`'s `ManagedAgentBase` now owns the shared `__init__`, `_push_subprocess_status` (10 inline relay broadcasts collapsed), and `_end_agent_session` (Codex / OpenCode / ACP end-of-session teardown — OpenCode also gained the MCP-token revoke its siblings had). All four agents inherit it. Still per-agent (recommended next): the `_forward_to_*` / restart flows and a shared one-shot-CLI executor base for codex/opencode. Claude Code's end path stays separate (it also terminates live monitors).
- **One-shot-CLI executor base** ✅ — `executors/oneshot_cli.py`'s `OneShotCliExecutor` now owns the subprocess plumbing that `codex.py` and `opencode.py` shared byte-for-byte modulo the agent name and session-reference field (stderr drain, exit logging, tree-kill cleanup, stop/cancel, and the unsupported `send_input`/`read_more_events`, plus non-streaming `execute`). Both executors inherit it and implement only their genuinely-different parts (command/env/start/prompt-write/event-parse/streaming).
- **Tool-install pipeline** 🟡 (in progress) — the streaming download variant already does the full download → verify → install, so the non-streaming `_download_codex_binary` / `_download_codex_acp_binary` now just drain their streaming twins (`_stream_codex_download` / `_stream_codex_acp_download`) instead of re-implementing it. Still to do: OpenCode and Claude Code (their plain path *raises* on error while the streaming path *yields* error events, so collapsing them needs the error semantics reconciled first), and adding the missing OpenCode checksum verification once its release-checksum format is wired in.
- **Pending** (recommended as a follow-up): finish the install pipeline (OpenCode/Claude Code + OpenCode checksums), session row↔object mapping helper, and moving domain logic (task state machine, PR-sync pipeline) out of route modules into services (see the `.importlinter` `ignore_imports` debt list).

## Quality gates (enforced)

- **Complexity**: `[tool.ruff.lint.mccabe] max-complexity = 15`. The ~21 functions that already exceed it are frozen with a per-function `# noqa: C901`; that list only shrinks. Do not raise the ceiling to silence a new offender — split the function.
- **Coverage**: `fail_under = 64`; PR-diff coverage gate (`diff-cover --fail-under 80`) grades only new/changed lines.
- **Layering**: `.importlinter` enforces `api → core → services → database` and forbids new importers of the deprecated `tool_manager` shim. The `ignore_imports` allowlist is the current violation debt (shrinks as the layering refactor lands).
- **Security**: CI runs `pip-audit` (resolved deps) and `gitleaks` (secret scan); `.github/dependabot.yml` opens weekly version-update PRs.
- **CI parity**: CI runs `ruff format --check` and lints `scripts/`, matching pre-commit; CI defaults to `contents: read` with the badge commit isolated to its own write-scoped push-to-main job.
- **S110** (`try/except/pass`) is no longer globally ignored — each best-effort site carries a line-scoped `# noqa: S110`.

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
| Codemod replacing hardcoded EdgeInsets with spacing tokens | ✅ done | Three safely-automatable, EdgeInsets-exclusive subsets codemodded to `kSpace*`/`kRadius*` tokens (line-local edits, no whole-tree format): `BorderRadius.circular(6\|10\|14)` → `kRadiusSmall/Medium/Large` (99 hits / 46 files); `EdgeInsets.all(4\|8\|12\|16\|24)` and `EdgeInsets.symmetric` `horizontal:`/`vertical:` token values → `kSpace1..5` (71 files). Base `kSpaceN` tokens used (not the semantic aliases) so each value maps 1:1 with no intent-guessing. **Follow-up pass (fromLTRB / only / gap):** a second line-local codemod, scoped to the 53 files that already import `spacing.dart`, converted `EdgeInsets.fromLTRB` / `EdgeInsets.only` calls whose every non-zero arg is a token value (mixed-value calls like `fromLTRB(10, 8, 10, 4)` skipped intact; `0` left as `0`) and single-dimension `SizedBox(width:/height:)` gap spacers (`4/8/12` → `kGapInline/Tight/Relaxed`, `16/24/32` → `kSpace4..6`). 305 conversions / 48 files, 305 ins / 305 del (pure 1:1, no reflow). Files not yet importing `spacing.dart` were left out of scope to avoid new-import churn |

## Phase 5 — Flutter State / Transport Split

✅ done. `AppState`, `PaneState`, `WebSocketService` carved into
per-feature stores and a `WebSocketTransport` / `lib/services/rest/*` split.

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

**Step 3 (AppState / PaneState → feature stores) — ✅ done.** Every cleanly
separable cluster on `AppState` is carved out, with `AppState` owning each and
delegating so the read-sites and Provider tree are unchanged:

- `lib/state/stores/`: `LinearIssueStore`, `TaskStore`, `ArtifactStore`
  (entity collections — map + query projections + list/upsert/remove),
  `TerminalSessionStore` (terminal-session collection), `ProjectDataCache`
  (per-project worktree/artifact panel cache).
- `lib/state/clipboard_paste_controller.dart` — dictation-tool paste detection.
- `lib/state/toast_notifier.dart` — settings-gated toast wrapper (public
  `ToastCategory`).

`AppState` 2292 → 2090 lines; all 485 client tests pass.

**Pane-host:** assessed — needs no store extraction. The split-tree algorithm
is already factored into `models/split_tree.dart`; what remains on `AppState`
(`splitRoot`/`panes`/`activePaneId` + `open*InPane`/split/close/nav-history) is
the `PaneHost` coordinator itself, which can't move to a store without a
high-risk Provider-tree change for little gain.

**PaneState (2073 → 1433):** three cohesive clusters carved into composed holders
(PaneState keeps notify + WebSocket-send + session lifecycle, delegates storage):

- `PaneViewTarget` (`lib/state/pane_view_target.dart`) — the "what non-chat
  content is this pane showing" fields (pendingWorktreePath/pendingTaskId/
  taskId/artifactId/linearIssueId/workerSettings*).
- `PaneQueueState` (`lib/state/pane_queue_state.dart`) — the queued-message
  mirror (ordered list + upsert/dequeue+renumber/update/replaceSnapshot/
  editText), with its own unit test.
- `PaneMessageStore` (`lib/state/pane_message_store.dart`, 763 lines) — the
  chat-content core: the display-message list, the live-streaming assembler
  (assistant text / tool blocks / agent-group nesting, the `_tickMs`-coalesced
  notify, `finalizeStream`), todo state, the queued-message reconciliation
  surface, and history pagination. A `part of 'pane_state.dart'` library so it
  keeps a back-reference to its owning `PaneState` (for the current session id,
  WebSocket, `notifyListeners`, and the shared right-panel selection) without
  widening either class's public API. The streaming output handlers and chat
  widgets still call the same names on the pane; PaneState forwards each to the
  store. Lifecycle resets (`switchSession`/`goHome`/`startNewChat`/
  `resubscribeSession`) collapse to a single `resetForSwitch()`.

**Message/stream cluster — extracted (was previously deferred).** The earlier
pass judged this behaviour-bound and left it in place; the carve was completed
in a later pass via the back-reference `part` store above, which separates the
~600 lines of chat-content behaviour from PaneState's session/transport role
without changing any external call site. Full client suite (503) stays green.

**Net step 3:** every cleanly-separable cluster on `AppState` (5 stores +
clipboard + toast) and `PaneState` (view-target + queue + message store) is
extracted; the pane-host is already factored via `split_tree.dart`.

## Phase 6 — Flutter Tests + Final Lint

| Slice | Status | Notes |
|-------|--------|-------|
| Tests for the new transport / dispatcher / REST / sub-state seams | ✅ done | `test/state/stores_test.dart` unit-tests the new `LinearIssueStore` / `TaskStore` / `ArtifactStore` (sort order, byWorker bucketing, per-worker replace isolation, get/upsert/remove, forTask/forSession queries). `RestClient` is covered indirectly via the existing `FakeWebSocketService` worker-connection tests. `WebSocketTransport` now has full lifecycle **and** socket-connected coverage (`test/services/web_socket_transport_test.dart`): a `ChannelConnector` typedef injected via the constructor lets tests supply a socket-free fake `WebSocketChannel` (`StreamChannelMixin`-backed). Covers connect→isConnected/status-true, input/output frame decode onto the broadcast streams, malformed-frame swallow, JSON-encoded `sendInput`/`sendOutput` onto the right sink, stream close/error → status-false, disconnect closes both sinks, plus the disconnected no-ops and post-dispose double-disconnect guard; `PaneQueueState` has its own unit test. Client suite 479 → 503 |
| Ruff `D` (docstrings) | ✅ done | The `D` family is enabled (pep257 convention). Format rules (D2xx/D3xx/D4xx) enforced + clean, the undocumented-public-API rules **D100-D104, D106** enforced across `src` (all of src documented: module + package docstrings on 51 files; ~155 class/method/function docstrings), **and** the prose rules **D205 / D401 / D417** (summary blank-line, imperative mood, param descriptions) now enforced. Remaining `ignore` is conventional only: D105 (magic methods), D107 (__init__ documented at class level). Tests (self-documenting) and generated migrations are scoped via per-file-ignores |
| Ruff `COM` (trailing commas) | ❌ not enabled | The `ruff format` step already handles trailing commas and the ruff docs flag `COM812` / `COM819` as conflicting with the formatter — enabling both produces oscillating fixes. Comment in `pyproject.toml` records the decision so future contributors don't reintroduce the conflict |
