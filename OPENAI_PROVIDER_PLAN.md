# OpenAI API Key Support — Implementation Plan

## Executive Summary

Add OpenAI as a third LLM provider to RCFlow alongside the existing Anthropic (direct API) and AWS Bedrock providers. This involves:

1. Adding the `openai` Python SDK as a dependency
2. Extending `LLMClient` to create an OpenAI async client and translate between Anthropic-style streaming events and OpenAI's streaming format
3. Adding OpenAI configuration fields (`OPENAI_API_KEY`, `OPENAI_MODEL`) to settings, config schema, and `.env`
4. Updating the tool format conversion to support OpenAI's tool/function calling schema
5. No database migrations required — LLM provider config lives in `.env` / Settings, not the database

The primary challenge is that the current codebase is deeply coupled to the Anthropic SDK's streaming event format and tool-call protocol. OpenAI uses a different streaming format (SSE with `delta` chunks), different tool call structure (function calling), and different message roles. The `LLMClient` class must abstract these differences behind the existing `LLMStreamEvent` interface (`TextChunk`, `ToolCallRequest`, `StreamDone`).

---

## Current Architecture Analysis

### Provider Selection (`src/core/llm.py`)

`LLMClient.__init__()` (line 72) selects the provider based on `settings.LLM_PROVIDER`:

- **`"anthropic"`** → `anthropic.AsyncAnthropic(api_key=...)` (line 84)
- **`"bedrock"`** → `anthropic.AsyncAnthropicBedrock(aws_region=..., ...)` (line 79)

Both clients expose an identical interface (`client.messages.stream(**kwargs)`) because they come from the same `anthropic` SDK. This means the streaming loop (lines 98–178) works identically for both — no branching needed.

**Key insight**: Adding OpenAI breaks this symmetry. The `openai` SDK has a completely different streaming interface (`client.chat.completions.create(stream=True)`), different message format, and different tool call structure. The streaming loop **must** be branched or abstracted.

### Streaming Event Flow

The current `stream_turn()` method (line 98) yields these event types:

```
TextChunk(content: str)           — streamed text fragment
ToolCallRequest(tool_use_id, tool_name, tool_input)  — complete tool call
StreamDone(stop_reason, usage)    — turn complete with usage stats
```

The agentic loop in `run_agentic_loop()` (line 180) consumes these events generically — it doesn't care which provider produced them. This is the abstraction boundary we need to maintain.

### Tool Format

Tools are currently formatted as Anthropic tool schemas via `ToolRegistry.to_anthropic_tools()` (`src/tools/registry.py:27`):

```python
{"name": ..., "description": ..., "input_schema": {...}}
```

OpenAI uses a different format:

```python
{"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
```

### Configuration System (`src/config.py`)

- `Settings` class (line 17): Pydantic `BaseSettings` with `.env` file support
- `CONFIG_OPTIONS` list (line 95): UI schema for the Flutter config screen, with `visible_when` conditional logic
- `CONFIGURABLE_KEYS` set (line 297): Auto-derived from `CONFIG_OPTIONS`
- `get_config_schema()` (line 271): Builds the config response with masked secrets
- `update_env_file()` (line 300): Persists changes to `.env`

### Hot-Reload (`src/api/http.py:1092`)

`_reload_components()` destroys the old `LLMClient`, creates a new one from updated settings, and patches it into the `PromptRouter`. This will work for OpenAI without modification as long as `LLMClient.__init__()` handles the new provider.

### Message Format in Conversation History

The agentic loop (`src/core/llm.py:209–246`) builds conversation history using Anthropic's message format:

```python
# Assistant message
{"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}

# Tool results
{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ..., "content": ...}]}
```

OpenAI uses a different format:

```python
# Assistant message
{"role": "assistant", "content": "text", "tool_calls": [{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}]}

# Tool results
{"role": "tool", "tool_call_id": ..., "content": ...}
```

This means the agentic loop's message construction must also be provider-aware.

---

## Detailed Implementation Steps

### Step 1: Add `openai` Dependency

**File:** `pyproject.toml`

Add `openai` to the dependencies list (line 7–21):

```python
dependencies = [
    ...
    "anthropic[bedrock]>=0.42.0",
    "openai>=1.60.0",          # <-- ADD
    ...
]
```

Then run `uv sync` to install.

### Step 2: Add OpenAI Settings Fields

