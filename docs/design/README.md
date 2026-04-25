---
updated: 2026-04-26
---

# RCFlow Design

Entry point for RCFlow architecture, conventions, and decisions. **Read this first** before starting any task — then jump to the relevant subdoc.

> CLAUDE.md (project root) wires this file in as the design source of truth. AI coding agents and humans both land here.

---

## Overview

RCFlow is a background server running on Linux, macOS, or Windows that exposes a WebSocket-based interface for executing actions on the host machine via natural language prompts. Users connect from client applications (Android and Windows/macOS/Linux desktop), send text prompts, and the server uses an LLM (Anthropic Messages API, AWS Bedrock, or OpenAI Chat Completions API) to interpret prompts into tool calls. Tools are pluggable and defined via JSON files. Results stream back to the client in real time.

## How to Use This Doc

This file is the **index**. Detailed sections live in sibling files (`./architecture.md`, `./sessions.md`, …) — each topic is its own file so you can read just the part relevant to your task instead of scanning a 3000-line monolith.

**For task-driven lookups:**

| If you're asking… | Open |
|---|---|
| What does endpoint X do? | [HTTP API](http-api.md) |
| What WebSocket message is `Y`? | [WebSocket API](websocket-api.md) |
| How does the session lifecycle / queue / token tracking work? | [Sessions](sessions.md) |
| How is a Claude Code / Codex / Worktree subprocess launched? | [Executors](executors.md) |
| What columns are on table `Z`? | [Database](database.md) (per-table H3 anchors) |
| What env var controls feature `F`? | [Configuration](configuration.md) |
| How does `@`/`#`/`$` mention work? | [Mentions](mentions.md) |
| How does `/` slash command work? | [Slash Commands](slash-commands.md) |
| How does the Flutter client lay out / route messages / persist drafts? | [Architecture](architecture.md) |
| How is a Linear issue synced/linked? | [Linear Integration](linear.md) |
| How does telemetry roll up? | [Telemetry](telemetry.md) |
| Where do permission prompts come from? | [Permissions](permissions.md) |
| How is a release built / signed / installed? | [Deployment](deployment.md) |

## Index

| Document | Covers |
|----------|--------|
| [Architecture](architecture.md) | High-level flow, request lifecycle, Flutter client multi-platform / split view / terminals / multi-worker, deep-link scheme |
| [HTTP API](http-api.md) | All REST endpoints, grouped by area, with auth + descriptions |
| [WebSocket API](websocket-api.md) | `/ws/input/text` & `/ws/output/text` protocols, all message types, queued/dequeued events, subscription, sessions/tasks/artifacts |
| [Sessions](sessions.md) | Lifecycle states, activity state, types, pre-planning, storage, todos, titles, queued user messages, token tracking |
| [Permissions](permissions.md) | Interactive permission approval flow, scopes, risk classification, edge cases |
| [Prompt Templates](prompt-templates.md) | Jinja base template, global prompt override, caveman mode |
| [Mentions](mentions.md) | `@ProjectName`, `#ToolName`, `$filename` context blocks |
| [Slash Commands](slash-commands.md) | `/`-triggered command palette, plugin management API |
| [Direct Tool Mode](direct-tool-mode.md) | `LLM_PROVIDER=none` operating mode |
| [Tools](tools.md) | Pluggable tool JSON schema, agent prompt format, tool definition fields, tool-management service, per-tool settings isolation |
| [Executors](executors.md) | Claude Code (PTY/pipe), Codex CLI, Worktree executor implementations |
| [Database](database.md) | All tables (sessions, tasks, artifacts, telemetry, queue, drafts, Linear) with per-table anchors |
| [Configuration](configuration.md) | All env vars, remote config endpoints, UPnP / NAT-PMP networking |
| [Linear Integration](linear.md) | Service, REST endpoints, WS messages, client UI |
| [Telemetry](telemetry.md) | Three-phase pipeline (raw → minutely → retention), REST endpoints |
| [Project Structure](project-structure.md) | Repo layout |
| [Deployment](deployment.md) | Platform support, systemd / Windows GUI / macOS menu bar, bundling, code signing |

## Technology Stack

| Component            | Technology                    |
|----------------------|-------------------------------|
| Language             | Python 3.12+                  |
| Package Manager      | uv                            |
| Web Framework        | FastAPI                       |
| ORM                  | SQLAlchemy 2.0 (async)        |
| Database             | SQLite (default) or PostgreSQL |
| LLM                  | Anthropic Messages API, AWS Bedrock, or OpenAI Chat Completions API |
| Prompt Templates     | Jinja2                        |
| Linting / Formatting | Ruff                          |
| Type Checking        | ty                            |
| Testing              | pytest                        |
| Config               | Environment variables + settings.json       |
| OS                   | Linux, Windows, macOS         |
| Client Platforms     | Android, Windows (desktop)    |
| Android Keep-Alive   | flutter_foreground_task       |
| File Picker          | file_picker (file attachments) |
| Bundling             | PyInstaller (self-contained distributable) |
| Windows GUI          | CustomTkinter (modern ctk widgets, system dark/light mode) |
| Windows Tray         | pystray + Pillow (system tray icon)        |
| Windows Terminal PTY | pywinpty (ConPTY wrapper)                   |
| Windows Installer    | Inno Setup 6 (setup.exe builder)           |
| macOS GUI            | CustomTkinter (Aqua-compatible, settings panel + inline log viewer) |
| macOS Menu Bar       | PyObjC (NSStatusBar / NSStatusItem, main thread via tkinter after()) |
| GUI Shared Core      | src.gui_core (ServerManager, LogBuffer, poll_server_status) |
| GUI Design Tokens    | src.theme (colours, typography, spacing constants) |
| macOS Distribution   | PyInstaller `.app` bundle wrapped in a styled DMG        |
| macOS Entitlements   | `scripts/rcflow_macos.entitlements` (hardened runtime, no sandbox) |
| Worktree Manager     | wtpython (WorktreeManager library)         |
| Telemetry Charts     | fl_chart (Flutter line/bar charts)         |
| Client Deep Links    | app_links (rcflow:// URL scheme handling) |
| Port Forwarding      | miniupnpc (UPnP IGD — optional, default off) |
| VPN Port Forwarding  | stdlib UDP (RFC 6886 NAT-PMP — optional, default off) |
| Bedrock Model Listing | aioboto3 (async `bedrock.list_foundation_models` for the dynamic model catalog) |

## Future Considerations

- **Sandboxed execution**: Docker-based tool execution for untrusted tools
- **Python callable tools**: Another executor type for direct Python function invocation
- **Hot-reload tools**: Watch the `tools/` directory for changes without restart
- **Multi-user support**: JWT-based auth with user accounts and per-user sessions
