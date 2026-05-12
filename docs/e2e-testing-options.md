# E2E Testing Options — RCFlow

**Date**: 2026-04-14  
**Scope**: Decision report comparing all plausible E2E testing strategies for the RCFlow repository.

---

## 1. Repository Context

RCFlow is a **self-hosted WebSocket-based agent orchestration platform** with two independently-versioned components:

| Component | Technology | Platforms |
|-----------|-----------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy, SQLite/PostgreSQL | Linux, macOS, Windows |
| Client | Flutter (Dart), Provider, web_socket_channel | Android, Windows, macOS, Linux |

### Communication Architecture

```
Flutter Client
  ├── /ws/input/text    ← user prompts, session control
  ├── /ws/output/text   ← streamed LLM output, tool events
  └── /ws/terminal      ← multiplexed PTY sessions (binary frames)
  
REST API
  ├── GET/POST /api/sessions/*
  ├── POST /api/uploads
  ├── GET /api/artifacts, /api/projects/{name}/*
  ├── GET/PATCH /api/config
  └── GET /api/telemetry/*
```

### Existing Test Coverage

| Suite | Files | Lines | What's covered |
|-------|-------|-------|----------------|
| Python pytest (unit/component) | 52 | 18,723 | Core pipeline, REST API, WebSocket handlers, executors, services |
| Flutter unit + widget tests | ~30 | 5,048 | Models, state, widgets, services |

**Key traits of existing tests:**
- LLM calls: fully mocked (`AsyncMock`, no live API credentials required)
- Database: in-memory SQLite (`sqlite+aiosqlite:///:memory:`)
- WebSocket handlers: mocked at the `websocket` object level (`AsyncMock`)
- No test exercises the full path **client ↔ live transport ↔ server ↔ DB**

### CI/CD Baseline

```yaml
# .github/workflows/ci.yml
jobs:
  backend:  # ubuntu-latest: ruff, ty, pytest --cov
  flutter:  # ubuntu-latest: flutter analyze, flutter test
```

No Docker, no container registry, no test database service defined in CI.

---

## 2. What "E2E" Means for This Repo

Three distinct scopes are possible:

| Scope | What it validates |
|-------|-------------------|
| **API-level E2E** | Full WebSocket/REST flow through real server code, real DB, mocked LLM |
| **Flutter integration E2E** | UI actions → real WS transport → backend responses → UI state |
| **System-level E2E** | Compiled binary + compiled Flutter app running on a real device/desktop |

This report covers all three. The viable options narrow significantly as scope increases.

---

## 3. Approaches Enumerated

### Option A — pytest + Starlette TestClient WebSocket (API-level E2E)

**What it is**: Use FastAPI's built-in `TestClient.websocket_connect()` (Starlette's synchronous WS test adapter) or the async `httpx`-based transport to drive the full server code path without binding a real TCP port. LLM calls mocked; SQLite in-memory.

**How it fits this repo**: Builds directly on `tests/conftest.py`. The `test_app` fixture already wires up the full FastAPI app with all dependencies. WebSocket tests in `tests/test_api/test_ws/` already import `test_app` but mock the websocket object — E2E tests would instead use `client.websocket_connect("/ws/input/text")` and let the real handler run.

```python
# Minimal skeleton
from starlette.testclient import TestClient

def test_prompt_to_session_end(test_app, mock_llm_stream):
    with TestClient(test_app) as client:
        # Subscribe to output
        with client.websocket_connect("/ws/output/text?api_key=test-api-key") as out_ws:
            out_ws.send_json({"type": "subscribe_all"})
            # Send prompt
            with client.websocket_connect("/ws/input/text?api_key=test-api-key") as in_ws:
                in_ws.send_json({"type": "prompt", "text": "hello"})
                ack = in_ws.receive_json()
                session_id = ack["session_id"]
            # Read output stream until session_end
            messages = []
            while True:
                msg = out_ws.receive_json()
                messages.append(msg)
                if msg["type"] == "session_end":
                    break
        assert any(m["type"] == "text_chunk" for m in messages)
        assert any(m["type"] == "session_end" for m in messages)
```

