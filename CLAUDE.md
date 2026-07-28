# CLAUDE.md — RCFlow Project Instructions

RCFlow is a background worker (Python/FastAPI, `src/`) that exposes WebSocket + REST APIs for turning natural-language prompts into tool executions (Claude Code, Codex, shell, …), plus a Flutter client (`rcflowclient/`) for Android and desktop. Full overview: `docs/design/README.md`; repo layout: `docs/design/project-structure.md`.

## Critical Rules

1. **Read `docs/design/README.md` before starting any new task in this project.** It is the design entry point and index — pick the relevant subdoc(s) under [`docs/design/`](docs/design/) for the area you're touching (HTTP API, WebSocket API, sessions, executors, database, mentions, slash commands, etc.). The design docs are the single source of truth for architecture, conventions, and decisions.
2. **Never use the built-in `EnterWorktree` tool.** Always use the `wt` CLI instead — it is bundled as a project dependency (`wtpython` in `pyproject.toml`) and available at `.venv/bin/wt` after `uv sync`. Use `wt new`, `wt attach`, `wt merge`, and `wt rm` for all worktree operations.
3. **Any changes to the system design must be reflected in the matching subdoc under `docs/design/`.** If a task modifies architecture, adds endpoints, changes data models, or alters any documented behavior, update the relevant `docs/design/<topic>.md` file as part of that task. Bump the `updated:` frontmatter date on any subdoc you edit. Update `docs/design/README.md` only when adding or removing whole topics.
4. Do not introduce new dependencies without documenting them in the Technology Stack table in `docs/design/README.md`.
5. Do not add or remove WebSocket endpoints, tool definition fields, or database models without updating `docs/design/websocket-api.md`, `docs/design/tools.md`, or `docs/design/database.md` respectively.
6. **Keep all endpoints well-documented with docstrings, type hints, and OpenAPI metadata** (summary, description, tags, response models) so that FastAPI can auto-generate accurate API documentation. Every endpoint must be self-documenting.
7. **Save all coding-agent-produced plans under `docs/plans/`.** Any plan, design sketch, or implementation outline the agent generates for this project goes in `docs/plans/` (gitignored — local working notes only, not checked in).

## Project Conventions

### Python (backend, `src/`)

- Python 3.12+ required
- Use `uv` for dependency management
- Use `ruff` for linting and formatting — docstrings on public modules/classes/functions are enforced (pydocstyle pep257 via ruff `D` rules; tests are exempt)
- Use `ty` for type checking. It is intentionally **not** a project dependency — it runs via `uvx ty check src/` (pre-commit, CI, and `just typecheck`). Do not add it to `pyproject.toml` or invoke it with `uv run`.
- Use `pytest` for testing
- Use SQLAlchemy 2.0 async style (not legacy 1.x patterns)
- Use FastAPI with async endpoints and WebSocket handlers
- All configuration via environment variables and `settings.json` in the data dir (a legacy `.env` is auto-migrated on first run; see `docs/design/configuration.md`)
- Type-annotate all public functions and class attributes

### Flutter (client, `rcflowclient/`)

- State management via Provider (`lib/state/`)
- `flutter_lints` defaults; CI runs `flutter analyze --fatal-warnings`
- No code generation — no build_runner, no `.g.dart`/`.freezed.dart` files

### Pre-commit hooks

`just dev` installs them. Every commit runs: ruff (auto-fix) + ruff-format + `uvx ty check src/` + a fast pytest subset (`tests/test_core`, `tests/test_executors`). If ruff modifies files during the commit, the commit aborts — restage and commit again.

## Common Commands

Run targets with `just <target>`. Run `just` with no arguments for the full annotated recipe list — bundling/packaging, emulator, uninstall, and cleanup targets live there and are not repeated here.

### Development

- `install` — install production dependencies (`uv sync`)
- `dev` — install with dev dependencies and set up pre-commit hooks
- `run` — start the server (`uv run rcflow run`)
- `run-gui` — start the worker GUI (dashboard + tray) in dev mode (`uv run --extra tray rcflow gui`)

