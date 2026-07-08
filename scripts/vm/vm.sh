#!/usr/bin/env bash
# RCFlow VM verification toolkit.
#
# Drives the Ubuntu VM (VMware Workstation, SSH host "vmubuntu") used for
# live worker + client verification. Designed to be invoked by AI coding
# agents and humans alike — every subcommand is non-interactive, prints
# plain parseable output, and exits non-zero on failure.
#
# See docs/design/vm-verification.md for the full verification playbook.
#
# Usage: scripts/vm/vm.sh <command> [args]   (or: just vm <command> [args])
set -euo pipefail

VM_HOST="${RCFLOW_VM_HOST:-vmubuntu}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACT_DIR="$REPO_ROOT/logs/vm"
CLIENT_BIN="/opt/rcflowclient/rcflowclient"
CLIENT_PROC="rcflowclient" # exact process name — pgrep/pkill -x avoids matching the ssh remote shell itself
CLIENT_LOG="/tmp/rcflowclient.log"

vssh() { ssh "${SSH_OPTS[@]}" "$VM_HOST" "$@"; }

die() { echo "ERROR: $*" >&2; exit 1; }

# --- discovery helpers -------------------------------------------------------

worker_port() {
  vssh "sudo python3 -c \"import json;print(json.load(open('/opt/rcflow/settings.json'))['RCFLOW_PORT'])\""
}

worker_api_key() {
  vssh "sudo python3 -c \"import json;print(json.load(open('/opt/rcflow/settings.json'))['RCFLOW_API_KEY'])\""
}

# --- commands ----------------------------------------------------------------

cmd_check() {
  echo "== SSH connectivity =="
  vssh 'echo "OK $(hostname) $(lsb_release -ds 2>/dev/null)"' || die "cannot reach VM '$VM_HOST' over SSH"
  echo "== Installed packages =="
  vssh 'dpkg-query -W -f="\${Package} \${Version}\n" rcflow rcflow-client 2>/dev/null || true'
  echo "== Worker service =="
  vssh 'systemctl is-active rcflow.service && systemctl is-enabled rcflow.service' || true
  echo "== Worker health =="
  cmd_health
  echo "== Display session =="
  vssh 'loginctl show-session $(loginctl list-sessions --no-legend | awk "\$NF==\"-\" && \$5!=\"-\" {print \$1; exit}") -p Type 2>/dev/null; ls /tmp/.X11-unix/ 2>/dev/null'
  echo "== Client process =="
  cmd_client_status || true
}

cmd_versions() {
  local local_worker local_client
  local_worker=$(grep -m1 '^version' "$REPO_ROOT/pyproject.toml" | sed 's/.*"\(.*\)"/\1/')
  local_client=$(grep -m1 '^version:' "$REPO_ROOT/rcflowclient/pubspec.yaml" | awk '{print $2}' | sed 's/+.*//')
  echo "local  worker: $local_worker"
  echo "local  client: $local_client"
  vssh 'dpkg-query -W -f="vm     worker: \${Version}\n" rcflow 2>/dev/null || echo "vm     worker: NOT INSTALLED"'
  vssh 'dpkg-query -W -f="vm     client: \${Version}\n" rcflow-client 2>/dev/null || echo "vm     client: NOT INSTALLED"'
  local port
  port=$(worker_port)
  vssh "curl -sk -H \"X-API-Key: \$(sudo python3 -c \"import json;print(json.load(open('/opt/rcflow/settings.json'))['RCFLOW_API_KEY'])\")\" https://localhost:$port/api/info" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("running worker:", d.get("version", "?"))' \
    || echo "running worker: UNREACHABLE"
}

cmd_sync() {
  "$REPO_ROOT/scripts/rsync-to-ubuntu.sh" "$@"
}

cmd_build_worker() {
  (cd "$REPO_ROOT" && just bundle-linux-worker "$@")
}

cmd_build_client() {
  (cd "$REPO_ROOT" && just bundle-linux-client)
}

latest_deb() { # $1 = worker|client
  ls -t "$REPO_ROOT"/dist/rcflow-v*-linux-"$1"-amd64.deb 2>/dev/null | head -1
}

check_deb() {
  # A .deb still being written by a running build passes `ls` but is truncated —
  # walking the payload tar catches that before we ship it to the VM.
  dpkg-deb --fsys-tarfile "$1" >/dev/null 2>&1 \
    || die "corrupt or incomplete .deb: $1 (is the build still running?)"
}

cmd_deploy_worker() {
  local deb="${1:-$(latest_deb worker)}"
  [[ -n "$deb" && -f "$deb" ]] || die "no worker .deb found — run 'vm.sh build-worker' first (or pass a path)"
  check_deb "$deb"
  echo "Deploying $(basename "$deb")"
  scp "${SSH_OPTS[@]}" "$deb" "$VM_HOST:/tmp/rcflow-worker.deb"
  vssh 'sudo dpkg -i /tmp/rcflow-worker.deb && sudo systemctl restart rcflow.service'
  cmd_wait_health
  cmd_versions
}

