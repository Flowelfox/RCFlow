#!/usr/bin/env bash
# ============================================================================
# RCFlow Linux Installer
#
# Installs RCFlow as a systemd service. Run as root:
#   sudo ./install.sh
#
# Options:
#   --prefix /path      Install directory (default: /opt/rcflow)
#   --user username      Service user (default: rcflow)
#   --port N             Server port (default: 53890)
#   --owner-user name    Main Linux user whose ~/Projects to access (default: $SUDO_USER)
#   --no-service         Skip systemd service setup
#   --unattended         Non-interactive mode (use all defaults)
# ============================================================================

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────

INSTALL_PREFIX="/opt/rcflow"
SERVICE_USER="rcflow"
RCFLOW_PORT="53890"
OWNER_USER="${SUDO_USER:-}"
SETUP_SERVICE=true
UNATTENDED=false

# ── Parse arguments ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)      INSTALL_PREFIX="$2"; shift 2 ;;
        --user)        SERVICE_USER="$2"; shift 2 ;;
        --port)        RCFLOW_PORT="$2"; shift 2 ;;
        --owner-user)  OWNER_USER="$2"; shift 2 ;;
        --no-service)  SETUP_SERVICE=false; shift ;;
        --unattended)  UNATTENDED=true; shift ;;
        -h|--help)
            head -15 "$0" | tail -13
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────────────

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
    # Generate a secure random API key
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null \
        || openssl rand -base64 32 2>/dev/null \
        || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32
}

# Check whether systemd is actually running (not the case on WSL2 by default)
has_systemd() { [ -d /run/systemd/system ]; }

# ── Checks ──────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    error "This installer must be run as root. Try: sudo $0"
    exit 1
fi

# Determine the directory where this script lives (the extracted bundle)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Verify this is a valid bundle
if [[ ! -f "$SCRIPT_DIR/rcflow" ]]; then
    error "Cannot find rcflow executable in $SCRIPT_DIR"
    error "Run this script from inside the extracted bundle directory."
    exit 1
fi

BUNDLE_VERSION="unknown"
if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
    BUNDLE_VERSION="$(cat "$SCRIPT_DIR/VERSION" | tr -d '[:space:]')"
fi

# ── Check for existing installation ─────────────────────────────────────────

UPGRADING=false
if [[ -d "$INSTALL_PREFIX" ]] && [[ -f "$INSTALL_PREFIX/rcflow" ]]; then
    EXISTING_VERSION="unknown"
    if [[ -f "$INSTALL_PREFIX/VERSION" ]]; then
        EXISTING_VERSION="$(cat "$INSTALL_PREFIX/VERSION" | tr -d '[:space:]')"
    fi
    warn "Existing installation detected: v${EXISTING_VERSION} at ${INSTALL_PREFIX}"
    info "Upgrading to v${BUNDLE_VERSION}. Data and configuration will be preserved."
    UPGRADING=true
    echo ""
fi

# ── Interactive configuration ───────────────────────────────────────────────

if ! $UPGRADING; then
    prompt_value "Install directory" "$INSTALL_PREFIX" INSTALL_PREFIX
    prompt_value "Service user" "$SERVICE_USER" SERVICE_USER
    prompt_value "Server port" "$RCFLOW_PORT" RCFLOW_PORT
    prompt_value "Owner user (whose ~/Projects to access, blank to skip)" "$OWNER_USER" OWNER_USER
    echo ""
fi

info "Install directory: ${INSTALL_PREFIX}"
info "Service user:      ${SERVICE_USER}"
info "Server port:       ${RCFLOW_PORT}"
info "Owner user:        ${OWNER_USER:-<none>}"

# ── Stop existing service ───────────────────────────────────────────────────

if has_systemd && systemctl is-active --quiet rcflow 2>/dev/null; then
    info "Stopping existing RCFlow service..."
    systemctl stop rcflow
    ok "Service stopped"
fi

# ── Create service user ─────────────────────────────────────────────────────

if ! id "$SERVICE_USER" &>/dev/null; then
    info "Creating system user: ${SERVICE_USER}"
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    ok "User created"
else
    ok "User '${SERVICE_USER}' already exists"
fi

# ── Grant access to owner user's home directory ──────────────────────────────

