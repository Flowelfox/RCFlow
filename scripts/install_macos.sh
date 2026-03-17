#!/usr/bin/env bash
# ============================================================================
# RCFlow macOS Installer (User-level LaunchAgent)
#
# Installs RCFlow under ~/.local and registers a launchd LaunchAgent
# running as the current user (no root/sudo required).
#
# Usage:
#   ./install_macos.sh
#
# Options:
#   --prefix /path      Install directory (default: $HOME/.local/lib/rcflow)
#   --bin-dir /path     Symlink directory (default: $HOME/.local/bin)
#   --port N            Server port (default: 53890)
#   --no-service        Skip launchd service setup
#   --unattended        Non-interactive mode (use all defaults)
# ============================================================================

set -euo pipefail

INSTALL_PREFIX="$HOME/.local/lib/rcflow"
BIN_DIR="$HOME/.local/bin"
RCFLOW_PORT="53890"
SERVICE_LABEL="com.rcflow.server"
SETUP_SERVICE=true
UNATTENDED=false
SKIP_MIGRATION=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) INSTALL_PREFIX="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        --port) RCFLOW_PORT="$2"; shift 2 ;;
        --service-label) SERVICE_LABEL="$2"; shift 2 ;;
        --no-service) SETUP_SERVICE=false; shift ;;
        --skip-migration) SKIP_MIGRATION=true; shift ;;
        --unattended) UNATTENDED=true; shift ;;
        -h|--help)
            head -17 "$0" | tail -15
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
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

prompt_value() {
    local prompt="$1" default="$2" var_name="$3"
    if $UNATTENDED; then
        eval "$var_name=\"$default\""
        return
    fi
    local input
    read -rp "$(echo -e "${CYAN}$prompt${NC} [$default]: ")" input
    eval "$var_name=\"${input:-$default}\""
}

generate_api_key() {
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null \
        || openssl rand -base64 32 2>/dev/null \
        || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32
}

stop_service() {
    # Stop new-style LaunchAgent
    local agent_plist="$HOME/Library/LaunchAgents/${SERVICE_LABEL}.plist"
    if [[ -f "$agent_plist" ]]; then
        launchctl unload "$agent_plist" >/dev/null 2>&1 || true
    fi

    # Stop old-style LaunchDaemon (requires sudo)
    local daemon_plist="/Library/LaunchDaemons/${SERVICE_LABEL}.plist"
    if [[ -f "$daemon_plist" ]]; then
        sudo launchctl bootout system "$daemon_plist" >/dev/null 2>&1 || true
    fi
}

migrate_from_daemon() {
    local old_prefix="/usr/local/lib/rcflow"
    local old_plist="/Library/LaunchDaemons/${SERVICE_LABEL}.plist"
    local old_user="rcflow"

    # Check if old daemon install exists
    if [[ ! -f "$old_plist" ]] && [[ ! -d "$old_prefix" ]]; then
        return 0
    fi

    echo ""
    warn "Detected old system-level LaunchDaemon installation."
    info "Migrating to user-level LaunchAgent..."
    info "This requires sudo (one-time) to clean up system-level artifacts."
    echo ""

    # Stop old daemon
    if [[ -f "$old_plist" ]]; then
        info "Stopping old LaunchDaemon..."
        sudo launchctl bootout system "$old_plist" >/dev/null 2>&1 || true
        sudo rm -f "$old_plist"
        ok "Old LaunchDaemon removed"
    fi

    # Migrate data and settings
    if [[ -d "$old_prefix" ]]; then
        mkdir -p "$INSTALL_PREFIX"

        if [[ -f "$old_prefix/settings.json" ]] && [[ ! -f "$INSTALL_PREFIX/settings.json" ]]; then
            info "Migrating settings.json..."
            sudo cp "$old_prefix/settings.json" "$INSTALL_PREFIX/settings.json"
            chown "$(whoami)" "$INSTALL_PREFIX/settings.json"
            # Fix DATABASE_URL path in migrated settings
            if command -v sed &>/dev/null; then
                sed -i '' "s|${old_prefix}|${INSTALL_PREFIX}|g" "$INSTALL_PREFIX/settings.json" 2>/dev/null || true
            fi
            ok "Settings migrated (DATABASE_URL paths updated)"
        fi

        if [[ -d "$old_prefix/data" ]] && [[ ! -d "$INSTALL_PREFIX/data" ]]; then
            info "Migrating data directory..."
            sudo cp -R "$old_prefix/data" "$INSTALL_PREFIX/data"
            chown -R "$(whoami)" "$INSTALL_PREFIX/data"
            ok "Data migrated"
        fi

        # Remove old install directory
        info "Removing old install directory..."
        sudo rm -rf "$old_prefix"
        ok "Old install directory removed"
    fi

    # Remove old symlink
    if [[ -L "/usr/local/bin/rcflow" ]]; then
        sudo rm -f "/usr/local/bin/rcflow"
        ok "Old symlink removed"
    fi

    # Remove old service user
    if dscl . -read "/Users/${old_user}" &>/dev/null 2>&1; then
        info "Removing old service user: ${old_user}"
        sudo dscl . -delete "/Users/${old_user}" 2>/dev/null || true
        ok "Old service user removed"
    fi

    echo ""
    ok "Migration from LaunchDaemon complete!"
    echo ""
}

