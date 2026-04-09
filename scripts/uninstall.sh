#!/usr/bin/env bash
# ============================================================================
# RCFlow Linux Uninstaller
#
# Removes RCFlow installation and systemd service.
# Run as root: sudo ./uninstall.sh
#
# Options:
#   --prefix /path      Install directory (default: /opt/rcflow)
#   --keep-data         Preserve data/ directory (database)
#   --keep-config       Preserve settings.json configuration
#   --yes               Skip confirmation prompt
# ============================================================================

set -euo pipefail

INSTALL_PREFIX="/opt/rcflow"
KEEP_DATA=false
KEEP_CONFIG=false
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)      INSTALL_PREFIX="$2"; shift 2 ;;
        --keep-data)   KEEP_DATA=true; shift ;;
        --keep-config) KEEP_CONFIG=true; shift ;;
        --yes)         SKIP_CONFIRM=true; shift ;;
        -h|--help)
            head -13 "$0" | tail -11
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

# Check whether systemd is actually running (not the case on WSL2 by default)
has_systemd() { [ -d /run/systemd/system ]; }

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

# Stop and disable service
if has_systemd; then
    if systemctl is-active --quiet rcflow 2>/dev/null; then
        info "Stopping RCFlow service..."
        systemctl stop rcflow
        ok "Service stopped"
    fi

    if systemctl is-enabled --quiet rcflow 2>/dev/null; then
        info "Disabling RCFlow service..."
        systemctl disable rcflow
        ok "Service disabled"
    fi

    if [[ -f /etc/systemd/system/rcflow.service ]]; then
        info "Removing systemd service file..."
        rm -f /etc/systemd/system/rcflow.service
        systemctl daemon-reload
        ok "Service file removed"
    fi
else
    warn "systemd not running — skipping service teardown"
    rm -f /etc/systemd/system/rcflow.service 2>/dev/null || true
fi

# Optionally preserve data
if $KEEP_DATA && [[ -d "$INSTALL_PREFIX/data" ]]; then
    BACKUP_DIR="/tmp/rcflow-data-backup-$(date +%s)"
    info "Backing up data to ${BACKUP_DIR}..."
    cp -r "$INSTALL_PREFIX/data" "$BACKUP_DIR"
    ok "Data backed up"
fi

if $KEEP_CONFIG && [[ -f "$INSTALL_PREFIX/settings.json" ]]; then
    BACKUP_CONFIG="/tmp/rcflow-cfg-backup-$(date +%s)"
    info "Backing up config to ${BACKUP_CONFIG}..."
    cp "$INSTALL_PREFIX/settings.json" "$BACKUP_CONFIG"
    ok "Config backed up"
fi

# Remove installation directory
info "Removing ${INSTALL_PREFIX}..."
rm -rf "$INSTALL_PREFIX"
ok "Installation removed"

# Remove user (optional — don't remove if other files might be owned by it)
if id "rcflow" &>/dev/null; then
    info "Removing rcflow system user..."
    userdel rcflow 2>/dev/null || warn "Could not remove user (may have running processes)"
fi

echo ""
ok "RCFlow has been uninstalled."

if $KEEP_DATA; then
    echo -e "  Data backup: ${BACKUP_DIR:-/tmp/rcflow-data-backup-*}"
fi
if $KEEP_CONFIG; then
    echo -e "  Config backup: ${BACKUP_CONFIG:-/tmp/rcflow-env-backup-*}"
fi
echo ""