| Dimension | Assessment |
|-----------|-----------|
| Fit for this repo | **Excellent** — builds on existing fixtures, same tools, same patterns |
| Setup complexity | **Low** — no new infra; add `pytest.mark.e2e`, extend conftest |
| Maintenance cost | **Low** — changes to API shape break tests immediately (good) |
| CI friendliness | **Excellent** — runs inside existing backend job, zero extra services |
| Cross-platform | Runs on any OS that runs Python; backend-only |
| Flakiness risk | **Low** — synchronous TestClient, no real network, deterministic |
| Auth/session handling | Real `X-API-Key` header or `?api_key=` param; uses `test_settings.RCFLOW_API_KEY` |
| Network mocking | LLM mocked; everything else real (DB, session manager, tool registry) |
| Visual regression | N/A |
| Parallelization | `pytest-xdist` compatible; in-memory DB per test |
| Developer experience | **Excellent** — same `just test` command, same IDE integration |
| Key risks | `TestClient` uses `anyio` threading bridge; async timing edge cases possible. Tests don't cover TLS path or real TCP |

**What would need to be added**:
- `mock_llm_stream` fixture yielding deterministic `TextChunk` → `StreamDone` sequence
- `e2e` pytest marker and optional `-m e2e` CI step
- Helper to drain output WS until `session_end` with timeout

---

### Option B — pytest + Real TCP Server (localhost)

**What it is**: Spin up `uvicorn` on a free port inside a pytest fixture using `anyio` or `asyncio.create_server`. Use Python `websockets` library (or `httpx` with WS extension) from test code to connect as a real client. Exercises TLS, actual TCP stack, and port binding.

**Differences from Option A**: Tests the full transport including TLS handshake and real socket I/O. Catches issues that TestClient's threading bridge hides (e.g., backpressure, partial frames, concurrent readers).

```python
# Skeleton fixture
@pytest.fixture
async def live_server(test_settings):
    config = uvicorn.Config(app=create_app(), host="127.0.0.1", port=0, ssl_keyfile=..., ssl_certfile=...)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.1)  # wait for bind
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"wss://127.0.0.1:{port}"
    server.should_exit = True
    await task
```

| Dimension | Assessment |
|-----------|-----------|
| Fit for this repo | **Good** — covers real TLS path (RCFlow always uses WSS by default) |
| Setup complexity | **Medium** — need `websockets` or `httpx[ws]` dep, port allocation, TLS cert handling |
| Maintenance cost | **Low-Medium** — more moving parts than Option A |
| CI friendliness | **Good** — no external services; port 0 avoids conflicts |
| Flakiness risk | **Medium** — startup timing, port binding, TLS cert generation latency |
| Auth/session | Same API key flow, real headers |
| Key risks | TLS self-signed cert validation (`ssl.CERT_NONE` in tests is required); startup race conditions |

**Additional dep needed**: `websockets>=13.0` or `httpx[ws]` (neither in `pyproject.toml` dev group yet).

---

### Option C — Flutter `integration_test` Package (UI-level E2E)

**What it is**: Flutter's official `integration_test` package (replaces legacy `flutter_driver`). Tests run inside the Flutter app process; can drive real widget interactions. Backend can be a real server or mocked via `MockServer`.

**Two sub-variants**:

**C1 — Mock backend (no real server)**  
Intercept `WebSocketChannel` at the service layer with a test double that replays scripted messages. Validates UI state transitions without backend process.

**C2 — Real backend**  
Start RCFlow backend as a subprocess in `setUpAll`; connect Flutter test app to it. Exercises full stack on a real device/emulator.

| Dimension | C1 (Mock WS) | C2 (Real Backend) |
|-----------|-------------|------------------|
| Fit for this repo | Good for UI state coverage | Best full-stack coverage |
| Setup complexity | Medium | High |
| CI friendliness | Good (emulator required) | Poor (backend + emulator in CI) |
| Flakiness risk | Medium (widget rendering timing) | High (boot timing, network, OS) |
| Platform coverage | Single platform per run | Single platform per run |
| Dev experience | Flutter-native tooling | Complex orchestration |

