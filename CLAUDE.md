# CLAUDE.md — RCFlow Project Instructions

## Critical Rules

1. **Read `docs/design/README.md` before starting any new task in this project.** It is the design entry point and index — pick the relevant subdoc(s) under [`docs/design/`](docs/design/) for the area you're touching (HTTP API, WebSocket API, sessions, executors, database, mentions, slash commands, etc.). The design docs are the single source of truth for architecture, conventions, and decisions.
2. **Never use the built-in `EnterWorktree` tool.** It is permanently denied in `.claude/settings.local.json`. Always use the `wt` CLI instead — it is bundled as a project dependency (`wtpython` in `pyproject.toml`) and available at `.venv/bin/wt` after `uv sync`. Use `wt new`, `wt attach`, `wt merge`, and `wt rm` for all worktree operations.
3. **Any changes to the system design must be reflected in the matching subdoc under `docs/design/`.** If a task modifies architecture, adds endpoints, changes data models, or alters any documented behavior, update the relevant `docs/design/<topic>.md` file as part of that task. Bump the `updated:` frontmatter date on any subdoc you edit. Update `docs/design/README.md` only when adding or removing whole topics.
4. Do not introduce new dependencies without documenting them in the Technology Stack table in `docs/design/README.md`.
5. Do not add or remove WebSocket endpoints, tool definition fields, or database models without updating `docs/design/websocket-api.md`, `docs/design/tools.md`, or `docs/design/database.md` respectively.
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

## Justfile Targets

Run targets with `just <target>`. Run `just` with no arguments to list all available recipes.

### Development

- `install` — install production dependencies (`uv sync`)
- `dev` — install with dev dependencies and set up pre-commit hooks
- `run` — start the server (`uv run rcflow`)
- `run-gui` — start the worker GUI (dashboard + tray) in dev mode (`uv run rcflow gui`)

### Code Quality

- `lint` — run ruff linter on `src/` and `tests/`
- `format` — auto-format and fix code with ruff
- `typecheck` — run ty type checker on `src/`
- `check` — run all static checks (ruff + ty + flutter analyze)

### Testing

- `test` — run all tests (Python pytest + Flutter)
- `coverage` — run Python tests with coverage report

### Database Migrations

- `migrate` — apply all pending Alembic migrations
- `migrate-gen <msg>` — generate a new Alembic migration with the given message
- `migrate-down` — rollback the last migration

### Bundling / Packaging

- `bundle [FLAGS]` — build distributable package for the current platform
- `bundle-linux-worker [FLAGS]` — build Linux worker `.deb` package
- `bundle-linux-worker-install` — build and install Linux worker `.deb`
- `bundle-linux-client` — build Linux Flutter client `.deb`
- `bundle-linux-client-install` — build and install Linux Flutter client `.deb`
- `bundle-macos-worker [FLAGS]` — build macOS worker DMG (macOS only)
- `bundle-macos-worker-install` — build and install macOS worker DMG (macOS only)
- `bundle-macos-client` — build macOS Flutter client `.dmg` (macOS only)
- `bundle-macos-client-install` — build and install macOS Flutter client (macOS only)
- `bundle-windows-worker [FLAGS]` — build Windows worker installer (Windows only)
- `bundle-windows-worker-install` — build and install Windows worker (Windows only)
- `bundle-windows-client` — build Windows Flutter client `.exe` installer (Windows only)
- `bundle-windows-client-install` — build and install Windows Flutter client (Windows only)

### Flutter / Emulator (Unix/WSL2)

- `start-emulator` — start Windows Android emulator (cold boot) from WSL2
- `setup-emulator` — set up WSL2 ADB connection to Windows emulator
- `run-android` — run Flutter app on Android emulator in hot reload mode (connects to Windows emulator)
- `flutter-build` — build Flutter debug APK
- `flutter-release` — build Flutter release APK (split per ABI)
- `flutter-windows` — build Flutter Windows desktop app (Windows only)

### Cleanup

- `clean` — remove build artifacts, caches, and coverage files

## Versioning

This project uses [Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH).

- **MAJOR** — breaking/incompatible changes
- **MINOR** — new features or significant enhancements (backward-compatible)
- **PATCH** — bug fixes, small improvements, refactors (backward-compatible)

When a new feature is implemented, the version **must** be bumped as part of that same task.

The backend and client are versioned independently.

- **rcflow backend** — version lives in `pyproject.toml` → `version` field under `[project]`. Update this when backend code changes.
- **rcflowclient** — version lives in `rcflowclient/pubspec.yaml` → `version` field. Update this when client code changes.
