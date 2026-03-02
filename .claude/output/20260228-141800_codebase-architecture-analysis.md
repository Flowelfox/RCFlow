# RCFlow Architecture Analysis

## Executive Summary

RCFlow is a WebSocket-based server system that provides natural language processing capabilities for executing actions on Linux hosts. The system uses a client-server architecture with:
- **Backend**: Python 3.12+, FastAPI, SQLAlchemy 2.0, PostgreSQL
- **Frontend**: Flutter (Android/Windows)
- **LLM Integration**: Anthropic Messages API or AWS Bedrock
- **Audio**: Opus/OGG format with pluggable STT/TTS providers

## Backend Architecture (Python/FastAPI)

### Core Directory Structure

```
src/
├── api/           # HTTP and WebSocket endpoints
│   ├── http.py    # REST API endpoints
│   └── ws/        # WebSocket handlers
│       ├── input_text.py   # Text prompt handling
│       ├── input_audio.py  # Audio input handling
│       ├── output_text.py  # Text streaming output
│       └── output_audio.py # Audio streaming output
├── core/          # Business logic
│   ├── session.py        # Session management
│   ├── buffer.py         # Message buffering
│   ├── llm.py           # LLM integration
│   └── prompt_router.py  # Prompt processing
├── models/        # Data models
│   └── db.py     # SQLAlchemy ORM models
├── executors/     # Tool executors
│   ├── base.py          # Base executor interface
│   ├── shell.py         # Shell command executor
│   ├── http.py          # HTTP request executor
│   └── claude_code.py   # Claude Code integration
├── tools/         # Tool management
│   └── loader.py  # Dynamic tool loading
└── db/           # Database layer
    └── migrations/  # Alembic migrations
```

### Key Database Models

1. **Session** (`sessions` table)
   - `id`: UUID primary key
   - `session_type`: one-shot, conversational, long-running
   - `status`: created, active, executing, paused, completed, failed, cancelled
   - `title`: Optional session title
   - `metadata_`: JSONB for flexible metadata

2. **SessionMessage** (`session_messages` table)
   - `session_id`: Foreign key to Session
   - `sequence`: Message order within session
   - `message_type`: Type of message
   - `content`: Text content
   - `metadata_`: JSONB for message metadata

3. **ToolExecution** (`tool_executions` table)
   - Tracks tool executions within sessions
   - Links to Session via `session_id`
   - Stores input, output, status, and timing

4. **LLMCall** (`llm_calls` table)
   - Records LLM API interactions
   - Tracks token usage and costs
   - Stores request/response data

### Session Management

- **ActiveSession** class: In-memory session state
  - Manages session lifecycle (created → active → executing → completed/failed)
  - Holds conversation history and metadata
  - Supports pause/resume functionality
  - Integrates Claude Code executor for specialized mode

- **SessionBuffer**: Message queue for each session
  - Handles text and audio messages
  - Maintains message sequencing
  - Supports message history retrieval

### WebSocket Architecture

The system uses **dual WebSocket channels**:

1. **Input Channel** (`/ws/input/text` or `/ws/input/audio`)
   - Receives prompts from clients
   - Handles question answers
   - Supports session-specific commands

2. **Output Channel** (`/ws/output/text` or `/ws/output/audio`)
   - Streams responses to subscribed clients
   - Supports multiple client subscriptions
   - Delivers system messages and tool outputs

### API Endpoints

#### HTTP REST API (`/api/`)
- `GET /health` - Health check (no auth)
- `GET /sessions` - List all sessions
- `GET /sessions/{id}/messages` - Get session messages (with pagination)
- `GET /tools` - List available tools
- `POST /sessions/{id}/cancel` - Cancel active session
- `POST /sessions/{id}/end` - End session
- `POST /sessions/{id}/pause` - Pause session
- `POST /sessions/{id}/resume` - Resume paused session
- `PUT /sessions/{id}/rename` - Rename session

## Flutter Client Architecture

### Directory Structure

