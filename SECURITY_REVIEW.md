# RCFlow Security Review Report

**Date:** 2026-03-05
**Scope:** Full codebase review — Python backend (`src/`), Flutter client (`rcflowclient/`), tool definitions (`tools/`), scripts, and configuration files.

---

## Executive Summary

RCFlow is a FastAPI-based AI orchestration server with a Flutter client. The review identified **2 Critical**, **3 High**, **11 Medium**, and **8 Low** severity findings across 10 security categories. The most serious issues relate to command injection via shell executor templates, lack of binary integrity verification for tool downloads, and SSRF potential in the HTTP executor.

The project demonstrates good practices in several areas: constant-time auth comparison, SQLAlchemy parameterized queries (no SQL injection), proper `.gitignore` coverage, no hardcoded secrets, safe tarball extraction, and atomic file writes in critical paths.

---

## Findings Summary

| ID | Severity | Category | Finding |
|----|----------|----------|---------|
| F1 | Critical | Injection | Shell command injection via `str.format()` in executors |
| F2 | Critical | Injection | SSRF via HTTP executor — no URL validation |
| F3 | High | Supply Chain | No checksum verification for Codex binary downloads |
| F4 | High | Configuration | systemd service template missing security hardening |
| F5 | High | Code Quality | Unbounded conversation history / agentic loop |
| F6 | Medium | Network | No WebSocket origin validation |
| F7 | Medium | Network | Default bind to `0.0.0.0` exposes all interfaces |
| F8 | Medium | Auth | API key in WebSocket query parameter (log exposure) |
| F9 | Medium | Auth | Dead-code API key model — no multi-key management |
| F10 | Medium | Info Disclosure | Internal exception messages leaked to clients |
| F11 | Medium | Code Quality | Unbounded buffer growth in SessionBuffer |
| F12 | Medium | Code Quality | No rate limiting on any endpoint |
| F13 | Medium | Auth | No per-session authorization (shared access) |
| F14 | Medium | Configuration | Config endpoint allows overwriting third-party API keys |
| F15 | Medium | Supply Chain | Python dependencies use `>=` without upper bounds |
| F16 | Medium | Configuration | Non-atomic `.env` file writes |
| F17 | Low | Secrets | Wispr Flow API key passed in WebSocket URL |
| F18 | Low | Info Disclosure | `/api/info` endpoint leaks OS details |
| F19 | Low | Info Disclosure | Debug-level logging of user prompts |
| F20 | Low | Configuration | Windows `.env` file lacks ACL restrictions |
| F21 | Low | Configuration | Install script prints API key to terminal |
| F22 | Low | Crypto | `hash_api_key` uses unsalted SHA-256 (dead code) |
| F23 | Low | Code Quality | Race condition in Claude Code forwarding path |
| F24 | Informational | Network | Health endpoint unauthenticated (by design) |

---

## Detailed Findings

### F1 — Shell Command Injection via `str.format()` [CRITICAL]

**Files:**
- `src/executors/shell.py:72`
- `src/executors/shell.py:122`

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

### F2 — SSRF via HTTP Executor [CRITICAL]

**Files:**
- `src/executors/http.py:43`
- `src/executors/http.py:100`

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

### F3 — No Checksum Verification for Codex Downloads [HIGH]

**File:** `src/services/tool_manager.py:466-506`

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

### F4 — systemd Service Template Missing Security Hardening [HIGH]

**File:** `systemd/rcflow.service`

**Description:**
The checked-in service file has no security hardening directives:

```ini
[Service]
Type=simple
User=rcflow
ExecStart=/opt/rcflow/.venv/bin/rcflow
```

Missing: `NoNewPrivileges`, `ProtectSystem`, `ProtectHome`, `PrivateTmp`, `ReadWritePaths`, `PrivateDevices`, `ProtectKernelTunables`, `ProtectControlGroups`.

Note: The install script (`scripts/install.sh:305-313`) generates a properly hardened service file, but the checked-in template does not match.

**Risk:** If someone uses the checked-in template directly (instead of the install script), the service runs with unnecessarily broad privileges.

**Remediation:**
Update `systemd/rcflow.service` to include the same hardening directives the install script generates:
```ini
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
ReadWritePaths=/opt/rcflow/data /opt/rcflow/logs /opt/rcflow/certs
```

---

### F5 — Unbounded Conversation History / Agentic Loop [HIGH]

**Files:**
- `src/core/session.py:62`
- `src/core/llm.py:402`

**Description:**
The conversation history (`self.conversation_history: list[dict]`) grows without bound. Each LLM turn appends messages including potentially large tool outputs. The agentic loop (`while True:` in `run_agentic_loop`) has no turn limit.

This creates two risks:
1. **Memory exhaustion (OOM):** Long sessions with verbose tools will consume unbounded memory.
2. **Runaway LLM loops:** A misconfigured tool or LLM hallucination could cause an infinite loop of tool calls, each incurring API costs.

