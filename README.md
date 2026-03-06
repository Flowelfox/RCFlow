# RCFlow

A WebSocket-based action server that translates natural language prompts into tool executions via LLM. Users connect from mobile or desktop clients, send text or voice prompts, and the server uses Anthropic Claude to interpret those prompts into tool calls — shell commands, HTTP API calls, Claude Code sessions, and more. Results stream back to the client in real time over WebSocket channels.

## Key Features

- **Natural language to action** — Send text or voice prompts; the LLM decides which tools to invoke and executes them on the host machine
- **Real-time streaming** — Separate WebSocket channels for text input, audio input, text output, and audio output
- **Pluggable tools** — Tools are defined as JSON files and loaded at startup; add new tools without code changes
- **Multiple executors** — Shell commands, HTTP API calls, Claude Code (interactive coding agent), and OpenAI Codex
- **Session management** — Persistent sessions with pause/resume/restore, conversation history, and automatic archival to database
- **Speech support** — Pluggable STT (Wispr Flow) and TTS providers for voice-driven workflows
- **Multi-backend LLM** — Supports Anthropic API directly or via AWS Bedrock
- **Cross-platform** — Server runs on Linux and Windows; Flutter client targets Android and Windows desktop
- **Hot-reloadable config** — Change LLM provider, API keys, and other settings at runtime via the API
- **Tool management** — Automatic detection, installation, and updates of managed CLI tools (Claude Code, Codex)

## Architecture

```
┌─────────────────┐
│  Mobile/Desktop  │
│     Client       │
└────┬───┬───┬───┬┘
     │   │   │   │
     ▼   ▼   ▼   ▼
  /ws/input  /ws/output    ← 4 WebSocket channels
  /text      /text
  /audio     /audio
     │              ▲
     ▼              │
  Prompt Router → LLM (Anthropic/Bedrock) → Tool Executor → Session Manager
                                                                    │
                                                                    ▼
                                                              Database
                                                        (SQLite / PostgreSQL)
```

**Request lifecycle:** Client sends text/audio → STT transcribes audio → Prompt Router creates/resumes a session → LLM generates tool calls → Executors run tools → Output streams back via WebSocket → Session archived to database on completion.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — Python package manager
- **Anthropic API key** or **AWS Bedrock** access for LLM inference
- **SQLite** (default, zero-config) or **PostgreSQL** for persistence
- **Flutter SDK 3.11+** (only if building the client)

## Installation

```bash
# Clone the repository
git clone <repo-url> && cd rcflow

# Install production dependencies
just install

# Or install with dev dependencies (linting, testing, pre-commit hooks)
just dev
```

For PostgreSQL support:

```bash
uv sync --extra postgres
```

## Configuration

Settings are stored in `settings.json`. The file is created automatically on first run with default values and a generated API key. You can also create it manually:

Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `RCFLOW_HOST` | Server bind address | `0.0.0.0` |
| `RCFLOW_PORT` | Server port | `8765` |
| `RCFLOW_API_KEY` | API key for client authentication | *(required)* |
| `LLM_PROVIDER` | `anthropic` or `bedrock` | `anthropic` |
| `ANTHROPIC_API_KEY` | Anthropic API key (when using direct API) | |
| `ANTHROPIC_MODEL` | Model ID | `claude-sonnet-4-20250514` |
| `DATABASE_URL` | SQLAlchemy async database URL | `sqlite+aiosqlite:///./data/rcflow.db` |
| `SSL_CERTFILE` / `SSL_KEYFILE` | TLS certificate paths (enables WSS) | |
| `STT_PROVIDER` | Speech-to-text provider | `wispr_flow` |
| `PROJECTS_DIR` | Root directory containing project folders | `~/Projects` |
| `TOOLS_DIR` | Directory containing tool JSON definitions | `./tools` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

See `Design.md` for the full list including AWS Bedrock, TTS, Codex, and tool management options. Environment variables set in the shell take precedence over values in `settings.json`.

## Usage

### Development

```bash
# Start the server
just run

# Or directly
uv run rcflow
```

The server will start on the configured host and port (default: `0.0.0.0:8765`).

### Database Migrations

```bash
# Apply all migrations
just migrate

# Generate a new migration after model changes
just migrate-gen "describe your change"

# Rollback the last migration
just migrate-down
```

### Production (systemd)

A systemd service file is provided at `systemd/rcflow.service`:

```bash
# Copy and enable the service
sudo cp systemd/rcflow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rcflow
```

