#!/usr/bin/env bash
# ============================================================================
# RCFlow macOS Uninstaller
#
# Removes the RCFlow installation and launchd daemon.
# Run as root:
#   sudo ./uninstall.sh
# ============================================================================

set -euo pipefail

INSTALL_PREFIX="/usr/local/lib/rcflow"
BIN_DIR="/usr/local/bin"
SERVICE_LABEL="com.rcflow.server"
KEEP_DATA=false
KEEP_CONFIG=false
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        --service-label) SERVICE_LABEL="$2"; shift 2 ;;
        --keep-data) KEEP_DATA=true; shift ;;
        --keep-config) KEEP_CONFIG=true; shift ;;
        --yes) SKIP_CONFIRM=true; shift ;;
        -h|--help)
            head -14 "$0" | tail -12
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

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}[ERROR]${NC} This uninstaller must be run as root. Try: sudo $0" >&2
    exit 1
fi

if [[ ! -d "$INSTALL_PREFIX" ]]; then
    echo -e "${RED}[ERROR]${NC} No installation found at ${INSTALL_PREFIX}" >&2
    exit 1
fi

echo ""
echo -e "${YELLOW}This will remove the RCFlow installation at ${INSTALL_PREFIX}${NC}"
if ! $KEEP_DATA; then
    echo -e "${YELLOW}  Including: database, data files${NC}"
fi
if ! $KEEP_CONFIG; then
    echo -e "${YELLOW}  Including: settings.json configuration${NC}"
fi
echo ""

if ! $SKIP_CONFIRM; then
    read -rp "Are you sure? [y/N] " confirm
    if [[ "$confirm" != [yY] ]]; then
        echo "Cancelled."
        exit 0
    fi
fi

PLIST_PATH="/Library/LaunchDaemons/${SERVICE_LABEL}.plist"
if [[ -f "$PLIST_PATH" ]]; then
    info "Stopping launchd service..."
    launchctl bootout system "$PLIST_PATH" >/dev/null 2>&1 || true
    ok "Service stopped"

    info "Removing launchd service file..."
    rm -f "$PLIST_PATH"
    ok "Service file removed"
fi

if $KEEP_DATA && [[ -d "$INSTALL_PREFIX/data" ]]; then
    BACKUP_DIR="/tmp/rcflow-data-backup-$(date +%s)"
    info "Backing up data to ${BACKUP_DIR}..."
    cp -R "$INSTALL_PREFIX/data" "$BACKUP_DIR"
    ok "Data backed up"
fi

if $KEEP_CONFIG && [[ -f "$INSTALL_PREFIX/settings.json" ]]; then
    BACKUP_CONFIG="/tmp/rcflow-cfg-backup-$(date +%s)"
    info "Backing up config to ${BACKUP_CONFIG}..."
    cp "$INSTALL_PREFIX/settings.json" "$BACKUP_CONFIG"
    ok "Config backed up"
fi

SYMLINK_PATH="$BIN_DIR/rcflow"
if [[ -L "$SYMLINK_PATH" ]] || [[ -f "$SYMLINK_PATH" ]]; then
    if [[ "$(readlink "$SYMLINK_PATH" 2>/dev/null || true)" == "$INSTALL_PREFIX/rcflow" ]]; then
        info "Removing ${SYMLINK_PATH}..."
        rm -f "$SYMLINK_PATH"
        ok "Symlink removed"
    fi
fi

info "Removing ${INSTALL_PREFIX}..."
rm -rf "$INSTALL_PREFIX"
ok "Installation removed"

echo ""
ok "RCFlow has been uninstalled."

if $KEEP_DATA; then
    echo -e "  Data backup: ${BACKUP_DIR:-/tmp/rcflow-data-backup-*}"
fi
if $KEEP_CONFIG; then
    echo -e "  Config backup: ${BACKUP_CONFIG:-/tmp/rcflow-cfg-backup-*}"
fi
echo ""
