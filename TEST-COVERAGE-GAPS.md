# RCFlow Backend — Test Coverage Gap Report

**Date**: 2026-03-23
**Last updated**: 2026-03-23 — High category addressed
**Scope**: Python backend (`src/`, `tests/`) — `rcflowclient` excluded
**Overall coverage estimate**: ~60% (up from ~40% after high-priority items addressed)

Core modules (buffer, session, slash_commands, telemetry) are well-tested. Critical areas (auth, WebSocket dispatch, LLM streaming) and high-priority areas (permissions, context, session lifecycle, artifacts API, worktrees API) are now covered. Medium-priority areas (remaining HTTP routes, audio, executors) remain open.

---

## Summary Table

| File | Status | Priority |
|------|--------|----------|
| `src/core/buffer.py` | Tested | — |
| `src/core/session.py` | Tested | — |
| `src/core/prompt_router.py` | Partial | High |
| `src/core/context.py` | **Tested** ✓ | ~~High~~ |
| `src/core/session_lifecycle.py` | **Tested** ✓ | ~~High~~ |
| `src/core/permissions.py` | **Tested** ✓ | ~~High~~ |
| `src/core/llm.py` | **Tested** ✓ | ~~Critical~~ |
| `src/core/attachment_store.py` | Untested | Medium |
| `src/core/background_tasks.py` | Untested | Medium |
| `src/api/deps.py` | **Tested** ✓ | ~~Critical~~ |
| `src/api/routes/sessions.py` | Tested | — |
| `src/api/routes/config.py` | Partial | Medium |
| `src/api/routes/slash_commands.py` | Tested | — |
| `src/api/routes/telemetry.py` | Tested | — |
| `src/api/routes/artifacts.py` | **Tested** ✓ | ~~High~~ |
| `src/api/routes/tools.py` | Untested | Medium |
| `src/api/routes/tasks.py` | Untested | Medium |
| `src/api/routes/projects.py` | Untested | Medium |
| `src/api/routes/worktrees.py` | **Tested** ✓ | ~~High~~ |
| `src/api/routes/auth.py` | Untested | Medium |
| `src/api/ws/input_text.py` | **Tested** ✓ | ~~Critical~~ |
| `src/api/ws/output_text.py` | **Tested** ✓ | ~~Critical~~ |
| `src/api/ws/input_audio.py` | Untested | Medium |
| `src/api/ws/output_audio.py` | Untested | Medium |
| `src/api/ws/terminal.py` | Untested | Medium |
| `src/services/telemetry_service.py` | Tested | — |
| `src/services/tool_settings.py` | Tested | — |
| `src/services/tool_manager.py` | Tested | — |
| `src/services/artifact_scanner.py` | Tested | — |
| `src/services/linear_service.py` | Tested | — |
| `src/executors/shell.py` | Tested | — |
| `src/executors/claude_code.py` | Tested | — |
| `src/executors/codex.py` | Tested | — |
| `src/executors/worktree.py` | Tested | — |
| `src/executors/http.py` | Untested | Medium |

---

## ~~Critical~~ — Addressed ✓

### 1. ~~WebSocket handlers~~ — `src/api/ws/input_text.py`, `output_text.py` ✓

**Covered in** `tests/test_api/test_ws/test_input_text.py` and `test_output_text.py`.

**Now tested:**
- Invalid JSON → `INVALID_JSON` error; connection remains open
- Empty / whitespace-only prompt → `EMPTY_PROMPT` error
- Unknown message type → `UNKNOWN_MESSAGE_TYPE` error
- `end_session`, `pause_session`, `resume_session`, `restore_session`, `dismiss_session_end_ask` — success and error paths, missing session_id
- `permission_response` — missing session_id, missing request_id, success
- `prompt` dispatch — ack with session_id, `ensure_session` called with correct args
- `list_sessions`, `list_tasks`, `list_artifacts` — no-DB in-memory paths
- `subscribe` to nonexistent session → `SESSION_NOT_FOUND`
- `subscribe_all` with no sessions — no crash
- `unsubscribe` unknown session — silently ignored

---

### 2. ~~LLM streaming~~ — `src/core/llm.py` ✓

**Covered in** `tests/test_core/test_llm.py`.

**Now tested:**
- `_parse_llm_json` — valid JSON, code fences, truncated/repaired JSON, unterminated strings, fallback
- `_build_assistant_message` — Anthropic and OpenAI formats (text, tool call, text+tool)
- `_build_tool_result_messages` — Anthropic (single user message) vs OpenAI (per-result messages)
- `_stream_turn_anthropic` — text chunks, tool call assembly, `StreamDone` with usage, malformed tool JSON
- `_stream_turn_openai` — text chunks, multi-chunk tool call assembly, `StreamDone` with usage
- `run_agentic_loop` — single turn (no tools), multi-turn (tool executed), `should_stop_after_tools`, orphaned assistant message rollback on tool exception

