# OpenAI Codex CLI Integration — Implementation Plan

## Executive Summary

Add OpenAI Codex CLI support to RCFlow as a new executor type, analogous to the existing Claude Code integration. Codex CLI is an open-source Rust-based agent (npm: `@openai/codex`) that uses OpenAI models. Its CLI interface differs fundamentally from Claude Code: it uses a **one-shot process model** (spawn per turn, resume via CLI args) rather than a persistent bidirectional stream-json process.

---

## Key Architectural Differences: Claude Code vs Codex CLI

| Aspect | Claude Code | Codex CLI |
|--------|-------------|-----------|
| **Process model** | Single persistent process, multi-turn via stdin JSON | New process per turn, resume via `codex exec resume SESSION_ID` |
| **Input** | JSON messages on stdin (`{"type":"user",...}`) | Prompt as positional arg or piped to stdin, then stdin closes |
| **Output** | JSONL on stdout (stream-json) | JSONL on stdout (with `--json` flag) |
| **Session management** | Managed internally in persistent process via `--session-id` | File-based in `~/.codex/sessions/`, resumed via CLI args |
| **Approval handling** | Can send approval responses via stdin JSON | Must be pre-configured: `--full-auto` or `--ask-for-approval never` |
| **Binary** | `claude` | `codex` |
| **Auth env var** | `ANTHROPIC_API_KEY` | `CODEX_API_KEY` (or `OPENAI_API_KEY`) |
| **Result event** | `{"type": "result", ...}` | `{"type": "turn.completed", "usage": {...}}` |
| **Session ID source** | Passed via `--session-id` flag | Extracted from first event `{"type": "thread.started", "thread_id": "..."}` |

### JSONL Event Format (Codex CLI with `--json`)

```json
{"type":"thread.started","thread_id":"0199a213-..."}
{"type":"turn.started"}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"ls","status":"in_progress"}}
{"type":"item.updated","item":{"id":"item_1","type":"command_execution","command":"ls","aggregated_output":"...","status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"ls","aggregated_output":"...","exit_code":0,"status":"completed"}}
{"type":"item.started","item":{"id":"item_2","type":"agent_message","text":""}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"Here are the files..."}}
{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}
```

---

## Files to Create

### 1. `src/executors/codex.py` — CodexExecutor class

The core executor. Unlike `ClaudeCodeExecutor` which manages a persistent process, this spawns a new process per turn and manages thread IDs for session continuity.

```
class CodexExecutor(BaseExecutor):
    - __init__(binary_path, thread_id, extra_env)
    - _build_command(parameters, config, resume=False) -> list[str]
    - _build_env() -> dict[str, str]
    - _start_process(tool, parameters, resume=False) -> Process
    - _drain_stderr() -> None
    - _read_events() -> AsyncGenerator[ExecutionChunk, None]
    - execute_streaming(tool, parameters) -> AsyncGenerator[ExecutionChunk, None]
    - restart_with_prompt(prompt) -> AsyncGenerator[ExecutionChunk, None]
    - read_more_events() -> AsyncGenerator[ExecutionChunk, None]  # Not applicable — raises
    - execute(tool, parameters) -> ExecutionResult
    - send_input(data) -> None  # Not applicable — raises
    - cancel() -> None
    - stop_process() -> None
    - is_running -> bool
    - thread_id -> str | None
```

**Key design decisions:**

- **Process lifecycle**: Each `execute_streaming()` or `restart_with_prompt()` call spawns `codex exec --json --full-auto [--cd DIR] [resume THREAD_ID] PROMPT`, reads JSONL until `turn.completed` or process exit, then the process naturally terminates.
- **Thread ID tracking**: The first event `{"type":"thread.started","thread_id":"..."}` provides the Codex session ID. Store this for subsequent `resume` calls.
- **No persistent process**: Unlike Claude Code, `send_input()` and `read_more_events()` raise `RuntimeError("Codex CLI does not support interactive input")` because Codex uses one-shot process spawns.
- **Follow-ups**: `restart_with_prompt(prompt)` spawns `codex exec --json --full-auto resume THREAD_ID PROMPT`.
- **Prompt delivery**: Write the prompt to stdin, then close stdin (matching the SDK pattern). Alternatively, pass as positional argument.

**Command construction:**

