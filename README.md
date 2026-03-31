# RCFlow

A coding agent orchestration platform: a backend server paired with a Flutter desktop and mobile client. Run and manage multiple coding agents — Claude Code, OpenAI Codex, and OpenCode — across your projects from a single place. Send prompts from any device, monitor sessions in real time, and let agents work in parallel while you stay in the loop.

## Key Features

- **Agent orchestration** — Spin up and manage Claude Code, Codex, and OpenCode agents concurrently across different projects and worktrees
- **Built-in client** — Flutter desktop and mobile app for sending prompts, reviewing output, approving tool calls, and managing sessions
- **Remote control** — Drive agents from any device over WebSocket; the server runs on your machine, the client runs anywhere
- **Real-time streaming** — Separate WebSocket channels for text input, audio input, text output, and audio output
- **Pluggable tools** — Tools are JSON files loaded at startup; extend agent capabilities without code changes
- **Session management** — Persistent sessions with pause/resume/restore, history, and automatic database archival
- **Speech support** — Pluggable STT (Wispr Flow) and TTS providers for voice-driven workflows
- **Multi-backend LLM** — Anthropic API, AWS Bedrock, or OpenAI-compatible providers
- **Hot-reloadable config** — Change LLM provider, API keys, and settings at runtime via the API

## Installation

### Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — Python package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- An LLM API key — **Anthropic**, **OpenAI**, or **AWS Bedrock** access

### From a Release

Download the latest release archive from the [Releases page](../../releases), extract it, and install:

```bash
# Extract the release archive
tar -xf rcflow-<version>.tar.gz
cd rcflow-<version>

# Install into /opt/rcflow (or any directory you prefer)
sudo mkdir -p /opt/rcflow
sudo cp -r . /opt/rcflow
cd /opt/rcflow

# Create virtual environment and install dependencies
uv sync --no-dev
```

For PostgreSQL support instead of the default SQLite:

```bash
uv sync --no-dev --extra postgres
```

### Run the Server

```bash
uv run rcflow
```

On first run, `settings.json` is created automatically with default values and a generated API key. The server binds to `0.0.0.0:53890` by default.

Apply database migrations before first use:

```bash
uv run alembic upgrade head
```

### systemd Service (Linux)

A service file is included in the release:

```bash
sudo cp systemd/rcflow.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rcflow
```

The service expects the application at `/opt/rcflow` with `settings.json` and the virtual environment in place.

### Flutter Client

Pre-built APKs (Android) and Windows installers are attached to each release. Download and install the appropriate artifact for your platform — no build step required.

## Configuration

Settings are stored in `settings.json` in the server directory. Edit the file directly or use the `/api/config` endpoint to update settings at runtime.

| Setting | Description | Default |
|---------|-------------|---------|
| `RCFLOW_HOST` | Server bind address | `0.0.0.0` |
| `RCFLOW_PORT` | Server port | `53890` |
| `RCFLOW_API_KEY` | API key for client authentication | *(auto-generated)* |
| `LLM_PROVIDER` | `anthropic`, `bedrock`, `openai`, or `none` | `anthropic` |
| `ANTHROPIC_API_KEY` | Anthropic API key | |
| `ANTHROPIC_MODEL` | Model ID | `claude-sonnet-4-6` |
| `DATABASE_URL` | SQLAlchemy async database URL | `sqlite+aiosqlite:///./data/rcflow.db` |
| `SSL_CERTFILE` / `SSL_KEYFILE` | TLS certificate paths (enables WSS) | |
| `STT_PROVIDER` | Speech-to-text provider | `wispr_flow` |
| `PROJECTS_DIR` | Root directory for project folders | `~/Projects` |
| `TOOLS_DIR` | Directory for tool JSON definitions | `./tools` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

Environment variables take precedence over `settings.json`. See `Design.md` for the full list including AWS Bedrock, TTS, Codex, and tool management options.

## API

OpenAPI docs are served at `/docs` while the server is running.

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

All authenticated endpoints require the `RCFLOW_API_KEY` header or query parameter.

## Tools

Tools are JSON files in the `tools/` directory. Each defines a name, description, parameters (JSON Schema), executor type, and configuration.

| Tool | Executor | Description |
|------|----------|-------------|
| `shell_exec` | `shell` | Execute shell commands on the host |
| `system_info` | `shell` | Gather system information |
| `claude_code` | `claude_code` | Interactive Claude Code coding sessions |
| `codex` | `codex` | OpenAI Codex coding sessions |

## Architecture

```
┌──────────────────┐
│  Mobile/Desktop  │
│      Client      │
└──┬───┬───┬───┬───┘
   │   │   │   │
   ▼   ▼   ▼   ▼
/ws/input    /ws/output      ← 4 WebSocket channels
/text        /text
/audio       /audio
   │                 ▲
   ▼                 │
Prompt Router → LLM (Anthropic/Bedrock) → Tool Executor → Session Manager
                                                                 │
                                                                 ▼
                                                           Database
                                                     (SQLite / PostgreSQL)
```

**Request lifecycle:** Client sends text/audio → STT transcribes audio → Prompt Router creates/resumes a session → LLM generates tool calls → Executors run tools → Output streams back via WebSocket → Session archived to database on completion.

## License

Not yet specified.