**Key challenge**: RCFlow's `AppState` hard-wires `WorkerConnection` which owns the WebSocket lifecycle. Injecting a mock requires a DI seam that doesn't exist yet (e.g., a `WorkerConnectionFactory` interface). Without this, mock injection requires monkey-patching.

**Android emulator specifics**: The `justfile` has `just start-emulator` and `just setup-emulator` for WSL2→Windows emulator. CI on `ubuntu-latest` has no emulator; would need `macos-latest` or a self-hosted runner with an emulator service (AVD manager). This adds significant CI runtime and cost.

**Blockers for C2 in CI**:
1. No emulator in standard `ubuntu-latest` GitHub Actions runner
2. Backend subprocess management from Flutter test context is non-trivial
3. macOS runners cost ~10× more compute minutes

---

### Option D — Appium + Flutter Driver Plugin

**What it is**: Appium with `appium-flutter-driver` plugin. Drives Flutter apps via Dart VM service protocol. Tests written in any Appium-compatible language (Python, JS, Java).

| Dimension | Assessment |
|-----------|-----------|
| Fit for this repo | **Poor** — Appium adds heavy infrastructure for minimal gain over `integration_test` |
| Setup complexity | **Very High** — Appium server, Flutter driver plugin, device/emulator |
| CI friendliness | **Poor** — requires device farm or self-hosted runner with Appium |
| Maintenance cost | **High** — Appium + Flutter driver versions often diverge |
| Key risks | Plugin maturity; no support for desktop Flutter (Windows/Linux/macOS) |

**Recommendation**: Skip. `integration_test` covers the same ground with less overhead.

---

### Option E — Playwright / Cypress / Puppeteer / Selenium

**What it is**: Browser-based E2E tools targeting web UIs.

**Applicability**: **Not applicable** to this repo. RCFlow's Flutter client targets native platforms (Android, Windows, macOS, Linux). There is no Flutter web build target in this repo (`web/` directory does not exist; `pubspec.yaml` has no web-specific dependencies; `analysis_options.yaml` has no web targets).

Flutter Web would be technically buildable but:
- Requires adding Flutter Web target
- Not the shipped product being tested
- Removes native platform behavior (foreground service, file picker, window management)
- Creates a separate test target that diverges from production

**Recommendation**: Skip entirely. If Flutter Web is ever added as a target, Playwright would be the right tool — but that would be a separate initiative.

---

### Option F — Containerized Smoke Tests

**What it is**: Dockerfile wrapping the RCFlow backend + a Python smoke client. `docker compose up` in CI; client pings health endpoint, exchanges one WS message flow, asserts session_end received.

**Fit for this repo**: Possible but heavyweight. The repo has no Docker infrastructure today. Main value is deployment-level validation (PyInstaller bundle or `uv run rcflow` startup, port binding, DB init, cert generation).

| Dimension | Assessment |
|-----------|-----------|
| Setup complexity | **High** — Dockerfile, compose file, smoke client, CI step |
| CI friendliness | **Medium** — Docker available in GitHub Actions; adds 2-5 min to CI |
| Maintenance cost | **Medium** — Docker layer caching; Python dep versions pinned separately |
| What it catches | Startup regressions, port binding, DB migration failures, TLS cert generation |
| What it misses | Tool execution detail, session lifecycle edge cases |

**When to add**: After Option A is in place. Smoke tests are a complement, not a substitute.

---

### Option G — Hybrid: pytest API-E2E + Flutter Widget Tests with WS Stubs

**What it is**: Combine Option A (pytest driving real backend via TestClient) with enhanced Flutter widget tests that replay pre-recorded WS message sequences via a `FakeWorkerConnection`. Each layer tested at its natural boundary.

This is the pragmatic middle ground: full server-side E2E fidelity from pytest; full client-side rendering fidelity from Flutter widget tests with deterministic server responses.

---

## 4. Comparison Matrix

