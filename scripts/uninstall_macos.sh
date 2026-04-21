#!/usr/bin/env bash
# ============================================================================
# RCFlow macOS Uninstaller
#
# Removes the RCFlow backend worker installation from macOS — covers both the
# DMG/app-bundle install (Contents/MacOS + ~/Library/Application Support) and
# the CLI tarball install (~/.local/lib/rcflow).
#
# No root/sudo required unless cleaning up a legacy system-level install.
#
# Usage:
#   ./uninstall_macos.sh [OPTIONS]
#
# Options:
#   --prefix /path      Override CLI install directory (default: ~/.local/lib/rcflow)
#   --bin-dir /path     Override symlink directory (default: ~/.local/bin)
#   --service-label L   Override launchd service label (default: com.rcflow.server)
#   --keep-data         Back up data directory instead of deleting it
#   --keep-config       Back up settings.json instead of deleting it
#   --keep-tools        Keep ~/.local/share/rcflow/tools (managed tool binaries)
#   --yes               Skip confirmation prompt
# ============================================================================

set -euo pipefail

INSTALL_PREFIX="$HOME/.local/lib/rcflow"
BIN_DIR="$HOME/.local/bin"
SERVICE_LABEL="com.rcflow.server"
GUI_LABEL="com.rcflow.worker"
KEEP_DATA=false
KEEP_CONFIG=false
KEEP_TOOLS=false
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        --service-label) SERVICE_LABEL="$2"; shift 2 ;;
        --keep-data) KEEP_DATA=true; shift ;;
        --keep-config) KEEP_CONFIG=true; shift ;;
        --keep-tools) KEEP_TOOLS=true; shift ;;
        --yes) SKIP_CONFIRM=true; shift ;;
        -h|--help)
            head -27 "$0" | tail -25
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── Detect what is actually installed ────────────────────────────────────────
APP_BUNDLE="/Applications/RCFlow Worker.app"
APP_SUPPORT="$HOME/Library/Application Support/rcflow"
MANAGED_TOOLS="$HOME/.local/share/rcflow/tools"
TRACE_LOG="$HOME/Library/Logs/rcflow-worker-trace.log"
CRASH_LOG="$HOME/Library/Logs/rcflow-worker-crash.log"

HAS_CLI=false
HAS_APP=false

[[ -d "$INSTALL_PREFIX" ]] && HAS_CLI=true
[[ -d "$APP_BUNDLE" ]]     && HAS_APP=true

if ! $HAS_CLI && ! $HAS_APP; then
    echo -e "${RED}[ERROR]${NC} No RCFlow installation found." >&2
    echo -e "  Checked: ${INSTALL_PREFIX}" >&2
    echo -e "  Checked: ${APP_BUNDLE}" >&2
    exit 1
fi

echo ""
echo -e "${YELLOW}The following will be removed:${NC}"
$HAS_CLI && echo -e "  CLI install:     ${INSTALL_PREFIX}"
$HAS_APP && echo -e "  App bundle:      ${APP_BUNDLE}"
[[ -d "$APP_SUPPORT" ]]   && echo -e "  App Support:     ${APP_SUPPORT}"
[[ -d "$MANAGED_TOOLS" ]] && ! $KEEP_TOOLS && echo -e "  Managed tools:   ${MANAGED_TOOLS}"
[[ -f "$TRACE_LOG" ]]     && echo -e "  Trace log:       ${TRACE_LOG}"
[[ -f "$CRASH_LOG" ]]     && echo -e "  Crash log:       ${CRASH_LOG}"
if ! $KEEP_DATA;   then echo -e "  Including: database and data files"; fi
if ! $KEEP_CONFIG; then echo -e "  Including: settings.json"; fi
echo ""

if ! $SKIP_CONFIRM; then
    read -rp "Are you sure? [y/N] " confirm
    if [[ "$confirm" != [yY] ]]; then
        echo "Cancelled."
        exit 0
    fi
fi

# ── Stop and remove LaunchAgents ─────────────────────────────────────────────
for label in "$SERVICE_LABEL" "$GUI_LABEL"; do
    plist="$HOME/Library/LaunchAgents/${label}.plist"
    if [[ -f "$plist" ]]; then
        info "Stopping LaunchAgent: ${label}..."
        launchctl unload "$plist" >/dev/null 2>&1 || true
        rm -f "$plist"
        ok "LaunchAgent removed: ${label}"
    fi
done

