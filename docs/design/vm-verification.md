---
updated: 2026-07-08
---

# VM Verification (Ubuntu VM — Worker + Client E2E)

How to run **live end-to-end verification** of the RCFlow worker and the Linux Flutter client on the dedicated Ubuntu VM (VMware Workstation Pro on the Windows host). This is the playbook for AI coding agents: every step is a non-interactive command with parseable output — no manual VM interaction required.

**See also:**
- [Deployment](deployment.md) — what the `.deb` packages install and how the systemd service works
- [Direct Tool Mode](direct-tool-mode.md) — the `LLM_PROVIDER=none` mode the VM worker runs in (no LLM keys needed for E2E)
- [WebSocket API](websocket-api.md) — the protocol the smoke test exercises

---

## Topology

```
WSL2 (dev machine, this repo)          Windows host              VMware VM
┌──────────────────────────┐   ssh    ┌─────────────┐   NAT    ┌──────────────────────────────┐
│ scripts/vm/vm.sh         │ ────────▶│ portproxy   │ ────────▶│ Ubuntu 25.04, user "osboxes" │
│ scripts/vm/smoke_test.py │  :2222   │ 172.28.96.1 │          │  rcflow.service (worker .deb)│
│ (runs via `just vm …`)   │          └─────────────┘          │  rcflow-client (.deb, X11 :0)│
└──────────────────────────┘                                   └──────────────────────────────┘
```

- SSH host alias **`vmubuntu`** (`~/.ssh/config`) → `172.28.96.1:2222`, user `osboxes`, key auth. Override with `RCFLOW_VM_HOST`.
- `osboxes` has **passwordless sudo** — package installs and service restarts need no password.
- Worker installed at `/opt/rcflow` (systemd `rcflow.service`, runs as user `rcflow`). Port and API key are **not fixed** — the toolkit reads them from `/opt/rcflow/settings.json` at runtime.
- Worker runs **`LLM_PROVIDER=none`** (direct tool mode), so E2E prompts use `#tool_name` syntax and need no LLM API keys.
- Client installed at `/opt/rcflowclient` (`/usr/bin/rcflowclient`), launched on the VM's X11 display `:0`. It is pre-configured with the VM worker (auto-connect, self-signed TLS allowed).
- The worker's port is only reachable **inside** the VM; the smoke test tunnels through SSH (`vm.sh smoke` handles this automatically).

## The Toolkit

Entry point: **`just vm <command> [args]`** (wraps `scripts/vm/vm.sh`). Run `just vm help` for the full list.

| Command | What it does |
|---|---|
| `just vm check` | SSH reachability, installed package versions, service state, health, display, client process |
| `just vm versions` | Local repo versions vs VM-installed `.deb` versions vs the running worker's `/api/info` version |
| `just vm health` / `wait-health [s]` | Hit (or poll) `/api/health` on the VM worker |
| `just vm build-worker` / `build-client` | Build the Linux `.deb` locally (`just bundle-linux-worker` / `bundle-linux-client`) |
| `just vm deploy-worker [deb]` | Copy newest (or given) worker `.deb` to VM, `dpkg -i`, restart service, wait for health |
| `just vm deploy-client [deb]` | Copy newest (or given) client `.deb` to VM, `dpkg -i` |
| `just vm smoke [--verbose]` | Full WebSocket E2E round-trip through an SSH tunnel (see below) |
| `just vm smoke-acp [--verbose]` | OpenCode-over-ACP E2E: `#opencode` prompt → agent banner → streamed thinking/answer → follow-up turn on the same live agent process → clean session end. Requires the managed OpenCode binary installed on the VM (ACP is the default executor mode; `RCFLOW_OPENCODE_EXECUTOR=legacy` would opt out) |
| `just vm worker-status/-start/-stop/-restart/-logs [n]` | systemd service control + `journalctl` tail |
| `just vm client-start/-stop/-status/-logs [n]` | Launch/kill the GUI client on display `:0`; logs go to `/tmp/rcflowclient.log` on the VM |
| `just vm screenshot [name]` | Capture the VM screen → `logs/vm/<name>.png` locally (gitignored) |
| `just vm shell '<cmd>'` | Arbitrary command on the VM over SSH |
| `just vm setup` | Install VM helper packages (scrot, wmctrl, xdotool, jq) — one-time / after VM rebuild |
| `just vm verify-worker` | `health` + `smoke` |
| `just vm verify-client` | Restart client → wait → screenshot → scan client log for errors |
| `just vm verify-all` | `check` + `verify-worker` + `verify-client` |

## The Smoke Test (`scripts/vm/smoke_test.py`)

Runs from the dev machine over real TLS/TCP (SSH tunnel opened by `vm.sh smoke`). Steps, all asserted:

1. `GET /api/health` → `{"status": "ok"}` (no auth)
2. `GET /api/info` with `X-API-Key` → version + backend id
3. WebSocket connect with a **wrong** API key is rejected
4. Connect `/ws/output/text` with the real key
5. Send `{"type": "prompt", "text": "#shell_exec echo <marker>"}` on `/ws/input/text` → `ack` with `session_id`
6. Subscribe to that session on the output socket
7. `tool_start` for `shell_exec` arrives
8. `tool_output` contains the unique marker
9. `turn_complete` arrives
10. `end_session` → `session_end` broadcast
11. `GET /api/sessions` shows the session persisted with status `completed`

