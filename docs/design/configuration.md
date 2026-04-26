---
updated: 2026-04-26
---

# Configuration

All configuration is via environment variables, loaded from a `settings.json` file. Environment variables set in the shell take precedence over values in `settings.json`. On first run, if a legacy `.env` file exists it is automatically migrated to `settings.json`.

**See also:**
- [HTTP API](http-api.md) — `/api/config` GET/PATCH for client-side editing
- [Tools](tools.md#per-tool-settings-isolation) — per-tool credential settings
- [Prompt Templates](prompt-templates.md) — `GLOBAL_PROMPT`, caveman settings
- [Deployment](deployment.md) — UPnP/NAT-PMP runtime behavior

---

## Environment Variables

| Variable                | Required | Default         | Description                          |
|-------------------------|----------|-----------------|--------------------------------------|
| `RCFLOW_HOST`           | no       | `127.0.0.1`     | Server bind address                  |
| `RCFLOW_PORT`           | no       | `53890` (Linux) / `53891` (Windows) | Server port                          |
| `RCFLOW_API_KEY`        | yes      |                 | API key for WebSocket auth           |
| `RCFLOW_BACKEND_ID`     | no       | auto-generated  | Unique backend instance ID (UUID). Auto-generated and persisted to `settings.json` on first run. Used to isolate sessions per backend when multiple backends share one database. |
| `SSL_CERTFILE`          | no       |                 | Path to TLS certificate (enables WSS when both cert+key set) |
| `SSL_KEYFILE`           | no       |                 | Path to TLS private key (enables WSS when both cert+key set) |
| `DATABASE_URL`          | no       | `sqlite+aiosqlite:///./data/rcflow.db` | Database connection string (SQLite or PostgreSQL) |
| `LLM_PROVIDER`          | no       | `anthropic`     | LLM provider: `anthropic`, `bedrock`, `openai`, or `none` (direct tool mode). Changing this invalidates the dynamic model catalog for the affected provider. |
| `ANTHROPIC_API_KEY`     | cond.    |                 | Anthropic API key (required when `LLM_PROVIDER=anthropic`) |
| `ANTHROPIC_MODEL`       | no       | `claude-sonnet-4-6`| Anthropic model ID (use Bedrock model IDs when `LLM_PROVIDER=bedrock`) |
| `AWS_REGION`            | no       | `us-east-1`     | AWS region (used when `LLM_PROVIDER=bedrock`) |
| `AWS_ACCESS_KEY_ID`     | no       |                 | AWS access key ID (optional if using IAM roles/instance profiles) |
| `AWS_SECRET_ACCESS_KEY` | no       |                 | AWS secret access key (optional if using IAM roles/instance profiles) |
| `OPENAI_API_KEY`        | cond.    |                 | OpenAI API key (required when `LLM_PROVIDER=openai`) |
| `OPENAI_MODEL`          | no       | `gpt-5.4`       | OpenAI model ID (e.g. gpt-5.4, gpt-4.1, o3) |
| `PROJECTS_DIR`          | no       | `~/Projects`    | Comma-separated list of project directories (used in system prompt, path resolution, and `/api/projects` endpoint) |
| `TOOLS_DIR`             | no       | `./tools`       | Path to tool definitions directory   |
| `CODEX_API_KEY`         | no       |                 | OpenAI API key for Codex CLI         |
| `TITLE_MODEL`           | no       | _(main model)_  | Model for session title generation. When blank, falls back to the main model. |
| `TASK_MODEL`            | no       | _(main model)_  | Model for task extraction and status evaluation. When blank, falls back to the main model. |
| `GLOBAL_PROMPT`         | no       |                 | Custom instructions appended to the system prompt for every session |
| `CAVEMAN_MODE`          | no       | `false`         | Enable terse caveman-style LLM responses (~65-75% fewer tokens) |
| `CAVEMAN_LEVEL`         | no       | `full`          | Caveman intensity: `lite`, `full`, or `ultra` |
| `SESSION_INPUT_TOKEN_LIMIT` | no   | `0` (unlimited) | Max total input tokens (LLM + tool) per session. `0` = no limit. |
| `SESSION_OUTPUT_TOKEN_LIMIT`| no   | `0` (unlimited) | Max total output tokens (LLM + tool) per session. `0` = no limit. |
| `ARTIFACT_INCLUDE_PATTERN` | no    | `*.md`          | Glob pattern for files to include in artifact extraction (case-insensitive) |
| `ARTIFACT_EXCLUDE_PATTERN` | no    | `node_modules/**,...` | Comma-separated glob patterns to exclude from extraction |
| `ARTIFACT_AUTO_SCAN`    | no       | `true`          | Auto-extract artifacts from messages in real time during session execution |
| `ARTIFACT_MAX_FILE_SIZE`| no       | `5242880`       | Max file size in bytes for artifact content viewing (default 5 MB) |
| `LOG_LEVEL`             | no       | `INFO`          | Logging level                        |
| `LINEAR_API_KEY`        | no       |                 | Linear personal API token for issue sync |
| `LINEAR_TEAM_ID`        | no       |                 | Optional. Linear team ID to restrict syncs to a specific team. When blank, issues are synced from all teams accessible via the API key. |
| `LINEAR_SYNC_ON_STARTUP`| no       | `false`         | Automatically sync Linear issues from API on server startup |
| `UPNP_ENABLED`          | no       | `false`         | Enable UPnP IGD port forwarding. When true, the worker asks the local router to forward an external port to its internal `RCFLOW_PORT` on startup and releases it on shutdown. Non-fatal if no IGD is discovered. Also togglable via `rcflow run --upnp` / `--no-upnp`. |
| `UPNP_LEASE_SECONDS`    | no       | `3600`          | Router-side lease duration for the UPnP mapping. Service renews at 50% of this value. `0` = permanent (not accepted by all routers). |
| `UPNP_DISCOVERY_TIMEOUT_MS` | no   | `2000`          | SSDP M-SEARCH timeout (ms) for IGD discovery. Increase on slow or congested LANs. |
| `NATPMP_ENABLED`        | no       | `false`         | Enable NAT-PMP (RFC 6886) port forwarding against a VPN gateway. Lets workers behind ISP CGNAT expose a public address through ProtonVPN Plus / Mullvad / etc. Toggleable via `rcflow run --natpmp`. |
| `NATPMP_GATEWAY`        | no       | `auto`          | VPN gateway IP that speaks NAT-PMP. `auto` tries the ProtonVPN default `10.2.0.1`, then the system default route. Override with an IPv4 literal for other providers. |
| `NATPMP_LEASE_SECONDS`  | no       | `60`            | Mapping lease the gateway should hold. Renewed at 50%. ProtonVPN enforces 60. |
| `NATPMP_INITIAL_TIMEOUT_MS` | no   | `250`           | Per-request timeout for the RFC 6886 retry ladder (doubled each retry, max 5 attempts). |
| `RCFLOW_UPDATE_AUTO_CHECK` | no  | `true`          | Worker GUI: poll the GitHub Releases API for newer versions on launch (subject to the 24-hour cache). Headless `rcflow run` ignores this. Set to `false` to disable all network update checks; manual "Check for Updates" buttons remain. |
| `RCFLOW_UPDATE_LAST_CHECK` | no  |                 | Worker GUI internal: ISO-8601 UTC timestamp of the last successful update check. Managed automatically; manual edits are overwritten. |
| `RCFLOW_UPDATE_CACHED_VERSION` | no |             | Worker GUI internal: latest version string returned by the most recent check. |
| `RCFLOW_UPDATE_CACHED_RELEASE_URL` | no |          | Worker GUI internal: GitHub release page URL for the cached version. |
| `RCFLOW_UPDATE_CACHED_DOWNLOAD_URL` | no |         | Worker GUI internal: platform-matched asset download URL for the cached version. |
| `RCFLOW_UPDATE_CACHED_ASSET_NAME` | no |           | Worker GUI internal: asset filename used to derive the local download path. |
| `RCFLOW_UPDATE_DISMISSED_VERSION` | no |           | Worker GUI internal: most recently dismissed version. The "Update available" banner stays hidden until a strictly newer version is observed. |

## Remote Configuration (Client-Side Editing)

The server exposes `GET /api/config` and `PATCH /api/config` endpoints that allow connected clients to view and edit a subset of server settings remotely. This enables users to configure API keys, provider selection, model IDs, and other options from the Flutter client without manual `settings.json` file editing.

**Config option metadata schema** (returned by `GET /api/config`):

| Field              | Type   | Description                                        |
|--------------------|--------|----------------------------------------------------|
| `key`              | string | Setting name (e.g. `LLM_PROVIDER`)                 |
| `label`            | string | Human-readable label                               |
| `type`             | string | `"string"`, `"textarea"`, `"select"`, `"boolean"`, `"secret"`, or `"model_select"` |
| `value`            | any    | Current value (masked for secrets — last 4 chars)   |
| `options`          | list   | Available choices (for `select` type only)          |
| `group`            | string | Grouping category (LLM, Prompt, Claude Code, Codex, Paths, Session Limits, Logging, Linear, etc.) |
| `description`      | string | Help text                                          |
| `required`         | bool   | Whether the field is required                      |
| `restart_required` | bool   | Whether changing requires a server restart          |
| `dynamic`          | bool   | (`model_select` only) When `true`, the client should fetch live options via `fetch_endpoint` and merge them over the seed list in `models`. Falls back to the seed list on network/API failure. |
| `fetch_endpoint`   | string | (`model_select` only) Endpoint to call (always `/api/models` today). |
| `fetch_scope`      | string | (`model_select` only) Credential scope passed to the endpoint: `global`, `claude_code`, `codex`, or `opencode`. |
| `fetch_provider`   | string | (`model_select` only, optional) Forces the upstream provider regardless of the resolved `provider_key` value. OpenCode sets this to `openrouter` so its dropdown always lists the OpenRouter catalog. |

### Configurable Groups

LLM, Prompt, Claude Code, Codex, Paths, Session Limits, Logging, Linear, Networking. Groups are rendered as collapsible sections in the client UI. The Networking group exposes both UPnP-IGD (`UPNP_*`) and NAT-PMP (`NATPMP_*`) toggles since they share the same purpose (external reachability) but address different network topologies.

**Excluded from remote config** (for security): `RCFLOW_API_KEY`, `RCFLOW_HOST`, `RCFLOW_PORT`, `SSL_CERTFILE`, `SSL_KEYFILE`, `DATABASE_URL`.

**Hot-reload**: When config is updated via `PATCH /api/config`, the server persists changes to `settings.json` and recreates the LLM client with the new settings. The old LLM client is gracefully closed. The dynamic model catalog (see below) is invalidated for any provider whose credentials changed in the request, so the next dropdown open re-fetches against the new key.

## Dynamic Model Catalog

`model_select` schema fields no longer rely solely on the bundled list in `src.config.PROVIDER_MODELS`. Instead the client calls `GET /api/models?provider=<name>&scope=<global|claude_code|codex|opencode>` and the worker returns a TTL-cached list pulled live from the upstream provider:

- **Anthropic** — `anthropic.AsyncAnthropic.models.list()`.
- **OpenAI** — `openai.AsyncOpenAI.models.list()` filtered to chat-capable IDs (kept: `gpt-N`, `oN`, `chatgpt-`; dropped: `audio`, `tts`, `whisper`, `embedding`, `moderation`, `dall-e`, `davinci`, `babbage`, `search`, `realtime`, `transcribe`, `image`).
- **Bedrock** — `aioboto3.Session.client("bedrock").list_foundation_models(byOutputModality="TEXT", byInferenceType="ON_DEMAND")` filtered to `providerName == "Anthropic"` with the regional inference-profile prefix prepended (`us.`, `eu.`, `apac.`).
- **OpenRouter** — public unauthenticated `https://openrouter.ai/api/v1/models`. Used for OpenCode whose model strings are in OpenRouter's `provider/model` form.

**Cache** lives in memory and on disk at `<data_dir>/model_cache.json`, keyed by `(provider, scope, sha256(api_key)[:8])` so swapping keys produces a fresh fetch and no raw key is ever persisted. Default TTL is 600 seconds. The cap of 64 entries prevents unbounded growth across many key rotations. Concurrent fetches for the same key are de-duplicated via per-key `asyncio.Lock`.

**Fallback semantics**: any fetch failure (missing key, network error, upstream 5xx) returns `source="fallback"` plus a non-null `error`, with the bundled `PROVIDER_MODELS` list as the options payload. The endpoint never returns 5xx for upstream errors — that lets the client render an "offline (fallback)" status badge without hiding the dropdown. The frontend renders a status chip ("live · 4m ago" / "cached" / "offline (fallback)") and a refresh button next to every dynamic `model_select` field.

**Per-tool credentials**: `scope=claude_code|codex|opencode` reads keys from each tool's isolated settings (`src/services/tool_settings.py`) so a managed CLI can list models against a key independent of the global `LLM_PROVIDER` configuration.

**Real-time refresh**: The Flutter client owns a per-scope generation counter on `WorkerConnection.modelCatalogGeneration` (`global`, `claude_code`, `codex`, `opencode`). It is bumped from the same call sites that mutate credentials — `PATCH /api/config` save (`global`), `PATCH /api/tools/{tool}/settings` save (matching tool scope), Anthropic Login completion (`claude_code`), Codex/ChatGPT login completion (`codex`). `_DynamicModelInput` listens to the counter and re-fetches with `refresh=true` when its scope's generation changes, so dropdowns update without closing the dialog. The server side mirrors this — `_invalidate_global_model_cache` (in `src/api/routes/config.py`), `_invalidate_tool_model_cache` (in `src/api/routes/tools.py`), and the Anthropic / Codex login handlers in `src/api/routes/auth.py` all call `ModelCatalog.invalidate(...)` so the next fetch goes upstream instead of returning stale cached entries.

## UPnP Port Forwarding

When `UPNP_ENABLED=true`, the worker attempts to create a UPnP IGD port mapping on the local router during lifespan startup so external clients can reach the worker without manual port forwarding. The feature is implemented in `src/services/upnp_service.py` and wired into `src/main.py` (lifespan).

- **Lifecycle**: discovery + mapping happen after the FastAPI lifespan has initialised the server; a background task renews the mapping at 50% of `UPNP_LEASE_SECONDS`, and a separate task re-checks the router's reported external IP every 5 minutes so ISP re-leases or router reboots are reflected in `/api/info`. Shutdown deletes the mapping best-effort.
- **Status vocabulary** (surfaced via `/api/info.upnp.status`): `disabled`, `discovering`, `mapped`, `failed`, `closing`. Clients show `external_ip:external_port` only when `status == "mapped"`.
- **Failure is non-fatal**: no IGD on the LAN, a router that blocks UPnP, or port conflicts mark `status=failed` with an `error` message but never prevent the worker from starting.
- **Port conflicts**: if the router refuses the preferred external port (equal to `RCFLOW_PORT`), the service retries up to 4 higher candidates (`internal_port + 1..+4`). The internal port is never changed.
- **CLI**: `rcflow run --upnp` / `rcflow run --no-upnp` overrides the persisted `UPNP_ENABLED` for a single invocation without writing to `settings.json`.
- **GUI**: both the Windows and macOS dashboards expose a "UPnP Port Forwarding" checkbox next to "WSS Enabled". The Instance Details card gains an "External Address" row populated from the `/api/info` poll. The macOS menu bar and Windows tray gain a disabled-info line showing the external address.

## NAT-PMP Port Forwarding (CGNAT escape via VPN)

UPnP-IGD punches the LAN router's NAT but is useless when an upstream NAT (ISP CGNAT, double-NAT) sits between the router and the public internet. NAT-PMP (RFC 6886) addresses this by negotiating a port mapping with a *VPN gateway* — most commonly ProtonVPN Plus on a P2P-capable server (gateway `10.2.0.1`) or Mullvad. The mapping exposes a public `vpn_exit_ip:external_port` that bypasses the ISP's CGNAT entirely.

When `NATPMP_ENABLED=true`, the worker runs `src/services/natpmp_service.py:NatPmpService` alongside the UPnP service. Both can be enabled simultaneously since they target different gateways.

- **Lifecycle**: `NatPmpService.start()` spawns a background bootstrap task: it resolves the gateway (per `NATPMP_GATEWAY`, defaulting to `10.2.0.1` then the OS default route), queries the public IP via NAT-PMP op=0 (`GetExternalAddress`), then requests a TCP port mapping via op=2 (`AddPortMapping`). On success a renewal task refreshes the mapping every `NATPMP_LEASE_SECONDS / 2` seconds, and a 5-minute IP-watch task notices VPN exit IP rotation. Shutdown sends a release request (lifetime=0) before exiting.
- **Status vocabulary** (`/api/info.natpmp.status`): `disabled`, `discovering`, `mapped`, `failed`, `closing`. Clients should surface `public_ip:external_port` only when `status == "mapped"`.
- **Failure is non-fatal**: gateway unreachable, NAT-PMP error code, VPN disconnect mid-session — the worker logs a warning and keeps running. Specific RFC 6886 result codes (NotAuthorized, UnsupportedOpcode, etc.) get actionable error messages explaining likely causes (free-tier VPN, wrong gateway IP, etc.).
- **Worker port semantics**: the worker keeps binding to `RCFLOW_PORT` as usual — NAT-PMP's `internal_port` field is set to `RCFLOW_PORT`, and the gateway returns the `external_port` external clients should dial. No listener rebinding.
- **GUI**: both dashboards expose a "VPN Port Forwarding (NAT-PMP)" checkbox next to UPnP. The Instance Details card gains a "VPN Address" row populated from `/api/info`. Tray (Windows) and menu bar (macOS) include a disabled-info line showing the VPN-exit address.

**Client UI**: The Flutter client shows a "Settings" button on each connected worker card. Tapping it opens a dialog (desktop) or bottom sheet (mobile) that renders a dynamic form based on the server's config schema. Fields are grouped by section and rendered as text fields, multi-line text areas, dropdowns, switches, or password fields depending on type.

**LLM-missing warning banner**: When the worker's configured `LLM_PROVIDER` (anthropic/openai) has no API key set, the new-session pane shows a yellow banner at the top reading *"LLM key is not configured."* with a **Configure** button that opens the worker edit dialog directly on the Server → LLM section (`initialTabIndex`/`initialServerSection` params on `showWorkerEditDialog`). Readiness is derived client-side from the existing `/api/config` response — no new endpoint — by checking that secrets returned masked by `_mask_secret` are non-empty (empty string → unset). Bedrock and the `none` provider are always treated as configured (AWS credential chain / direct tool mode). The banner lives on `WorkerConnection.hasLlmConfigured` and refreshes via `WorkerConnection.reloadDerivedConfig()` whenever the user saves changes in the embedded config screen, so it clears without a reconnect.