**File:** `src/config.py`

**2a.** Add settings fields to `Settings` class (after line 47, the Bedrock section):

```python
# OpenAI (used when LLM_PROVIDER = "openai")
OPENAI_API_KEY: str = ""
OPENAI_MODEL: str = "gpt-4o"
```

**2b.** Add `"openai"` option to the `LLM_PROVIDER` select in `CONFIG_OPTIONS` (line 101):

```python
"options": [
    {"value": "anthropic", "label": "Anthropic Key"},
    {"value": "bedrock", "label": "Bedrock"},
    {"value": "openai", "label": "OpenAI"},           # <-- ADD
],
```

**2c.** Add OpenAI-specific config options after the Bedrock options (after line 158):

```python
{
    "key": "OPENAI_API_KEY",
    "label": "OpenAI API Key",
    "type": "secret",
    "group": "LLM",
    "description": "API key for OpenAI API access",
    "required": False,
    "restart_required": True,
    "visible_when": {"key": "LLM_PROVIDER", "value": "openai"},
},
{
    "key": "OPENAI_MODEL",
    "label": "OpenAI Model",
    "type": "string",
    "group": "LLM",
    "description": "OpenAI model ID (e.g. gpt-4o, gpt-4.1, o3)",
    "required": False,
    "restart_required": True,
    "visible_when": {"key": "LLM_PROVIDER", "value": "openai"},
},
```

### Step 3: Update `.env.example`

**File:** `.env.example`

Add after the Bedrock section (after line 28):

```env
# OpenAI (used when LLM_PROVIDER=openai)
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o
```

### Step 4: Add OpenAI Tool Format Conversion

**File:** `src/tools/registry.py`

Add a new method to `ToolRegistry` (after `to_anthropic_tools()`, line 36):

```python
def to_openai_tools(self) -> list[dict[str, Any]]:
    """Convert all registered tools to OpenAI function calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in self._tools.values()
    ]
```

### Step 5: Refactor `LLMClient` for Multi-Provider Support

**File:** `src/core/llm.py`

This is the largest change. The refactoring strategy is to keep `LLMClient` as the unified interface but branch internally based on the provider. We avoid a full abstract base class hierarchy to keep changes minimal and focused.

**5a.** Add OpenAI import (after line 9):

```python
import openai
```

**5b.** Extend `__init__()` to handle OpenAI (modify lines 72–88):

```python
def __init__(self, settings: Settings, tool_registry: ToolRegistry) -> None:
    self._provider = settings.LLM_PROVIDER.lower()
    self._tool_registry = tool_registry

    if self._provider == "bedrock":
        kwargs: dict[str, Any] = {"aws_region": settings.AWS_REGION}
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_key"] = settings.AWS_SECRET_ACCESS_KEY
        self._anthropic_client: anthropic.AsyncAnthropic | anthropic.AsyncAnthropicBedrock | None = (
            anthropic.AsyncAnthropicBedrock(**kwargs)
        )
        self._openai_client: openai.AsyncOpenAI | None = None
        self._model = settings.ANTHROPIC_MODEL
        logger.info("LLM provider: AWS Bedrock (region=%s)", settings.AWS_REGION)

    elif self._provider == "anthropic":
        self._anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._openai_client = None
        self._model = settings.ANTHROPIC_MODEL
        logger.info("LLM provider: Anthropic (direct API)")

    elif self._provider == "openai":
        self._anthropic_client = None
        self._openai_client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self._model = settings.OPENAI_MODEL
        logger.info("LLM provider: OpenAI (model=%s)", settings.OPENAI_MODEL)

    else:
        msg = f"Unknown LLM provider: {self._provider!r}. Must be 'anthropic', 'bedrock', or 'openai'."
        raise ValueError(msg)

    self._summary_model = settings.SUMMARY_MODEL or self._model
    self._system_prompt = PromptBuilder().build(
        projects_dir=str(settings.PROJECTS_DIR.expanduser().resolve()),
        os_name=platform.system(),
    )
```

**5c.** Refactor `stream_turn()` to dispatch by provider (replace lines 98–178):

```python
async def stream_turn(
    self,
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> AsyncIterator[LLMStreamEvent]:
    """Stream a single LLM turn, yielding text chunks and tool call requests."""
    if self._provider == "openai":
        async for event in self._stream_turn_openai(messages, system):
            yield event
    else:
        async for event in self._stream_turn_anthropic(messages, system):
            yield event
```

