# RCFlow Streaming Architecture Analysis

## Current Architecture Overview

### Backend (Python/FastAPI)

#### Core Components:
1. **Session Management** (`src/core/session.py`)
   - `ActiveSession`: Manages in-memory session state with conversation history and buffers
   - `SessionManager`: Handles session lifecycle and archival to PostgreSQL
   - Session states: CREATED, ACTIVE, EXECUTING, PAUSED, COMPLETED, FAILED, CANCELLED

2. **Message Buffering** (`src/core/buffer.py`)
   - `SessionBuffer`: Core streaming component that buffers messages and notifies subscribers
   - Supports multiple subscribers per session with full history replay on subscribe
   - Separate text and audio message queues
   - Push-based notification system using `asyncio.Queue`

3. **WebSocket Endpoints** (`src/api/ws/`)
   - `/ws/input/text`: Client → Server prompts
   - `/ws/output/text`: Server → Client streaming text responses
   - `/ws/input/audio`: Client → Server voice input (PCM)
   - `/ws/output/audio`: Server → Client TTS audio (Opus)

4. **Message Types**:
   - `text_chunk`: Streaming LLM text output
   - `tool_start`: Tool execution begins
   - `tool_output`: Tool execution results
   - `error`: Error messages
   - `session_end`: Session terminated
   - `session_end_ask`: Prompt to end/continue session
   - `summary`: Session summary
   - `agent_group_start`/`agent_group_end`: Claude Code mode blocks
   - `session_paused`/`session_resumed`: Session state changes
   - `plan_mode_ask`/`plan_review_ask`: Planning mode interactions

5. **Streaming Model**:
   - Full buffering in `SessionBuffer` with sequence numbers
   - Subscribe/unsubscribe pattern for session output
   - Replay full history on subscribe, then live updates
   - Multiple concurrent subscribers supported per session

### Frontend (Flutter/Dart)

#### Core Components:
1. **WebSocket Service** (`websocket_service.dart`)
   - Manages dual WebSocket connections (input + output channels)
   - Handles JSON message encoding/decoding
   - Ping keepalive for connection stability
   - Supports self-signed certificates

2. **State Management**:
   - `AppState`: Global state with session list and WebSocket management
   - `PaneState`: Per-pane state for split view, manages messages and streaming
   - Output handlers registry for message type routing

3. **Message Display**:
   - Dynamic streaming with character queue and timed rendering
   - Collapsible agent groups for Claude Code output
   - Tool blocks with expandable output
   - Various interactive cards (session end, plan mode, etc.)

4. **Streaming Features**:
   - Character-by-character text animation
   - Accelerating display based on queue depth
   - Tool output buffering and display

## SSE Migration Path

### Why SSE Instead of WebSocket for Output Streaming

**Advantages:**
1. **Simpler Protocol**: SSE is unidirectional (server→client), perfect for output streaming
2. **HTTP/2 Compatible**: Better performance with multiplexing
3. **Automatic Reconnection**: Built-in retry logic with EventSource API
4. **Firewall Friendly**: Standard HTTP, no WebSocket upgrade needed
5. **Easier Load Balancing**: Standard HTTP requests

**Current WebSocket Features to Preserve:**
- Session subscription/unsubscription
- Full history replay on subscribe
- Multiple session monitoring
- Session list queries

### Proposed Architecture Changes

#### Backend Changes:

1. **New SSE Endpoint**: `/api/sessions/{session_id}/stream`
   - Returns `text/event-stream` content type
   - Streams all message types as SSE events
   - Supports `Last-Event-ID` for resumption
   - Query param `?replay=true` for full history

2. **Session Buffer Adaptation**:
   - Keep existing `SessionBuffer` structure
   - Add SSE formatter for messages
   - Use event types matching current message types
   - Include sequence numbers as event IDs

3. **RESTful Session Control**:
   - `POST /api/sessions`: Create new session
   - `POST /api/sessions/{id}/prompts`: Send prompts
   - `POST /api/sessions/{id}/pause`: Pause session
   - `POST /api/sessions/{id}/resume`: Resume session
   - `POST /api/sessions/{id}/end`: End session
   - `DELETE /api/sessions/{id}`: Cancel session

#### Frontend Changes:

1. **EventSource Integration**:
   - Replace output WebSocket with EventSource API
   - Keep input WebSocket for low-latency prompts (or migrate to REST)
   - Handle automatic reconnection gracefully

2. **State Management Updates**:
   - Minimal changes to `PaneState`
   - Update message handlers to work with SSE events
   - Preserve existing display logic

### Implementation Phases

#### Phase 1: Backend SSE Endpoint (Parallel to WebSocket)
- Add SSE endpoint alongside existing WebSocket
- Reuse `SessionBuffer` subscription mechanism
- Format messages as SSE events
- Test with curl/httpie

#### Phase 2: Frontend EventSource Client
- Add EventSource service alongside WebSocket
- Parse SSE events into existing message format
- Route through existing output handlers
- Feature flag for SSE vs WebSocket

#### Phase 3: Migration & Cleanup
- Default to SSE for new sessions
- Migrate session control to REST endpoints
- Keep WebSocket for backwards compatibility
- Eventually deprecate WebSocket output

### Technical Considerations

1. **Message Format**:
```
event: text_chunk
data: {"session_id": "uuid", "sequence": 42, "content": "Hello"}
id: 42

event: tool_start
data: {"session_id": "uuid", "sequence": 43, "tool_name": "shell_exec", "tool_input": {...}}
id: 43
```

2. **History Replay**:
   - On initial connection, stream full history with original sequence numbers
   - Use `Last-Event-ID` header for resume after disconnect

3. **Connection Management**:
   - EventSource handles reconnection automatically
   - Server tracks active SSE connections per session
   - Clean up on client disconnect

4. **Performance**:
   - SSE has lower overhead than WebSocket for unidirectional streaming
   - HTTP/2 multiplexing improves multi-session monitoring
   - Consider compression for large message payloads

### Risks & Mitigations

1. **Browser Compatibility**: EventSource is well-supported but check Flutter web_socket_channel alternatives
2. **Connection Limits**: Browsers limit concurrent EventSource connections (6 per domain)
3. **Binary Data**: SSE is text-only; audio streaming stays on WebSocket or moves to separate endpoint
4. **Load Balancing**: Sticky sessions needed for stateful streaming

## Recommendations

1. **Start with Parallel Implementation**: Keep WebSocket working while building SSE
2. **Reuse Existing Components**: `SessionBuffer` and message types work well for SSE
3. **Incremental Migration**: Test thoroughly with feature flags before switching
4. **Monitor Performance**: Compare latency and resource usage between approaches
5. **Document Changes**: Update Design.md and API documentation as implementation progresses

## Next Steps

1. Create proof-of-concept SSE endpoint using FastAPI's `StreamingResponse`
2. Test with simple EventSource client
3. Implement full message type support
4. Add Flutter EventSource integration
5. Performance testing and optimization
6. Gradual rollout with feature flags