**Risk:** Denial of service via memory exhaustion; uncontrolled API cost amplification.

**Remediation:**
- Add a maximum turn count to the agentic loop (e.g., 50 turns).
- Implement conversation history truncation or summarization.
- Add a per-session memory budget.

---

### F6 — No WebSocket Origin Validation [MEDIUM]

**File:** `src/main.py:146-164`

**Description:**
The FastAPI app has no CORS middleware and no origin validation on WebSocket endpoints. WebSocket connections are not protected by browser same-origin policy — any web page that knows the API key can connect. No `Origin` header checking is performed.

**Risk:** Cross-site WebSocket hijacking if the API key is known.

**Remediation:** Validate the `Origin` header on WebSocket connections against a configurable allowlist.

---

### F7 — Default Bind to 0.0.0.0 [MEDIUM]

**File:** `src/config.py:25`

```python
RCFLOW_HOST: str = "0.0.0.0"
```

**Description:** Server binds to all network interfaces by default, exposing it to the local network and potentially the internet.

**Risk:** Increased attack surface for development setups.

**Remediation:** Default to `127.0.0.1` for development; use `0.0.0.0` explicitly for production deployments.

---

### F8 — API Key in WebSocket Query Parameter [MEDIUM]

**File:** `src/api/deps.py:19`

**Description:** WebSocket authentication passes the API key as a URL query parameter (`?api_key=SECRET`). Query parameters appear in server access logs, browser history, proxy logs, and network monitoring tools.

**Risk:** API key leakage via logs and URL exposure.

**Remediation:** Support API key via the first message after WebSocket connection, or via WebSocket subprotocols.

---

### F9 — Dead-Code API Key Model [MEDIUM]

**Files:**
- `src/models/db.py:12-19` — `ApiKey` model with `key_hash`
- `src/api/deps.py:13-15` — `hash_api_key()` function

**Description:** The database has an `ApiKey` model with hashing support, but it is never used. All auth compares directly against `settings.RCFLOW_API_KEY`. This means no key rotation without downtime, no per-client keys, and no revocation capability.

**Risk:** Operational security gap — no key management capabilities despite the infrastructure existing.

**Remediation:** Either implement the multi-key system or remove the dead code to avoid confusion.

---

### F10 — Internal Exception Messages Leaked to Clients [MEDIUM]

**Files:**
- `src/api/http.py:351` — `{"step": "error", "message": str(e)}`
- `src/api/http.py:382` — Same pattern for install failures
- `src/api/http.py:405` — `raise HTTPException(status_code=400, detail=str(e))`
- `src/api/ws/input_text.py:69-70` — `{"type": "error", "content": str(e)}`

**Description:** Raw exception messages (`str(e)`) are sent directly to clients. These can leak internal file paths, system configuration details, and implementation specifics.

**Risk:** Information disclosure aiding further attacks.

**Remediation:** Return generic error messages to clients. Log detailed exceptions server-side only.

---

### F11 — Unbounded Buffer Growth in SessionBuffer [MEDIUM]

**File:** `src/core/buffer.py:50-76`

**Description:** `SessionBuffer._text_messages` and `_audio_chunks` have no size limits. Long-running sessions cause unbounded memory growth.

**Risk:** Memory exhaustion / OOM.

**Remediation:** Add a maximum buffer size with oldest-message eviction.

---

### F12 — No Rate Limiting [MEDIUM]

**Files:** `src/api/ws/input_text.py`, `src/api/http.py`

**Description:** No rate limiting exists on any endpoint. A client with a valid API key could send thousands of prompts per second, spawning unbounded LLM calls (with associated API costs), or create unlimited sessions.

**Risk:** Resource exhaustion and cost amplification.

**Remediation:** Add per-client rate limiting and a maximum concurrent session count.

---

### F13 — No Per-Session Authorization [MEDIUM]

**Files:** `src/api/ws/input_text.py`, `src/api/ws/output_text.py`

**Description:** All authenticated clients share a single API key and can access any session — subscribe to outputs, send prompts, cancel, or restore any session. No session-level access control exists.

**Risk:** No multi-tenancy isolation if the server is shared.

**Remediation:** Implement per-session ownership or per-client key scoping.

---

### F14 — Config Endpoint Allows Overwriting Third-Party API Keys [MEDIUM]

**File:** `src/api/http.py:1054-1089`