**5d.** Rename existing streaming logic to `_stream_turn_anthropic()` (the current `stream_turn` body becomes this private method):

```python
async def _stream_turn_anthropic(
    self,
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> AsyncIterator[LLMStreamEvent]:
    """Stream a turn using the Anthropic/Bedrock client."""
    assert self._anthropic_client is not None
    tools = self._tool_registry.to_anthropic_tools()
    # ... (existing implementation, lines 104-178 unchanged)
```

**5e.** Add `_stream_turn_openai()` method:

```python
async def _stream_turn_openai(
    self,
    messages: list[dict[str, Any]],
    system: str | None = None,
) -> AsyncIterator[LLMStreamEvent]:
    """Stream a turn using the OpenAI client."""
    assert self._openai_client is not None
    tools = self._tool_registry.to_openai_tools()

    openai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system or self._system_prompt}
    ]
    openai_messages.extend(messages)

    kwargs: dict[str, Any] = {
        "model": self._model,
        "max_tokens": 4096,
        "messages": openai_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        kwargs["tools"] = tools

    started_at = datetime.now(UTC)

    # Track tool calls being assembled across chunks
    tool_calls_in_progress: dict[int, dict[str, Any]] = {}

    async with await self._openai_client.chat.completions.create(**kwargs) as stream:
        async for chunk in stream:
            if not chunk.choices and chunk.usage:
                # Final chunk with usage stats (no choices)
                ended_at = datetime.now(UTC)
                usage = chunk.usage
                yield StreamDone(
                    stop_reason="end_turn",
                    usage=TurnUsage(
                        message_id=chunk.id or "",
                        model=chunk.model or self._model,
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens,
                        cache_creation_input_tokens=0,
                        cache_read_input_tokens=getattr(usage, "prompt_tokens_details", None)
                        and getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0,
                        stop_reason="end_turn",
                        service_tier=getattr(chunk, "service_tier", None),
                        inference_geo=None,
                        started_at=started_at,
                        ended_at=ended_at,
                    ),
                )
                continue

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Text content
            if delta and delta.content:
                yield TextChunk(content=delta.content)

            # Tool calls (streamed incrementally)
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_in_progress:
                        tool_calls_in_progress[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    tc = tool_calls_in_progress[idx]
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc["arguments"] += tc_delta.function.arguments

            # End of response — emit completed tool calls
            if choice.finish_reason:
                for tc in tool_calls_in_progress.values():
                    try:
                        tool_input = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        tool_input = {}
                        logger.error("Failed to parse OpenAI tool arguments: %s", tc["arguments"])

                    yield ToolCallRequest(
                        tool_use_id=tc["id"],
                        tool_name=tc["name"],
                        tool_input=tool_input,
                    )
                tool_calls_in_progress.clear()

                stop_reason = choice.finish_reason  # "stop", "tool_calls", etc.
                if stop_reason == "tool_calls":
                    stop_reason = "tool_use"  # normalize to Anthropic convention

                # If there's no usage chunk coming (shouldn't happen with stream_options),
                # emit StreamDone here
                if not chunk.usage:
                    ended_at = datetime.now(UTC)
                    yield StreamDone(stop_reason=stop_reason)
```

**5f.** Refactor `run_agentic_loop()` to build provider-appropriate messages (replace lines 180–253):

The key change is in how assistant messages and tool results are appended to the conversation history. Add a helper method:

```python
def _build_assistant_message(self, turn: ConversationTurn) -> dict[str, Any]:
    """Build the assistant message in the correct format for the current provider."""
    if self._provider == "openai":
        msg: dict[str, Any] = {"role": "assistant"}
        if turn.text:
            msg["content"] = turn.text
        else:
            msg["content"] = None
        if turn.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.tool_use_id,
                    "type": "function",
                    "function": {
                        "name": tc.tool_name,
                        "arguments": json.dumps(tc.tool_input),
                    },
                }
                for tc in turn.tool_calls
            ]
        return msg
    else:
        # Anthropic format
        content: list[dict[str, Any]] = []
        if turn.text:
            content.append({"type": "text", "text": turn.text})
        for tc in turn.tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.tool_use_id,
                "name": tc.tool_name,
                "input": tc.tool_input,
            })
        return {"role": "assistant", "content": content}

def _build_tool_results(self, tool_calls: list[ToolCallRequest], results: list[str]) -> list[dict[str, Any]]:
    """Build tool result messages in the correct format for the current provider."""
    if self._provider == "openai":
        # OpenAI: one message per tool result with role="tool"
        return [
            {
                "role": "tool",
                "tool_call_id": tc.tool_use_id,
                "content": result,
            }
            for tc, result in zip(tool_calls, results)
        ]
    else:
        # Anthropic: single user message with tool_result content blocks
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.tool_use_id,
                        "content": result,
                    }
                    for tc, result in zip(tool_calls, results)
                ],
            }
        ]
```

