# CLAUDE.md — RCFlow Project Instructions

## Critical Rules

1. **Read `Design.md` before starting any new task in this project.** The design document is the single source of truth for architecture, conventions, and decisions.
2. **Never use the built-in `EnterWorktree` tool.** It is permanently denied in `.claude/settings.local.json`. Always use the `wt` CLI instead — it is bundled as a project dependency (`wtpython` in `pyproject.toml`) and available at `.venv/bin/wt` after `uv sync`. Use `wt new`, `wt attach`, `wt merge`, and `wt rm` for all worktree operations.
3. **Any changes to the system design must be reflected in `Design.md`.** If a task modifies architecture, adds endpoints, changes data models, or alters any documented behavior, update `Design.md` as part of that task.
4. Do not introduce new dependencies without documenting them in `Design.md` under the Technology Stack section.
5. Do not add or remove WebSocket endpoints, tool definition fields, or database models without updating the corresponding sections in `Design.md`.
6. **Keep all endpoints well-documented with docstrings, type hints, and OpenAPI metadata** (summary, description, tags, response models) so that FastAPI can auto-generate accurate API documentation. Every endpoint must be self-documenting.

## Project Conventions

- Python 3.12+ required
- Use `uv` for dependency management
- Use `ruff` for linting and formatting
- Use `ty` for type checking
- Use `pytest` for testing
- Use SQLAlchemy 2.0 async style (not legacy 1.x patterns)
- Use FastAPI with async endpoints and WebSocket handlers
- All configuration via environment variables / `.env` file
- Type-annotate all public functions and class attributes

## Versioning

This project uses [Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH).

- **MAJOR** — breaking/incompatible changes
- **MINOR** — new features or significant enhancements (backward-compatible)
- **PATCH** — bug fixes, small improvements, refactors (backward-compatible)

When a new feature is implemented, the version **must** be bumped as part of that same task.

The backend and client are versioned independently.

- **rcflow backend** — version lives in `pyproject.toml` → `version` field under `[project]`. Update this when backend code changes.
- **rcflowclient** — version lives in `rcflowclient/pubspec.yaml` → `version` field. Update this when client code changes.