if [[ -n "$OWNER_USER" ]]; then
    if id "$OWNER_USER" &>/dev/null; then
        info "Granting ${SERVICE_USER} access to /home/${OWNER_USER}/Projects..."
        usermod -aG "$OWNER_USER" "$SERVICE_USER"
        chmod 710 "/home/$OWNER_USER"
        if [[ -d "/home/$OWNER_USER/Projects" ]]; then
            chmod 750 "/home/$OWNER_USER/Projects"
        else
            warn "/home/$OWNER_USER/Projects does not exist — skipping chmod on Projects"
        fi
        ok "Group '${OWNER_USER}' membership and directory permissions set"
    else
        warn "Owner user '${OWNER_USER}' not found — skipping home directory access setup"
        OWNER_USER=""
    fi
fi

# ── Copy SSH key for git push operations ────────────────────────────────────

if [[ -n "$OWNER_USER" ]]; then
    SSH_KEY=""
    for key_file in id_ed25519 id_ecdsa id_rsa; do
        if [[ -f "/home/$OWNER_USER/.ssh/$key_file" ]]; then
            SSH_KEY="/home/$OWNER_USER/.ssh/$key_file"
            break
        fi
    done
    if [[ -n "$SSH_KEY" ]]; then
        info "Copying SSH key for git operations (${SSH_KEY})..."
        mkdir -p "${INSTALL_PREFIX}/ssh"
        cp "$SSH_KEY" "${INSTALL_PREFIX}/ssh/id"
        chown "$SERVICE_USER:$SERVICE_USER" "${INSTALL_PREFIX}/ssh" "${INSTALL_PREFIX}/ssh/id"
        chmod 700 "${INSTALL_PREFIX}/ssh"
        chmod 600 "${INSTALL_PREFIX}/ssh/id"
        printf 'GIT_SSH_COMMAND="ssh -i %s/ssh/id -o StrictHostKeyChecking=accept-new"\n' \
            "${INSTALL_PREFIX}" > "${INSTALL_PREFIX}/env"
        chown "$SERVICE_USER:$SERVICE_USER" "${INSTALL_PREFIX}/env"
        ok "SSH key configured — git push will authenticate as ${OWNER_USER}"
    else
        warn "No SSH key found in /home/${OWNER_USER}/.ssh/ — git push over SSH needs manual setup"
    fi
fi

# ── Create install directory ────────────────────────────────────────────────

info "Installing to ${INSTALL_PREFIX}..."
mkdir -p "$INSTALL_PREFIX"

# Copy executable and internal dependencies
cp -f "$SCRIPT_DIR/rcflow" "$INSTALL_PREFIX/rcflow"
chmod 755 "$INSTALL_PREFIX/rcflow"

# Copy _internal directory (PyInstaller runtime)
if [[ -d "$SCRIPT_DIR/_internal" ]]; then
    rm -rf "$INSTALL_PREFIX/_internal"
    cp -r "$SCRIPT_DIR/_internal" "$INSTALL_PREFIX/_internal"
fi

# Copy tool definitions
if [[ -d "$SCRIPT_DIR/tools" ]]; then
    # Preserve user modifications to tools — merge new files
    mkdir -p "$INSTALL_PREFIX/tools"
    cp -n "$SCRIPT_DIR/tools/"*.json "$INSTALL_PREFIX/tools/" 2>/dev/null || true
    # Update existing tool files
    cp -f "$SCRIPT_DIR/tools/"*.json "$INSTALL_PREFIX/tools/" 2>/dev/null || true
    ok "Tool definitions installed"
fi

# Copy alembic migrations
if [[ -d "$SCRIPT_DIR/migrations" ]]; then
    rm -rf "$INSTALL_PREFIX/migrations"
    cp -r "$SCRIPT_DIR/migrations" "$INSTALL_PREFIX/migrations"
    ok "Database migrations installed"
fi

# Copy alembic.ini
if [[ -f "$SCRIPT_DIR/alembic.ini" ]]; then
    cp -f "$SCRIPT_DIR/alembic.ini" "$INSTALL_PREFIX/alembic.ini"
fi

# Copy templates
if [[ -d "$SCRIPT_DIR/templates" ]]; then
    rm -rf "$INSTALL_PREFIX/templates"
    cp -r "$SCRIPT_DIR/templates" "$INSTALL_PREFIX/templates"
fi

# Copy VERSION
cp -f "$SCRIPT_DIR/VERSION" "$INSTALL_PREFIX/VERSION"

