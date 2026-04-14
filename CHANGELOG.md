# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Backend and client are versioned independently. Entries are grouped by release date
and note which component is affected where it matters.

---

## [Unreleased]

### Fixed
- **"Exit code: None" error on follow-up messages to Claude Code** — two bugs caused false "process exited unexpectedly" errors when sending intermediate messages: (1) `ConnectionResetError`/`OSError` handlers in `_read_events_pipe` and `_read_events_pty` did not set `_done` or call `_wait_and_log_exit()`, leaving exit code uncollected as `None`; (2) `_read_claude_code_followup` showed an error instead of restarting when the process died between turns (race: `is_running` was True but process already exiting). Now both error handlers properly finalize state, and the follow-up reader falls back to `--resume` restart — the same path taken when `is_running` is False (Backend)
- **Mid-turn user messages lost when Claude Code process exits after result** — `_forward_to_claude_code` previously sent follow-up messages to stdin *before* waiting for the current turn to finish; if Claude Code exited after emitting its `result` event (common in `--print` mode), the stdin message was discarded unread. Now waits for the in-progress stream task first, then checks `is_running` — if the process exited, restarts with `--resume` instead of trying to write to dead stdin. Also fixed a race condition where `_suppress_session_end_ask` was set too late (after the relay already checked it), causing duplicate SESSION_END_ASK prompts; the flag is now set in `handle_prompt` before any await and re-checked at push time in `_fire_summary_task` (Backend)
- **Caveman mode not engaging for externally-installed Claude Code** — `_get_managed_config_overrides` gated caveman `--append-system-prompt` injection on `tool.managed`, so externally-installed Claude Code never received the system-prompt flag. Moved caveman injection outside the managed-only guard so it fires regardless of install method (Backend)
- **Agent group block shows display name instead of tool name** — `AgentGroupBlock` used `displayName ?? toolName` priority, showing "Claude Code" instead of "claude_code" on the collapsible agent output container. Swapped to `toolName ?? displayName` to match the `AgentSessionStartBubble` fix (Client)
- **Switching to older loaded session clears session list** — removed unnecessary `refreshSessions()` call from `switchSession()`; real-time `session_update` events already keep the list current, so the full reload was redundant and wiped out sessions loaded via "Load more" (Client)
- **Sending message resets expanded session list** — `refreshSessions()` now requests `max(sessions.length, pageSize)` instead of always requesting the first page, so "load more" sessions survive refreshes triggered by ack, pause, resume, end, restore, rename, and other session mutations (Client)
- **Claude Code ignoring intermediate messages** — `_forward_to_claude_code` now waits for the in-progress stream task to complete before starting a follow-up read, instead of cancelling it. The cancel-based approach discarded remaining events from the current turn, causing the new read to mistake the old turn's `result` event for the follow-up's and never read the actual follow-up response. Also adds post-wait status checks (completed/paused/executor-cleared) and proper `AGENT_GROUP_START`/`SUBPROCESS_STATUS` pushes for follow-up turns (Backend)
- **Duplicate "Task complete. End this chat?" prompts** — when a follow-up message is sent while a Claude Code turn is still in progress, the finishing turn's `SESSION_END_ASK` is now suppressed via `_suppress_session_end_ask` flag on `ActiveSession`; only the follow-up turn's completion fires the prompt. Client also deduplicates pending `sessionEndAsk` messages as defense-in-depth (Backend + Client)
- **Session list "load more" ordering** — `list_all_sessions()` now sorts by `sort_order` ASC / `created_at` DESC before pagination slicing, so the first page always contains the newest sessions and subsequent pages load progressively older ones (Backend)
- **Draft cleared on send** — sending a message now clears the stored draft (local cache, backend) and resets the multi-pane dirty guard (`_lastLoadedDraft`) so stale drafts no longer persist after a message is sent (Client)

### Added
- Android shell screen (`android_shell.dart`)
- Session identity bar widget (`session_identity_bar.dart`)
- Right-click context menu on artifact list items to remove artifacts from tracking (Client)
- **Artifact multi-selection** — Shift+click range selection, Ctrl/Meta+click toggle, bulk right-click delete, selection bar with count, and Escape to clear; matches existing task/session multi-select UX (Client)

---

## [Backend 0.38.0 / Client 1.44.0] — 2026-04-11

### Added
- **Caveman mode** — backend strips filler words from LLM output (~65–75% token reduction); configurable per-session
- **Session drafts** — unsent message draft persisted per session in the database (`drafts` table)
- **Client self-update** — client discovers new releases via GitHub Releases API and prompts the user to update
- **Session reordering** — drag-and-drop session sorting with server-side persistence (`sort_order` column)
- **CLAUDE_CODE_UNDERCOVER** — configurable setting to hide Claude Code identity from the model; disabled by default

### Changed
- Session sort helper extracted into dedicated utility
- CI push trigger restricted to `main` to prevent duplicate PR runs

### Fixed
- Migration `down_revision` corrected to reference squashed initial schema
- Duplicate `targetWorker` field in draft-related WebSocket messages
- Null-aware map elements to satisfy Dart `use_null_aware_elements` lint

---

## [Backend 0.31.4 / Client 1.33.4] — 2026-03-30

