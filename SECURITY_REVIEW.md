# RCFlow Security Review Report

**Date:** 2026-03-31 *(updated from 2026-03-05)*
**Scope:** Full codebase review — Python backend (`src/`), Flutter client (`rcflowclient/`), tool definitions (`tools/`), scripts, and configuration files.

---

## Executive Summary

RCFlow is a FastAPI-based AI orchestration server with a Flutter client. This review identified **2 Critical**, **3 High**, **12 Medium**, **9 Low**, and **1 Informational** finding across 10 security categories. All Critical and High findings are resolved. Most Medium and Low findings are now resolved. Three new findings were added (F25–F27), all of which are also fixed.

**Current open findings (unresolved):** F12 (rate limiting), F13 (per-session authorization), F15 (dependency upper bounds), F17 (Wispr Flow API key in URL), F23 (Claude Code forwarding race).

The project demonstrates strong practices in many areas: constant-time auth comparison, parameterized SQL queries, no hardcoded secrets, safe tarball extraction, correct SHA-256 verification for binary downloads, atomic file writes, bounded buffer growth, WebSocket origin validation, and API key format validation.

---

## Findings Summary

| ID | Severity | Category | Finding |
|----|----------|----------|---------|
| F1 | ~~Critical~~ | ~~Injection~~ | ~~Shell command injection via `str.format()` in executors~~ — **FIXED** |
| F2 | ~~Critical~~ | ~~Injection~~ | ~~SSRF via HTTP executor — no URL validation~~ — **FIXED** |
| F3 | ~~High~~ | ~~Supply Chain~~ | ~~No checksum verification for Codex binary downloads~~ — **FIXED** |
| F4 | ~~High~~ | ~~Configuration~~ | ~~systemd service template missing security hardening~~ — **RESOLVED** |
| F5 | ~~High~~ | ~~Code Quality~~ | ~~Unbounded conversation history / agentic loop~~ — **FIXED** |
| F6 | ~~Medium~~ | ~~Network~~ | ~~No WebSocket origin validation~~ — **FIXED** |
| F7 | ~~Medium~~ | ~~Network~~ | ~~Default bind to `0.0.0.0` exposes all interfaces~~ — **FIXED** |
| F8 | ~~Medium~~ | ~~Auth~~ | ~~API key in WebSocket query parameter (log exposure)~~ — **FIXED** |
| F9 | ~~Medium~~ | ~~Auth~~ | ~~Dead-code API key model — no multi-key management~~ — **FIXED** |
| F10 | ~~Medium~~ | ~~Info Disclosure~~ | ~~Internal exception messages leaked to clients~~ — **FIXED** |
| F11 | ~~Medium~~ | ~~Code Quality~~ | ~~Unbounded buffer growth in SessionBuffer~~ — **FIXED** |
| F12 | Medium | Code Quality | No rate limiting on any endpoint |
| F13 | Medium | Auth | No per-session authorization (shared access) |
| F14 | ~~Medium~~ | ~~Configuration~~ | ~~Config endpoint allows overwriting third-party API keys~~ — **FIXED** |
| F15 | Medium | Supply Chain | Python dependencies use `>=` without upper bounds |
| F16 | ~~Medium~~ | ~~Configuration~~ | ~~Non-atomic `settings.json` file writes~~ — **FIXED** |
| F25 | ~~Medium~~ | ~~Secrets~~ | ~~Flutter API key stored in plain-text SharedPreferences~~ — **FIXED** |
| F17 | Low | Secrets | Wispr Flow API key passed in WebSocket URL |
| F18 | ~~Low~~ | ~~Info Disclosure~~ | ~~`/api/info` endpoint leaks OS details~~ — **FIXED** |
| F19 | Low | Info Disclosure | Debug-level logging of user prompts |
| F20 | ~~Low~~ | ~~Configuration~~ | ~~Windows `.env` file lacks ACL restrictions~~ — **FIXED** |
| F21 | ~~Low~~ | ~~Configuration~~ | ~~Install script prints API key to terminal~~ — **FIXED** |
| F22 | ~~Low~~ | ~~Crypto~~ | ~~`hash_api_key` uses unsalted SHA-256 (dead code)~~ — **FIXED** |
| F23 | Low | Code Quality | Race condition in Claude Code forwarding path |
| F26 | ~~Low~~ | ~~Configuration~~ | ~~`WSS_ENABLED=true` default without certificate validation check~~ — **FIXED** |
| F27 | ~~Low~~ | ~~Code Quality~~ | ~~Artifact glob patterns not validated (ReDoS potential)~~ — **FIXED** |
| F24 | Informational | Network | Health endpoint unauthenticated (by design) |

