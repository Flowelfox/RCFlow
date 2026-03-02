# Session State Tracking Analysis — RCFlow

## Executive Summary

RCFlow has a **coarse-grained session status** (`SessionStatus` enum) that tracks the lifecycle of a session (created → active → executing → completed/failed/cancelled), but it **lacks fine-grained activity state tracking** that would answer "what is the session doing right now?" at any given moment. The existing status is a lifecycle marker, not an activity indicator.

---

## 1. Current Architecture

### 1.1 Session Lifecycle (`src/core/session.py`)

The `SessionStatus` enum (line 23-30):

```
CREATED → ACTIVE → EXECUTING → COMPLETED
                 ↘ PAUSED ↗    → FAILED
                                → CANCELLED
```

- **CREATED**: Session just instantiated, no prompt processed yet.
- **ACTIVE**: Session has received a prompt; LLM processing or idle between turns.
- **EXECUTING**: A tool is currently being executed (shell/HTTP).
- **PAUSED**: User explicitly paused the session.
- **COMPLETED/FAILED/CANCELLED**: Terminal states.

**Key observation**: `ACTIVE` and `EXECUTING` are the only non-terminal, non-paused states, and they are **overloaded** — `ACTIVE` means both "waiting for user input" AND "LLM is streaming a response" AND "Claude Code subprocess is running."

### 1.2 Status Transitions

| Trigger | Method | From → To |
|---------|--------|-----------|
| First prompt received | `set_active()` (session.py:74) | CREATED → ACTIVE |
| Tool execution starts | `set_executing()` (session.py:82) | ACTIVE → EXECUTING |
| Tool execution ends | `set_active()` (prompt_router.py:591) | EXECUTING → ACTIVE |
| User pauses | `pause()` (session.py:118) | any non-terminal → PAUSED |
| User resumes | `resume()` (session.py:130) | PAUSED → ACTIVE |
| Session completes | `complete()` (session.py:90) | any → COMPLETED |
| Session fails | `fail()` (session.py:100) | any → FAILED |
| User cancels | `cancel()` (session.py:110) | any → CANCELLED |

### 1.3 Where Status Transitions Happen in prompt_router.py

1. **`handle_prompt()` (line 333-500)**: Sets `ACTIVE` at line 368 before LLM call. If an error occurs, sets `FAILED` at line 497.

2. **`_execute_tool()` (line 502-592)**: Sets `EXECUTING` at line 526 when a tool starts. Sets `ACTIVE` at line 591 when a non-claude_code tool finishes. For claude_code tools, control transfers to `_start_claude_code()`.

3. **`_start_claude_code()` (line 613-660)**: Attaches a `ClaudeCodeExecutor` to the session and spawns a background streaming task. Status stays `EXECUTING` until the background task finishes.

4. **`_end_claude_code_session()` (line 784-802)**: Sets `COMPLETED` and archives. This runs when Claude Code's streaming background task ends.

---

## 2. Gaps and Ambiguities

### 2.1 The `ACTIVE` Status is Overloaded

`ACTIVE` currently means ANY of:
- **Idle**: The LLM has responded, session is waiting for user input
- **LLM streaming**: The agentic loop is running in `handle_prompt()`
- **Claude Code running**: A background `_claude_code_stream_task` is reading events
- **Processing internally**: Between tool calls in the agentic loop

There is **no way to distinguish** these four states by looking at `session.status` alone.

### 2.2 `EXECUTING` is Underused

`set_executing()` (line 526) is called when a tool starts, but only for **non-claude_code** tools. For claude_code tools, `_start_claude_code()` (line 613) never calls `set_executing()` — the status remains whatever it was before (usually `ACTIVE`).

Furthermore, `set_executing()` returns to `ACTIVE` immediately after the tool returns (line 591). For shell commands that take a few seconds, this is fine. But the transition is invisible to clients because it happens so fast they might never see `EXECUTING`.

### 2.3 Claude Code Mode Has No Status Differentiation

When a Claude Code subprocess is active:
- The session has `claude_code_executor is not None` (an in-memory check)
- The session has `_claude_code_stream_task` (an asyncio.Task)
- But `session.status` is still `ACTIVE`

The only way to know "is Claude Code running" is to check `session.claude_code_executor is not None` AND `executor.is_running`. This is not exposed to clients.

### 2.4 "Waiting for User Input" Cannot Be Queried