**Description:** `PATCH /api/config` allows modifying `ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `OPENAI_API_KEY`, etc. Values are written to `.env` without validation. An attacker with a compromised RCFlow API key could redirect LLM traffic to a malicious proxy by changing the API keys.

Note: `RCFLOW_API_KEY` is correctly excluded from `CONFIGURABLE_KEYS`.

**Risk:** Credential manipulation if the RCFlow API key is compromised.

**Remediation:** Add value-format validation for API keys. Consider requiring re-authentication for sensitive config changes.

---

### F15 — Python Dependencies Use `>=` Without Upper Bounds [MEDIUM]

**File:** `pyproject.toml`

**Description:** Dependencies use minimum pins (e.g., `fastapi>=0.115.0`) without upper bounds. While `uv.lock` ensures reproducible builds, a fresh `uv sync` without the lockfile could pull untested future versions with breaking changes or vulnerabilities.

**Risk:** Supply chain risk on fresh installs.

**Remediation:** Consider using compatible-release pins (e.g., `fastapi~=0.115.0`) or document that `uv.lock` must always be used.

---

### F16 — Non-Atomic `.env` File Writes [MEDIUM]

**File:** `src/config.py:326-355`

```python
path.write_text("\n".join(new_lines) + "\n")
```

**Description:** The `.env` file is written directly, not atomically via temp-file + rename. A process crash mid-write could corrupt the file. Contrast with `tool_settings.py:431-437` which correctly uses atomic writes.

**Risk:** Configuration corruption causing service outage.

**Remediation:** Use the same atomic write pattern (`tmp_path.rename(settings_path)`) as `tool_settings.py`.

---

### F17 — Wispr Flow API Key in WebSocket URL [LOW]

**File:** `src/speech/stt/wispr_flow.py:26`

**Description:** The STT API key is passed as a query parameter in the WebSocket URL. Mitigated by `wss://` encryption, but may appear in logs.

---

### F18 — `/api/info` Endpoint Leaks OS Details [LOW]

**File:** `src/api/http.py:55-62`

**Description:** Returns OS type, version, architecture, and hostname. Behind authentication, but assists attackers with a compromised key.

---

### F19 — Debug-Level Logging of User Prompts [LOW]

**File:** `src/executors/claude_code.py:263`

**Description:** User prompts are logged at debug level. If debug logging is enabled in production, user content appears in log files.

**Remediation:** Redact or truncate user content in logs, or ensure debug logging is disabled in production.

---

### F20 — Windows `.env` File Lacks ACL Restrictions [LOW]

**File:** `scripts/install.ps1:280`

**Description:** The Windows installer does not set restrictive NTFS ACLs on the `.env` file. Any user with directory access can read secrets. The Linux installer correctly uses `chmod 600`.

**Remediation:** Add `icacls` commands to restrict `.env` file access to the service account only.

---

### F21 — Install Script Prints API Key to Terminal [LOW]

**File:** `scripts/install.sh:262-264`

**Description:** The generated API key is printed to stdout during installation. Could appear in terminal scrollback or CI/CD logs.

---

### F22 — `hash_api_key` Uses Unsalted SHA-256 [LOW]

**File:** `src/api/deps.py:13-15`

**Description:** The `hash_api_key()` function uses plain SHA-256 without a salt. This is dead code (never called), but if implemented, it would be vulnerable to rainbow table attacks. Credential storage should use bcrypt/scrypt/argon2.

---

### F23 — Race Condition in Claude Code Forwarding Path [LOW]

**File:** `src/core/prompt_router.py:715-730`

**Description:** The Claude Code forwarding path operates outside the session's `_prompt_lock`. Two rapid prompts could be forwarded concurrently without serialization. Unlikely to trigger due to asyncio's cooperative scheduling.

---

### F24 — Health Endpoint Unauthenticated [INFORMATIONAL]

**File:** `src/api/http.py:40-46`

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
| **Atomic Writes** | Tool settings use atomic write via temp-file + rename (`src/services/tool_settings.py:431-437`). |
| **Claude Code Checksums** | Claude Code binary downloads verified with SHA-256 (`src/services/tool_manager.py:437`). |
| **Linux .env Permissions** | Install script sets `chmod 600` on `.env` file. |

---

## Recommendations Priority

### Immediate (Critical/High)
1. **Sanitize shell executor parameters** — Apply `shlex.quote()` or switch to list-based subprocess execution.
2. **Add SSRF protection to HTTP executor** — Block private IP ranges and cloud metadata endpoints.
3. **Add checksum verification for Codex downloads** — Match the Claude Code verification approach.
4. **Add agentic loop turn limits** — Prevent runaway LLM loops.
5. **Update systemd template** — Add security hardening directives to the checked-in file.

### Short-Term (Medium)
6. Add WebSocket origin validation.
7. Default bind address to `127.0.0.1`.
8. Implement rate limiting.
9. Sanitize error messages returned to clients.
10. Use atomic writes for `.env` file updates.
11. Add per-session buffer size limits.

### Long-Term (Low / Architectural)
12. Implement the multi-key API system (or remove dead code).
13. Move WebSocket API key auth to first-message or subprotocol.
14. Add per-session authorization / multi-tenancy support.
15. Set Windows `.env` file ACLs in installer.