```python
def _build_command(self, parameters, config, *, resume=False):
    cmd = [self._binary_path, "exec", "--json", "--skip-git-repo-check"]

    # Sandbox / approval mode
    approval_mode = config.get("approval_mode", "full-auto")
    if approval_mode == "full-auto":
        cmd.append("--full-auto")
    elif approval_mode == "yolo":
        cmd.append("--dangerously-bypass-approvals-and-sandbox")

    # Model override
    model = parameters.get("model") or config.get("model")
    if model:
        cmd.extend(["--model", model])

    # Working directory
    working_dir = parameters.get("working_directory")
    if working_dir:
        cmd.extend(["--cd", working_dir])

    # Resume existing thread
    if resume and self._thread_id:
        cmd.extend(["resume", self._thread_id])

    # Prompt goes as positional arg (or via stdin)
    prompt = parameters.get("prompt", "")
    if prompt:
        cmd.append(prompt)

    return cmd
```

**Event parsing** (in `_read_events`):

```python
async def _read_events(self):
    while True:
        line = await self._process.stdout.readline()
        if not line:
            self._done = True
            break

        decoded = line.decode("utf-8").rstrip("\n")
        if not decoded:
            continue

        try:
            event = json.loads(decoded)
        except json.JSONDecodeError:
            yield ExecutionChunk(stream="stdout", content=decoded + "\n")
            continue

        yield ExecutionChunk(stream="stdout", content=decoded)

        event_type = event.get("type")

        # Capture thread ID from first event
        if event_type == "thread.started":
            self._thread_id = event.get("thread_id")

        # Turn completion marks end of this invocation
        if event_type in ("turn.completed", "turn.failed"):
            self._result_text = json.dumps(event)
            self._done = True
            break
```

### 2. `tools/codex.json` — Tool definition

```json
{
  "name": "codex",
  "description": "Start an OpenAI Codex coding agent session. Codex can read, write, and execute code autonomously using OpenAI models. Use for complex tasks: implementing features, fixing bugs, refactoring, writing tests, etc. The working_directory must be an existing project directory. User projects live under the projects directory mentioned in the system prompt, so when the user mentions a project by name, use the absolute path from the system prompt as the working_directory. Always verify the directory exists before calling this tool. If the exact name does not match, list the projects directory to find a case-insensitive or partial match.",
  "version": "1.0.0",
  "session_type": "long-running",
  "llm_context": "session-scoped",
  "executor": "codex",

  "parameters": {
    "type": "object",
    "properties": {
      "prompt": {
        "type": "string",
        "description": "Task instructions for Codex"
      },
      "working_directory": {
        "type": "string",
        "description": "Project directory to work in"
      },
      "model": {
        "type": "string",
        "description": "Model override (e.g. 'gpt-5-codex', 'o3')"
      }
    },
    "required": ["prompt", "working_directory"]
  },

  "executor_config": {
    "codex": {
      "binary_path": "codex",
      "approval_mode": "full-auto",
      "model": "",
      "timeout": 600
    }
  }
}
```

### 3. `tests/test_executors/test_codex.py` — Unit tests

Mirror the structure of `tests/test_executors/test_claude_code.py`:

- `TestBuildCommand` — verify command construction (basic, with model, with resume, with working dir)
- `TestBuildEnv` — verify environment variable handling (CODEX_API_KEY injection, cleanup)
- `TestExecuteStreaming` — verify JSONL event parsing, thread ID extraction, turn completion detection
- `TestRestartWithPrompt` — verify resume command construction, thread ID reuse
- `TestSendInput` — verify it raises RuntimeError (not supported)
- `TestCancel` — verify process cleanup
- `TestExecute` — verify non-streaming collection
- `TestIsRunning` / `TestThreadId` — property tests

---

## Files to Modify

### 4. `src/tools/loader.py`

**Changes:**

a. Add `"codex"` to `VALID_EXECUTORS`:
```python
VALID_EXECUTORS = {"shell", "http", "claude_code", "codex"}
```

b. Add `CodexExecutorConfig` Pydantic model:
```python
class CodexExecutorConfig(BaseModel):
    binary_path: str = "codex"
    approval_mode: str = "full-auto"  # "full-auto" | "yolo"
    model: str = ""
    timeout: int = 600
```

c. Add `get_codex_config()` method to `ToolDefinition`:
```python
def get_codex_config(self) -> CodexExecutorConfig:
    return CodexExecutorConfig(**self.executor_config["codex"])
```

### 5. `src/config.py`

**Changes:**

Add Codex CLI configuration settings:

```python
# Codex CLI (OpenAI Codex)
CODEX_BINARY: str = "codex"
CODEX_API_KEY: str = ""
```

### 6. `src/core/session.py`

**Changes:**