Then update `run_agentic_loop()` to use these helpers:

```python
async def run_agentic_loop(
    self,
    messages: list[dict[str, Any]],
    execute_tool_fn: Callable[[ToolCallRequest], Coroutine[Any, Any, str]],
    system: str | None = None,
    should_stop_after_tools: Callable[[], bool] | None = None,
) -> AsyncIterator[LLMStreamEvent]:
    while True:
        turn = ConversationTurn()

        async for event in self.stream_turn(messages, system):
            yield event
            match event:
                case TextChunk(content=text):
                    turn.text += text
                case ToolCallRequest() as tool_call:
                    turn.tool_calls.append(tool_call)
                case StreamDone(stop_reason=reason):
                    turn.stop_reason = reason

        assistant_msg = self._build_assistant_message(turn)
        if assistant_msg.get("content") or assistant_msg.get("tool_calls"):
            messages.append(assistant_msg)

        if not turn.tool_calls:
            break

        try:
            results: list[str] = []
            for tc in turn.tool_calls:
                result = await execute_tool_fn(tc)
                results.append(result)

            tool_msgs = self._build_tool_results(turn.tool_calls, results)
            messages.extend(tool_msgs)
        except BaseException:
            messages.pop()  # remove orphaned assistant message
            raise

        if should_stop_after_tools is not None and should_stop_after_tools():
            break
```

**5g.** Update `generate_title()` and `summarize()` methods (lines 255–280):

These methods currently call `self._client.messages.create()` directly. They need provider branching:

```python
async def generate_title(self, user_prompt: str, assistant_response: str) -> str:
    """Generate a short title for a conversation from the first exchange."""
    if assistant_response:
        content = f"User: {user_prompt}\n\nAssistant: {assistant_response}"
    else:
        content = f"User: {user_prompt}"

    system_msg = (
        "Generate a short title (max 6 words) for this conversation. "
        "Return only the title, no quotes or punctuation."
    )

    if self._provider == "openai":
        assert self._openai_client is not None
        response = await self._openai_client.chat.completions.create(
            model=self._summary_model,
            max_tokens=30,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": content},
            ],
        )
        return (response.choices[0].message.content or "").strip()
    else:
        assert self._anthropic_client is not None
        response = await self._anthropic_client.messages.create(
            model=self._summary_model,
            max_tokens=30,
            system=system_msg,
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text.strip()

async def summarize(self, text: str) -> str:
    """Generate a short TTS-friendly summary of the given text using a fast model."""
    system_msg = (
        "You are a concise summarizer. Produce a 2-3 sentence summary of the following text. "
        "The summary will be read aloud via text-to-speech, so keep it natural, conversational, "
        "and free of markdown, code blocks, or special formatting."
    )

    if self._provider == "openai":
        assert self._openai_client is not None
        response = await self._openai_client.chat.completions.create(
            model=self._summary_model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": text},
            ],
        )
        return (response.choices[0].message.content or "").strip()
    else:
        assert self._anthropic_client is not None
        response = await self._anthropic_client.messages.create(
            model=self._summary_model,
            max_tokens=256,
            system=system_msg,
            messages=[{"role": "user", "content": text}],
        )
        return response.content[0].text
```

**5h.** Update `close()` method (line 282):

```python
async def close(self) -> None:
    if self._anthropic_client is not None:
        await self._anthropic_client.close()
    if self._openai_client is not None:
        await self._openai_client.close()
```

### Step 6: Update Prompt Router for Message Format Awareness

**File:** `src/core/prompt_router.py`