# Copy uninstall script
if [[ -f "$SCRIPT_DIR/uninstall.sh" ]]; then
    cp -f "$SCRIPT_DIR/uninstall.sh" "$INSTALL_PREFIX/uninstall.sh"
    chmod 755 "$INSTALL_PREFIX/uninstall.sh"
fi

ok "Files installed"

# ── Create data directories ─────────────────────────────────────────────────

mkdir -p "$INSTALL_PREFIX/data"
mkdir -p "$INSTALL_PREFIX/logs"
mkdir -p "$INSTALL_PREFIX/certs"

# ── Create settings.json configuration ────────────────────────────────────────

if [[ ! -f "$INSTALL_PREFIX/settings.json" ]]; then
    info "Creating default configuration..."

    API_KEY=$(generate_api_key)

    if [[ -n "$OWNER_USER" ]]; then
        PROJECTS_DIR_VALUE="/home/$OWNER_USER/Projects"
    else
        PROJECTS_DIR_VALUE="~/Projects"
    fi

    cat > "$INSTALL_PREFIX/settings.json" <<JSONEOF
{
  "RCFLOW_HOST": "0.0.0.0",
  "RCFLOW_PORT": "${RCFLOW_PORT}",
  "RCFLOW_API_KEY": "${API_KEY}",
  "RCFLOW_BACKEND_ID": "",
  "DATABASE_URL": "sqlite+aiosqlite:///${INSTALL_PREFIX}/data/rcflow.db",
  "WS_ALLOWED_ORIGINS": "",
  "WSS_ENABLED": "true",
  "SSL_CERTFILE": "",
  "SSL_KEYFILE": "",
  "LLM_PROVIDER": "anthropic",
  "ANTHROPIC_API_KEY": "",
  "ANTHROPIC_MODEL": "claude-sonnet-4-6",
  "AWS_REGION": "us-east-1",
  "AWS_ACCESS_KEY_ID": "",
  "AWS_SECRET_ACCESS_KEY": "",
  "OPENAI_API_KEY": "",
  "OPENAI_MODEL": "gpt-5.4",
  "CODEX_API_KEY": "",
  "TITLE_MODEL": "",
  "TASK_MODEL": "",
  "GLOBAL_PROMPT": "",
  "PROJECTS_DIR": "${PROJECTS_DIR_VALUE}",
  "TOOLS_DIR": "${INSTALL_PREFIX}/tools",
  "TOOL_AUTO_UPDATE": "true",
  "TOOL_UPDATE_INTERVAL_HOURS": "6",
  "SESSION_INPUT_TOKEN_LIMIT": "0",
  "SESSION_OUTPUT_TOKEN_LIMIT": "0",
  "ARTIFACT_INCLUDE_PATTERN": "*.md",
  "ARTIFACT_EXCLUDE_PATTERN": "node_modules/**,__pycache__/**,.git/**,.venv/**,venv/**,.env/**,build/**,dist/**,target/**,*.pyc",
  "ARTIFACT_AUTO_SCAN": "true",
  "ARTIFACT_MAX_FILE_SIZE": "5242880",
  "LINEAR_API_KEY": "",
  "LINEAR_TEAM_ID": "",
  "LINEAR_SYNC_ON_STARTUP": "false",
  "TELEMETRY_RETENTION_DAYS": "90",
  "LOG_LEVEL": "INFO"
}
JSONEOF

    chmod 600 "$INSTALL_PREFIX/settings.json"

    # Write the API key to a root-readable file instead of printing it to
    # stdout (which may be captured in shell history, logs, or CI output).
    echo "$API_KEY" > "$INSTALL_PREFIX/initial-key.txt"
    chmod 600 "$INSTALL_PREFIX/initial-key.txt"

    ok "Configuration created with generated API key"
    echo ""
    echo -e "  ${YELLOW}API key saved to: ${INSTALL_PREFIX}/initial-key.txt${NC}"
    echo -e "  ${YELLOW}Read with: sudo cat ${INSTALL_PREFIX}/initial-key.txt${NC}"
    echo -e "  ${YELLOW}Delete after copying: sudo rm ${INSTALL_PREFIX}/initial-key.txt${NC}"
    echo -e "  ${YELLOW}Config file: ${INSTALL_PREFIX}/settings.json${NC}"
    echo ""