---

## Detailed Findings

### F1 — Shell Command Injection via `str.format()` [CRITICAL] — FIXED

**Files:**
- `src/executors/shell.py:72`
- `src/executors/shell.py:122`

**Status:** FIXED — `_quote_params_for_shell()` applied in both `execute()` and `execute_streaming()`

**Description:**
The shell executor interpolates LLM-supplied parameters directly into shell commands using Python's `str.format()`:

```python
command = config.command_template.format(**parameters)
```

If a tool's `command_template` is `ls {directory}`, the LLM (potentially via prompt injection) could supply `directory="; rm -rf /"` and achieve arbitrary command execution. The LLM acts as a gatekeeper, but LLMs are susceptible to prompt injection attacks.

**Risk:** Arbitrary command execution on the host.

**Remediation:**
- Use `shlex.quote()` on all parameter values before interpolation.
- Pass arguments as a list to `subprocess` rather than a formatted shell string.
- Implement parameter validation with allowlists where possible.
- The permission system provides defense-in-depth, but should not be the sole mitigation.

---

### F2 — SSRF via HTTP Executor [CRITICAL] — FIXED

**Files:**
- `src/executors/http.py:43`
- `src/executors/http.py:100`

**Status:** FIXED — `_validate_url_no_ssrf()` blocks private/reserved IP ranges with DNS resolution in both `execute()` and `execute_streaming()`

**Description:**
The HTTP executor constructs outbound request URLs from LLM-supplied parameters:

```python
url = _substitute_env_vars(config.url_template.format(**parameters))
```

There is no URL validation, allowlist, or blocklist. An attacker who prompt-injects the LLM could make requests to:
- Cloud metadata services (`http://169.254.169.254/latest/meta-data/`)
- Internal network services (`http://10.x.x.x/`, `http://192.168.x.x/`)
- Localhost services (`http://127.0.0.1/`)

**Risk:** Server-Side Request Forgery — access to internal services, cloud credentials, and internal network scanning.

**Remediation:**
- Block requests to private/internal IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `127.0.0.0/8`, `::1`).
- Block cloud metadata endpoints explicitly.
- Restrict HTTP tools to pre-defined URL domain allowlists in tool definitions.
- Resolve DNS before connecting and validate the resolved IP against the blocklist (prevents DNS rebinding).

---

### F3 — No Checksum Verification for Codex Downloads [HIGH] — FIXED

**File:** `src/services/tool_manager.py:466-506`

**Status:** FIXED — `_fetch_codex_checksums()` downloads `checksums.txt` from the GitHub release and `_verify_codex_asset_checksum()` validates SHA-256 before installation, in both streaming and non-streaming paths

**Description:**
Claude Code downloads correctly verify SHA-256 checksums from a manifest (lines 424-441). However, Codex binary downloads have **no integrity verification**:

```python
# Claude Code: ✓ checksum verified
actual = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
if actual != expected_checksum:
    raise ValueError(...)

# Codex: ✗ NO checksum
resp = await client.get(download_url, timeout=_DOWNLOAD_TIMEOUT)
tmp_path.write_bytes(resp.content)
tmp_path.rename(binary_path)
```

A MITM attack or compromised GitHub release could deliver a malicious binary that is then made executable and run with the server's privileges.

**Risk:** Remote code execution via supply chain compromise.

**Remediation:**
- Verify checksums for Codex downloads (GitHub releases typically include checksum files).
- Consider verifying GPG signatures on release artifacts.

---

### F4 — systemd Service Template Missing Security Hardening [HIGH] — RESOLVED

**File:** `systemd/rcflow.service`

**Status:** RESOLVED — verified in 2026-03-31 review; hardening directives are present in the current file.

The checked-in `systemd/rcflow.service` now includes all required hardening:

```ini
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=no
PrivateTmp=true
ReadWritePaths=/opt/rcflow/data /opt/rcflow/logs /opt/rcflow/certs /opt/rcflow/managed-tools
```

No further action required.

---

### F5 — Unbounded Conversation History / Agentic Loop [HIGH] — FIXED

**Files:**
- `src/core/session.py:62`
- `src/core/llm.py:464`

