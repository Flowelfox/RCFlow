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

| Slice | Status | Notes |
|-------|--------|-------|
| Route tests for dashboard.py + projects.py | ✅ done | Coverage 52.99 → 53.12% |
| Direct agent unit tests | ⏳ deferred | Best landed alongside Phase 2d so the targets are isolated classes rather than mixins |
| Real implementations for `*_plan.py` files | n/a | Audit was misled by filename — those files are real tests for plan-mode infrastructure, not "to-do" stubs |
| Tests for `auth`, `config`, `models`, `slash_commands`, `tools`, `uploads`, `telemetry`, `rcflow_plugins` routes | ⏳ partial | Existing `test_claude_code_login.py`, `test_config_reload.py`, `test_models_route.py`, `test_slash_commands.py`, `test_rcflow_plugins.py`, `test_uploads.py` already cover the most-used paths; `telemetry` and `tools` need DB / ToolManager fixtures and ship in dedicated PRs |

## Phase 4 — Flutter File Splits

| Slice | Status | Notes |
|-------|--------|-------|
| `lib/theme/spacing.dart` shared tokens | ✅ done | `kSpace1..6`, `kPadCompact/Default/Comfortable`, `kGapInline/Tight/Relaxed`, `kRadiusSmall/Medium/Large`. Replaces ad-hoc literals; widget rewrites consume these in follow-ups |
| `server_config_screen.dart` (3959) split | ⏳ deferred | 28 embedded classes; extract `lib/ui/widgets/config_fields/` + `lib/ui/widgets/config_layout/` |
| `input_area.dart` (2605) split | ⏳ deferred | Extract autocomplete, worktree picker, attachment strip, key shortcuts |
| `settings_menu.dart` (1778) split | ⏳ deferred | Promote each section to its own file under `lib/ui/widgets/settings/` |
| `task_pane.dart` (1821) split | ⏳ deferred | Header / list / detail / actions |
| Codemod replacing hardcoded EdgeInsets with spacing tokens | ⏳ deferred | One mechanical PR after the file splits land |

## Phase 5 — Flutter State / Transport Split

⏳ deferred. `AppState`, `PaneState`, `WebSocketService` get carved into
per-feature `ChangeNotifier`s and a `WebSocketTransport` /
`MessageDispatcher` / `lib/services/rest/*` trio. Lands only after
Phase 4 file splits are stable — the smaller files surface seams
that aren't visible inside the current god-files.

## Phase 6 — Flutter Tests + Final Lint

| Slice | Status | Notes |
|-------|--------|-------|
| Tests for the new transport / dispatcher / REST / sub-state seams | ⏳ deferred | Gated on Phase 5 |
| Ruff `D` (docstrings) | ⏳ deferred | Thousands of violations on existing code; needs a docstring sweep PR or per-module incremental enablement |
| Ruff `COM` (trailing commas) | ❌ not enabled | The `ruff format` step already handles trailing commas and the ruff docs flag `COM812` / `COM819` as conflicting with the formatter — enabling both produces oscillating fixes. Comment in `pyproject.toml` records the decision so future contributors don't reintroduce the conflict |
