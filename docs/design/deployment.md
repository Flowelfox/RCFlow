---
updated: 2026-04-27
---

# Platform Support, Deployment & Bundling

Supported platforms, deployment topologies (systemd, Windows GUI/tray, macOS menu bar), bundling pipeline, and code-signing.

**See also:**
- [Configuration](configuration.md) — env vars consumed at deployment time
- [Architecture](architecture.md#add-to-client-deep-link) — `rcflow://` deep-link scheme registration

---

## Platform Support

RCFlow supports **Linux (x64, arm64)** and **Windows (x64)**.

### Platform-Specific Behavior

| Feature | Linux | Windows |
|---------|-------|---------|
| Managed tools directory | `~/.local/share/rcflow/tools/` | `%LOCALAPPDATA%\rcflow\tools\` |
| Default shell | `/bin/bash` | `powershell.exe` |
| Claude Code binary | `claude` | `claude.exe` |
| Codex binary | `codex` | `codex.exe` |
| Codex archive format | `.tar.gz` | `.zip` |
| Process isolation | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP` |
| Process tree kill | `os.killpg(SIGKILL)` | `taskkill /T /F /PID` |
| Claude Code stdin/stdout | PTY master fd (`pty.openpty`) | asyncio pipe (`PIPE`) |
| Background mode | systemd service **and** GUI dashboard + tray (`rcflow gui`) | GUI window (`rcflow gui`) / system tray (`rcflow tray`) | Menu bar app (`rcflow gui`) |
| Auto-start | systemd enable, **or** XDG `~/.config/autostart/rcflow-worker.desktop` for the user-level GUI | Registry `HKCU\...\Run` key | LaunchAgent `~/Library/LaunchAgents/com.rcflow.worker.plist` |

### Database

SQLite is the default database — no external server required. The database file is created automatically at the path specified in `DATABASE_URL` (default: `./data/rcflow.db`). SQLite WAL mode and foreign keys are enabled automatically.

For heavier workloads or multi-backend deployments, PostgreSQL is supported. Install the `postgres` extra (`pip install rcflow[postgres]` or `uv pip install rcflow[postgres]`) and set `DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/rcflow`.

### Cross-Platform Process Management

Process creation and termination are abstracted in `src/utils/process.py`:

- `new_session_kwargs()` — returns the correct kwargs to isolate child process trees (`start_new_session` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows).
- `kill_process_tree()` — kills a process and all its children (`os.killpg` on POSIX, `taskkill /T /F` on Windows).

Both `ClaudeCodeExecutor` and `CodexExecutor` use these helpers.

`src/utils/pty_utils.py` (Unix-only) provides PTY helpers used by `ClaudeCodeExecutor` in PTY mode:

- `configure_raw(fd)` — sets a PTY slave fd to raw mode (no echo, no `OPOST`, no `ICANON`).
- `set_winsize(fd, rows, cols)` — configures terminal dimensions via `TIOCSWINSZ`.
- `PtyLineReader` — async line reader over a PTY master fd using `loop.add_reader`.
- `strip_ansi(text)` — strips ANSI/VT100 escape sequences from decoded output.

---

## Deployment

### Development

```bash
uv run rcflow                    # or: uv run rcflow run — starts the server
```

### Production (Windows — GUI + System Tray)

`rcflow gui` (or `rcflow tray`, which delegates to it) launches a combined tkinter window and system tray application (`src/gui/windows.py`). This is the default mode for frozen Windows builds. The server runs as a subprocess — closing the window minimizes to the system tray; double-clicking the tray icon restores the window. "Quit" from the tray stops the server and exits. Only one instance may run at a time: a file lock (`<data_dir>/.worker.lock`, `msvcrt.locking`) is held for the process lifetime, and a second `rcflow gui` invocation uses the loopback IPC channel (see "Singleton IPC" below) to reveal the running instance's dashboard before exiting 0.

**Features:**
- **Server settings** — IP address and port text fields, pre-populated from `settings.json` configuration.
- **Start/Stop button** — Starts the server as a child subprocess (`rcflow run` with `CREATE_NO_WINDOW`). Settings fields are disabled while the server is running.
- **Status indicator** — Shows "Running" (green), "Stopped" (gray), "Starting..."/"Stopping..." (yellow), or error messages (red).
- **Instance details panel** — Displays bound address, uptime (HH:MM:SS), active session count, and backend ID. Session count and backend ID are fetched from the `/api/info` endpoint every 5 seconds.
- **Log output** — Scrollable dark-themed text area with real-time display of the server subprocess stdout. ERROR/CRITICAL lines are highlighted red, WARNING lines orange. Auto-scrolls when at the bottom; capped at 5,000 lines.
- **System tray icon** — Shows server status. Right-click menu: status line, "Open" (restores window), "Start with Windows" toggle (Windows registry autostart), "Quit".

**Architecture:**
- The GUI process spawns `rcflow run` as a child subprocess with stdout/stderr piped. A reader thread consumes subprocess output and feeds it into a `queue.Queue` that the tkinter main loop drains into the text widget.
- `pystray` runs in a background daemon thread; tkinter runs in the main thread.
- Closing the window (X button) calls `root.withdraw()` to hide the window — the tray icon remains and the server keeps running.
- Double-clicking the tray icon calls `root.deiconify()` to restore the window.
- "Quit" from the tray menu terminates the server subprocess (graceful with 10s timeout, then kill), stops the tray icon, and destroys the tkinter window.
- If `pystray`/`Pillow` are not installed, the GUI still works but without a tray icon — closing the window exits the application.
- Port availability is checked before starting (same socket-bind check as `rcflow run`).
- Environment variables `RCFLOW_HOST` and `RCFLOW_PORT` are set in the subprocess environment so the server picks up the GUI-configured values.
- The server auto-starts on application launch.

**Autostart:** Registry key `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\RCFlow` stores `"<exe>" gui`. The `rcflow tray` command is kept for backwards compatibility but delegates to `run_gui()`.

**Icon:** `assets/tray_icon.ico` (generated by `scripts/generate_icon.py`, same design as the client app icon). Copied into the bundle root as `tray_icon.ico`. The frozen build loads it from `{install_dir}/tray_icon.ico`; dev builds look in `{project_root}/assets/tray_icon.ico`. Fallback: generates a blue rounded square with "RC" text.

### Production (macOS — Menu Bar App)

`rcflow gui` (or `rcflow tray`) launches the macOS menu bar app (`src/gui/macos.py`). This is the default mode for frozen macOS builds. The app runs as an `LSUIElement` (no Dock icon, lives entirely in the macOS menu bar). The settings panel **is shown on launch**; the close button (⌘W / X) hides it back to the menu bar while the server keeps running. A single-instance file lock (`fcntl.flock` on `~/Library/Application Support/RCFlow/.worker.lock`) plus the loopback IPC channel (see "Singleton IPC" below) ensures a second launch reveals the existing window instead of failing silently.

**Menu bar icon menu:**
- Status line (non-clickable) — "RCFlow Worker: Running" or "RCFlow Worker: Stopped"
- **Start Server / Stop Server** — Toggles the server subprocess; label updates to reflect running state
- **Open Settings…** — Reveals the tkinter settings panel
- **Copy Token** — Copies the API key to the macOS pasteboard (works while window is hidden)
- **Start with macOS** (checkable) — Toggles LaunchAgent-based autostart
- **Quit** — Stops the server subprocess and exits

**Settings panel features:**
- **Server settings** — IP address, port, WSS Enabled checkbox; pre-populated from `settings.json`.
- **Start/Stop button** — Starts the server as a child subprocess (`rcflow run`).
- **Copy Token button** — Copies the API key to the macOS pasteboard.
- **Status indicator** — "Running (WSS)", "Stopped", "Starting…", error (red).
- **Instance details** — Bound address, uptime, active session count, backend ID (polled from `/api/info` every 5 s).
- **Inline log viewer** — Scrollable text area (Menlo 11 pt) showing live server subprocess stdout. Colors follow system appearance: dark background in Dark Mode, light background in Light Mode. ERROR/CRITICAL lines red, WARNING lines amber.

**Architecture:**
- tkinter runs on the main thread using the native macOS `aqua` theme (Aqua controls).
- `NSStatusItem` is created via PyObjC (`AppKit.NSStatusBar`) entirely on the main thread. A `_TrayDelegate` NSObject subclass (registered with the ObjC runtime at module import time) handles `NSMenuItem` action callbacks. The status item is initialised via `root.after(0, …)` so it runs once the tkinter event loop has started (NSApp is running by then). No background thread is needed — this avoids the `NSUpdateCycleInitialize() is called off the main thread` crash that occurs when `NSApplication.run()` is called from a non-main thread on macOS 26+.
- The settings window is shown on launch. "Open Settings…" (and the IPC `SHOW` path) calls `root.deiconify()` + `root.lift()` + brief topmost-flag nudge so the panel is raised to the front even though the app is an LSUIElement.
- Closing the window (⌘W / X button) hides it back to the menu bar (`root.withdraw()`). The server keeps running.
- "Quit" from the menu bar is deferred via a `_quit_requested` flag consumed on the Tk event loop — the ObjC callback itself must not call `stop_sync()` directly, because doing so from inside the NSMenu modal tracking loop blocks AppKit and produces a stuck beachball cursor over the menu bar region. Once the flag is drained, `_on_tray_quit` removes the NSStatusItem, tears down the IPC listener, stops the server subprocess (10 s timeout then kill), and destroys the window.
- System appearance (light/dark) is detected once at startup via `tk::mac::isDarkMode`; log widget colors are set accordingly.
- Log draining, server HTTP polling, and subprocess I/O all use the same queue + timer pattern as the Windows GUI.

**Autostart:** Writes a `LaunchAgent` plist to `~/Library/LaunchAgents/com.rcflow.worker.plist` with `RunAtLoad=true`, `KeepAlive=false`, `ProcessType=Interactive`. Does **not** call `launchctl load` / `unload` — placing the plist is sufficient for launchd to pick it up on next login, and avoiding `load` prevents a duplicate instance from being spawned while the app is already running; avoiding `unload` prevents SIGTERM from killing the current app when the user toggles autostart off. The plist passes `--minimized` in `ProgramArguments` so the login-triggered launch starts with the dashboard hidden (tray icon only) — user-initiated launches omit the flag and the dashboard pops up normally. The same flag is added to the Windows autostart registry value (`"<exe>" gui --minimized`).

**Icon:** `assets/tray_icon.icns`. Copied into `Contents/Resources/tray_icon.icns` inside the `.app` bundle (standard macOS resource location). Also bundled as a PyInstaller data file at `tray_icon.icns` (accessible via `get_install_dir()`). Fallback: generates a blue rounded square with "RC" text using Pillow.

**Note on LSUIElement apps:** Because `LSUIElement = true` removes the Dock icon, the app cannot be activated normally via Cmd+Tab or clicking the Dock. The window is shown/hidden exclusively through the menu bar icon. If PyObjC is unavailable and the menu bar icon cannot be created, the settings window remains visible as a fallback so the app is not completely invisible.

**Crash handling:** `run_gui_macos()` is wrapped in a top-level `try/except` that writes tracebacks to `~/Library/Logs/rcflow-worker-crash.log` and shows a `tkinter.messagebox` error dialog before re-raising. This prevents silent exits in windowed (`console=False`) frozen builds where stderr goes nowhere.

**Singleton IPC (macOS + Windows):** The first GUI process binds a loopback-only TCP listener on an ephemeral port and writes the chosen port to `<data_dir>/.worker.ipc` (permissions `0o600` on POSIX). A second `rcflow gui` invocation detects the held file lock, reads `.worker.ipc`, connects to `127.0.0.1:<port>`, and sends the literal string `SHOW\n`; the running instance sets the existing `_show_window_requested` flag (macOS) or schedules `_show_window` on the Tk main thread (Windows) and the accept loop closes the connection. The second process then exits 0 without starting a second server. Both helpers (`start_ipc_server`, `send_show_to_existing`, `remove_ipc_file`) live in `src/gui/core.py` so macOS and Windows share one implementation. The IPC file is removed on graceful quit.

**Orphaned-server recovery:** The GUI spawns the backend as a `subprocess.Popen` child. If the GUI crashes (e.g. a Cocoa re-entrancy after macOS auto-lock / sleep-wake), the subprocess is reparented to `launchd` and would otherwise keep serving clients with no UI to stop it. Two mechanisms defend against this:

1. **Parent-death watchdog in the server** — `ServerManager.start()` passes `RCFLOW_PARENT_PID=<gui_pid>` to the child. `_cmd_run` in `src/__main__.py` starts a daemon thread (`_install_parent_death_watchdog`) that polls `os.kill(parent_pid, 0)` every 2 s and sends `SIGTERM` to its own pid when the parent is gone, letting uvicorn shut down gracefully. The env var is absent for systemd / launchd daemon installs, in which case the watchdog is disabled.
2. **Pidfile-based adoption on GUI relaunch** — `ServerManager.start()` writes the child PID to `<data_dir>/.worker.pid` (resolved to `~/Library/Application Support/rcflow/.worker.pid` on macOS frozen builds). `stop_sync()` / `clear()` delete it on graceful shutdown. On launch, both GUIs call `ServerManager.adopt_if_running()` *before* attempting to spawn a new server: if the pidfile references a live pid, the manager records it as **adopted** (no `Popen` handle — the process is not our child). `is_running()` / `stop()` / `stop_sync()` all handle the adopted path by tracking the pid directly and terminating it via raw signals (`_kill_pid` falls back to `TerminateProcess` on Windows). The GUI shows "Running (WSS) — recovered" in the status pill so the user knows the backend was picked up from a previous crashed session and can stop it normally from the menu.

### Production (Linux — GUI Dashboard + Tray)

The same CustomTkinter dashboard + pystray tray icon used on Windows is reused on Linux via `src/gui/windows.py`.  `rcflow gui` (or `rcflow tray`, which delegates to it) launches the dashboard on the user's session; the worker `.deb` ships an XDG `.desktop` entry (`/usr/share/applications/rcflow-worker.desktop`) so the GUI shows up in GNOME Activities / KDE app menus alongside the existing **RCFlow Client** entry.  The `rcflow-worker` icon is installed into the hicolor theme at standard sizes (48 / 64 / 128 / 256 / 512 px).

The Windows GUI codebase is platform-aware so most of the dashboard, log viewer, IPC singleton, and updater logic is shared verbatim across the Win / Linux branches:

- **Tray backend** — `pystray` chooses `AyatanaAppIndicator3` first and `_xorg` last.  The `_xorg` backend opens its own Xlib connection from a daemon thread and races Tk on `xcb_io.c:157 ... !xcb_xlib_unknown_seq_number`, so we skip the tray entirely when AppIndicator GI bindings are unreachable (`_linux_appindicator_available()` in `src/gui/windows.py`).  Closing the window in window-only mode quits the app, mirroring the no-tray fallback that already exists on Windows when `pystray` is missing.
- **Window icon** — Tk's `iconbitmap` accepts the Windows `.ico` only on Windows; on Linux we install a PNG copy (`tray_icon.png`, generated from the same `.ico` at bundle time) via `iconphoto`.
- **Autostart** — Mirrors Windows' `HKCU\...\Run` value with an XDG `~/.config/autostart/rcflow-worker.desktop` file that runs `rcflow gui --minimized` at login.  Toggled from the tray menu's **Start with Linux** entry.
- **`XInitThreads` shim** — Called via `ctypes.CDLL("libX11.so.6").XInitThreads()` before any X traffic, since modern libxcb (Ubuntu 25.04+) aborts the process unless threading is initialised before the first request.
- **Headless escape hatch** — `RCFLOW_DISABLE_TRAY=1` forces window-only mode at runtime; useful when the user has not installed an AppIndicator extension or wants to suppress the tray icon entirely.

**Recommends (deb control)** — `libtcl9.0 | libtcl8.6`, `libtk9.0 | libtk8.6`, `libxcb1`, `libxft2`, `libxss1`, `libfontconfig1`, `gir1.2-ayatanaappindicator3-0.1`.  Headless installs (servers, containers, WSL without X) can ignore the recommends; desktop installs already pull them in via the default GNOME / KDE meta-packages.

**Note on Ubuntu 25.04 + bundled tk** — PyInstaller's bundled tcl/tk on some Linux hosts fails the libxcb 1.17+ sequence-number assertion even with `XInitThreads()` called early; this is a known upstream interaction.  The systemd-managed headless mode is unaffected and remains the recommended deployment for headless servers.

### Production (systemd — Linux)

```ini
# systemd/rcflow.service
[Unit]
Description=RCFlow Action Server
After=network.target

[Service]
Type=simple
User=rcflow
WorkingDirectory=/opt/rcflow
# Settings loaded from /opt/rcflow/settings.json by the application
ExecStart=/opt/rcflow/.venv/bin/python -m rcflow
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable rcflow
sudo systemctl start rcflow
sudo journalctl -u rcflow -f     # View logs
```

### Auto-Update (Worker GUI Only)

The worker GUI (Windows tray, macOS menu bar, future Linux dashboard) polls the GitHub Releases API on launch to surface newer versions. The headless `rcflow run` entry point — including systemd, Docker, and any other non-GUI deployment — never instantiates the updater and never makes outbound HTTP calls for update discovery; package managers handle those installs.

**How it works:**

- On launch, the GUI calls `GET https://api.github.com/repos/Flowelfox/RCFlow/releases/latest` once per 24-hour TTL (cached in `settings.json`). Manual "Check for Updates" buttons in the dashboard and tray bypass the cache.
- The fetcher picks the asset whose name ends in the platform/arch suffix produced by the bundle pipeline: `linux-worker-amd64.deb`, `linux-worker-arm64.deb`, `windows-worker-amd64.exe`, `macos-worker-arm64.dmg`, `macos-worker-x86_64.dmg`. macOS arch is taken from `platform.machine()` so a Rosetta 2 install of the x86_64 binary correctly fetches the x86_64 update.
- When a newer version is available, an amber banner appears above the status pill and a "Update available — install vX.Y.Z" item appears near the top of the tray/menu-bar menu. The user clicks **Download & Install**, the installer is streamed to a per-platform cache dir (`%TEMP%\rcflow-updates`, `~/Library/Caches/rcflow/updates`, `~/.cache/rcflow/updates`), and a modal asks whether to launch it (`os.startfile` / `open` / `xdg-open`) or reveal it in the file manager.
- The worker process keeps running during the download. The installer is responsible for prompting the user to close the worker before overwriting the binary.
- Auto-checks can be turned off from the dashboard's **Updates** card or by setting `RCFLOW_UPDATE_AUTO_CHECK=false`. Manual checks still work in either case.
- Versions are normalized (`v1.2.3+45` → `1.2.3`) and compared by numeric dot-segments, so `1.10.0` is correctly newer than `1.9.0`. Dismissing a version hides the banner until a strictly newer version appears.
- Dev (unfrozen) builds skip the auto-check on launch when no `rcflow` package version is resolvable, but the manual "Check for Updates" button still works for testing.

No checksum or signature verification is performed beyond TLS — the user-facing install flow matches the existing Flutter client. Stalled `*.partial` downloads older than one day are garbage-collected on each GUI startup.

---

## Bundling & Distribution

RCFlow is distributed as a self-contained package built with PyInstaller. End users download a single archive, run an install script, and get RCFlow running as a system service.

### Build

All artifacts follow the naming convention `rcflow-v{version}-{platform}-{component}-{arch}.{ext}` where `component` is `worker` (backend) or `client` (Flutter desktop/mobile).

| Target | Command | Output |
|--------|---------|--------|
| Linux worker (.deb) | `just bundle-linux-worker` | `dist/rcflow-v{version}-linux-worker-amd64.deb` |
| Linux client (.deb) | `just bundle-linux-client` | `dist/rcflow-v{version}-linux-client-amd64.deb` |
| macOS worker arm64 (DMG) | `just bundle-macos-worker` *(on Apple Silicon)* | `dist/rcflow-v{version}-macos-worker-arm64.dmg` |
| macOS worker amd64 (DMG) | `just bundle-macos-worker` *(on Intel Mac)* | `dist/rcflow-v{version}-macos-worker-amd64.dmg` |
| macOS client arm64 (DMG) | `just bundle-macos-client` *(on Apple Silicon)* | `dist/rcflow-v{version}-macos-client-arm64.dmg` |
| macOS client amd64 (DMG) | `just bundle-macos-client` *(on Intel Mac)* | `dist/rcflow-v{version}-macos-client-amd64.dmg` |
| Windows worker (.exe) | `just bundle-windows-worker` | `dist/rcflow-v{version}-windows-worker-amd64.exe` |
| Windows client (.exe) | `just bundle-windows-client` | `dist/rcflow-v{version}-windows-client-amd64.exe` |
| Android client (APKs, CI only) | *(release.yml / build.yml)* | `dist/rcflow-v{version}-android-client-{arch}.apk` |

Backend build script: `scripts/bundle.py`. Requires PyInstaller (`uv add --dev pyinstaller`). Cross-compilation is not supported — build on the target platform. Client targets build the Flutter desktop app (`rcflowclient`) for the respective platform.

The `bundle-linux-client` target requires the following system packages in addition to Flutter SDK 3.11+:

```
sudo apt-get install cmake ninja-build clang pkg-config libgtk-3-dev
```

- `cmake` — CMake 3.13+ (Flutter's Linux build system)
- `ninja-build` — Ninja build tool (the `ninja` binary)
- `clang` — C/C++ compiler (`clang++` is Flutter's default on Linux)
- `pkg-config` — used to locate GTK and GLib libraries
- `libgtk-3-dev` — GTK+-3.0 headers and shared libraries

The recipe checks for these binaries at startup and prints the install command above if any are missing.

The `bundle-linux-worker` target builds a `.deb` package that installs RCFlow to `/opt/rcflow` with a systemd service. Requires `dpkg-deb` (standard on Debian/Ubuntu). Install with `sudo dpkg -i dist/rcflow_*.deb`.

The `bundle-windows-worker` target builds a windowed (no console) executable with GUI + system tray support and compiles a `setup.exe` installer using Inno Setup 6. Requires Inno Setup 6 installed on the build machine (`iscc.exe` on PATH or in default location).

### Bundle Contents

The archive contains: the PyInstaller executable + runtime (`_internal/`), tool JSON definitions (`tools/`), alembic migrations (`migrations/`), prompt templates (`templates/`), install/uninstall scripts, systemd service template (Linux), tray icon (Windows), and a `VERSION` file.

### Installation

#### One-line install (Linux / macOS)

Worker and client can both be installed via a `curl | sh` pattern that detects the platform and architecture, resolves the latest GitHub release, downloads the correct artifact, and runs the platform installer:

```bash
# Worker (backend server)
curl -fsSL https://rcflow.app/get-worker.sh | sh

# Desktop client
curl -fsSL https://rcflow.app/get-client.sh | sh
```

Pin a version with `RCFLOW_VERSION=0.35.0` and pass options with `sh -s -- --port 8080 --unattended`. The scripts are hosted at `scripts/get-worker.sh` and `scripts/get-client.sh` in the repository.

**`get-worker.sh`** — Downloads the `.tar.gz` (Linux) or `.dmg` (macOS) worker artifact. On Linux it delegates to the bundled `install.sh` inside the tarball. On macOS it mounts the DMG, extracts the `.app` contents, and performs a headless install (copies to `~/.local/lib/rcflow`, creates a LaunchAgent, symlinks to `~/.local/bin/rcflow`). Passes `--unattended` automatically when stdin is not a terminal.

**`get-client.sh`** — Downloads the `.deb` (Linux) or `.dmg` (macOS) client artifact. On Linux it installs via `dpkg -i`. On macOS it copies the `.app` to `/Applications`.

Environment variables: `RCFLOW_VERSION` (pin version), `RCFLOW_REPO` (override GitHub owner/repo), `INSTALL_DIR` (override install prefix, worker only).

#### Manual installation

**Linux (.deb):** `sudo dpkg -i rcflow_*.deb` — installs to `/opt/rcflow/`, creates `rcflow` system user, sets up systemd service, generates `settings.json` on first server start. Remove with `sudo apt remove rcflow` (or `--purge` to also delete data).

**Linux (tar.gz/manual):** `sudo ./install.sh` — installs to `/opt/rcflow/`, creates `rcflow` system user, sets up systemd service, generates `settings.json` with random API key, runs migrations.

**Windows (zip/manual):** `.\install.ps1` (as Administrator) — installs to `C:\RCFlow\`, downloads NSSM, registers Windows Service, generates `settings.json` with random API key, runs migrations, creates firewall rule.

**Windows (setup.exe):** Run the Inno Setup installer — installs to `%PROGRAMFILES%\RCFlow\` (user-level, no admin required), runs migrations, optionally registers "Start with Windows" autostart, and optionally launches the GUI. `settings.json` is generated automatically on first server start. The GUI runs the server as a background subprocess and provides a window with server controls, live logs, and a system tray icon.

All install scripts are idempotent — safe to run again for upgrades. Existing `settings.json` and `data/` are preserved.

### Path Resolution

The `src/paths.py` module provides functions that resolve paths correctly in both development (source) and frozen (PyInstaller) environments:

- `get_bundle_dir()` — `sys._MEIPASS` when frozen, project root otherwise
- `get_install_dir()` — directory containing the executable (read-only in macOS `.app` bundles)
- `get_data_dir()` — user-writable data directory for settings, logs, certs, and database:
  - **macOS frozen** → `~/Library/Application Support/rcflow/`
  - **Windows** → `%LOCALAPPDATA%/rcflow/`
  - **Linux / dev** → same as `get_install_dir()`
- `get_default_tools_dir()` — `{install_dir}/tools`
- `get_migrations_dir()` — `{install_dir}/migrations` when frozen
- `get_templates_dir()` — `{_MEIPASS}/templates` when frozen
- `get_alembic_ini()` — `{install_dir}/alembic.ini` when frozen

### CLI Commands

The `rcflow` entry point supports subcommands relevant to bundled operation:

- `rcflow` / `rcflow run` — Start the server (headless)
- `rcflow gui` — Run with GUI window + system tray (default on frozen Windows / macOS builds)
- `rcflow tray` — Alias for `rcflow gui` (backwards compatibility)
- `rcflow migrate [revision]` — Run database migrations (default: `head`)
- `rcflow version` — Print version
- `rcflow info` — Print server configuration (bind address, port, WSS status)
- `rcflow api-key` — Print the current API key
- `rcflow set-api-key <value>` — Save a new API key

On frozen Windows builds, the default command (no subcommand) launches `gui` mode.

### Code Signing

All release artifacts are signed to prevent OS security warnings and verify integrity. Signing is optional (controlled by `--sign` flag in `bundle.py`) and requires platform-specific credentials via environment variables.

| Platform | Tool | What is signed |
|----------|------|---------------|
| **Windows** | `signtool.exe` (Authenticode) | `rcflow.exe`, `setup.exe` (Inno Setup installer) |
| **macOS** | `codesign` + `notarytool` | `rcflow` binary, `.pkg` installer, `RCFlow.app` client bundle |
| **Linux** | GPG detached signatures | `.tar.gz` archive, `.deb` package |
| **Android** | Gradle `signingConfigs` | Release APK (via `key.properties` keystore) |

**Environment variables for signing:**

- **Windows:** `SIGN_CERT_PATH` (`.pfx` path), `SIGN_CERT_PASSWORD`, `SIGN_TIMESTAMP_URL` (default: `http://timestamp.digicert.com`)
- **macOS:** `SIGN_IDENTITY` (Developer ID Application), `SIGN_INSTALLER_IDENTITY` (Developer ID Installer), `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD`
- **Linux:** `GPG_KEY_ID`
- **Android:** `key.properties` file in `rcflowclient/android/` pointing to a release keystore (gitignored)

**Build commands with signing:**

```bash
just bundle --sign                          # Current platform
just bundle-linux-worker --sign             # Linux .deb + GPG
just bundle-macos-worker --sign             # macOS .pkg + notarization
just bundle-windows-worker --sign           # Windows setup.exe + Authenticode
```

**CI/CD:** The `release.yml` GitHub Actions workflow triggers on version tags (`v*.*.*`), builds all platforms in parallel with signing (including separate arm64 and x64 jobs for macOS), generates `SHA256SUMS` (GPG-signed), and publishes a GitHub Release with all artifacts.

**Artifact verification:**

- All platforms: `sha256sum -c SHA256SUMS` + `gpg --verify SHA256SUMS.asc`
- Windows: Right-click → Properties → Digital Signatures
- macOS: `codesign -dv --verbose=2 /path/to/binary` or `spctl --assess`
- Android: `apksigner verify --print-certs app.apk`