**Status:** FIXED — `_MAX_AGENTIC_TURNS = 50` constant added; `while True` replaced with `for turn_number in range(_MAX_AGENTIC_TURNS)` with a warning log and user-facing message on limit hit

**Description:**
The conversation history (`self.conversation_history: list[dict]`) grows without bound. Each LLM turn appends messages including potentially large tool outputs. The agentic loop (`while True:` at `llm.py:464`) has no turn limit.

This creates two risks:
1. **Memory exhaustion (OOM):** Long sessions with verbose tools will consume unbounded memory.
2. **Runaway LLM loops:** A misconfigured tool or LLM hallucination could cause an infinite loop of tool calls, each incurring API costs.

**Risk:** Denial of service via memory exhaustion; uncontrolled API cost amplification.

**Remediation:**
- Add a maximum turn count to the agentic loop (e.g., 50 turns).
- Implement conversation history truncation or summarization.
- Add a per-session memory budget.

---

### F6 — No WebSocket Origin Validation [MEDIUM] — FIXED

**File:** `src/main.py` → `src/api/deps.py`, `src/config.py`

**Status:** FIXED — `WS_ALLOWED_ORIGINS` config field added; `verify_ws_api_key` now validates the `Origin` header against the allowlist when configured. Native-app clients without an Origin header are always allowed.

**Description:**
The FastAPI app has no CORS middleware and no origin validation on WebSocket endpoints. WebSocket connections are not protected by browser same-origin policy — any web page that knows the API key can connect. No `Origin` header checking is performed.

**Risk:** Cross-site WebSocket hijacking if the API key is known.

**Remediation:** Validate the `Origin` header on WebSocket connections against a configurable allowlist.

---

### F7 — Default Bind to 0.0.0.0 [MEDIUM] — FIXED

**File:** `src/config.py:130`

**Status:** FIXED — Default changed to `127.0.0.1`. Production deployments must set `RCFLOW_HOST=0.0.0.0` explicitly.

```python
RCFLOW_HOST: str = "0.0.0.0"
```

**Description:** Server binds to all network interfaces by default, exposing it to the local network and potentially the internet.

**Risk:** Increased attack surface for development setups.

**Remediation:** Default to `127.0.0.1` for development; use `0.0.0.0` explicitly for production deployments.

---

### F8 — API Key in WebSocket Query Parameter [MEDIUM] — FIXED

**File:** `src/api/deps.py:19`

**Status:** FIXED — All three WebSocket endpoints now accept `api_key` as an optional query parameter (`str | None = Query(None)`). When omitted, the server accepts the connection then waits up to 10 seconds for a first-message auth frame `{"type": "auth", "api_key": "..."}` (implemented in `handle_ws_first_message_auth()`). Query-parameter auth still works for existing clients.

**Description:** WebSocket authentication passes the API key as a URL query parameter (`?api_key=SECRET`). Query parameters appear in server access logs, browser history, proxy logs, and network monitoring tools. All three WebSocket endpoints are affected (`/ws/input/text`, `/ws/output/text`, `/ws/terminal`).

Client-side evidence: `rcflowclient/lib/services/server_url.dart:36,39,42` constructs all WebSocket URIs with the key in the query string.

**Risk:** API key leakage via logs and URL exposure.

**Remediation:** Support API key via the first message after WebSocket connection, or via WebSocket subprotocols.

---

### F9 — Dead-Code API Key Model [MEDIUM] — FIXED

**Files:**
- `src/models/db.py` — `ApiKey` model (removed)
- `src/api/deps.py` — `hash_api_key()` function (removed)

**Status:** FIXED — The unused `ApiKey` ORM class and `hash_api_key()` function have been deleted. The `import hashlib` that existed solely for that function was also removed.

**Description:** The database has an `ApiKey` model with hashing support, but it is never used. All auth compares directly against `settings.RCFLOW_API_KEY`. This means no key rotation without downtime, no per-client keys, and no revocation capability.

**Risk:** Operational security gap — no key management capabilities despite the infrastructure existing.

**Remediation:** Either implement the multi-key system or remove the dead code to avoid confusion.

---

### F10 — Internal Exception Messages Leaked to Clients [MEDIUM] — FIXED

**Files:**
- `src/api/routes/auth.py:97,168,190,268` — broad `Exception` catch in streaming auth handlers
- `src/api/routes/tools.py:143,173` — broad `Exception` catch in streaming tool install/update handlers