There is **no field or method** that definitively answers "is this session waiting for user input right now?" The closest heuristic is:
- `status == ACTIVE` AND
- `claude_code_executor is None` AND
- `_prompt_lock` is **not** held (no concurrent `handle_prompt()` running)

But `_prompt_lock` is private, and there's no API to query it.

### 2.5 Race Window During LLM Calls

In `handle_prompt()` (line 367-500), the flow is:
1. Acquire `_prompt_lock` (line 367)
2. Set `ACTIVE` (line 368)
3. Run agentic loop (line 418-452)
4. Release lock

Between steps 2 and 3, another prompt arriving would block on the lock. But from the client's perspective, the status is `ACTIVE` — identical to when the session is idle. The client cannot tell if the session is processing or waiting.

### 2.6 Background Tasks Are Invisible

Several operations happen in fire-and-forget background tasks that are invisible to session state:
- `_fire_title_task()` (line 804) — title generation
- `_fire_summary_task()` (line 819) — TTS summary generation
- `_fire_log_task()` (line 954) — LLM call logging
- `_fire_archive_task()` (line 1014) — session archiving

These don't affect session status but the session may emit buffer messages (SUMMARY, SESSION_END_ASK) after the status has already transitioned.

### 2.7 Client-Side Status Is String-Based

The Flutter client (`pane_state.dart`) uses string literals (`'active'`, `'executing'`, `'paused'`, etc.) with no enum validation. It relies on:
- `_sessionEnded` boolean flag
- `_sessionPaused` boolean flag
- `canSendMessage` computed property

But `canSendMessage` returns `true` whenever the session is `ACTIVE` — even if the LLM is currently streaming. The client prevents double-sends via `pendingAck` but that only gates on ACK receipt, not on processing completion.

---

## 3. Specific Scenario Analysis

### Scenario A: User sends prompt → LLM responds with text only

```
Status timeline:
  ACTIVE ──────[handle_prompt runs]──────── ACTIVE
                ↑ set_active()               (unchanged)

Client sees: status=active the entire time
Can distinguish from idle? NO
```

### Scenario B: User sends prompt → LLM calls shell tool → LLM responds

```
Status timeline:
  ACTIVE ──[LLM]── EXECUTING ──[shell]── ACTIVE ──[LLM]── ACTIVE
           ↑                              ↑                 (unchanged)
           set_active()                   set_active()

Client sees: active → executing → active (briefly)
```

### Scenario C: User sends prompt → LLM calls claude_code

```
Status timeline:
  ACTIVE ──[LLM]── ACTIVE (claude_code_executor set, background task running)
                    ↑ never set_executing() for claude_code!

Client sees: active the entire time
Claude Code subprocess: running in background, can take minutes
Can distinguish from idle? Only via `session_update` message content (agent_group_start/end)
```

### Scenario D: Claude Code finishes → user sends follow-up

```
Status timeline:
  ACTIVE (cc running) → ACTIVE (cc idle, awaiting input) → ACTIVE (cc running again)

Client sees: active the entire time
Distinction between "cc processing" and "cc idle"? NO
```

---

## 4. Proposed Approaches

### Approach A: Add a Fine-Grained `ActivityState` Field

Add a new field `activity_state` to `ActiveSession` alongside the existing `status`:

```python
class ActivityState(StrEnum):
    IDLE = "idle"                           # Waiting for user input
    PROCESSING_LLM = "processing_llm"       # LLM is generating a response
    EXECUTING_TOOL = "executing_tool"        # A tool (shell/http) is running
    RUNNING_SUBPROCESS = "running_subprocess" # Claude Code subprocess active
    FINISHING = "finishing"                   # Post-processing (summary/title generation)
```

**Transitions:**
- `IDLE` → `PROCESSING_LLM`: When `handle_prompt()` starts the agentic loop
- `PROCESSING_LLM` → `EXECUTING_TOOL`: When `_execute_tool()` runs a non-claude_code tool
- `EXECUTING_TOOL` → `PROCESSING_LLM`: When tool execution returns to the agentic loop
- `PROCESSING_LLM` → `RUNNING_SUBPROCESS`: When `_start_claude_code()` spawns the subprocess
- `RUNNING_SUBPROCESS` → `IDLE`: When Claude Code emits a `result` event (waiting for follow-up)
- `RUNNING_SUBPROCESS` → `PROCESSING_LLM` (implicit): When user sends follow-up to Claude Code
- `PROCESSING_LLM` → `IDLE`: When the agentic loop completes without starting claude_code
- Any → `FINISHING`: When summary/title generation runs (optional — might be too noisy)

