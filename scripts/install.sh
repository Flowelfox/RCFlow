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
#   --no-service         Skip systemd service setup
#   --unattended         Non-interactive mode (use all defaults)
# ============================================================================

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────

INSTALL_PREFIX="/opt/rcflow"
SERVICE_USER="rcflow"
RCFLOW_PORT="53890"
SETUP_SERVICE=true
UNATTENDED=false

# ── Parse arguments ─────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)   INSTALL_PREFIX="$2"; shift 2 ;;
        --user)     SERVICE_USER="$2"; shift 2 ;;
        --port)     RCFLOW_PORT="$2"; shift 2 ;;
        --no-service) SETUP_SERVICE=false; shift ;;
        --unattended) UNATTENDED=true; shift ;;
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

# ── Banner ──────────────────────────────────────────────────────────────────

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       RCFlow Installer v${BUNDLE_VERSION}$(printf '%*s' $((14 - ${#BUNDLE_VERSION})) '')║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

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
    echo ""
fi

info "Install directory: ${INSTALL_PREFIX}"
info "Service user:      ${SERVICE_USER}"
info "Server port:       ${RCFLOW_PORT}"
echo ""

# ── Stop existing service ───────────────────────────────────────────────────

if systemctl is-active --quiet rcflow 2>/dev/null; then
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

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
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
echo "  Service commands:"
echo "    sudo systemctl status rcflow    # Check status"
echo "    sudo systemctl restart rcflow   # Restart"
echo "    sudo systemctl stop rcflow      # Stop"
echo "    journalctl -u rcflow -f         # View logs"
echo ""
echo "  Edit configuration:"
echo "    sudo nano ${INSTALL_PREFIX}/settings.json"
echo "    sudo systemctl restart rcflow"
echo ""
echo "  Uninstall:"
echo "    sudo ${INSTALL_PREFIX}/uninstall.sh"
echo ""

if ! $UPGRADING; then
    echo -e "  ${YELLOW}IMPORTANT: Edit ${INSTALL_PREFIX}/settings.json to set your ANTHROPIC_API_KEY${NC}"
    echo -e "  ${YELLOW}before using the server.${NC}"
    echo ""
fi