The `PromptRouter` builds the initial `messages` list that gets passed to `LLMClient.run_agentic_loop()`. Examine how it constructs user messages — these are typically just `{"role": "user", "content": "..."}` which is compatible with both Anthropic and OpenAI. No changes should be needed here as long as:

- User messages use `{"role": "user", "content": "text"}` (compatible with both)
- The `LLMClient` handles assistant/tool message format differences internally (done in Step 5f)

However, **conversation history restoration** needs attention. If a session's `conversation_history` was saved while using Anthropic format and later loaded with OpenAI selected (or vice versa), the formats will be incompatible. Two options:

- **Option A (recommended)**: Store conversation history in a **provider-neutral format** and convert on load. This is a larger refactor.
- **Option B (simpler)**: Clear conversation history when switching providers. Add a warning in the config UI.

For the initial implementation, go with **Option B** — it's pragmatic and avoids over-engineering. Provider switching mid-conversation is an edge case. Document this limitation.

### Step 7: No Database Changes Required

The `LLMCall` model (`src/models/db.py`) records `model`, `input_tokens`, `output_tokens`, etc. These fields are generic enough to store OpenAI usage data. The `message_id` field can store OpenAI's response ID. No schema migration needed.

### Step 8: Update Design.md

**File:** `Design.md`

Update the Technology Stack table to include OpenAI:

```markdown
| LLM | Anthropic Messages API, AWS Bedrock, or OpenAI Chat Completions API |
```

Add OpenAI to any sections that enumerate supported providers.

---

## Configuration / Environment Variable Changes

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `"anthropic"` | Now accepts `"openai"` in addition to `"anthropic"` and `"bedrock"` |
| `OPENAI_API_KEY` | `""` | OpenAI API key (required when `LLM_PROVIDER=openai`) |
| `OPENAI_MODEL` | `"gpt-4o"` | OpenAI model ID |

---

## API Endpoint Changes

**No new endpoints.** The existing `GET /api/config` and `PATCH /api/config` endpoints automatically pick up the new `CONFIG_OPTIONS` entries. The Flutter client renders them dynamically based on the schema.

The `visible_when` conditional logic on the new OpenAI fields ensures they only appear in the UI when `LLM_PROVIDER=openai` is selected.

---

## Frontend Changes

**No frontend code changes required.** The Flutter `server_config_screen.dart` dynamically renders config fields based on the schema returned by `GET /api/config`. The `visible_when` mechanism already supports conditional visibility. When the user selects "OpenAI" from the LLM Provider dropdown:

- The Anthropic API Key field hides
- The Bedrock fields hide
- The OpenAI API Key and OpenAI Model fields appear

This works automatically because the schema-driven UI evaluates `visible_when` client-side.

---

## Files Changed Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `pyproject.toml` | Modified | Add `openai>=1.60.0` dependency |
| `src/config.py` | Modified | Add `OPENAI_API_KEY`, `OPENAI_MODEL` settings; add `"openai"` to `LLM_PROVIDER` options; add OpenAI config options to `CONFIG_OPTIONS` |
| `src/core/llm.py` | Modified (major) | Add OpenAI client init, `_stream_turn_openai()`, provider-aware message builders, update `generate_title()` and `summarize()` |
| `src/tools/registry.py` | Modified | Add `to_openai_tools()` method |
| `.env.example` | Modified | Add `OPENAI_API_KEY` and `OPENAI_MODEL` entries |
| `Design.md` | Modified | Document OpenAI as supported provider |

No new files are created. No database migrations needed.

---

## Testing Strategy

### Unit Tests

1. **`LLMClient.__init__()` provider selection** — Test that `LLM_PROVIDER=openai` creates an `AsyncOpenAI` client
2. **`to_openai_tools()` format conversion** — Test that tool definitions are correctly converted to OpenAI function calling format
3. **`_build_assistant_message()`** — Test both Anthropic and OpenAI message format output
4. **`_build_tool_results()`** — Test both Anthropic (single user message with tool_result blocks) and OpenAI (multiple tool role messages) format
5. **Config schema** — Test that OpenAI options appear in `get_config_schema()` with correct `visible_when` conditions
6. **Invalid provider** — Test that unknown provider raises `ValueError`

### Integration Tests