**Broadcast**: Include `activity_state` in `session_update` WebSocket messages alongside `status`.

**Pros:**
- Precise, unambiguous state at any moment
- Backward-compatible (existing `status` field unchanged)
- Clients can query via session list or subscribe to updates
- Natural place for future states (e.g., "waiting_for_approval")

**Cons:**
- More transitions to manage = more places to get out of sync
- Need to handle edge cases (e.g., multiple concurrent tool calls in agentic loop)

### Approach B: Boolean Flags on ActiveSession

Instead of a single enum, add computed/tracked boolean properties:

```python
@property
def is_waiting_for_input(self) -> bool:
    """True when the session is idle and ready for user input."""

@property
def is_llm_streaming(self) -> bool:
    """True when the LLM is actively generating a response."""

@property
def is_subprocess_running(self) -> bool:
    """True when a Claude Code subprocess is actively executing."""
```

**Implementation**: Track via internal flags set at the appropriate points in prompt_router.py, exposed as read-only properties.

**Pros:**
- Simpler to reason about individually
- Can be composed (e.g., `is_busy = is_llm_streaming or is_subprocess_running`)

**Cons:**
- Multiple independent booleans can become inconsistent
- Harder to broadcast as a single state change
- Doesn't prevent invalid combinations without validation

### Approach C: Replace `SessionStatus` Entirely

Merge lifecycle and activity into one richer state machine:

```
CREATED → WAITING_FOR_INPUT → PROCESSING_LLM → EXECUTING_TOOL → PROCESSING_LLM → ...
                             → RUNNING_SUBPROCESS → SUBPROCESS_IDLE → RUNNING_SUBPROCESS → ...
                             → PAUSED
                             → COMPLETED / FAILED / CANCELLED
```

**Pros:**
- Single source of truth
- No ambiguity possible

**Cons:**
- Breaking change for clients
- State machine becomes complex (many transitions)
- Lifecycle and activity are conceptually different concerns — coupling them makes each harder to extend

---

## 5. Recommendation

**Approach A (ActivityState as a separate field)** is the strongest option because:

1. **Separation of concerns**: `status` tracks lifecycle (is the session alive?), `activity_state` tracks activity (what is it doing right now?). These are orthogonal.

2. **Backward compatibility**: Existing client code that checks `status` continues to work. `activity_state` is additive.

3. **Queryability**: Both fields are included in `session_update` broadcasts and session list responses. Any client can answer "what is this session doing?" by checking `activity_state`.

4. **Correctness**: A single enum prevents invalid combinations (you can't be simultaneously `PROCESSING_LLM` and `RUNNING_SUBPROCESS`).

5. **Implementation scope**: Changes are localized to:
   - `src/core/session.py` — add the enum and field + setter with broadcast
   - `src/core/prompt_router.py` — set activity state at ~8-10 transition points
   - `src/core/session.py:broadcast_session_update()` — include activity_state in payload
   - Flutter client — add `activityState` to `SessionInfo` model + UI mapping

### Transition Points (for Approach A)

| Location | File:Line | Set To |
|----------|-----------|--------|
| `handle_prompt()` enters lock, before LLM | prompt_router.py:368 | `PROCESSING_LLM` |
| `_execute_tool()` non-claude_code starts | prompt_router.py:526 | `EXECUTING_TOOL` |
| `_execute_tool()` non-claude_code ends | prompt_router.py:591 | `PROCESSING_LLM` |
| `_start_claude_code()` subprocess spawned | prompt_router.py:657 | `RUNNING_SUBPROCESS` |
| `_relay_claude_code_stream()` receives `result` event | prompt_router.py:728-738 | `IDLE` |
| `_forward_to_claude_code()` sends follow-up | prompt_router.py:877 | `RUNNING_SUBPROCESS` |
| `handle_prompt()` agentic loop completes | prompt_router.py:454 | `IDLE` |
| `handle_prompt()` error | prompt_router.py:487 | `IDLE` |
| Session paused | session.py:125 | `IDLE` |
| Session resumed (completed while paused) | prompt_router.py:246 | `IDLE` |

### For "Waiting for User Input"

With this approach, the answer is definitive:
```python
session.activity_state == ActivityState.IDLE
```

No heuristics, no lock-checking, no NULL-checking executors.