# ── Clean up legacy LaunchDaemon (old system-level install) ──────────────────
OLD_DAEMON_PLIST="/Library/LaunchDaemons/${SERVICE_LABEL}.plist"
if [[ -f "$OLD_DAEMON_PLIST" ]]; then
    warn "Found legacy LaunchDaemon at ${OLD_DAEMON_PLIST}"
    info "Removing (requires sudo)..."
    sudo launchctl bootout system "$OLD_DAEMON_PLIST" >/dev/null 2>&1 || true
    sudo rm -f "$OLD_DAEMON_PLIST"
    ok "Legacy LaunchDaemon removed"

    if dscl . -read "/Users/rcflow" &>/dev/null 2>&1; then
        info "Removing legacy service user: rcflow"
        sudo dscl . -delete "/Users/rcflow" 2>/dev/null || true
        ok "Legacy service user removed"
    fi
fi

# ── Clean up old system-level install directory ──────────────────────────────
OLD_INSTALL="/usr/local/lib/rcflow"
if [[ -d "$OLD_INSTALL" ]]; then
    warn "Found old system-level install at ${OLD_INSTALL}"
    sudo rm -rf "$OLD_INSTALL"
    ok "Old install directory removed"
fi
OLD_SYMLINK="/usr/local/bin/rcflow"
if [[ -L "$OLD_SYMLINK" ]]; then
    sudo rm -f "$OLD_SYMLINK"
    ok "Old symlink removed"
fi

# ── Remove .app bundle ───────────────────────────────────────────────────────
if $HAS_APP; then
    info "Removing app bundle..."
    rm -rf "$APP_BUNDLE"
    ok "App bundle removed"
fi

# ── Back up or remove data/config from CLI install ───────────────────────────
if $HAS_CLI; then
    if $KEEP_DATA && [[ -d "$INSTALL_PREFIX/data" ]]; then
        BACKUP_DIR="/tmp/rcflow-data-backup-$(date +%s)"
        info "Backing up data to ${BACKUP_DIR}..."
        cp -R "$INSTALL_PREFIX/data" "$BACKUP_DIR"
        ok "Data backed up to ${BACKUP_DIR}"
    fi

    if $KEEP_CONFIG && [[ -f "$INSTALL_PREFIX/settings.json" ]]; then
        BACKUP_CONFIG="/tmp/rcflow-cfg-backup-$(date +%s)"
        info "Backing up config to ${BACKUP_CONFIG}..."
        cp "$INSTALL_PREFIX/settings.json" "$BACKUP_CONFIG"
        ok "Config backed up to ${BACKUP_CONFIG}"
    fi

    SYMLINK_PATH="$BIN_DIR/rcflow"
    if [[ -L "$SYMLINK_PATH" ]] || [[ -f "$SYMLINK_PATH" ]]; then
        if [[ "$(readlink "$SYMLINK_PATH" 2>/dev/null || true)" == "$INSTALL_PREFIX/rcflow" ]]; then
            rm -f "$SYMLINK_PATH"
            ok "Symlink removed"
        fi
    fi

    info "Removing CLI install at ${INSTALL_PREFIX}..."
    rm -rf "$INSTALL_PREFIX"
    ok "CLI installation removed"
fi

# ── Remove ~/Library/Application Support/rcflow (frozen-app data dir) ────────
if [[ -d "$APP_SUPPORT" ]]; then
    if $KEEP_CONFIG && [[ -f "$APP_SUPPORT/settings.json" ]]; then
        BACKUP_CONFIG="/tmp/rcflow-cfg-backup-$(date +%s)"
        info "Backing up config to ${BACKUP_CONFIG}..."
        cp "$APP_SUPPORT/settings.json" "$BACKUP_CONFIG"
        ok "Config backed up to ${BACKUP_CONFIG}"
    fi
    info "Removing Application Support data..."
    rm -rf "$APP_SUPPORT"
    ok "Application Support data removed"
fi

# ── Remove managed tools ──────────────────────────────────────────────────────
if [[ -d "$MANAGED_TOOLS" ]]; then
    if $KEEP_TOOLS; then
        info "Keeping managed tools at ${MANAGED_TOOLS} (--keep-tools)"
    else
        info "Removing managed tools..."
        rm -rf "$MANAGED_TOOLS"
        # Remove parent ~/.local/share/rcflow only if empty
        rmdir "$HOME/.local/share/rcflow" 2>/dev/null || true
        ok "Managed tools removed"
    fi
fi

# ── Remove logs ───────────────────────────────────────────────────────────────
for log in "$TRACE_LOG" "$CRASH_LOG"; do
    if [[ -f "$log" ]]; then
        rm -f "$log"
        ok "Removed $(basename "$log")"
    fi
done

echo ""
ok "RCFlow Worker has been uninstalled."
echo ""