cmd_deploy_client() {
  local deb="${1:-$(latest_deb client)}"
  [[ -n "$deb" && -f "$deb" ]] || die "no client .deb found — run 'vm.sh build-client' first (or pass a path)"
  check_deb "$deb"
  echo "Deploying $(basename "$deb")"
  scp "${SSH_OPTS[@]}" "$deb" "$VM_HOST:/tmp/rcflow-client.deb"
  vssh 'sudo dpkg -i /tmp/rcflow-client.deb'
  vssh "dpkg-query -W -f='installed rcflow-client \${Version}\n' rcflow-client"
}

cmd_health() {
  local port
  port=$(worker_port)
  local body
  body=$(vssh "curl -sk --max-time 5 https://localhost:$port/api/health" || true)
  if [[ "$body" == *'"ok"'* ]]; then
    echo "healthy (port $port): $body"
  else
    die "worker unhealthy on port $port: '${body:-no response}'"
  fi
}

cmd_wait_health() {
  local timeout="${1:-30}" port
  port=$(worker_port)
  echo "Waiting for worker health on port $port (timeout ${timeout}s)…"
  for ((i = 0; i < timeout; i++)); do
    if vssh "curl -sk --max-time 2 https://localhost:$port/api/health" 2>/dev/null | grep -q '"ok"'; then
      echo "healthy after ${i}s"
      return 0
    fi
    sleep 1
  done
  echo "== last worker logs ==" >&2
  cmd_worker_logs 30 >&2 || true
  die "worker did not become healthy within ${timeout}s"
}

cmd_worker_status() { vssh 'systemctl status rcflow.service --no-pager -l' || true; }
cmd_worker_restart() { vssh 'sudo systemctl restart rcflow.service'; cmd_wait_health; }
cmd_worker_start() { vssh 'sudo systemctl start rcflow.service'; cmd_wait_health; }
cmd_worker_stop() { vssh 'sudo systemctl stop rcflow.service'; echo "stopped"; }
cmd_worker_logs() { vssh "sudo journalctl -u rcflow.service --no-pager -n ${1:-100}"; }

cmd_smoke() {
  local port key local_port tunnel_pid
  port=$(worker_port)
  key=$(worker_api_key)
  # Pick a free local port for the SSH tunnel.
  local_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  ssh "${SSH_OPTS[@]}" -f -N -o ExitOnForwardFailure=yes \
    -L "$local_port:localhost:$port" "$VM_HOST"
  tunnel_pid=$(pgrep -f "ssh.*-L $local_port:localhost:$port" | head -1)
  trap '[[ -n "${tunnel_pid:-}" ]] && kill "$tunnel_pid" 2>/dev/null || true' EXIT
  echo "SSH tunnel 127.0.0.1:$local_port → VM:$port (pid $tunnel_pid)"
  (cd "$REPO_ROOT" && uv run python scripts/vm/smoke_test.py \
    --base-url "https://127.0.0.1:$local_port" --api-key "$key" "$@")
}

cmd_smoke_acp() {
  # ACP E2E: OpenCode-over-ACP prompt round-trip. ACP is the default mode;
  # needs the managed OpenCode installed on the worker (and the flag not forced
  # to legacy).
  local port key local_port tunnel_pid
  port=$(worker_port)
  key=$(worker_api_key)
  local_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  ssh "${SSH_OPTS[@]}" -f -N -o ExitOnForwardFailure=yes \
    -L "$local_port:localhost:$port" "$VM_HOST"
  tunnel_pid=$(pgrep -f "ssh.*-L $local_port:localhost:$port" | head -1)
  trap '[[ -n "${tunnel_pid:-}" ]] && kill "$tunnel_pid" 2>/dev/null || true' EXIT
  echo "SSH tunnel 127.0.0.1:$local_port → VM:$port (pid $tunnel_pid)"
  (cd "$REPO_ROOT" && uv run python scripts/vm/acp_smoke_test.py \
    --base-url "https://127.0.0.1:$local_port" --api-key "$key" "$@")
}

cmd_client_start() {
  vssh "pgrep -x '$CLIENT_PROC' >/dev/null" && { echo "client already running"; return 0; }
  vssh "DISPLAY=:0 nohup $CLIENT_BIN >$CLIENT_LOG 2>&1 & echo \"started pid \$!\""
  sleep 5
  cmd_client_status
}

cmd_client_stop() {
  vssh "pkill -x '$CLIENT_PROC' 2>/dev/null; true"
  echo "client stopped"
}

cmd_client_status() {
  if vssh "pgrep -ax '$CLIENT_PROC'" 2>/dev/null; then
    echo "-- windows --"
    vssh 'DISPLAY=:0 wmctrl -l' || true
  else
    echo "client not running"
    return 1
  fi
}

cmd_client_logs() { vssh "tail -n ${1:-100} $CLIENT_LOG 2>/dev/null || echo '(no client log)'"; }

cmd_screenshot() {
  local name="${1:-vm-$(date +%Y%m%d-%H%M%S)}"
  mkdir -p "$ARTIFACT_DIR"
  vssh 'DISPLAY=:0 scrot -o /tmp/vm-screenshot.png'
  scp -q "${SSH_OPTS[@]}" "$VM_HOST:/tmp/vm-screenshot.png" "$ARTIFACT_DIR/$name.png"
  echo "$ARTIFACT_DIR/$name.png"
}