else
    ok "Existing configuration preserved at ${INSTALL_PREFIX}/settings.json"
fi

# ── Fix ownership ───────────────────────────────────────────────────────────

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_PREFIX"
ok "Ownership set to ${SERVICE_USER}"

# ── Run database migrations ─────────────────────────────────────────────────

info "Running database migrations..."
cd "$INSTALL_PREFIX"
su -s /bin/bash "$SERVICE_USER" -c "cd ${INSTALL_PREFIX} && ./rcflow migrate" || {
    error "Migration failed. Check your DATABASE_URL in ${INSTALL_PREFIX}/settings.json"
    error "You can retry with: cd ${INSTALL_PREFIX} && sudo -u ${SERVICE_USER} ./rcflow migrate"
}
ok "Database migrations complete"

# ── Setup systemd service ───────────────────────────────────────────────────

if $SETUP_SERVICE; then
    if has_systemd; then
        info "Setting up systemd service..."

        # Generate service file from template, substituting actual values
        cat > /etc/systemd/system/rcflow.service <<SVCEOF
[Unit]
Description=RCFlow Action Server
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_PREFIX}
# Settings loaded from ${INSTALL_PREFIX}/settings.json by the application
ExecStart=${INSTALL_PREFIX}/rcflow
Restart=on-failure
RestartSec=5

# Allow rcflow to read git repos owned by other users (git >= 2.35.2 safe.directory check)
Environment="GIT_CONFIG_COUNT=1"
Environment="GIT_CONFIG_KEY_0=safe.directory"
Environment="GIT_CONFIG_VALUE_0=*"
# SSH key and other optional overrides (written by installer when an owner SSH key is found)
EnvironmentFile=-${INSTALL_PREFIX}/env

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=no
ReadWritePaths=${INSTALL_PREFIX}/data ${INSTALL_PREFIX}/logs ${INSTALL_PREFIX}/certs ${INSTALL_PREFIX}/managed-tools
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

        systemctl daemon-reload
        systemctl enable rcflow
        ok "Systemd service installed and enabled"

        info "Starting RCFlow service..."
        systemctl start rcflow

        # Wait a moment and check status
        sleep 2
        if systemctl is-active --quiet rcflow; then
            ok "RCFlow is running!"
        else
            warn "Service may have failed to start. Check: journalctl -u rcflow -n 50"
        fi
    else
        warn "systemd not running — skipping service setup"
        warn "To start RCFlow manually: cd ${INSTALL_PREFIX} && sudo -u ${SERVICE_USER} ./rcflow"
    fi
fi

# ── Done ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Installation complete!           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  Install directory:  ${INSTALL_PREFIX}"
echo "  Configuration:      ${INSTALL_PREFIX}/settings.json"
echo "  Data directory:     ${INSTALL_PREFIX}/data"
echo "  Logs directory:     ${INSTALL_PREFIX}/logs"
echo ""
if has_systemd; then
    echo "  Service commands:"
    echo "    sudo systemctl status rcflow    # Check status"
    echo "    sudo systemctl restart rcflow   # Restart"
    echo "    sudo systemctl stop rcflow      # Stop"
    echo "    journalctl -u rcflow -f         # View logs"
    echo ""
    echo "  Edit configuration:"
    echo "    sudo nano ${INSTALL_PREFIX}/settings.json"
    echo "    sudo systemctl restart rcflow"
else
    echo "  Service commands (systemd not available — WSL2 or similar):"
    echo "    sudo service rcflow status      # Check status"
    echo "    sudo service rcflow restart     # Restart"
    echo "    sudo service rcflow stop        # Stop"
    echo "    tail -f ${INSTALL_PREFIX}/logs/rcflow.log   # View logs"
    echo ""
    echo "  Edit configuration:"
    echo "    sudo nano ${INSTALL_PREFIX}/settings.json"
    echo "    sudo service rcflow restart"
fi
echo ""
echo "  Uninstall:"
echo "    sudo ${INSTALL_PREFIX}/uninstall.sh"
echo ""

if ! $UPGRADING; then
    echo -e "  ${YELLOW}IMPORTANT: Edit ${INSTALL_PREFIX}/settings.json to set your ANTHROPIC_API_KEY${NC}"
    echo -e "  ${YELLOW}before using the server.${NC}"
    echo ""
fi