### Added
- **Task planning system** — plan artifacts linked to sessions; plan review card in client UI
- **Selectable code blocks** — long-press to select text inside markdown code blocks in the client
- **macOS menu bar GUI** — native menu bar app with DMG distribution; shared GUI core/theme across platforms
- **Windows client installer** — Inno Setup-based `.exe` installer for the Flutter Windows desktop app
- **Collapsible per-tool sidebar navigation** — sidebar sections collapse/expand per tool
- **Per-worker project/agent caching** — client caches last-used project and agent per worker connection
- **Persistent worktree selection** — selected worktree survives session switching and app restarts
- **Interrupted session recovery** — sessions interrupted mid-turn are flagged and resumable
- **Project picker** — UI for selecting the active project directory per worker
- Unified artifact naming across platforms; macOS x64 build support added to CI

### Changed
- Security hardening pass; agentic turn limit added; removed sound/summary features (privacy-sensitive)
- Database migrations squashed into a single initial schema (16 migrations → 1)
- README overhauled for public release; self-hosted coverage badge via `anybadge` SVG

### Fixed
- `cryptography` dependency bumped to 46.0.7 — buffer overflow CVE
- Non-existent worktree paths now return HTTP 404; `CancelledError` propagated correctly
- `BuildContext` used across async gap in worktree fetch corrected
- Various CI fixes: GStreamer deps for Linux Flutter build, Kotlin config, Gradle file-system watching

---

## [Backend 0.21.0 / Client 1.22.0] — 2026-03-18

### Added
- **Linear integration** — issues panel, issue tiles, sync button, and task linking from session artifacts
- **Telemetry subsystem** — per-session token/cost/turn statistics stored in DB; statistics pane with charts
- **Project panel** — sidebar pane tracking `main_project_path` per session
- **Worktree panel** — UI panel for managing `wtpython`-based git worktrees; max-turns pause card
- **Multimodal attachments** — file picker and attachment chips in input area; vision support via LLM
- **Slash commands** — `GET /api/slash-commands` endpoint; `#tool` mention support in prompt routing
- WebSocket routing hardened against missing fields; defensive handling added

### Changed
- Pane state refactored for multi-panel sidebar and richer pause context
- Flutter client models and services extended for worktrees and attachments

### Fixed
- PermissionError opening log files for non-root users on startup
- WSS enabled by default; log helpers redirected to stderr

---

## [Backend 0.11.0 / Client 1.9.0] — 2026-03-16

### Added
- **OpenAI reasoning models** — support for `o1`/`o3`/`o4` series with reasoning token tracking
- **Model selector** — `model_select` field type with provider-aware model dropdowns in settings
- **macOS platform support** — Darwin detection for managed-tool downloads; LaunchAgent install (replaces LaunchDaemon)
- **Anthropic login/logout** — Claude Code provider auto-set on Anthropic credential changes
- Bundle `--install` flag and justfile install/uninstall targets
- `wt` (wtpython) CLI integrated as project dependency for worktree management
- Root/sudo guard and log directory path resolution in installer

### Changed
- Tool bubble expand arrow shown only after completion with content (reduces visual noise)

### Fixed
- `systemctl` calls skipped in WSL2 where systemd is not running

---

## [Backend 0.5.0 / Client 1.5.0] — 2026-03-09

### Added
- **Artifacts panel** — file-based artifacts produced by agents listed and openable from the UI
- **Task tracking** — in-session task list with status indicators
- **Token tracking** — input/output/cache token counts displayed per message
- **Thinking blocks** — Claude extended thinking rendered in the client
- **Hotkeys** — keyboard shortcuts for common actions
- **Notifications** — system notifications on session events
- **OpenAI provider** — chat completions via OpenAI API as an alternative to Claude Code
- **Terminal sessions** — persistent terminal pane within a session
- **Windows GUI** — Windows system-tray/taskbar launcher
- **Claude Code direct tool mode** — bypass prompt routing for raw tool execution

### Changed
- Major UI overhaul: redesigned message bubbles, session sidebar, and layout

---

## [Backend 0.1.0 / Client 1.0.0] — 2026-03-02

### Added
- Initial release of RCFlow
- FastAPI backend with WebSocket-based agent orchestration
- Flutter client (Linux, Android) with session-based chat interface
- Claude Code executor (Anthropic) and Codex executor (OpenAI/ChatGPT)
- SQLite database with SQLAlchemy 2.0 async ORM; Alembic migrations
- Interactive permission approval flow for agent tool calls
- Session management: create, list, switch, delete
- Settings system via environment variables and `.env` file
- Linux systemd service install/uninstall scripts
- `justfile` with dev, test, lint, format, and bundle targets

[Unreleased]: https://github.com/Flowelfox/RCFlow/compare/v0.38.0...HEAD
[Backend 0.38.0 / Client 1.44.0]: https://github.com/Flowelfox/RCFlow/compare/v0.31.4...v0.38.0
[Backend 0.31.4 / Client 1.33.4]: https://github.com/Flowelfox/RCFlow/compare/v0.21.0...v0.31.4
[Backend 0.21.0 / Client 1.22.0]: https://github.com/Flowelfox/RCFlow/compare/v0.11.0...v0.21.0
[Backend 0.11.0 / Client 1.9.0]: https://github.com/Flowelfox/RCFlow/compare/v0.5.0...v0.11.0
[Backend 0.5.0 / Client 1.5.0]: https://github.com/Flowelfox/RCFlow/compare/v0.1.0...v0.5.0
[Backend 0.1.0 / Client 1.0.0]: https://github.com/Flowelfox/RCFlow/releases/tag/v0.1.0