| | **A: pytest+TestClient** | **B: pytest+Real TCP** | **C1: Flutter+Mock WS** | **C2: Flutter+Real Backend** | **F: Containerized Smoke** |
|-|:---:|:---:|:---:|:---:|:---:|
| Arch fit | ★★★★★ | ★★★★☆ | ★★★☆☆ | ★★★★★ | ★★★☆☆ |
| Setup cost | ★★★★★ | ★★★☆☆ | ★★★☆☆ | ★★☆☆☆ | ★★☆☆☆ |
| CI cost | ★★★★★ | ★★★★☆ | ★★☆☆☆ | ★☆☆☆☆ | ★★★☆☆ |
| Flakiness | ★★★★★ | ★★★★☆ | ★★★☆☆ | ★★☆☆☆ | ★★★★☆ |
| Coverage depth | ★★★★☆ | ★★★★★ | ★★★☆☆ | ★★★★★ | ★★☆☆☆ |
| Maintenance | ★★★★★ | ★★★★☆ | ★★★☆☆ | ★★☆☆☆ | ★★★☆☆ |
| Dev experience | ★★★★★ | ★★★★☆ | ★★★☆☆ | ★★☆☆☆ | ★★★☆☆ |

---

## 5. Recommendation

### Primary: Option A — pytest + Starlette TestClient WebSocket

**Rationale:**

1. **Zero new infrastructure.** Runs inside the existing `backend` CI job. No additional services, no Docker, no emulators.
2. **Builds on proven patterns.** `conftest.py`, `test_app`, `test_settings`, `mock_llm_stream` already 80% done. E2E tests look like slightly wider unit tests.
3. **Tests the right thing.** The Flutter client is a thin rendering layer over the WS protocol. If the server-side WS flow is correct, the client works. Protocol regression = test failure.
4. **Deterministic.** In-memory SQLite + mocked LLM = no external state, no timing dependencies.
5. **Can graduate to Option B.** If TLS bugs appear or real TCP issues surface, Option A tests can be duplicated as Option B with minimal refactoring.

### Fallback 1: Option B — pytest + Real TCP Server

Add when/if TLS-specific bugs surface or when testing authentication flows that require real socket semantics (origin validation, close codes, handshake timing). Small incremental step from Option A.

### Fallback 2: Option C1 — Flutter `integration_test` with Mock WS

Add when client-side UI regressions become costly. Requires adding DI seam for `WorkerConnection` factory. Scoped to desktop platforms (Linux/Windows/macOS) which don't need an Android emulator.

---

## 6. First Minimal E2E Slice

A minimal first E2E test should exercise the **happy path** of the most common user action: send a prompt, receive text output, session ends.

### What to cover

```
1. Connect to /ws/output/text, authenticate, subscribe_all
2. Connect to /ws/input/text, authenticate
3. Send {"type": "prompt", "text": "say hello"}
4. Receive {"type": "ack", "session_id": "<uuid>"}
5. Receive ≥1 {"type": "text_chunk"} on output channel
6. Receive {"type": "session_end"} on output channel
7. Assert session persisted: GET /api/sessions → contains session_id
8. Assert session status == "completed"
```

### Fixtures needed

```python
# tests/conftest.py additions

@pytest.fixture
def mock_llm_stream():
    """Injects deterministic LLM response: one text chunk + end_turn."""
    from src.core.llm import TextChunk, StreamDone
    chunks = [TextChunk(text="hello"), StreamDone(stop_reason="end_turn", usage=...)]
    with patch("src.core.prompt_router.LLMClient.stream_text", return_value=aiter(chunks)):
        yield

@pytest.fixture
def e2e_client(test_app):
    """Starlette TestClient for E2E flows."""
    from starlette.testclient import TestClient
    with TestClient(test_app, raise_server_exceptions=True) as client:
        yield client
```

### File location

```
tests/
└── test_e2e/
    ├── conftest.py          # e2e-specific fixtures
    ├── test_prompt_flow.py  # happy path: prompt → output → session_end
    ├── test_session_cancel.py
    └── test_auth.py         # wrong key, expired token, origin header
```