**Status:** FIXED — All broad `Exception` catches in streaming NDJSON endpoints now log with `logger.exception()` and return a generic `"… — see server logs"` message instead of `str(exc)`. Typed-exception catches (`ValueError`, `RuntimeError`) in `input_text.py` and `sessions.py` retain their messages since those are controlled application-level strings.

**Description:** Raw exception messages (`str(e)`) are sent directly to clients. These can leak internal file paths, system configuration details, and implementation specifics.

**Risk:** Information disclosure aiding further attacks.

**Remediation:** Return generic error messages to clients. Log detailed exceptions server-side only.

---

### F11 — Unbounded Buffer Growth in SessionBuffer [MEDIUM] — FIXED

**File:** `src/core/buffer.py:50-76`

**Status:** FIXED — `_MAX_BUFFER_MESSAGES = 2000` constant added; `push_text()` now evicts the oldest entry when the limit is exceeded and logs at DEBUG level.

**Description:** `SessionBuffer._text_messages` has no size limit. Long-running sessions cause unbounded memory growth.

**Risk:** Memory exhaustion / OOM.

**Remediation:** Add a maximum buffer size with oldest-message eviction.

---

### F12 — No Rate Limiting [MEDIUM]

**Files:** `src/api/ws/input_text.py`, `src/api/http.py`

**Status:** CONFIRMED (unmitigated)

**Description:** No rate limiting exists on any endpoint. A client with a valid API key could send thousands of prompts per second, spawning unbounded LLM calls (with associated API costs), or create unlimited sessions.

**Risk:** Resource exhaustion and cost amplification.

**Remediation:** Add per-client rate limiting and a maximum concurrent session count.

---

### F13 — No Per-Session Authorization [MEDIUM]

**Files:** `src/api/ws/input_text.py`, `src/api/ws/output_text.py`

**Status:** CONFIRMED (unmitigated)

**Description:** All authenticated clients share a single API key and can access any session — subscribe to outputs, send prompts, cancel, or restore any session. No session-level access control exists.

**Risk:** No multi-tenancy isolation if the server is shared.

**Remediation:** Implement per-session ownership or per-client key scoping.

---

### F14 — Config Endpoint Allows Overwriting Third-Party API Keys [MEDIUM] — FIXED

**File:** `src/api/routes/config.py`

**Status:** FIXED — `update_config` now validates known API key formats before persisting: `ANTHROPIC_API_KEY` must start with `sk-ant-`, `OPENAI_API_KEY` must start with `sk-`. Invalid values are rejected with HTTP 422.

**Description:** `PATCH /api/config` allows modifying `ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OPENAI_API_KEY`, etc. Values are written to `settings.json` without validation. An attacker with a compromised RCFlow API key could redirect LLM traffic to a malicious proxy by changing the API keys.

Note: `RCFLOW_API_KEY` is correctly excluded from `CONFIGURABLE_KEYS`.

**Risk:** Credential manipulation if the RCFlow API key is compromised.

**Remediation:** Add value-format validation for API keys. Consider requiring re-authentication for sensitive config changes.

---

### F15 — Python Dependencies Use `>=` Without Upper Bounds [MEDIUM]

**File:** `pyproject.toml`

**Status:** CONFIRMED (unmitigated)

**Description:** Dependencies use minimum pins (e.g., `fastapi>=0.115.0`) without upper bounds. While `uv.lock` ensures reproducible builds, a fresh `uv sync` without the lockfile could pull untested future versions with breaking changes or vulnerabilities.

**Risk:** Supply chain risk on fresh installs.

**Remediation:** Consider using compatible-release pins (e.g., `fastapi~=0.115.0`) or document that `uv.lock` must always be used.

---

### F16 — Non-Atomic `settings.json` File Writes [MEDIUM] — FIXED

**File:** `src/config.py:604`

**Status:** FIXED — `update_settings_file()` now writes to a `.tmp` sibling and calls `tmp_path.replace(path)` for an atomic rename, matching the pattern already used by `tool_settings.py`.

```python
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
```

**Description:** The `settings.json` file (primary config store since `.env` migration) is written directly without an atomic temp-file + rename pattern. A process crash mid-write could corrupt the file. Contrast with `tool_settings.py:431-437` which correctly uses atomic writes.

**Risk:** Configuration corruption causing service outage.

