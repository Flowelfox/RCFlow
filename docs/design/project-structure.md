---
updated: 2026-04-26
---

# Project Structure

Repository layout for the Python backend, tool definitions, and tests. Flutter client (`rcflowclient/`) and worktree manager are tracked independently.

```
RCFlow/
├── CLAUDE.md                    # Claude Code project instructions (points engines at docs/design/README.md)
├── README.md                    # User-facing project README
├── pyproject.toml               # Project metadata and dependencies (uv)
├── settings.json                 # Server configuration (JSON, auto-created on first run)
├── .python-version              # Python version pin
├── ruff.toml                    # Ruff linter/formatter config
│
├── docs/
│   └── design/                  # Design documents — entry point is docs/design/README.md
│       ├── README.md            #   Index, overview, tech stack, future considerations
│       ├── architecture.md
│       ├── http-api.md
│       ├── websocket-api.md
│       ├── sessions.md
│       ├── permissions.md
│       ├── prompt-templates.md
│       ├── mentions.md
│       ├── slash-commands.md
│       ├── direct-tool-mode.md
│       ├── tools.md
│       ├── executors.md
│       ├── database.md
│       ├── configuration.md
│       ├── linear.md
│       ├── telemetry.md
│       ├── project-structure.md
│       └── deployment.md
│
├── src/
│   ├── __init__.py
│   ├── main.py                  # Entry point, FastAPI app, lifespan
│   ├── config.py                # Settings loaded from env vars
│   │
│   ├── cli/
│   │   └── __init__.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py              # Shared dependencies (auth, db session)
│   │   ├── http.py              # Main API router (assembles sub-routers)
│   │   ├── routes/
│   │   │   ├── __init__.py      # Collects and re-exports all sub-routers
│   │   │   ├── sessions.py      # Session CRUD & lifecycle endpoints
│   │   │   ├── tools.py         # Tool management & settings endpoints
│   │   │   ├── auth.py          # Claude Code & Codex login/logout
│   │   │   ├── tasks.py         # Task CRUD & session attachment
│   │   │   ├── artifacts.py     # Artifact CRUD & settings
│   │   │   └── config.py        # Health, info, config, projects
│   │   ├── ws/
│   │   │   ├── __init__.py
│   │   │   ├── input_text.py    # /ws/input/text handler
│   │   │   └── output_text.py   # /ws/output/text handler
│   │   └── integrations/
│   │       ├── __init__.py
│   │       └── linear.py        # /api/integrations/linear/ endpoints
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── session.py           # Session manager and session state
│   │   ├── prompt_router.py     # Routes text to LLM pipeline (orchestrator)
│   │   ├── session_lifecycle.py # Session create/cancel/end/pause/resume (mixin)
│   │   ├── context.py           # Mention extraction & context building (mixin)
│   │   ├── agent_claude_code.py # Claude Code agent lifecycle (mixin)
│   │   ├── agent_codex.py       # Codex CLI agent lifecycle (mixin)
│   │   ├── background_tasks.py  # Fire-and-forget background tasks (mixin)
│   │   ├── llm.py               # LLM client (Anthropic, Bedrock, OpenAI)
│   │   └── buffer.py            # Output buffer for session history
│   │
│   ├── executors/
│   │   ├── __init__.py
│   │   ├── base.py              # Base executor interface
│   │   ├── shell.py             # Shell command executor
│   │   ├── http.py              # HTTP API executor
│   │   ├── claude_code.py       # Claude Code CLI executor
│   │   └── codex.py             # Codex CLI executor (OpenAI)
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── tool_manager.py      # Auto-install/update for Claude Code, Codex & OpenCode CLIs
│   │   └── linear_service.py    # Linear GraphQL API client
│   │
│   ├── prompts/
│   │   ├── __init__.py          # Exports PromptBuilder
│   │   ├── builder.py           # PromptBuilder class (uses Jinja2)
│   │   └── templates/
│   │       └── system_prompt.j2    # System prompt in Jinja2 format
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── db.py                # SQLAlchemy models
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py            # Async engine and session factory
│   │   └── migrations/          # Alembic migrations
│   │
│   └── tools/
│       ├── __init__.py
│       ├── loader.py            # Load and validate tool JSON files
│       └── registry.py          # Tool registry for LLM integration
│
├── tools/                       # Pluggable tool definition JSON files
│   ├── shell_exec.json
│   ├── codex.json               # OpenAI Codex CLI agent tool
│   └── ...
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared fixtures
│   ├── test_api/
│   │   └── test_ws/
│   │       ├── test_input_text.py
│   │       └── test_output_text.py
│   ├── test_core/
│   │   ├── test_session.py
│   │   ├── test_prompt_router.py
│   │   ├── test_llm.py
│   │   └── test_buffer.py
│   ├── test_executors/
│   │   ├── test_shell.py
│   │   ├── test_http.py
│   │   ├── test_claude_code.py
│   │   └── test_codex.py
│   ├── test_services/
│   │   └── test_tool_manager.py
│   ├── test_prompts/
│   │   └── test_builder.py
│   └── test_tools/
│       ├── test_loader.py
│       └── test_registry.py
│
└── systemd/
    └── rcflow.service           # Systemd unit file
```