a. Add `codex_executor` field to `ActiveSession` alongside `claude_code_executor`:
```python
from src.executors.codex import CodexExecutor  # TYPE_CHECKING

self.codex_executor: CodexExecutor | None = None
self._codex_stream_task: asyncio.Task[None] | None = None
```

**Alternative approach** (recommended): Generalize the executor field:
```python
# Instead of separate fields for each agent type:
self.agent_executor: BaseExecutor | None = None
self.agent_executor_type: str | None = None  # "claude_code" | "codex"
self._agent_stream_task: asyncio.Task[None] | None = None
```

This avoids adding a new field for every future agent CLI. However, it's a larger refactor. **For the initial implementation, add explicit `codex_executor` fields to match the existing pattern, then refactor to a generic field in a follow-up.**

### 7. `src/core/prompt_router.py`

**Changes (the bulk of the work):**

a. **Import CodexExecutor**:
```python
from src.executors.codex import CodexExecutor
```

b. **`_get_executor()`** — add codex case:
```python
if executor_type == "codex":
    binary_path = "codex"
    if tool_def is not None:
        config = tool_def.get_codex_config()
        binary_path = config.binary_path
    if self._settings and self._settings.CODEX_BINARY:
        binary_path = self._settings.CODEX_BINARY
    return CodexExecutor(
        binary_path=binary_path,
        extra_env=self._build_codex_extra_env(),
    )
```

c. **`_build_codex_extra_env()`** — new method:
```python
def _build_codex_extra_env(self) -> dict[str, str]:
    extra_env: dict[str, str] = {}
    if self._settings and self._settings.CODEX_API_KEY:
        extra_env["CODEX_API_KEY"] = self._settings.CODEX_API_KEY
    return extra_env
```

d. **`_execute_tool()`** — handle codex executor type:
```python
if tool_def.executor == "codex":
    return await self._start_codex(session, tool_def, tool_call)
```

And push `AGENT_GROUP_START` for codex tools (same as claude_code):
```python
if tool_def is not None and tool_def.executor in ("claude_code", "codex"):
    session.buffer.push_text(MessageType.AGENT_GROUP_START, {...})
```

e. **`_start_codex()`** — new method (mirrors `_start_claude_code`):
```python
async def _start_codex(self, session, tool_def, tool_call) -> str:
    # Validate working directory (same logic as _start_claude_code)
    # Create CodexExecutor
    # Set session.codex_executor = executor
    # Set session.session_type = SessionType.LONG_RUNNING
    # Store metadata (codex_thread_id, codex_working_directory, codex_tool_name, codex_parameters)
    # Start background streaming task
    # Return "Codex session started in {working_path}"
```

f. **`_stream_codex_events()`** — background task to stream events:
```python
async def _stream_codex_events(self, session, executor, tool_def, tool_call):
    try:
        await self._relay_codex_stream(session, executor.execute_streaming(tool_def, tool_call.tool_input))
    except Exception:
        # Error handling (push AGENT_GROUP_END, ERROR, end session)
        ...
    # After streaming completes, push AGENT_GROUP_END
    # Process has naturally exited (one-shot model)
```

g. **`_relay_codex_stream()`** — parse Codex JSONL events into buffer messages:
```python
async def _relay_codex_stream(self, session, stream):
    async for chunk in stream:
        line = chunk.content.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            session.buffer.push_text(MessageType.TEXT_CHUNK, {...})
            continue

        event_type = event.get("type")

        if event_type == "item.started":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "command_execution":
                session.buffer.push_text(MessageType.TOOL_START, {
                    "session_id": session.id,
                    "tool_name": "command_execution",
                    "tool_input": {"command": item.get("command", "")},
                })
            elif item_type == "file_change":
                session.buffer.push_text(MessageType.TOOL_START, {
                    "session_id": session.id,
                    "tool_name": "file_change",
                    "tool_input": {"changes": item.get("changes", [])},
                })

        elif event_type == "item.completed":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "agent_message":
                session.buffer.push_text(MessageType.TEXT_CHUNK, {
                    "session_id": session.id,
                    "content": item.get("text", ""),
                    "finished": False,
                })
            elif item_type == "command_execution":
                session.buffer.push_text(MessageType.TOOL_OUTPUT, {
                    "session_id": session.id,
                    "tool_name": "command_execution",
                    "content": item.get("aggregated_output", ""),
                    "stream": "stdout",
                })

        elif event_type == "item.updated":
            # Optionally relay incremental updates for real-time streaming
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "agent_message":
                session.buffer.push_text(MessageType.TEXT_CHUNK, {
                    "session_id": session.id,
                    "content": item.get("text", ""),
                    "finished": False,
                })

        elif event_type == "turn.completed":
            session.set_activity(ActivityState.IDLE)
            # Extract final agent message for summary
            # Fire summary task if applicable
            self._fire_summary_task(session, "Codex task completed", push_session_end_ask=True)

        elif event_type == "turn.failed":
            error = event.get("error", {})
            session.buffer.push_text(MessageType.ERROR, {
                "session_id": session.id,
                "content": error.get("message", "Codex turn failed"),
                "code": "CODEX_TURN_FAILED",
            })

        elif event_type == "error":
            session.buffer.push_text(MessageType.ERROR, {
                "session_id": session.id,
                "content": event.get("message", "Codex error"),
                "code": "CODEX_ERROR",
            })
```