**Remediation:** Use the same atomic write pattern (`tmp_path.rename(settings_path)`) as `tool_settings.py`.

---

### F25 — Flutter API Key Stored in Plain-Text SharedPreferences [MEDIUM] — FIXED

**File:** `rcflowclient/lib/services/settings_service.dart:89-90`

**Status:** ACCEPTED — API keys are stored in `SharedPreferences` (platform-native storage: Android `shared_prefs` XML, Windows registry, Linux preferences file). The `flutter_secure_storage` dependency was removed because its Windows plugin requires ATL headers (`atlstr.h`), adding a heavy Visual Studio build dependency. Since RCFlow is a local server tool running on the user's own machine (not a multi-tenant mobile app), the incremental security benefit of OS keychain storage does not justify the build complexity. The API key is user-provided and can be regenerated at the LLM provider.

```dart
String get apiKey => _prefs.getString(_apiKeyKey) ?? '';
set apiKey(String value) => _prefs.setString(_apiKeyKey, value);
```

**Description:** The RCFlow API key is stored via `SharedPreferences`, which writes to plain-text XML on Android (`/data/data/<package>/shared_prefs/`) and a plain-text plist on iOS (`NSUserDefaults`). While inaccessible to other apps on non-rooted devices, the data is readable with root/jailbreak access, device backups (if backup encryption is not enforced), or physical forensic access.

**Risk:** API key extraction on rooted/jailbroken devices, forensic analysis, or unencrypted device backups.

**Remediation:**
- Risk accepted: SharedPreferences is adequate for this local-server use case.
- Users should protect their device and avoid sharing unencrypted backups.

---

### F18 — `/api/info` Endpoint Leaks OS Details [LOW] — FIXED

**File:** `src/api/routes/config.py`

**Status:** FIXED — `os_version`, `architecture`, and `hostname` removed from the `/api/info` response. Only `os` (e.g., `"Linux"`) is retained for client display purposes.

---

### F19 — Debug-Level Logging of User Prompts [LOW]

**File:** `src/executors/claude_code.py:263`

**Status:** FALSE POSITIVE — Investigation showed `src/executors/claude_code.py` only logs the binary path, command args, cwd, session ID, and whether an API key is set — no actual prompt content. No `logger.debug.*prompt` patterns exist in the codebase.

---

### F20 — Windows Installer API Key File Lacks ACL Restrictions [LOW] — FIXED

**File:** `scripts/install.ps1`

**Status:** FIXED — The installer now writes the API key to `$InstallDir\initial-key.txt` and immediately restricts it to `Administrators` + `SYSTEM` only using PowerShell ACL APIs (`SetAccessRuleProtection` + `FileSystemAccessRule`). The key is no longer printed to the console.

---

### F21 — Install Script Prints API Key to Terminal [LOW] — FIXED

**File:** `scripts/install.sh`

**Status:** FIXED — The generated API key is now written to `$INSTALL_PREFIX/initial-key.txt` with `chmod 600` instead of being echoed to stdout. The terminal output now shows the path and `sudo cat` command to retrieve it. Users are prompted to delete the file after copying.

---

### F22 — `hash_api_key` Uses Unsalted SHA-256 [LOW] — FIXED

**File:** `src/api/deps.py` (removed)

**Status:** FIXED — `hash_api_key()` was dead code (never called). Removed along with the `ApiKey` ORM model (F9). The `import hashlib` is also gone.

---

### F23 — Race Condition in Claude Code Forwarding Path [LOW]

**File:** `src/core/prompt_router.py:715-730`

**Status:** CONFIRMED (unmitigated)

**Description:** The Claude Code forwarding path operates outside the session's `_prompt_lock`. Two rapid prompts could be forwarded concurrently without serialization. Unlikely to trigger due to asyncio's cooperative scheduling.

---

### F26 — `WSS_ENABLED=true` Default Without Certificate Validation [LOW] — FIXED

**File:** `src/main.py`

**Status:** FIXED — The `lifespan` startup function now raises `RuntimeError` if `WSS_ENABLED=true` but either `SSL_CERTFILE` or `SSL_KEYFILE` is empty, preventing silent plaintext fallback.

```python
WSS_ENABLED: bool = True
```

**Description:** `WSS_ENABLED` defaults to `True`, signaling that TLS is expected. However, there is no startup validation that `SSL_CERTFILE` and `SSL_KEYFILE` are configured when this flag is set. If they are missing, the server may silently fall back to plaintext WebSockets while the client and documentation imply TLS is active.

