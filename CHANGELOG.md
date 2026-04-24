# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Backend and client are versioned independently. Entries are grouped by release date
and note which component is affected where it matters.

---

## [Unreleased]

### Added
- **"Add to Client" deep link** — new button next to *Copy Token* in the worker GUI (Windows settings panel, macOS settings panel, and macOS menu-bar icon) launches the installed Flutter client via a `rcflow://add-worker` URL with the host, port, token, SSL flag, and worker hostname pre-filled. The client opens the Add Worker dialog with the fields populated; if a worker with the same host+port+token is already configured, it shows *"Already added as '<existing name>'"* instead of creating a duplicate. URL-scheme registration is installed on Windows (Inno Setup registry keys), macOS/iOS (`CFBundleURLTypes`), Android (`intent-filter` on `MainActivity`) and Linux (`.desktop` file with `x-scheme-handler/rcflow`). Deep-link receiver uses the `app_links` Flutter plugin (Backend + Client)
- **`--minimized` flag on `rcflow gui` / `rcflow tray`** — starts the app with the dashboard hidden (tray-only). Login autostart entries (macOS LaunchAgent plist + Windows registry `Run` value) now pass the flag so rebooting does not steal focus with a dashboard popup; the tray icon is still visible and clicking it opens the dashboard (Backend)

### Changed
- **Worker dashboard visible on launch (macOS)** — the settings window is shown when `rcflow gui` starts instead of starting minimised to the menu bar. The close button still hides the window back to the menu bar (unchanged). The app dynamically switches between regular (Dock icon visible) while the window is open and accessory (menu-bar-only) while the window is closed (Backend)
- **macOS "open again" opens dashboard** — double-clicking the .app in Finder / clicking it in Dock / running `open <app>` while it's already running now reliably raises the dashboard. Implemented via the `kAEReopenApplication` AppleEvent handler, which catches the reopen event that LaunchServices sends to the existing LSUIElement process (Backend)

### Fixed
- **Menu bar beachball on Quit (macOS)** — clicking "Quit" in the menu bar no longer freezes the cursor near the menu or leaves the status item visible while the server reaps. The quit action is now drained through the same flag pattern as the other menu items so the NSMenu modal tracking loop returns before `stop_sync()` blocks. The singleton file lock is released correctly so the next `rcflow gui` launch works immediately (Backend)
- **Second `rcflow gui` launch reveals the existing dashboard (macOS + Windows)** — a loopback IPC channel (`<data_dir>/.worker.ipc`) lets the running instance raise its window when a second launch is attempted. Previously macOS relied on an AppleScript fallback that only worked for registered `.app` bundles, and Windows had no singleton at all (Backend)

---

## [Backend 0.40.1 / Client 1.43.2] — 2026-04-23

### Added
- **Markdown copy context menu** — right-clicking rendered markdown anywhere in a pane (user/assistant/agent-start message bubbles, artifact pane, task description, plan-review card) now opens a menu with "Copy" (plain text, markdown syntax stripped) and "Copy as Markdown" (raw source). Lets users paste into either a plain-text destination or a markdown-aware editor without losing formatting (Client)
- **Markdown rendering in agent-start bubble** — the prompt shown in the "Claude Code started" / agent-start block now renders Markdown (headings, lists, code, links) instead of showing raw `##` / `**` characters (Client)
- **Queued user messages** — prompts sent while the agent is still processing a prior turn are held in a persistent queue and pinned at the bottom of the chat with a "queued" indicator until the agent actually picks them up. Queued messages can be edited in place (text only, any position) or cancelled while queued; once delivered they are immutable. Queue survives backend restarts (`session_pending_messages` table); attachments spill to `data/pending_attachments/`. New WebSocket message types: `message_queued`, `message_dequeued`, `message_queued_updated`, `cancel_ack`, `edit_ack`; inputs `cancel_queued` and `edit_queued`; `ack` and `session_update` gain `queued`/`queued_id` and `queued_messages` respectively. See the "Queued User Messages" section in `Design.md` (Backend + Client)
- **Caveman mode** — backend strips filler words from LLM output (~65–75% token reduction); configurable per-session
- **Session drafts** — unsent message draft persisted per session in the database (`drafts` table)
- **Client self-update** — client discovers new releases via GitHub Releases API and prompts the user to update
- **Session reordering** — drag-and-drop session sorting with server-side persistence (`sort_order` column)
- **CLAUDE_CODE_UNDERCOVER** — configurable setting to hide Claude Code identity from the model; disabled by default
- Android shell screen (`android_shell.dart`)
- Session identity bar widget (`session_identity_bar.dart`)
- Right-click context menu on artifact list items to remove artifacts from tracking (Client)
- **Artifact multi-selection** — Shift+click range selection, Ctrl/Meta+click toggle, bulk right-click delete, selection bar with count, and Escape to clear; matches existing task/session multi-select UX (Client)
- **Unified badge list in session responses** — `session_update` broadcasts and session list responses now include a `badges` array (status, worker, agent, caveman, project, worktree) computed by `BadgeState`; archived sessions use `compute_archived()`. Client falls back to `LegacyBadgeAdapter` for pre-0.39.0 servers (Backend + Client)
- **Agent badge in new-chat identity bar** — when an agent mention chip is selected before sending the first message, the identity bar previews the corresponding agent badge immediately (Client)
- **Diff stats on collapsed tool blocks** — Edit/Write blocks show a compact `+N −M` line count beside the filename when collapsed; blocks auto-expand in loaded history when a diff is present (Client)
- **Styled checkboxes in markdown** — checkbox list items in assistant bubbles, the artifact pane, and the task pane now render as icon checkboxes themed to the app accent/secondary colours (Client)
- **"Create worktree" in input area** — worktree chip dropdown now includes a "Create worktree" option that opens the shared `showCreateWorktreeDialog`; the new worktree is auto-selected for new sessions (via `pendingWorktreePath`) or pushed to the server for active sessions (Client)
- **Backend version in `/server-info`** — response now includes a `"version"` field populated from the installed `rcflow` package metadata (Backend)
- **System prompt task-routing rules** — system prompt rewritten with explicit routing strategy (answer directly / `shell_exec` / coding agent), agent delegation guidelines (self-contained prompt, working directory, no pasted file contents), and session flow guidance (Backend)

