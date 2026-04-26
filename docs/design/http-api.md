---
updated: 2026-04-26
---

# HTTP API

REST endpoints. All except `/api/health` require `X-API-Key` header (same key as `RCFLOW_API_KEY`).

**See also:**
- [WebSocket API](websocket-api.md) — streaming protocol
- [Configuration](configuration.md) — what `/api/config` exposes
- [Telemetry](telemetry.md) — `/api/telemetry/*`

---

## Contents

- [Health & Info](#health--info)
- [Config](#config)
- [Models](#models)
- [Sessions](#sessions)
- [Drafts](#drafts)
- [Projects](#projects)
- [Tools — discovery & management](#tools--discovery--management)
- [Tools — per-tool settings](#tools--per-tool-settings)
- [Tools — Codex login](#tools--codex-login)
- [Tools — Claude Code login](#tools--claude-code-login)
- [Tasks](#tasks)
- [Uploads / Attachments](#uploads--attachments)
- [Worktrees](#worktrees)
- [Artifacts](#artifacts)
- [Telemetry](#telemetry)
- [Linear Integration](#linear-integration)
- [Auth header](#auth-header)
- [Swagger / ReDoc](#api-documentation-swagger--redoc)

---

## Health & Info

| Method | Endpoint     | Auth | Description |
|--------|--------------|------|-------------|
| GET    | `/api/health`| No   | Health check — returns `{"status": "ok"}` |
| GET    | `/api/info`  | Yes  | Server metadata — `{"os", "backend_id", "active_sessions", "version", "supports_attachments", "attachment_capabilities", "upnp", "natpmp"}`. `upnp` reports the LAN-router IGD mapping; `natpmp` reports the VPN-gateway (RFC 6886) mapping. Each has `{enabled, status, ...}` with status one of `disabled`, `discovering`, `mapped`, `failed`, `closing`. |

## Config

| Method | Endpoint    | Auth | Description |
|--------|-------------|------|-------------|
| GET    | `/api/config` | Yes | Server configuration schema with current values. Secret values masked. Options grouped by section. |
| PATCH  | `/api/config` | Yes | Update config. Body: `{"updates": {"KEY": "value", ...}}`. Persists to `settings.json`, reloads settings, hot-reloads LLM client. Returns updated schema. Invalidates the dynamic model catalog for any provider whose credentials changed. |

## Models

| Method | Endpoint      | Auth | Description |
|--------|---------------|------|-------------|
| GET    | `/api/models` | Yes  | Dynamic LLM model catalog. Query params: `provider` (one of `anthropic`, `openai`, `bedrock`, `openrouter`), `scope` (one of `global`, `claude_code`, `codex`, `opencode`; default `global`), `refresh` (bool, default `false`). Returns `{provider, scope, options: [{value, label}], allow_custom: true, source: "live"|"cached"|"fallback", fetched_at: ISO8601|null, ttl_seconds: int, error: str|null}`. Upstream failures stay `200` with `source="fallback"` and `error` populated; 422 for unknown provider/scope. Credentials resolve from `Settings` for `scope=global` and from `ToolSettingsManager.get_settings(scope)` otherwise. See [Configuration → Dynamic Model Catalog](configuration.md#dynamic-model-catalog) for cache semantics. |

## Sessions

| Method | Endpoint                                | Auth | Description |
|--------|-----------------------------------------|------|-------------|
| GET    | `/api/sessions`                         | Yes  | List all sessions (in-memory + archived) sorted by `created_at` desc. Includes `title`. |
| GET    | `/api/sessions/{session_id}/messages`   | Yes  | Get message history (in-memory or archived). Cursor pagination via `?limit=N` + `?before=SEQ`. Response includes `pagination: {total_count, has_more, next_cursor}`. Omitting `limit` returns all (backward compat). |
| POST   | `/api/sessions/{session_id}/cancel`     | Yes  | Cancel a running session (kills subprocess) |
| POST   | `/api/sessions/{session_id}/end`        | Yes  | Gracefully end a session (user-confirmed completion) |
| POST   | `/api/sessions/{session_id}/pause`      | Yes  | Pause an active session. Kills any running Claude Code subprocess. New prompts rejected until resumed. |
| POST   | `/api/sessions/{session_id}/interrupt`  | Yes  | Kill any running subprocess without pausing the session. Session stays ACTIVE. Broadcasts a null `subprocess_status` ephemeral message. |
| POST   | `/api/sessions/{session_id}/resume`     | Yes  | Resume a paused session. Client can subscribe to receive all buffered output. |
| POST   | `/api/sessions/{session_id}/restore`    | Yes  | Restore an archived (completed/failed/cancelled) session back to active. Rebuilds conversation history, buffer, Claude Code executor state. |
| PATCH  | `/api/sessions/{session_id}/title`      | Yes  | Set or clear a session title (max 200 chars). Body: `{"title": "..."}` or `{"title": null}`. |
| PATCH  | `/api/sessions/{session_id}/reorder`    | Yes  | Reorder sessions. Body: `{"after_session_id": "uuid" \| null}`. See [WebSocket — Session Reordering](websocket-api.md#session-reordering). |
| PATCH  | `/api/sessions/{session_id}/worktree`   | Yes  | Set or clear the selected worktree. Body: `{"path": string \| null}`. When set, Claude Code and Codex use this path as `cwd`. Returns `{"session_id", "selected_worktree_path"}`. |

## Drafts

| Method | Endpoint                              | Auth | Description |
|--------|---------------------------------------|------|-------------|
| PUT    | `/api/sessions/{session_id}/draft`    | Yes  | Save or update unsent message draft. Body: `{"content": "..."}`. Returns 204. |
| GET    | `/api/sessions/{session_id}/draft`    | Yes  | Retrieve unsent draft. Returns `{"content": "...", "updated_at": "..."}`. Returns `content: ""` (never 404) when no draft exists. |

## Projects

| Method | Endpoint                          | Auth | Description |
|--------|-----------------------------------|------|-------------|
| GET    | `/api/projects`                   | Yes  | List directory names from all configured project dirs (`PROJECTS_DIR`, comma-separated). Optional `?q=` substring filter. Returns `{"projects": [...]}`. |
| GET    | `/api/projects/{name}/artifacts`  | Yes  | List artifacts whose `file_path` is under the given project directory. Resolves name against `PROJECTS_DIR`. Returns `{"project_name", "project_path", "artifacts": [{artifact_id, file_path, file_name, file_extension, file_size, mime_type, discovered_at, modified_at, session_id}]}`. 404 if project not found. |

## Tools — discovery & management

| Method | Endpoint                              | Auth | Description |
|--------|---------------------------------------|------|-------------|
| GET    | `/api/tools`                          | Yes  | List registered tool names + descriptions. Optional `?q=` substring filter. Returns `{"tools": [{"name": "...", "description": "..."}]}`. |
| GET    | `/api/tools/status`                   | Yes  | Installation status, versions, update availability for managed CLI tools (Claude Code, Codex, OpenCode). |
| GET    | `/api/tools/auth/preflight`           | Yes  | Per-coding-agent auth-readiness check. Returns `{"agents": {"claude_code": {"ready": bool, "issue": String?}, "codex": {...}, "opencode": {...}}}`. Client calls this when user picks an agent chip so missing API key/login surfaces as immediate warning instead of silent CLI hang. OAuth flows (Anthropic Login, ChatGPT) and OpenCode global mode always report `ready: true` because state lives in CLI's own credential store. |
| POST   | `/api/tools/update`                   | Yes  | Check + install updates to RCFlow-managed CLI tools. Only updates tools managed by RCFlow. |
| POST   | `/api/tools/{name}/install`           | Yes  | Download + install managed version. Re-detects so both sources available. |
| POST   | `/api/tools/{name}/source`            | Yes  | Switch tool between managed and external source. |

## Tools — per-tool settings

| Method | Endpoint                              | Auth | Description |
|--------|---------------------------------------|------|-------------|
| GET    | `/api/tools/{tool_name}/settings`     | Yes  | Per-tool settings schema + current values for a managed CLI tool. |
| PATCH  | `/api/tools/{tool_name}/settings`     | Yes  | Update per-tool settings. Body: `{"updates": {"key": value, ...}}`. Returns updated schema+values. |

See [Tools — Per-Tool Settings Isolation](tools.md#per-tool-settings-isolation).

## Tools — Codex login

| Method | Endpoint                              | Auth | Description |
|--------|---------------------------------------|------|-------------|
| POST   | `/api/tools/codex/login`              | Yes  | Start Codex ChatGPT login. Optional `?device_code=true` for device-code flow. Streams NDJSON: `auth_url` or `device_code`, `waiting`, `complete`, `error`. Times out after 5 min. |
| GET    | `/api/tools/codex/login/status`       | Yes  | Returns `{"logged_in": bool, "method": "ChatGPT"|null}`. |

## Tools — Claude Code login

| Method | Endpoint                                  | Auth | Description |
|--------|-------------------------------------------|------|-------------|
| POST   | `/api/tools/claude_code/login`            | Yes  | Start Anthropic OAuth login. Returns `{"auth_url": "..."}`. Opens a `claude auth login` process that waits for a code. |
| POST   | `/api/tools/claude_code/login/code`       | Yes  | Submit OAuth code. Body: `{"code": "..."}`. Returns `{"logged_in": bool, "email": String?, "subscription": String?}`. |
| GET    | `/api/tools/claude_code/login/status`     | Yes  | Returns `{"logged_in": bool, "method": String?, "email": String?, "subscription": String?}`. |
| POST   | `/api/tools/claude_code/logout`           | Yes  | Returns `{"logged_out": true}`. |

## Tasks

| Method | Endpoint                                | Auth | Description |
|--------|-----------------------------------------|------|-------------|
| GET    | `/api/tasks`                            | Yes  | List all tasks for current backend. Optional `?status=` and `?source=` filters. Sorted by `updated_at` desc. |
| GET    | `/api/tasks/{task_id}`                  | Yes  | Single task with attached sessions. |
| POST   | `/api/tasks`                            | Yes  | Create. Body: `{"title", "description?", "source?", "session_id?"}`. Returns 201. |
| PATCH  | `/api/tasks/{task_id}`                  | Yes  | Update fields (title, description, status). Status transitions validated (409 on invalid). |
| DELETE | `/api/tasks/{task_id}`                  | Yes  | Delete task + all session associations. |
| POST   | `/api/tasks/{task_id}/sessions`         | Yes  | Attach session to task. Body: `{"session_id": "..."}`. Returns 201. |
| DELETE | `/api/tasks/{task_id}/sessions/{sid}`   | Yes  | Detach session from task. |
| POST   | `/api/tasks/{task_id}/plan`             | Yes  | Start a read-only planning session for a task. Body: `{"project_name"?, "selected_worktree_path"?}`. Returns `{"session_id", "task_id"}`. Plan saved as Markdown artifact + linked via `plan_artifact_id` when session ends. See [Pre-Planning Sessions](sessions.md#pre-planning-sessions). |

## Uploads / Attachments

| Method | Endpoint        | Auth | Description |
|--------|-----------------|------|-------------|
| POST   | `/api/uploads`  | Yes  | Upload file attachment. `multipart/form-data` field `file`. Returns `{attachment_id, file_name, mime_type, size, is_image}`. Max 20 MB. Expires after 10 min if not consumed. |

## Worktrees

Linux/macOS only.

| Method | Endpoint                              | Auth | Description |
|--------|---------------------------------------|------|-------------|
| GET    | `/api/worktrees`                      | Yes  | List worktrees for a repo. Required `?repo_path=`. Returns `{"worktrees": [{name, branch, base, path, created_at}]}`. |
| POST   | `/api/worktrees`                      | Yes  | Create. Body: `{"branch", "base"="main", "repo_path"}`. Branch must follow `type/ticket/description`. Returns 201 with `{"worktree": {...}}`. |
| POST   | `/api/worktrees/{name}/merge`         | Yes  | Squash-merge into base + clean up. Body: `{"message", "repo_path", "into"?, "no_ff"?, "keep"?}`. |
| DELETE | `/api/worktrees/{name}`               | Yes  | Remove worktree + branch without merging. Required `?repo_path=`. |

## Artifacts

| Method | Endpoint                          | Auth | Description |
|--------|-----------------------------------|------|-------------|
| GET    | `/api/artifacts`                  | Yes  | List artifacts. Query params: `search`, `limit`, `offset` |
| GET    | `/api/artifacts/search`           | Yes  | Autocomplete search. Query: `q`. Max 10 results with `file_name`, `file_path`, `file_extension`, `file_size`, `mime_type`, `is_text`. |
| GET    | `/api/artifacts/{id}`             | Yes  | Artifact metadata |
| GET    | `/api/artifacts/{id}/content`     | Yes  | Raw file content (text/plain) |
| DELETE | `/api/artifacts/{id}`             | Yes  | Delete artifact record (not file) |
| GET    | `/api/artifacts/settings`         | Yes  | Get extraction settings |
| PATCH  | `/api/artifacts/settings`         | Yes  | Update extraction settings |

## Telemetry

| Method | Endpoint                                       | Auth | Description |
|--------|------------------------------------------------|------|-------------|
| GET    | `/api/telemetry/summary`                       | Yes  | Global lifetime summary for this backend: total tokens, avg LLM/tool latencies, top-10 tools by call count. |
| GET    | `/api/telemetry/worker/summary`                | Yes  | Worker-level aggregate: session count, turn/token/tool totals, avg/p95 LLM + tool latency, error rate, top-10 tools. |
| GET    | `/api/telemetry/sessions/{session_id}/summary` | Yes  | Per-session: turn-by-turn breakdown (timestamps, tokens, TTFT, tool calls per turn) + aggregate (avg/p95 LLM + tool latency, error rate, duration). |
| GET    | `/api/telemetry/timeseries`                    | Yes  | Pre-aggregated time-series. Required: `zoom` (`minute`/`hour`/`day`), `start`, `end` (ISO8601 UTC). Optional: `session_id` (UUID, filter to one session; omit for global rollup), `metric` (return only that field). Returns `{zoom, start, end, session_id, last_updated_at, series: [{bucket, tokens_sent, tokens_received, avg_llm_duration_ms, avg_tool_duration_ms, turn_count, tool_call_count, error_count}]}`. |

## Linear Integration

All under `/api/integrations/linear/`. See [Linear Integration](linear.md#http-endpoints) for full table.

## Auth header

All authenticated endpoints use `X-API-Key: <RCFLOW_API_KEY>`.

## API Documentation (Swagger / ReDoc)

FastAPI auto-generated docs exposed **only when running from source** (e.g. `just run` / `uv run rcflow`). Released/frozen builds (PyInstaller bundles via `just bundle-*`) set `docs_url`, `redoc_url`, `openapi_url` to `None` — `/docs`, `/redoc`, `/openapi.json` return `404`. Toggle keys off `src.paths.is_frozen()` in `src/main.py::create_app()`.