Mark with `@pytest.mark.e2e` and add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = ["e2e: full WebSocket flow tests (no external deps)"]
```

### Seed data / environment setup

- No seed data needed for first slice (sessions created by tests themselves)
- `test_settings` fixture provides all config
- `tool_registry` loads from `tests/tools/` (existing)
- LLM mock injected per-test via `mock_llm_stream` fixture

### Session tool execution E2E (phase 2)

Once the basic flow works, extend to cover tool execution:
```
prompt → LLM returns tool_use block → ShellExecutor runs → tool_output → LLM resumes → session_end
```
Use `tests/tools/` JSON definitions. Mock the actual subprocess (or allow safe read-only commands in test).

---

## 7. Blockers and Missing Infrastructure

| Blocker | Severity | Resolution |
|---------|---------|-----------|
| `TestClient.websocket_connect()` requires synchronous test functions or `anyio` event loop | Low | Use `starlette.testclient.TestClient` (sync) rather than async; or use `pytest-anyio` for async variant |
| No `mock_llm_stream` fixture that replays a complete turn (TextChunk + StreamDone + usage stats) | Medium | Add to `tests/conftest.py` — about 20 lines |
| LLM mock must be wired at the `PromptRouter` layer, not the `LLMClient` — the router calls `stream_text` directly | Medium | Patch `src.core.prompt_router.PromptRouter._call_llm` or the `LLMClient` method; inspect exact call site in `src/core/prompt_router.py` |
| `PromptRouter` expects `db_session_factory` for background flush tasks; `None` is passed in test fixtures | Low | Already handled in `test_app` fixture (`db_session_factory=None`); background tasks skip DB when factory is None |
| No `websockets` or `httpx[ws]` in dev deps | Low (Option A only) | Not needed — `starlette.testclient` handles WS without additional deps |
| Flutter E2E (Option C) requires `WorkerConnection` to be injectable | High (for Option C) | Add `WorkerConnectionFactory` interface; pass factory to `AppState` constructor — about 2 hours of refactor |
| CI emulator for Flutter integration tests | High (for Option C2) | Would require `macos-latest` runner + AVD setup (~15 min CI overhead) |
| No Dockerfile for smoke tests | Medium (for Option F) | Requires adding `Dockerfile`, `docker-compose.yml`, and smoke client script |

---

## 8. What NOT to Pursue

- **Playwright/Cypress/Selenium**: No web UI target. Flutter web not a production build target.
- **Appium**: Too much infra overhead; `integration_test` covers the same with less.
- **Visual regression testing**: Flutter's `golden_test` exists for widget snapshots but is OS-font-sensitive across CI platforms. Adds more maintenance than it saves at this stage.
- **Load/stress testing with E2E tooling**: Better handled by a dedicated `locust` or `k6` harness against a staging backend, not as part of the E2E suite.

---

## 9. Suggested CI Integration

```yaml
# Addition to .github/workflows/ci.yml backend job:

- name: Run E2E tests
  run: uv run pytest tests/test_e2e/ -v -m e2e
  env:
    RCFLOW_API_KEY: test-api-key
    DATABASE_URL: sqlite+aiosqlite:///:memory:
```

Run E2E in a separate step (not mixed with unit tests) so failures are clearly attributed. E2E tests can share the same coverage collection (`--cov`) or be excluded from coverage (they're measuring integration, not line coverage).

---

## 10. Summary

| Decision | Choice |
|---------|--------|
| Primary E2E approach | pytest + `starlette.testclient` WebSocket (Option A) |
| First test scope | Prompt → text_chunk → session_end happy path |
| LLM mocking | Keep mocked (same pattern as unit tests) |
| Database | In-memory SQLite (same as unit tests) |
| CI integration | Add `test_e2e/` step to existing backend job |
| Flutter E2E | Defer until `WorkerConnection` DI seam added |
| Smoke tests | Future work, after API-level E2E is established |
| Browser-based tools | Not applicable |

The repo's architecture — a well-typed async WebSocket API with clean dependency injection — is a natural fit for in-process API-level E2E tests. The gap between "unit tests that mock the WebSocket object" and "E2E tests that use a real TestClient WebSocket connection" is smaller here than in most projects. The primary investment is writing the `mock_llm_stream` fixture and the first `test_prompt_flow.py` — everything else already exists.