The service expects the application to be installed at `/opt/rcflow` with a `settings.json` file and virtual environment in place.

### Flutter Client

```bash
# Run in hot reload mode (Android emulator)
just flutter-run

# Build debug APK
just flutter-build

# Build release APK (split per ABI)
just flutter-release

# Build Windows desktop release
just flutter-windows
```

Helper scripts for Android emulator setup on WSL2:

```bash
just start-emulator    # Start Windows Android emulator (cold boot)
just setup-emulator    # Setup WSL2 ADB connection
```

## API

RCFlow exposes a REST API and four WebSocket endpoints. The server auto-generates OpenAPI documentation at `/docs` when running.

### REST Endpoints (`/api`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check (no auth) |
| `GET` | `/api/info` | Server OS and platform info |
| `GET` | `/api/sessions` | List all sessions |
| `GET` | `/api/sessions/{id}/messages` | Get session message history (paginated) |
| `POST` | `/api/sessions/{id}/cancel` | Cancel a running session |
| `POST` | `/api/sessions/{id}/end` | End a session |
| `POST` | `/api/sessions/{id}/pause` | Pause a session |
| `POST` | `/api/sessions/{id}/resume` | Resume a paused session |
| `POST` | `/api/sessions/{id}/restore` | Restore an archived session |
| `PATCH` | `/api/sessions/{id}/title` | Rename a session |
| `GET` | `/api/tools` | List available tool definitions |
| `GET` | `/api/tools/status` | Managed tool installation status |
| `POST` | `/api/tools/update` | Trigger tool updates |
| `GET` | `/api/projects` | List project directories |
| `GET` | `/api/config` | Get server configuration |
| `PATCH` | `/api/config` | Update server configuration |

### WebSocket Endpoints

| Path | Direction | Description |
|------|-----------|-------------|
| `/ws/input/text` | Client → Server | Send text prompts |
| `/ws/input/audio` | Client → Server | Send audio for STT transcription |
| `/ws/output/text` | Server → Client | Receive streaming text output |
| `/ws/output/audio` | Server → Client | Receive streaming audio (TTS) |

All authenticated endpoints require the `RCFLOW_API_KEY` header/query parameter.

## Tools

Tools are defined as JSON files in the `tools/` directory. Each tool specifies its name, description, parameters (JSON Schema), executor type, and configuration. Built-in tools:

| Tool | Executor | Description |
|------|----------|-------------|
| `shell_exec` | `shell` | Execute shell commands on the host |
| `system_info` | `shell` (script) | Gather system information |
| `claude_code` | `claude_code` | Interactive Claude Code coding sessions |
| `codex` | `codex` | OpenAI Codex coding sessions |

## Testing

```bash
# Run tests
just test

# Run tests with coverage report
just coverage

# Run all checks (lint + typecheck + test)
just check
```

Individual checks:

```bash
just lint        # Ruff linting
just typecheck   # ty type checking
just format      # Auto-format with Ruff
```

## Project Structure

```
rcflow/
├── src/                    # Python server
│   ├── api/                # FastAPI routes
│   │   ├── http.py         # REST endpoints
│   │   └── ws/             # WebSocket handlers (input/output, text/audio)
│   ├── core/               # Core logic
│   │   ├── llm.py          # LLM client (Anthropic/Bedrock)
│   │   ├── prompt_router.py# Routes prompts through LLM and tools
│   │   ├── session.py      # Session lifecycle management
│   │   └── buffer.py       # Message buffering and history
│   ├── executors/          # Tool executors (shell, http, claude_code, codex)
│   ├── tools/              # Tool registry and JSON loader
│   ├── speech/             # STT/TTS provider abstractions
│   ├── prompts/            # Prompt template builder (Jinja2)
│   ├── services/           # Tool management and settings
│   ├── models/             # SQLAlchemy models
│   ├── db/                 # Database engine and Alembic migrations
│   ├── config.py           # Settings (pydantic-settings)
│   └── main.py             # FastAPI app factory and lifespan
├── rcflowclient/           # Flutter client (Android, Windows, Linux, macOS, Web)
├── tools/                  # Tool definition JSON files
├── tests/                  # pytest test suite
├── systemd/                # systemd service file
├── scripts/                # Helper scripts (emulator setup, icon generation)
├── certs/                  # TLS certificates (gitignored)
├── alembic.ini             # Alembic migration config
├── justfile                # Build, run, test, and deploy commands
├── pyproject.toml          # Python project metadata and dependencies
└── Design.md               # Detailed design document
```

## License

Not yet specified.