h. **`_forward_to_codex()`** — handle follow-up messages:
```python
async def _forward_to_codex(self, session, text):
    executor = session.codex_executor
    if executor is None:
        return

    session.set_activity(ActivityState.RUNNING_SUBPROCESS)
    session.buffer.push_text(MessageType.AGENT_GROUP_START, {
        "session_id": session.id,
        "tool_name": "codex",
    })

    # Codex always uses restart (new process with resume)
    session._codex_stream_task = asyncio.create_task(
        self._restart_codex_with_prompt(session, executor, text)
    )
```

i. **`_restart_codex_with_prompt()`** — spawn resume process:
```python
async def _restart_codex_with_prompt(self, session, executor, prompt):
    try:
        await self._relay_codex_stream(session, executor.restart_with_prompt(prompt))
    except Exception:
        # Error handling
        ...
    session.buffer.push_text(MessageType.AGENT_GROUP_END, {"session_id": session.id})
```

j. **`_end_codex_session()`** — cleanup:
```python
async def _end_codex_session(self, session):
    if session.codex_executor is not None:
        await session.codex_executor.stop_process()
    session.codex_executor = None
    session._codex_stream_task = None
    # Same completion logic as _end_claude_code_session
```

k. **`handle_prompt()`** — add codex executor check alongside claude_code:
```python
# After the existing claude_code check:
if session.codex_executor is not None:
    session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": text, "role": "user"})
    await self._forward_to_codex(session, text)
    return session.id
```

l. **`cancel_session()`, `end_session()`, `pause_session()`** — handle codex cleanup:
Add the same pattern used for claude_code:
```python
if session.codex_executor is not None:
    await session.codex_executor.cancel()
    session.codex_executor = None

if session._codex_stream_task is not None and not session._codex_stream_task.done():
    session._codex_stream_task.cancel()
    ...
session._codex_stream_task = None
```

m. **`restore_session()`** — handle codex session restore:
```python
codex_thread_id = session.metadata.get("codex_thread_id")
codex_tool_name = session.metadata.get("codex_tool_name")
if codex_thread_id and codex_tool_name:
    tool_def = self._tool_registry.get(codex_tool_name)
    if tool_def is not None and tool_def.executor == "codex":
        binary_path = "codex"
        config = tool_def.get_codex_config()
        binary_path = config.binary_path
        if self._settings and self._settings.CODEX_BINARY:
            binary_path = self._settings.CODEX_BINARY

        executor = CodexExecutor(
            binary_path=binary_path,
            thread_id=codex_thread_id,
            extra_env=self._build_codex_extra_env(),
        )
        executor._tool_def = tool_def
        executor._last_parameters = session.metadata.get("codex_parameters", {})

        session.codex_executor = executor
        session.session_type = SessionType.LONG_RUNNING
```

### 8. `.env.example`

Add:
```env
# Codex CLI (OpenAI Codex)
CODEX_BINARY=codex
CODEX_API_KEY=your-openai-key-here
```

### 9. `Design.md`

Update the following sections:

- **Technology Stack**: Add `Codex CLI | OpenAI Codex (codex exec)` as an agent tool
- **Tool Definitions**: Document the `codex` executor type and its config schema
- **Executors**: Add `CodexExecutor` description with its one-shot process model
- **Session Types**: Document that codex sessions are `long-running` like claude_code
- **Configuration**: Document `CODEX_BINARY` and `CODEX_API_KEY` environment variables

---

## Implementation Order

### Phase 1: Core Executor (can be developed and tested independently)
1. Create `src/executors/codex.py` — the `CodexExecutor` class
2. Create `tests/test_executors/test_codex.py` — unit tests with mocked subprocess
3. Verify all tests pass