---

### 3. ~~API authentication~~ — `src/api/deps.py` ✓

**Covered in** `tests/test_api/test_deps.py`.

**Now tested:**
- `hash_api_key` — SHA-256 digest, determinism, uniqueness
- `verify_http_api_key` — valid key accepted, wrong/empty/partial key → HTTP 401
- `verify_ws_api_key` — valid key accepted, wrong/empty/partial key → WS 1008 Policy Violation

---

## ~~High~~ — Addressed ✓

### 4. ~~Context building~~ — `src/core/context.py` ✓

**Covered in** `tests/test_core/test_context.py`.

**Now tested:**
- `_format_file_size` — byte/KB/MB formatting
- `_extract_tool_mentions` — `#tool` regex, edge cases (no mention, mid-word hash)
- `_extract_file_references` — `$file` regex
- `_build_project_context_from_path` — project name and path in output
- `_build_active_worktree_context` — none when no worktree, path/branch/repo when set
- `_build_tool_context` — MUST directive for agent tools, preference for shell, two-step orchestration for worktree+agent
- `_resolve_project_path` — no settings, directory not found, found in multiple search dirs
- `_parse_direct_tool_prompt` — shell/claude_code/codex/single-required-param executors, project `@Mention`, display text stripping
- `_build_file_context` — no-DB fast path returns None

---

### 5. ~~Session lifecycle~~ — `src/core/session_lifecycle.py` ✓

**Covered in** `tests/test_core/test_session_lifecycle.py`.

**Now tested:**
- `ensure_session` — None/unknown/terminal/active session handling
- `end_session` — active, already-completed (idempotent), terminal state error, paused session
- `pause_session` — state transition, buffer message, executor teardown, pending permission cancellation
- `resume_session` — state transition, buffer message, non-paused error
- `interrupt_subprocess` — kills executor, session stays ACTIVE, raises for terminal/paused
- `_reap_inactive_sessions` — ends stale sessions, skips recent/paused/completed
- `_check_token_limit_exceeded` — no limits/input limit/output limit/buffer error message
- `_contains_session_end_ask` — tag detection in string and list content

---

### 6. ~~Permissions~~ — `src/core/permissions.py` ✓

**Covered in** `tests/test_core/test_permissions.py`.

**Now tested:**
- `classify_risk` — all tool types including Bash destructive-pattern escalation, sensitive path escalation, worktree action-based risk
- `describe_tool_action` — human-readable descriptions for all tool types
- `get_scope_options` — available scopes per tool, `tool_path` availability
- `PermissionManager` — rule storage, ONCE/TOOL_SESSION/ALL_SESSION/TOOL_PATH scope semantics, approval workflow, timeout auto-deny, snapshot/restore roundtrip, `cancel_all_pending`

---

### 7. ~~Artifacts API~~ — `src/api/routes/artifacts.py` ✓

**Covered in** `tests/test_api/test_routes_artifacts.py`.

**Now tested:**
- `GET /artifacts/settings` — returns required fields, auth enforcement
- `PATCH /artifacts/settings` — calls `update_settings_file` with correct keys, empty body no-op
- `GET /artifacts` — no-DB returns empty list, accepts search/limit/offset params
- `GET /artifacts/search` — no-DB returns empty list, accepts `q` param
- `GET /artifacts/{id}` — no-DB returns 404, invalid UUID returns 400 when DB present
- `GET /artifacts/{id}/content` — no-DB returns 404, invalid UUID returns 400
- `DELETE /artifacts/{id}` — no-DB returns 500, invalid UUID returns 400

---

### 8. ~~Worktrees API~~ — `src/api/routes/worktrees.py` ✓

**Covered in** `tests/test_api/test_routes_worktrees.py`.

**Now tested:**
- `GET /worktrees` — success with worktrees list, empty list, non-git repo → 400, missing param → 422
- `POST /worktrees` — 201 success, `WorktreeExists` → 409, `InvalidBranchType` → 422, non-git → 400
- `POST /worktrees/{name}/merge` — success, `WorktreeNotFound` → 404, `MergeError` → 500, `UncommittedChanges` → 409, `GitOperationError` → 500
- `DELETE /worktrees/{name}` — success, `WorktreeNotFound` → 404, `GitOperationError` → 500
- `_map_exception` — all 7 exception types produce correct HTTP codes

---

### 9. `prompt_router.py` — partial coverage

Only `cancel_session` is tested.