> **Protocol gotcha (why subscribe comes after the ack):** `subscribe_all` only attaches to sessions that exist when it is sent. A session created afterwards streams nothing to that socket except broadcast `session_update`s. The smoke test therefore subscribes to the specific `session_id` *after* the ack — subscription replays the session's full history, so no events are lost to the race.

Exit code 0 = pass. On failure it prints `FAIL <reason>` to stderr and exits 1.

## Verification Playbooks

### Full verification of the current working tree (worker + client)

The complete "does what I built actually work on a real install" loop:

```bash
just vm check                 # VM reachable + baseline state
just vm build-worker          # ~ minutes (PyInstaller)
just vm deploy-worker         # install .deb, restart service, wait for health
just vm verify-worker         # health + full WS smoke test
just vm build-client          # ~ minutes (Flutter release build)
just vm deploy-client
just vm verify-client         # launch GUI, screenshot, log scan
just vm versions              # confirm VM now runs the freshly built versions
```

**Then Read the screenshot** printed by `verify-client` (`logs/vm/client-verify-*.png`). Confirm visually:
- the RCFlow window rendered (no blank/black window),
- the worker (`osboxes`) shows a green/connected indicator in the Workers panel,
- sessions created by the smoke test appear in the session list (titles like `echo rcflow-smoke-…`),
- no error banner other than expected ones (e.g. "Claude Code is not installed" is normal on this VM — no coding agents are installed there).

### Quick worker-only check (no rebuild)

```bash
just vm verify-worker
```

### Client-only check (no rebuild)

```bash
just vm verify-client        # then Read the screenshot it prints
```

### Interacting with the client UI

For richer client checks, drive X11 directly (xdotool installed via `just vm setup`):

```bash
just vm shell 'DISPLAY=:0 wmctrl -l'                          # list windows
just vm shell 'DISPLAY=:0 xdotool search --name RCFlow windowactivate'
just vm shell 'DISPLAY=:0 xdotool key ctrl+n'                 # send keystrokes
just vm shell 'DISPLAY=:0 xdotool mousemove 640 400 click 1'  # click at coords
just vm screenshot after-click                                # observe result
```

Loop: act with `xdotool` → `screenshot` → Read the PNG → next action. Screen is 1280×800 by default (`xrandr` to confirm).

### Debugging failures

```bash
just vm worker-logs 200            # journalctl tail of rcflow.service
just vm client-logs 100            # client stdout/stderr
just vm shell 'sudo cat /opt/rcflow/settings.json'
just vm shell 'sudo ss -tlnp | grep rcflow'
just vm screenshot debug           # see what's actually on screen
```

## Conventions & Caveats

- **Artifacts land in `logs/vm/`** (gitignored). Screenshots are PNGs an AI agent can Read directly.
- **Local builds carry a `-dev.g<hash>` version suffix** (see [Deployment — Auto-Update](deployment.md#auto-update-worker-gui-only)); `dpkg -i` installs them fine over a release version.
- **Upgrades preserve VM state**: `.deb` installs keep `/opt/rcflow/settings.json` and `data/` (sessions DB), so the API key and port survive deploys.
- **Never hardcode the port or API key** — they differ per install. Use `vm.sh` (it discovers both from `/opt/rcflow/settings.json` via sudo) or `just vm shell`.
- **Process matching on the VM uses `pgrep/pkill -x rcflowclient`** — a `-f` pattern containing the binary path would match the ssh remote shell running the command and kill the connection itself.
- The VM keeps old smoke-test sessions around; they are archived (`completed`) and harmless. Clean up via the client UI or REST if the list gets noisy.
- **The worker service runs with `PrivateTmp=true` and `ProtectHome=read-only`** (systemd hardening) — files you create under `/tmp` or `/home` on the VM are invisible or read-only to the worker. For REST tests that need a repo the worker can touch (e.g. the worktrees API), create it under `/opt/rcflow/Projects/` as the `rcflow` user (`sudo -u rcflow …`).
- `verify-client` counts `exception|segfault|fatal` matches in the client log; benign Flutter warnings (e.g. GTK accessibility chatter) don't match.

## Failure Triage

| Symptom | Likely cause / fix |
|---|---|
| `cannot reach VM over SSH` | VM powered off — start it in VMware Workstation on the Windows host. Or Windows portproxy (`172.28.96.1:2222`) not up. |
| `worker unhealthy` after deploy | `just vm worker-logs 100` — migration failure or port conflict; `settings.json` port may have changed. |
| Smoke step 3 fails (wrong key accepted) | Auth regression in WS handshake — check `ws_auth` handling. |
| Smoke times out waiting for tool events | Check the worker is in direct tool mode (`LLM_PROVIDER=none`) and `shell_exec` tool exists in `/opt/rcflow/tools`. |
| Screenshot black / empty | VM screen locked or X session restarted — log in on the VM console once; check `loginctl` shows an active `x11` session. |
| Client window absent in screenshot | `just vm client-logs` — missing GTK libs after a client `.deb` change, or the client crashed on startup. |
| `dpkg -i` dependency errors | Client `.deb` deps changed — `just vm shell 'sudo apt-get -f install -y'` then re-deploy. |
| `corrupt or incomplete .deb` on deploy | The bundle build is still running (deploy validates the archive before copying) — wait for `build-worker`/`build-client` to finish. |
| `build-client` fails at CMake `pkg_check_modules` | Missing dev libs on the **build host** (not the VM) — the recipe prints the exact `apt-get install` line (GTK + GStreamer for the audioplayers plugin). |
