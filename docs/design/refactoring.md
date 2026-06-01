---
updated: 2026-06-01
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

Current floors: Python **52%**, Flutter **14%**.

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
| 2b — ToolManager split | ⏳ deferred | `tests/test_services/test_tool_manager.py` imports six private helpers (`_detect_*`, `_parse_version`, `_atomic_install_binary`) by name. The split should land alongside an update to the test fixtures and is its own focused PR |
| 2c — ActiveSession partition | ⏳ deferred | Plan calls for splitting into `SessionPendingState`, `SessionWakeMirror`, `SessionTokenAccumulator`, `SessionSubprocessTracker` with the existing attribute surface preserved via properties. Mechanical but every property is a potential breakage seam — dedicated PR with full session test pass |
| 2d — PromptRouter mixins → composition | ⏳ deferred | The largest piece. Every agent / lifecycle / context method on `PromptRouter` already carries `# ty:ignore[unresolved-attribute]` for cross-mixin state, so the refactor is straight-line; the risk is fan-out into every WebSocket and route handler. Land after 2b and 2c so the helpers it composes (SessionPendingState, BaseToolInstaller) already exist |

## Phase 3 — Backend Test Gaps

In progress. Adds: direct unit tests for `agent_claude_code` / `agent_codex` /
`agent_opencode` once the per-agent classes land, real implementations
for the `*_plan.py` stubs, and minimal route tests for the 12 untested
endpoints (auth, config, dashboard, models, projects, rcflow_plugins,
slash_commands, telemetry, tools, uploads, …).

## Phase 4 — Flutter File Splits

Pending. Targets: `server_config_screen.dart`, `input_area.dart`,
`settings_menu.dart`, `task_pane.dart`, plus a `lib/theme/spacing.dart`
constants file replacing inline padding literals across ~60 widget
files.

## Phase 5 — Flutter State / Transport Split

Pending. `AppState`, `PaneState`, `WebSocketService` get carved into
per-feature notifiers and a transport/dispatcher/REST trio. High-risk;
lands only after Phase 4 is stable.

## Phase 6 — Flutter Tests + Final Lint

Pending. Tests for the new transport/dispatcher/REST/state seams;
ruff `D` + `COM` rule families enabled.