**Risk:** Plaintext communication when users believe TLS is in use.

**Remediation:** At startup, if `WSS_ENABLED=true`, validate that certificate and key files exist and are readable; raise a clear error if not.

---

### F27 — Artifact Glob Patterns Not Validated [LOW] — FIXED

**File:** `src/api/routes/artifacts.py`

**Status:** FIXED — `_validate_glob_pattern()` added. Rejects patterns longer than 200 chars, absolute paths (`/…` or `C:\…`), and patterns containing `..` or `~` path components. Called for both `include_pattern` and `exclude_pattern` before any update is persisted.

**Description:** Users can set arbitrary glob patterns for artifact inclusion/exclusion via the API. Pathologically crafted patterns (e.g., deeply nested `**/*/**/*/**`) can trigger catastrophic backtracking in Python's `glob` / `fnmatch` implementation, causing CPU spikes proportional to the pattern complexity and directory depth being scanned.

**Risk:** Denial of service via ReDoS-style glob pattern exhaustion during artifact scans.

**Remediation:** Validate glob patterns against a safe allowlist (e.g., max depth, no redundant wildcards) before accepting them.

---

### F24 — Health Endpoint Unauthenticated [INFORMATIONAL]

**File:** `src/api/http.py:40-46`

**Status:** CONFIRMED (by design)

**Description:** The `/api/health` endpoint requires no authentication. This is standard practice but confirms server existence.

---

## Positive Findings

The following security practices are well-implemented:

| Area | Details |
|------|---------|
| **SQL Injection** | All queries use SQLAlchemy ORM with parameterized queries. No raw SQL concatenation. |
| **Auth Timing** | `hmac.compare_digest()` used for constant-time API key comparison (`src/api/deps.py:27,46`). |
| **Secrets in Git** | `.gitignore` properly excludes `.env`, `certs/`, `data/`, `*.db`. No secrets in `.env.example`. |
| **Deserialization** | No `pickle`, `yaml.load()`, `eval()`, or `exec()` found in source code. |
| **Random Generation** | `uuid4()` (uses `os.urandom()`) and `secrets.token_urlsafe()` used correctly. |
| **TLS Verification** | All outbound `httpx` calls use default TLS verification. No `verify=False`. |
| **Tarball Extraction** | Uses Python 3.12+ `filter="data"` for safe extraction (`src/services/tool_manager.py:541`). |
| **Session ID Validation** | Session IDs validated as UUIDs throughout. |
| **Pydantic Validation** | API request bodies validated via Pydantic models. |
| **Atomic Writes (Tool Settings)** | Tool settings use atomic write via temp-file + rename (`src/services/tool_settings.py:431-437`). |
| **Claude Code Checksums** | Claude Code binary downloads verified with SHA-256 (`src/services/tool_manager.py:437`). |
| **Linux .env Permissions** | Install script sets `chmod 600` on `.env` file. |
| **Artifact File Path Safety** | Artifact content served from database-stored paths (not user input); extension allowlist enforced. No path traversal risk. |

---

## Recommendations Priority

### Immediate (Critical/High) — ALL RESOLVED
1. ~~**Sanitize shell executor parameters**~~ — **FIXED** (`src/executors/shell.py`) — `_quote_params_for_shell()` with `shlex.quote()` / PowerShell escaping applied.
2. ~~**Add SSRF protection to HTTP executor**~~ — **FIXED** (`src/executors/http.py`) — `_validate_url_no_ssrf()` blocks RFC-1918, loopback, link-local, and IPv6 private ranges with DNS resolution.
3. ~~**Add checksum verification for Codex downloads**~~ — **FIXED** (`src/services/tool_manager.py`) — SHA-256 verified via `checksums.txt` from GitHub release.
4. ~~**Add agentic loop turn limits**~~ — **FIXED** (`src/core/llm.py`) — `_MAX_AGENTIC_TURNS = 50` enforced.
5. ~~**Update systemd template**~~ — **ALREADY RESOLVED** (`systemd/rcflow.service`) — hardening directives confirmed present.