### Code Quality

- `lint` — run ruff linter on `src/` and `tests/`
- `format` — auto-format and fix code with ruff
- `typecheck` — run ty type checker on `src/` (via `uvx` so the resolver mirrors CI)
- `check` — full local CI gate: ruff + ty + Python tests with coverage floor + flutter analyze + Flutter tests with coverage floor. Slow — use `lint`/`typecheck` for quick static checks.

### Testing

- `test` — run all tests (Python + Flutter; slow — prefer targeted runs while iterating)
- `coverage` — run Python tests with coverage report
- Single Python test: `uv run pytest tests/test_core/test_session.py::test_name` — async mode is auto (no marker needed), 60s per-test timeout, LLM calls are mocked (no API keys required)
- Single Flutter test: `cd rcflowclient && flutter test test/<path>_test.dart`
- `vm <command>` — live worker/client E2E verification on the Ubuntu VM (see `docs/design/vm-verification.md`; `just vm help` lists subcommands)

**Coverage floors are enforced** by `just check` and CI: Python ≥ 64% (`fail_under` in `pyproject.toml`), Flutter ≥ 14% (`rcflowclient/coverage_threshold.txt`). New code needs tests to keep the gates green. The floors are ratchets — raise them as coverage grows; never lower them.

On pull requests CI additionally runs **diff-cover**: new/changed lines must be ≥80% covered, so a large PR can't hide untested code behind the repo-wide average.

### Database Migrations

- `migrate` — apply all pending Alembic migrations
- `migrate-gen <msg>` — generate a new Alembic migration with the given message
- `migrate-down` — rollback the last migration

## Versioning

This project uses [Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH).

- **MAJOR** — breaking/incompatible changes
- **MINOR** — new features or significant enhancements (backward-compatible)
- **PATCH** — bug fixes, small improvements, refactors (backward-compatible)

Version bumps happen only when cutting a release, not per-feature.

The backend and client are versioned independently.

- **rcflow backend** — version lives in `pyproject.toml` → `version` field under `[project]`. Update this when backend code changes.
- **rcflowclient** — version lives in `rcflowclient/pubspec.yaml` → `version` field. Update this when client code changes.

## Changelog

`CHANGELOG.md` is user-facing. Follow these rules whenever you add or update an entry.

### What to compare

- **Compare against the most recent published release**, not against unreleased work-in-progress. The `[Unreleased]` section accumulates everything that ships in the next release; entries should describe how that next release differs from the previous published version, not how the current commit differs from a few commits ago.
- When cutting a release, rename `[Unreleased]` to the new version and date, then start a fresh empty `[Unreleased]` section above it.

### How to write entries

- **Audience is end users, not developers.** Describe behaviour and impact, not implementation.
- Avoid: file paths, function/class names, variable names, env-var names (unless the user sets them), SQL table names, library names, framework internals, stack traces, line counts, and "this commit changes X" mechanics.
- Prefer: what the user sees, what they can now do, what was broken before, what is fixed now.
- One sentence per bullet when possible. If a longer explanation is needed, keep it to one short paragraph.
- Lead with a short bold title, then a plain-language description.
- Tag the affected component in parentheses at the end: `(Backend)`, `(Client)`, or `(Backend + Client)`.
- Group entries under `### Added`, `### Changed`, `### Fixed`, `### Performance`, `### Removed`, `### Security` — in that order, omitting empty sections.
- Order entries within a section roughly by user-visible significance, not by commit order.

### Example

Bad (too technical):
> **Caveman mode not engaging for externally-installed Claude Code** — `_get_managed_config_overrides` gated caveman `--append-system-prompt` injection on `tool.managed`, so externally-installed Claude Code never received the system-prompt flag. Moved caveman injection outside the managed-only guard.

Good:
> **Caveman mode didn't engage for externally-installed Claude Code** — caveman now applies regardless of how Claude Code was installed (Backend).