# ── Warn if running as root ──────────────────────────────────────────────
if [[ $EUID -eq 0 ]]; then
    warn "Running as root is not recommended. This installer sets up a"
    warn "user-level LaunchAgent and should run as your normal user."
    if ! $UNATTENDED; then
        read -rp "Continue anyway? [y/N] " confirm
        if [[ "$confirm" != [yY] ]]; then
            echo "Cancelled."
            exit 0
        fi
    fi
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/rcflow" ]]; then
    error "Cannot find rcflow executable in $SCRIPT_DIR"
    error "Run this script from inside the extracted bundle directory."
    exit 1
fi

BUNDLE_VERSION="unknown"
if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
    BUNDLE_VERSION="$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION")"
fi

echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  RCFlow Installer v${BUNDLE_VERSION} (macOS)${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# ── Migrate from old daemon install if present ───────────────────────────
if ! $SKIP_MIGRATION; then
    migrate_from_daemon
fi

# ── Pick up data migrated by pkg postinstall ─────────────────────────────
if [[ -f "$SCRIPT_DIR/settings.json.migrated" ]] && [[ ! -f "$INSTALL_PREFIX/settings.json" ]]; then
    mkdir -p "$INSTALL_PREFIX"
    mv "$SCRIPT_DIR/settings.json.migrated" "$INSTALL_PREFIX/settings.json"
    chmod 600 "$INSTALL_PREFIX/settings.json"
    ok "Migrated settings.json from old daemon install"
fi
if [[ -d "$SCRIPT_DIR/data.migrated" ]] && [[ ! -d "$INSTALL_PREFIX/data" ]]; then
    mkdir -p "$INSTALL_PREFIX"
    mv "$SCRIPT_DIR/data.migrated" "$INSTALL_PREFIX/data"
    ok "Migrated data from old daemon install"
fi

UPGRADING=false
if [[ -d "$INSTALL_PREFIX" ]] && [[ -f "$INSTALL_PREFIX/rcflow" ]]; then
    EXISTING_VERSION="unknown"
    if [[ -f "$INSTALL_PREFIX/VERSION" ]]; then
        EXISTING_VERSION="$(tr -d '[:space:]' < "$INSTALL_PREFIX/VERSION")"
    fi
    warn "Existing installation detected: v${EXISTING_VERSION} at ${INSTALL_PREFIX}"
    info "Upgrading to v${BUNDLE_VERSION}. Data and configuration will be preserved."
    UPGRADING=true
    echo ""
fi

if ! $UPGRADING; then
    prompt_value "Install directory" "$INSTALL_PREFIX" INSTALL_PREFIX
    prompt_value "Binary symlink directory" "$BIN_DIR" BIN_DIR
    prompt_value "Server port" "$RCFLOW_PORT" RCFLOW_PORT
    echo ""
fi

info "Install directory: ${INSTALL_PREFIX}"
info "Binary symlink dir: ${BIN_DIR}"
info "Server port:        ${RCFLOW_PORT}"
echo ""

stop_service

info "Installing to ${INSTALL_PREFIX}..."
mkdir -p "$INSTALL_PREFIX"

if [[ "$SCRIPT_DIR" != "$INSTALL_PREFIX" ]]; then
    cp -f "$SCRIPT_DIR/rcflow" "$INSTALL_PREFIX/rcflow"

    if [[ -d "$SCRIPT_DIR/_internal" ]]; then
        rm -rf "$INSTALL_PREFIX/_internal"
        cp -R "$SCRIPT_DIR/_internal" "$INSTALL_PREFIX/_internal"
    fi

    if [[ -d "$SCRIPT_DIR/tools" ]]; then
        mkdir -p "$INSTALL_PREFIX/tools"
        cp -f "$SCRIPT_DIR/tools/"*.json "$INSTALL_PREFIX/tools/" 2>/dev/null || true
        ok "Tool definitions installed"
    fi

    if [[ -d "$SCRIPT_DIR/migrations" ]]; then
        rm -rf "$INSTALL_PREFIX/migrations"
        cp -R "$SCRIPT_DIR/migrations" "$INSTALL_PREFIX/migrations"
        ok "Database migrations installed"
    fi

    if [[ -d "$SCRIPT_DIR/templates" ]]; then
        rm -rf "$INSTALL_PREFIX/templates"
        cp -R "$SCRIPT_DIR/templates" "$INSTALL_PREFIX/templates"
    fi

    for file_name in alembic.ini VERSION uninstall.sh install.sh LICENSE; do
        if [[ -f "$SCRIPT_DIR/$file_name" ]]; then
            cp -f "$SCRIPT_DIR/$file_name" "$INSTALL_PREFIX/$file_name"
        fi
    done
fi

chmod 755 "$INSTALL_PREFIX/rcflow"
if [[ -f "$INSTALL_PREFIX/install.sh" ]]; then
    chmod 755 "$INSTALL_PREFIX/install.sh"
fi
if [[ -f "$INSTALL_PREFIX/uninstall.sh" ]]; then
    chmod 755 "$INSTALL_PREFIX/uninstall.sh"
fi

ok "Files installed"

mkdir -p "$INSTALL_PREFIX/data" "$INSTALL_PREFIX/logs" "$INSTALL_PREFIX/certs"

if [[ ! -f "$INSTALL_PREFIX/settings.json" ]]; then
    info "Creating default configuration..."

    API_KEY="$(generate_api_key)"

    cat > "$INSTALL_PREFIX/settings.json" <<JSONEOF
{
  "RCFLOW_HOST": "0.0.0.0",
  "RCFLOW_PORT": "${RCFLOW_PORT}",
  "RCFLOW_API_KEY": "${API_KEY}",
  "DATABASE_URL": "sqlite+aiosqlite:///${INSTALL_PREFIX}/data/rcflow.db",
  "LLM_PROVIDER": "anthropic",
  "ANTHROPIC_API_KEY": "",
  "ANTHROPIC_MODEL": "claude-sonnet-4-6",
  "AWS_REGION": "us-east-1",
  "AWS_ACCESS_KEY_ID": "",
  "AWS_SECRET_ACCESS_KEY": "",
  "OPENAI_API_KEY": "",
  "OPENAI_MODEL": "gpt-5.4",
  "STT_PROVIDER": "wispr_flow",
  "STT_API_KEY": "",
  "TTS_PROVIDER": "none",
  "TTS_API_KEY": "",
  "PROJECTS_DIR": "~/Projects",
  "TOOLS_DIR": "${INSTALL_PREFIX}/tools",
  "TOOL_AUTO_UPDATE": "true",
  "TOOL_UPDATE_INTERVAL_HOURS": "6",
  "LOG_LEVEL": "INFO"
}
JSONEOF

    chmod 600 "$INSTALL_PREFIX/settings.json"
    ok "Configuration created with generated API key"
    echo ""
    echo -e "  ${YELLOW}API Key: ${API_KEY}${NC}"
    echo -e "  ${YELLOW}Save this key — you'll need it to connect clients.${NC}"
    echo -e "  ${YELLOW}Config file: ${INSTALL_PREFIX}/settings.json${NC}"
    echo ""
else
    ok "Existing configuration preserved at ${INSTALL_PREFIX}/settings.json"
fi

info "Running database migrations..."
cd "$INSTALL_PREFIX"
if ./rcflow migrate; then
    ok "Database migrations complete"
else
    error "Migration failed. Check your DATABASE_URL in ${INSTALL_PREFIX}/settings.json"
    error "You can retry with: cd ${INSTALL_PREFIX} && ./rcflow migrate"
fi

mkdir -p "$BIN_DIR"
ln -sfn "$INSTALL_PREFIX/rcflow" "$BIN_DIR/rcflow"
ok "Symlink installed at ${BIN_DIR}/rcflow"

# ── Check if BIN_DIR is in PATH ──────────────────────────────────────────
if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
    echo ""
    warn "${BIN_DIR} is not in your \$PATH."
    warn "Add it to your shell profile:"
    warn "  echo 'export PATH=\"${BIN_DIR}:\$PATH\"' >> ~/.zshrc"
    warn "  source ~/.zshrc"
fi

if $SETUP_SERVICE; then
    info "Setting up launchd LaunchAgent..."
    mkdir -p "$HOME/Library/LaunchAgents"
    PLIST_PATH="$HOME/Library/LaunchAgents/${SERVICE_LABEL}.plist"

    cat > "$PLIST_PATH" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${INSTALL_PREFIX}/rcflow</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${INSTALL_PREFIX}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>${INSTALL_PREFIX}/logs/service-stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${INSTALL_PREFIX}/logs/service-stderr.log</string>
</dict>
</plist>
PLISTEOF

    chmod 644 "$PLIST_PATH"
    launchctl load -w "$PLIST_PATH"

    if launchctl list "$SERVICE_LABEL" >/dev/null 2>&1; then
        ok "LaunchAgent installed and running"
    else
        warn "Service registration may have failed. Check: launchctl list ${SERVICE_LABEL}"
    fi
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Installation complete!           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  Install directory:  ${INSTALL_PREFIX}"
echo "  Configuration:      ${INSTALL_PREFIX}/settings.json"
echo "  Data directory:     ${INSTALL_PREFIX}/data"
echo "  Logs directory:     ${INSTALL_PREFIX}/logs"
echo "  Binary symlink:     ${BIN_DIR}/rcflow"
echo ""
if $SETUP_SERVICE; then
    echo "  Service commands:"
    echo "    launchctl list ${SERVICE_LABEL}"
    echo "    launchctl unload ${PLIST_PATH} && launchctl load -w ${PLIST_PATH}"
    echo "    launchctl unload ${PLIST_PATH}"
    echo ""
fi
echo "  Edit configuration:"
echo "    nano ${INSTALL_PREFIX}/settings.json"
if $SETUP_SERVICE; then
    echo "    launchctl unload ${PLIST_PATH} && launchctl load -w ${PLIST_PATH}"
else
    echo "    ${BIN_DIR}/rcflow"
fi
echo ""
echo "  Uninstall:"
echo "    ${INSTALL_PREFIX}/uninstall.sh"
echo ""

if ! $UPGRADING; then
    echo -e "  ${YELLOW}IMPORTANT: Edit ${INSTALL_PREFIX}/settings.json to set your ANTHROPIC_API_KEY${NC}"
    echo -e "  ${YELLOW}before using the server.${NC}"
    echo ""
fi