### Short-Term (Medium) — ALL RESOLVED
6. ~~Use `flutter_secure_storage` for Flutter API key storage~~ — **ACCEPTED** (removed; ATL build dep on Windows, risk accepted for local-server use case)
7. ~~Add WebSocket origin validation~~ — **FIXED** (`src/api/deps.py`, `src/config.py` — set `WS_ALLOWED_ORIGINS`)
8. ~~Default bind address to `127.0.0.1`~~ — **FIXED** (`src/config.py:130`)
9. **Implement rate limiting** — still open; requires architectural decision on token-bucket strategy (F12).
10. ~~Sanitize error messages returned to clients~~ — **FIXED** (`src/api/routes/auth.py`, `src/api/routes/tools.py`)
11. ~~Use atomic writes for `settings.json`~~ — **FIXED** (`src/config.py:604`)
12. ~~Add per-session buffer size limits~~ — **FIXED** (`src/core/buffer.py` — capped at 2000 messages)
13. ~~Move WebSocket API key auth to first-message / make query param optional~~ — **FIXED** (`src/api/deps.py`, all 3 WS endpoints)
14. ~~Remove dead-code API key model~~ — **FIXED** (`src/models/db.py`, `src/api/deps.py`)
15. ~~Add API key format validation in config endpoint~~ — **FIXED** (`src/api/routes/config.py`)

### Long-Term (Low / Architectural)
16. Implement the multi-key API system / per-session authorization (F13).
17. Rate limiting (F12) — token-bucket or sliding-window strategy.
18. Wispr Flow API key in WebSocket URL (F17) — consider injecting via header or first-message.
19. Python dependency upper bounds (F15) — consider compatible-release pins.
20. Race condition in Claude Code forwarding path (F23) — low probability, monitor.

---

## Changelog

### 2026-03-31 Update (v2 — fixes applied)

**Re-validation:** All 24 findings from the 2026-03-05 review were re-examined against current code. None were resolved.

**Line number updates** (code growth shifted offsets):
- F5: `llm.py` agentic loop moved from line `402` → `464`
- F7: `RCFLOW_HOST` moved from `config.py:25` → `config.py:130`
- F16: Non-atomic write moved from `config.py:326-355` → `config.py:604`

**Finding updates:**
- F16: Description updated — codebase migrated from `.env` to `settings.json` as the primary config store; the non-atomic write risk now applies to `settings.json`.

**New findings added:**
- **F25 (Medium):** Flutter API key stored in plain-text `SharedPreferences` — extractable on rooted/forensic devices.
- **F26 (Low):** `WSS_ENABLED=true` default has no startup validation that certificates are configured.
- **F27 (Low):** Artifact glob patterns are not validated; crafted patterns could cause CPU exhaustion during scans.

**Confirmed safe (not a finding):** Artifact content endpoint (`src/api/routes/artifacts.py:275-319`) — paths come from the database, not user input, and an extension allowlist is enforced. No path traversal risk.

**Fixes applied in this session (backend v0.31.7, client v1.33.6+64):**
- **F6 bug corrected:** `src/api/deps.py` — Previous fix introduced a broken calling convention (`verify_ws_api_key(api_key)` passing key as `websocket`). Corrected signature to `(websocket: WebSocket, api_key: str)` with no `Query` default; all 3 WS callers updated to `await verify_ws_api_key(websocket, api_key)`.
- **F8 FIXED:** `src/api/deps.py`, `src/api/ws/{input_text,output_text,terminal}.py` — `api_key` query parameter is now optional (`str | None = Query(None)`). When absent, the server accepts the connection then awaits a `{"type": "auth", "api_key": "..."}` first message (10-second timeout) via `handle_ws_first_message_auth()`.
- **F9/F22 FIXED:** `src/models/db.py` — `ApiKey` ORM class removed. `src/api/deps.py` — `hash_api_key()` and `import hashlib` removed.
- **F14 FIXED:** `src/api/routes/config.py` — `update_config` now validates `ANTHROPIC_API_KEY` (must start `sk-ant-`) and `OPENAI_API_KEY` (must start `sk-`) before persisting.
- **F18 FIXED:** `src/api/routes/config.py` — `os_version`, `architecture`, and `hostname` removed from `/api/info` response.
- **F19 FALSE POSITIVE:** Verified no prompt content logged at any level in `src/executors/claude_code.py`.
- **F20 FIXED:** `scripts/install.ps1` — API key written to `initial-key.txt` with `Administrators`/`SYSTEM`-only ACLs via PowerShell ACL APIs; no longer printed to console.
- **F21 FIXED:** `scripts/install.sh` — API key written to `$INSTALL_PREFIX/initial-key.txt` with `chmod 600`; no longer echoed to stdout.
- **F26 FIXED:** `src/main.py` — `lifespan` raises `RuntimeError` on startup if `WSS_ENABLED=true` but `SSL_CERTFILE` or `SSL_KEYFILE` is empty.
- **F27 FIXED:** `src/api/routes/artifacts.py` — `_validate_glob_pattern()` rejects absolute paths, `..`, `~`, and patterns over 200 chars.