### Changed
- Session sort helper extracted into dedicated utility
- CI push trigger restricted to `main` to prevent duplicate PR runs
- **Diff view padding** — Edit/Write diff blocks now sit inside a subtle rounded frame with 12 px horizontal inset (matching the rest of the tool body) and a little more breathing room per row, so they no longer bleed flush against the tool card edge (Client)
- **Session reorder gated to the grip icon.** `ReorderableDragStartListener` in `worker_group.dart` previously wrapped the entire session tile, so a long-press anywhere on a row could start a reorder by accident. It now wraps only the leading drag-indicator icon (still nested with the cross-pane `Draggable` so both the reorder and the drag-to-pane gestures remain available from the grip) (Client)
- **Sessions list is now sliver-virtualized per tile.** `SessionListPanel._buildWorkersTab` used an outer `ListView.builder` over workers, and each worker rendered its expanded session list via `ReorderableListView.builder(shrinkWrap: true, physics: NeverScrollableScrollPhysics())` inside a `Column` — `shrinkWrap: true` defeats virtualization, so a worker with 400+ sessions built every tile synchronously on expand/connect, locking the UI for a visible moment. The outer container is now a single `CustomScrollView`, and `WorkerGroup.build()` returns a `SliverMainAxisGroup` composed of a `SliverToBoxAdapter` header plus `SliverList.builder` / `SliverReorderableList` / per-project sliver fragments. Only the session tiles in the current viewport (typically 10-20) are built, no matter how many sessions the worker holds; reorder-within-project and terminals-first ordering are preserved (Client)

### Performance
- **Android transcript no longer lags during streaming.** Four compounding fixes in the WS-driven render path (Client):
  - `PaneState` streaming-path mutations (`appendAssistantChunk`, `startToolBlock`, `appendToolOutput`, `applyDiffToLastToolBlock`, `addDisplayMessageInStream`) now coalesce through `_scheduleNotify()` instead of calling `notifyListeners()` immediately and then again via the 16 ms `_enqueueText` debounce — halves rebuild fan-out per chunk on structural transitions.
  - `OutputDisplay` `ListView.builder` now keys each item with `ObjectKey(msg)`. Without a key, every `PaneState` notify caused Flutter to treat all visible bubbles as fresh items and re-inflate them.
  - `AssistantBubble` and `AgentSessionStartBubble` are now `StatefulWidget`s that cache the inner `MarkdownBody` widget tree by content; finished bubbles further up the transcript no longer rebuild their full `MarkdownStyleSheet` + parse tree on every per-frame notify driven by the actively-streaming bubble below.
  - `SessionListPanel._buildWorkersTab` now uses a `Selector<AppState, _SessionsTabSignature>` keyed on a cheap fingerprint of fields the tab actually displays (worker config, session id/title/status/sortOrder/badges, terminals). Per-token `PaneState` notifications bubble up to `AppState` but no longer alter the signature, so the Sessions tab — which Android's `IndexedStack` keeps mounted in the background — stops re-running its filter/grouping logic ~30×/sec while the user is on Chat.