1. **OpenAI streaming** — Mock `openai.AsyncOpenAI` and verify `_stream_turn_openai()` correctly yields `TextChunk`, `ToolCallRequest`, and `StreamDone` events
2. **Agentic loop with OpenAI** — Mock the OpenAI client and verify the full tool-call loop works with OpenAI message format
3. **Config hot-reload** — Test switching from `anthropic` to `openai` via `PATCH /api/config` and verify the new LLM client is correctly created
4. **Title generation / summarization** — Test both provider paths produce valid results

### Manual Testing

1. Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY=sk-...` in `.env`
2. Start the server, verify startup log shows "LLM provider: OpenAI"
3. Send a text prompt via WebSocket, verify streaming response
4. Test a prompt that triggers tool calls, verify tools execute and results loop back
5. Switch provider via the Flutter config screen, verify hot-reload works
6. Test with various OpenAI models (gpt-4o, gpt-4.1, o3-mini)

---

## Migration Considerations

- **Existing deployments**: No migration needed. The new settings have defaults (`OPENAI_API_KEY=""`, `OPENAI_MODEL="gpt-4o"`) and `LLM_PROVIDER` defaults to `"anthropic"`, so existing installations continue working unchanged.
- **Conversation history**: Switching providers mid-session will break conversation history format. The system should clear/reset the conversation when the provider changes, or at minimum warn the user.
- **SUMMARY_MODEL**: When using OpenAI, the `SUMMARY_MODEL` setting must reference an OpenAI model ID (not an Anthropic one). If `SUMMARY_MODEL` is blank, it defaults to the main model, which is correct. If it's set to an Anthropic model ID and the user switches to OpenAI, it will fail. Consider adding validation or documentation for this.

---

## Potential Risks and Mitigations

### 1. Streaming Format Differences
**Risk**: OpenAI's streaming format has subtle differences (e.g., tool call chunks arrive incrementally with index-based assembly).
**Mitigation**: The implementation in Step 5e carefully handles index-based tool call assembly. Thorough integration tests with mocked responses will catch edge cases.

### 2. Token Usage Reporting
**Risk**: OpenAI reports `prompt_tokens` / `completion_tokens` while Anthropic uses `input_tokens` / `output_tokens`. Cache token reporting differs.
**Mitigation**: Map OpenAI fields to the existing `TurnUsage` dataclass in `_stream_turn_openai()`. Cache tokens map to `prompt_tokens_details.cached_tokens` if available.

### 3. Max Tokens Behavior
**Risk**: OpenAI and Anthropic handle `max_tokens` differently. Some OpenAI models (o1, o3) don't accept `max_tokens` and use `max_completion_tokens` instead.
**Mitigation**: For the initial implementation, use `max_tokens=4096` which works with GPT-4o and GPT-4.1 models. Add model-specific handling later if needed for reasoning models.

### 4. Tool Call ID Format
**Risk**: Anthropic uses `toolu_...` prefixed IDs, OpenAI uses `call_...` prefixed IDs. The `ToolCallRequest.tool_use_id` field must pass these through correctly.
**Mitigation**: The implementation treats tool IDs as opaque strings, which is already correct.

### 5. System Prompt Handling
**Risk**: Anthropic uses a top-level `system` parameter, OpenAI uses a `{"role": "system", ...}` message.
**Mitigation**: Handled in `_stream_turn_openai()` by prepending the system message to the messages list.

### 6. Error Handling
**Risk**: OpenAI SDK raises different exception types (`openai.APIError`, `openai.RateLimitError`, etc.) than Anthropic.
**Mitigation**: For the initial implementation, let exceptions propagate naturally — the existing error handling in `PromptRouter` catches generic exceptions. Add provider-specific error mapping as a follow-up if needed.

### 7. Conversation History Format Incompatibility
**Risk**: Saved Anthropic-format conversation history won't work when loaded with OpenAI provider.
**Mitigation**: Clear conversation history when provider is switched. Document this behavior. A future enhancement could implement a provider-neutral history format.

---

## Implementation Order

Execute in this order to maintain a working system at each step:

1. **Add dependency** (`pyproject.toml` + `uv sync`)
2. **Add settings** (`src/config.py` — Settings fields + CONFIG_OPTIONS)
3. **Add tool format** (`src/tools/registry.py` — `to_openai_tools()`)
4. **Refactor LLMClient** (`src/core/llm.py` — all changes from Step 5)
5. **Update .env.example** (documentation)
6. **Update Design.md** (documentation)
7. **Write tests**
8. **Manual end-to-end testing**