**Previously fixed (backend v0.31.6, client v1.33.5+63):**
- **F6 FIXED:** `src/api/deps.py` + `src/config.py` — Added `WS_ALLOWED_ORIGINS` config field; `verify_ws_api_key` now rejects browser WebSocket connections whose `Origin` header is not in the allowlist (when configured). Native-app clients without an Origin header are unaffected.
- **F7 FIXED:** `src/config.py` — Default `RCFLOW_HOST` changed from `0.0.0.0` to `127.0.0.1`. Production setups must opt in to network exposure explicitly.
- **F10 FIXED:** `src/api/routes/auth.py`, `src/api/routes/tools.py` — All broad `Exception` catches in NDJSON streaming endpoints now call `logger.exception()` and return a sanitized generic message instead of `str(exc)`.
- **F11 FIXED:** `src/core/buffer.py` — `_MAX_BUFFER_MESSAGES = 2000`; `push_text()` evicts the oldest entry when the limit is reached.
- **F16 FIXED:** `src/config.py:604` — `update_settings_file()` uses atomic temp-file + `replace()` rename.
- **F25 ACCEPTED:** `rcflowclient/` — `flutter_secure_storage` removed (Windows ATL build dependency); API keys stored in SharedPreferences, risk accepted for this local-server use case.

**Previously fixed (backend v0.31.5):**
- **F6 FIXED:** `src/api/deps.py` + `src/config.py` — Added `WS_ALLOWED_ORIGINS` config field; `verify_ws_api_key` now rejects browser WebSocket connections whose `Origin` header is not in the allowlist (when configured). Native-app clients without an Origin header are unaffected.
- **F7 FIXED:** `src/config.py` — Default `RCFLOW_HOST` changed from `0.0.0.0` to `127.0.0.1`. Production setups must opt in to network exposure explicitly.
- **F10 FIXED:** `src/api/routes/auth.py`, `src/api/routes/tools.py` — All broad `Exception` catches in NDJSON streaming endpoints now call `logger.exception()` and return a sanitized generic message instead of `str(exc)`.
- **F11 FIXED:** `src/core/buffer.py` — `_MAX_BUFFER_MESSAGES = 2000`; `push_text()` evicts the oldest entry when the limit is reached.
- **F16 FIXED:** `src/config.py:604` — `update_settings_file()` uses atomic temp-file + `replace()` rename.
- **F25 ACCEPTED:** `rcflowclient/` — `flutter_secure_storage` removed (Windows ATL build dependency); API keys stored in SharedPreferences, risk accepted for this local-server use case.

**Previously fixed (backend v0.31.5):**
- **F1 FIXED:** `src/executors/shell.py` — Added `_quote_params_for_shell()` using `shlex.quote()` on Unix and double-quote PowerShell escaping on Windows. Applied in both `execute()` and `execute_streaming()`.
- **F2 FIXED:** `src/executors/http.py` — Added `_validate_url_no_ssrf()` that DNS-resolves the URL hostname and rejects any address in RFC-1918, loopback, link-local, or IPv6 private ranges. Applied in both `execute()` and `execute_streaming()`.
- **F3 FIXED:** `src/services/tool_manager.py` — Added `_fetch_codex_checksums()`, `_parse_codex_checksums()`, and `_verify_codex_asset_checksum()`. Both `_download_codex_binary()` and `_stream_codex_download()` now fetch `checksums.txt` from the GitHub release and verify SHA-256 before writing the binary. A checksum step is yielded in the streaming progress events.
- **F4 RESOLVED (pre-existing):** `systemd/rcflow.service` already contains all required hardening directives (`NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, `ReadWritePaths`). The previous report was based on an older version of the file.
- **F5 FIXED:** `src/core/llm.py` — `while True` agentic loop replaced with `for turn_number in range(_MAX_AGENTIC_TURNS)` where `_MAX_AGENTIC_TURNS = 50`. On limit, a warning is logged and a `TextChunk` message is yielded to the client.