**Untested:**
- `execute_prompt()` — full orchestration: context build → LLM call → tool dispatch → buffer write
- `handle_tool_output()` — resumes LLM turn after tool result
- `resume_paused_session()` — restores session state and re-queues prompt

**Risk**: `execute_prompt` is the central orchestration function. Any regression here would break all prompt handling.

---

## Medium

### 10. Auth routes — `src/api/routes/auth.py`

**Untested:** Codex device-code flow, Claude Code OAuth login, token storage and refresh, logout cleanup.

### 11. Tasks API — `src/api/routes/tasks.py`

**Untested:** Full CRUD, status transition validation, task-to-session linking.

### 12. Projects API — `src/api/routes/projects.py`

**Untested:** Project path resolution, artifact filtering by directory, missing-directory handling.

### 13. Config routes — `src/api/routes/config.py`

**Untested:** `server_info()`, `list_projects()`, `get_config()`, `update_config()`.

### 14. HTTP executor — `src/executors/http.py`

**Untested:** HTTP tool execution, timeout handling, response parsing.

### 15. Audio WebSocket handlers — `src/api/ws/input_audio.py`, `output_audio.py`

**Untested:** Audio chunk buffering, STT/TTS provider integration, stream teardown.

### 16. Telemetry gaps — `src/services/telemetry_service.py`

Service is tested but missing: aggregation watermark advancement, retention/cleanup logic, turn recording with tool calls.

---

## Integration Gaps

These cross-module flows have no end-to-end coverage:

| Flow | Risk |
|------|------|
| Prompt → session creation → buffer fill → WebSocket output | Core user path entirely untested as a unit |
| Session pause during active LLM stream | Can leave session in inconsistent state |
| Multi-subscriber buffer synchronization | Race conditions between concurrent clients |
| Permission denial → tool blocked → user feedback | Approval workflow untested end-to-end |
| DB transaction rollback on write failure | Partial writes could corrupt session state |
| Inactivity reaper → session pause → client notification | Background task lifecycle untested |

---

## Edge Cases and Failure Paths Not Covered

| Category | Specific Gap |
|----------|-------------|
| Concurrency | Multi-session concurrent prompts; simultaneous WebSocket subscriptions to the same session |
| Resource cleanup | Orphaned tasks on server shutdown; memory leak on session destroy |
| Malformed input | Invalid JSON in WS messages; oversized uploads past limit; corrupted attachment content |
| Database failures | Connection loss mid-turn; partial write rollback |
| Tool timeouts | Subprocess kill on timeout; timeout error propagated to LLM |
| Project resolution | Missing project directory; ambiguous project name matching |
| File operations | Symlink traversal in artifact scanner; permission denied on file read |

---

## Existing Test Quality Notes

| Test File | Assessment |
|-----------|-----------|
| `test_buffer.py` | Excellent — thorough state machine coverage |
| `test_session.py` | Excellent — lifecycle, metadata, worktree context all covered |
| `test_slash_commands.py` | Excellent — caching, fallback, filtering all covered |
| `test_http.py` | Good — sessions routes well-covered; config/tools routes missing |
| `test_routes_artifacts.py` | Good — all endpoints covered; DB-backed paths need integration tests |
| `test_routes_worktrees.py` | Good — all exception mappings and HTTP codes covered |
| `test_permissions.py` | Excellent — full scope semantics, workflow, and snapshot/restore |
| `test_context.py` | Good — mention extraction, tool context, project path, direct-mode parse |
| `test_session_lifecycle.py` | Good — all lifecycle methods, reaper, token limits, tag detection |
| `test_telemetry_service.py` | Good — aggregation core covered; retention and watermark missing |
| `test_prompt_router.py` | Minimal — cancel only; the entire execution path is untested |
| `test_uploads.py` | Good — size limits and MIME types covered |
| `test_tool_settings.py` | Basic — schema validation only; executor integration missing |

---

## Recommended Implementation Order

1. ~~`src/api/deps.py`~~ — **Done** ✓
2. ~~`src/api/ws/input_text.py` + `output_text.py`~~ — **Done** ✓
3. ~~`src/core/llm.py`~~ — **Done** ✓
4. ~~`src/core/context.py`~~ — **Done** ✓
5. ~~`src/core/session_lifecycle.py`~~ — **Done** ✓
6. ~~`src/core/permissions.py`~~ — **Done** ✓
7. ~~`src/api/routes/artifacts.py`~~ — **Done** ✓
8. ~~`src/api/routes/worktrees.py`~~ — **Done** ✓
9. `src/core/prompt_router.py` — execute_prompt and handle_tool_output
10. Remaining HTTP routes (tasks, projects, config, auth, tools)