### Fixed
- **Android release APK build failure** — Flutter 3.41.x emits a `GeneratedPluginRegistrant.java` entry for every dev dependency that ships an Android plugin. `integration_test` (`IntegrationTestPlugin`) was registered there but absent from the release Gradle compile classpath, so `compileReleaseJavaWithJavac` failed. Removed `integration_test` from `dev_dependencies` and excluded `integration_test/` from analyzer scope so `--fatal-warnings` no longer trips on the orphaned import (Client)
- **Android bottom navigation never appeared** — `AndroidShell` (Sessions / Chat / Settings tabs) was added in an earlier commit but never wired into `main.dart`, so Android still rendered the desktop fallback layout from `HomeScreen._buildNonDesktop()` with no bottom switcher. `main.dart` now routes to `AndroidShell` on Android, and the shell triggers the first-run setup wizard so a clean install still goes through onboarding (Client)
- **Claude Code Bash output and Edit/Write diffs missing from client UI** — `_relay_claude_code_stream` matched top-level `event_type == "tool_result"` events, but Claude Code actually emits tool results as content blocks inside `{"type":"user", "message":{"content":[{"type":"tool_result",...}]}}`. The legacy branch was dead code in production, so no `TOOL_OUTPUT` message was ever pushed — Bash blocks had no expandable stdout and Edit/Write blocks had no red/green diff. Added a `user` event handler that iterates `message.content` and processes each `tool_result` block through the shared `_process_tool_result` helper; legacy branch kept for existing synthetic-event tests (Backend)
- **Orphaned backend after macOS GUI crash (no tray icon, server still running)** — the GUI spawned the backend as a `subprocess.Popen` child without any lifetime binding, so when the macOS app crashed after auto-lock/sleep-wake, the server was reparented to `launchd` and kept serving clients with no UI to stop it. Two defenses added in `src/gui/core.py` + `src/__main__.py`: (1) `ServerManager.start()` now passes `RCFLOW_PARENT_PID` to the subprocess and `_install_parent_death_watchdog` polls the parent every 2 s and sends SIGTERM to uvicorn when the GUI is gone (cross-platform via `os.kill`/`OpenProcess`); (2) `ServerManager` now writes a pidfile at `<data_dir>/.worker.pid` on start and deletes it on graceful stop, and a relaunched GUI calls `ServerManager.adopt_if_running()` before spawning — if the pidfile references a live pid, the manager tracks it as **adopted** and exposes Stop via raw signal so the user can terminate the orphan. Both GUIs show "Running (…) — recovered" in the status pill when an orphan is adopted (Backend)
- Migration `down_revision` corrected to reference squashed initial schema
- Duplicate `targetWorker` field in draft-related WebSocket messages
- Null-aware map elements to satisfy Dart `use_null_aware_elements` lint
- **"Copy Token" shows "No API token configured" on clean install** — three root causes: (1) GUI process read `RCFLOW_API_KEY` from a stale `os.environ` loaded before the server subprocess generated the token — fixed by adding `read_token_from_file()` that reads directly from `settings.json`, bypassing the env cache; (2) `ServerManager.start()` now calls `get_settings()` before spawning the subprocess, generating and writing the token to `settings.json` before the server process starts and eliminating the startup race window; (3) `_update_ui` unconditionally overwrote any Copy Token feedback within 300 ms — fixed with a 3-second sticky status that prevents the "Running" label from trampling success/error messages (Backend)
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
- **Non-JSON Claude Code stdout contaminating the assistant stream** — startup banners and debug lines leaking to stdout were relayed as `TEXT_CHUNK`, which closed the active agent tool group and caused subsequent file-diff blocks to be applied to an orphaned block. Now routed as `AGENT_LOG` (silently consumed, never rendered); a `_classify_log_level()` helper tags each line as debug/info/warn/error (Backend + Client)
- **Edit/Write diffs silently dropped when tool content is empty** — `_relay_claude_code_stream` checked `if content:` before emitting tool output, so diff-only results (empty content, non-empty diff) were never sent. Condition is now `if content or diff:` (Backend)
- **Session sort order lost across restarts** — `sort_order` was not written in `save_all_sessions` or restored in `reload_stale_sessions`, so custom session ordering reset on every backend restart (Backend)
- **`#tool_mention` tags visible in chat history** — when the user typed an agent mention directly (rather than selecting via the chip), the raw `#claude_code` prefix was stored in the buffer and echoed back in history. `handle_prompt` now derives a `display_text` by stripping all `#mention` markers before buffering; `PaneState.sendMessage` applies the same strip for the local echo (Backend + Client)
- **Worker badge on archived sessions showed internal backend ID** — archived sessions never receive `session_update` messages, so the worker badge label was never replaced with the user-configured friendly name. Session list processing in `WorkerConnection` now substitutes the friendly name for worker badges in both the session list and individual `session_update` handlers (Client)
- **Dead stub sessions appear as ghost sessions after crash** — on restart, `reload_stale_sessions` now detects sessions with no title, no conversation history, and no buffered messages (crashed before first response) and deletes them from the DB instead of restoring them as empty active sessions (Backend)
- **Agent mention restored from draft used raw key instead of tool name** — `_selectedToolMention` was set directly from the `agent` pluck value (e.g. `"claude_code"`) without normalizing through `kAgentMentionNames`, causing a mismatch with the mention chip display (Client)
- **Mojibake in HTTP-fetched session history (`â€—`, `â†’` instead of `—`, `→`)** — `WebSocketService` decoded all REST response bodies via `const io.SystemEncoding().decoder`, which uses the OS locale (cp1252 on Windows, GBK on zh-CN, etc.) to decode UTF-8 JSON sent by FastAPI. Loaded session messages, tasks, plugin manifests, and other history endpoints rendered double-encoded punctuation. WebSocket frames were unaffected because `web_socket_channel` already returns text frames as proper UTF-8 strings. Replaced all 68 occurrences with `utf8.decoder` so REST decoding is locale-independent (Client)

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