### Phase 2: Tool Definition and Loader
4. Add `CodexExecutorConfig` to `src/tools/loader.py`
5. Add `"codex"` to `VALID_EXECUTORS`
6. Add `get_codex_config()` to `ToolDefinition`
7. Create `tools/codex.json`
8. Verify tool loads correctly

### Phase 3: Configuration
9. Add `CODEX_BINARY` and `CODEX_API_KEY` to `src/config.py`
10. Update `.env.example`

### Phase 4: Session Integration
11. Add `codex_executor` and `_codex_stream_task` fields to `ActiveSession` in `src/core/session.py`

### Phase 5: Prompt Router Integration (largest change)
12. Add `_build_codex_extra_env()` to `PromptRouter`
13. Add codex case to `_get_executor()`
14. Add `_start_codex()`, `_stream_codex_events()`, `_relay_codex_stream()`
15. Add `_forward_to_codex()`, `_restart_codex_with_prompt()`
16. Add `_end_codex_session()`
17. Update `handle_prompt()` to check for codex executor
18. Update `cancel_session()`, `end_session()`, `pause_session()`, `restore_session()` for codex cleanup

### Phase 6: Documentation
19. Update `Design.md`

### Phase 7: Integration Testing
20. Manual end-to-end test with actual Codex CLI installed

---

## Error Handling Considerations

1. **Codex not installed**: If `codex` binary is not found, the subprocess will fail. Handle `FileNotFoundError` from `create_subprocess_exec` and push a clear error message to the session buffer.

2. **Missing API key**: If `CODEX_API_KEY` is not set, Codex CLI will fail at startup. The stderr drain will capture the error message.

3. **Git repo requirement**: Codex CLI requires a git repo by default. Use `--skip-git-repo-check` to bypass this.

4. **Turn failures**: Handle `turn.failed` events with error messages.

5. **Process timeout**: Codex doesn't have a built-in timeout flag like Claude Code's `CLAUDE_CODE_TIMEOUT`. Implement a timeout wrapper using `asyncio.wait_for()` on the read loop with the configured timeout value.

6. **Thread ID not received**: If the first event isn't `thread.started`, the thread ID will be None. Follow-up `resume` calls should fail gracefully with a clear error.

---

## Notes on Differences from Claude Code Integration

### No Bidirectional stdin Protocol
The most significant difference. Claude Code's executor keeps a persistent process alive and sends follow-up messages via stdin JSON. Codex CLI's executor must:
- Spawn a new process for each turn
- Use `codex exec resume THREAD_ID` for follow-ups
- Close stdin after writing the prompt (or pass prompt as positional arg)

### No `send_input()` / `read_more_events()`
These methods exist on `BaseExecutor` as abstract methods. The Codex executor should implement them as:
```python
async def send_input(self, data: str) -> None:
    raise RuntimeError("Codex CLI does not support interactive input; use restart_with_prompt()")

async def read_more_events(self) -> AsyncGenerator[ExecutionChunk, None]:
    raise RuntimeError("Codex CLI does not support reading more events from a completed turn")
```

### Different Event Schema
Claude Code emits events like `{"type": "assistant", "message": {"content": [...]}}` and `{"type": "result", ...}`.
Codex emits `{"type": "item.started/updated/completed", "item": {...}}` and `{"type": "turn.completed/failed", ...}`.
The relay function (`_relay_codex_stream`) must translate Codex events to RCFlow's buffer message types.

### Session ID vs Thread ID
Claude Code uses "session ID" (passed as `--session-id`), Codex uses "thread ID" (extracted from the first stdout event). The Codex executor stores this as `_thread_id` rather than `_session_id`.

### Process Stops After Each Turn
After `turn.completed`, the Codex process exits naturally. No need to call `stop_process()` to kill it — just wait for exit. The `stop_process()` method should still clean up if the process hasn't exited yet (e.g., on cancel/timeout).

---

## Testing Strategy

### Unit Tests (Phase 1-2)
- Mock `asyncio.create_subprocess_exec` to return processes with predefined JSONL output
- Test command construction for all flag combinations
- Test event parsing for all Codex event types
- Test thread ID extraction from `thread.started` event
- Test resume command construction
- Test error handling (turn.failed, process crash, missing thread ID)

### Integration Tests (Phase 7)
- Requires `codex` binary installed and `CODEX_API_KEY` set
- Start a simple task, verify events stream correctly
- Send a follow-up, verify resume works
- Cancel mid-task, verify cleanup
- Test with invalid working directory