cmd_shell() { vssh "$@"; }

cmd_setup() {
  echo "Installing VM helper packages (scrot, wmctrl, xdotool, jq)…"
  vssh 'sudo apt-get install -y -qq scrot wmctrl xdotool jq'
  echo "done"
}

cmd_verify_worker() {
  echo "### VERIFY WORKER ###"
  cmd_health
  cmd_smoke
  echo "### WORKER VERIFIED ###"
}

cmd_verify_client() {
  echo "### VERIFY CLIENT ###"
  cmd_client_stop
  cmd_client_start
  sleep 8 # give Flutter time to render + auto-connect to the worker
  local shot
  shot=$(cmd_screenshot "client-verify-$(date +%H%M%S)" | tail -1)
  local errors
  errors=$(vssh "grep -ciE 'exception|segfault|fatal' $CLIENT_LOG 2>/dev/null || true")
  echo "client log error-ish lines: ${errors:-0}"
  [[ "${errors:-0}" == "0" ]] || { cmd_client_logs 40; }
  echo "SCREENSHOT: $shot"
  echo "### CLIENT LAUNCHED — inspect the screenshot above to confirm the UI rendered and shows the worker as connected ###"
}

cmd_verify_all() {
  cmd_check
  cmd_verify_worker
  cmd_verify_client
}

usage() {
  cat <<'EOF'
RCFlow VM verification toolkit — scripts/vm/vm.sh <command> [args]

Environment / status:
  check                     SSH + versions + service + health + display overview
  versions                  Compare local repo versions vs VM installed vs running
  health                    Hit /api/health on the VM worker
  wait-health [timeout=30]  Poll health until OK (used after deploys)
  shell <cmd…>              Run an arbitrary command on the VM over SSH
  setup                     Install VM helper packages (scrot, wmctrl, xdotool, jq)

Build & deploy (build runs locally, deploy copies .deb to VM + installs):
  build-worker [flags]      just bundle-linux-worker
  build-client              just bundle-linux-client
  deploy-worker [deb]       scp + dpkg -i latest (or given) worker .deb, restart, wait-health
  deploy-client [deb]       scp + dpkg -i latest (or given) client .deb
  sync [--dry-run]          rsync working tree to VM ~/Projects/RCFlow (source overlay)

Worker service:
  worker-status | worker-start | worker-stop | worker-restart
  worker-logs [n=100]       journalctl tail

Worker E2E:
  smoke [--verbose]         Full WS round-trip test through an SSH tunnel
                            (health → auth → prompt → tool output → session end)
  smoke-acp [--verbose]     OpenCode-over-ACP E2E (#opencode prompt → agent banner →
                            streamed answer → follow-up turn → session end).
                            Needs OpenCode installed on the worker (ACP is the default)

Client (GUI on VM display :0):
  client-start | client-stop | client-status
  client-logs [n=100]       Tail the client stdout/stderr log
  screenshot [name]         Capture VM screen → logs/vm/<name>.png (Read it to inspect)

Composite:
  verify-worker             health + smoke
  verify-client             restart client + screenshot + log scan
  verify-all                check + verify-worker + verify-client

SSH host is "$RCFLOW_VM_HOST" (default: vmubuntu, from ~/.ssh/config).
EOF
}

cmd="${1:-}"
[[ -n "$cmd" ]] || { usage; exit 1; }
shift || true

case "$cmd" in
  check)          cmd_check "$@" ;;
  versions)       cmd_versions "$@" ;;
  sync)           cmd_sync "$@" ;;
  build-worker)   cmd_build_worker "$@" ;;
  build-client)   cmd_build_client "$@" ;;
  deploy-worker)  cmd_deploy_worker "$@" ;;
  deploy-client)  cmd_deploy_client "$@" ;;
  health)         cmd_health "$@" ;;
  wait-health)    cmd_wait_health "$@" ;;
  worker-status)  cmd_worker_status "$@" ;;
  worker-start)   cmd_worker_start "$@" ;;
  worker-stop)    cmd_worker_stop "$@" ;;
  worker-restart) cmd_worker_restart "$@" ;;
  worker-logs)    cmd_worker_logs "$@" ;;
  smoke)          cmd_smoke "$@" ;;
  smoke-acp)      cmd_smoke_acp "$@" ;;
  client-start)   cmd_client_start "$@" ;;
  client-stop)    cmd_client_stop "$@" ;;
  client-status)  cmd_client_status "$@" ;;
  client-logs)    cmd_client_logs "$@" ;;
  screenshot)     cmd_screenshot "$@" ;;
  shell)          cmd_shell "$@" ;;
  setup)          cmd_setup "$@" ;;
  verify-worker)  cmd_verify_worker "$@" ;;
  verify-client)  cmd_verify_client "$@" ;;
  verify-all)     cmd_verify_all "$@" ;;
  help|-h|--help) usage ;;
  *) die "unknown command '$cmd' — run 'vm.sh help'" ;;
esac