```
rcflowclient/lib/
├── main.dart              # App entry point
├── models/                # Data models
│   ├── session_info.dart  # Session metadata
│   └── split_tree.dart    # Pane splitting logic
├── services/              # Business services
│   ├── websocket_service.dart    # WebSocket communication
│   ├── settings_service.dart     # App settings
│   ├── foreground_service.dart   # Android foreground task
│   └── server_url.dart          # URL building
├── state/                 # State management
│   ├── app_state.dart     # Global app state
│   ├── pane_state.dart    # Per-pane session state
│   └── output_handlers.dart  # Message processing
└── ui/                    # UI components
    ├── screens/           # Screen layouts
    └── widgets/           # Reusable widgets
        ├── message_components/  # Message UI types
        ├── split_view.dart      # Pane splitting UI
        └── session_panel.dart   # Session list
```

### State Management Architecture

1. **AppState** (Global)
   - Connection management
   - Session list (shared across panes)
   - Split pane tree structure
   - Active pane tracking
   - WebSocket service coordination

2. **PaneState** (Per-pane)
   - Individual session view
   - Message history
   - Streaming state
   - Question/answer handling
   - Session lifecycle management

3. **Split Pane System**
   - Supports multiple concurrent sessions
   - Dynamic pane splitting (horizontal/vertical)
   - Each pane maintains independent state
   - Shared session list across all panes

### WebSocket Communication

The Flutter client maintains two WebSocket connections:

1. **Input Channel**: Sends user prompts and answers
   ```dart
   {
     "type": "prompt",
     "text": "user message",
     "session_id": "uuid"
   }
   ```

2. **Output Channel**: Receives streamed responses
   - Subscribes to specific sessions
   - Handles multiple message types
   - Processes tool execution updates

### Message Types

- **User Messages**: Direct user input
- **Assistant Messages**: LLM responses
- **Tool Blocks**: Tool execution status/results
- **Question Blocks**: Interactive questions
- **System Messages**: Connection/error notifications
- **Agent Groups**: Grouped tool executions

## Key Design Patterns

1. **Session-Based Architecture**
   - Each conversation is a session with unique ID
   - Sessions persist across reconnections
   - Support for parallel sessions

2. **Streaming Response Model**
   - Real-time token streaming from LLM
   - Progressive UI updates
   - Buffered message handling

3. **Pluggable Tool System**
   - Tools defined via JSON configuration
   - Dynamic tool loading at runtime
   - Executor pattern for tool execution

4. **State Synchronization**
   - Backend maintains authoritative state
   - Client subscribes to session updates
   - Automatic reconnection with state recovery

## Development Commands

```bash
# Backend
make dev          # Install dev dependencies
make run          # Run server
make test         # Run tests
make migrate      # Apply DB migrations

# Flutter
make flutter-run      # Run in hot reload mode
make flutter-build    # Build debug APK
make flutter-release  # Build release APK

# WSL2/Emulator Setup
make setup-emulator   # Configure ADB connection
make start-emulator   # Start Android emulator
```

## Configuration

- Environment variables via `.env` file
- Required: `ANTHROPIC_API_KEY` or AWS Bedrock credentials
- Database: `DATABASE_URL` (PostgreSQL)
- Optional: STT/TTS provider settings

## Testing Architecture

- Backend: pytest with async support
- Coverage tracking for src/ modules
- Mock WebSocket clients for integration tests
- Flutter: Standard Flutter testing framework

## Security

- API key authentication for HTTP/WebSocket
- Hashed key storage in database
- TLS support with self-signed certificates
- Session isolation and access control

## Next Steps for Development

Based on the architecture analysis, common development tasks would include:

1. **Adding new tools**: Create JSON definition in `tools/` directory
2. **Extending API**: Add endpoints to `src/api/http.py`
3. **New message types**: Update both backend and Flutter handlers
4. **UI enhancements**: Modify Flutter widgets in `lib/ui/widgets/`
5. **Session features**: Extend `ActiveSession` and `PaneState` classes