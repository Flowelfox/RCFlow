#!/usr/bin/env sh
# ============================================================================
# RCFlow Worker — One-line Installer
#
# Install the latest RCFlow worker (backend server) with:
#
#   curl -fsSL https://rcflow.app/get-worker.sh | sh
#
# Or install a specific version:
#
#   curl -fsSL https://rcflow.app/get-worker.sh | RCFLOW_VERSION=0.35.0 sh
#
# All additional arguments are forwarded to the platform install script:
#
#   curl ... | sh -s -- --port 8080 --unattended
#
# Environment variables:
#   RCFLOW_VERSION    Pin a specific version (e.g. "0.35.0", without the "v" prefix)
#   RCFLOW_REPO       GitHub owner/repo (default: Flowelfox/RCFlow)
#   INSTALL_DIR       Override the install directory (forwarded to install.sh --prefix)
# ============================================================================

set -eu

# ── Constants ──────────────────────────────────────────────────────────────

REPO="${RCFLOW_REPO:-Flowelfox/RCFlow}"
GITHUB_API="https://api.github.com"
GITHUB_DL="https://github.com"

# ── Helpers ────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$*" >&2; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*" >&2; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*" >&2; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }
fatal() { error "$@"; exit 1; }

need_cmd() {
    if ! command -v "$1" > /dev/null 2>&1; then
        fatal "Required command '$1' not found. Please install it and try again."
    fi
}

# Portable download helper — prefers curl, falls back to wget
download() {
    url="$1"
    out="$2"
    if command -v curl > /dev/null 2>&1; then
        curl -fsSL -o "$out" "$url"
    elif command -v wget > /dev/null 2>&1; then
        wget -qO "$out" "$url"
    else
        fatal "Neither curl nor wget found. Install one and try again."
    fi
}

# Fetch URL content to stdout
fetch() {
    url="$1"
    if command -v curl > /dev/null 2>&1; then
        curl -fsSL "$url"
    elif command -v wget > /dev/null 2>&1; then
        wget -qO- "$url"
    else
        fatal "Neither curl nor wget found."
    fi
}

cleanup() {
    if [ -n "${TMPDIR_CREATED:-}" ] && [ -d "${TMPDIR_CREATED}" ]; then
        rm -rf "${TMPDIR_CREATED}"
    fi
    if [ -n "${DMG_MOUNT:-}" ] && [ -d "${DMG_MOUNT}" ]; then
        hdiutil detach "${DMG_MOUNT}" -quiet 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM

# ── Detect platform & architecture ────────────────────────────────────────

detect_platform() {
    os="$(uname -s)"
    case "$os" in
        Linux)  echo "linux" ;;
        Darwin) echo "macos" ;;
        *)      fatal "Unsupported operating system: $os. This installer supports Linux and macOS." ;;
    esac
}

detect_arch() {
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64)   echo "amd64" ;;
        aarch64|arm64)  echo "arm64" ;;
        *)              fatal "Unsupported architecture: $arch" ;;
    esac
}

# ── Resolve version ───────────────────────────────────────────────────────

resolve_version() {
    if [ -n "${RCFLOW_VERSION:-}" ]; then
        # Strip leading "v" if present
        echo "${RCFLOW_VERSION#v}"
        return
    fi

    info "Resolving latest release..."

    # Use the GitHub API redirect to find the latest release tag
    latest_tag=$(fetch "${GITHUB_API}/repos/${REPO}/releases/latest" \
        | grep '"tag_name"' | head -1 | sed 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/')

    if [ -z "$latest_tag" ]; then
        fatal "Could not determine the latest release. Set RCFLOW_VERSION explicitly."
    fi

    echo "${latest_tag#v}"
}

# ── Banner ─────────────────────────────────────────────────────────────────

show_banner() {
    printf "\n"
    printf "${CYAN}╔══════════════════════════════════════════╗${NC}\n"
    printf "${CYAN}║      RCFlow Worker Installer             ║${NC}\n"
    printf "${CYAN}╚══════════════════════════════════════════╝${NC}\n"
    printf "\n"
}

# ── Linux install ──────────────────────────────────────────────────────────

install_linux() {
    version="$1"
    arch="$2"
    shift 2

    artifact="rcflow-v${version}-linux-worker-${arch}.tar.gz"
    url="${GITHUB_DL}/${REPO}/releases/download/v${version}/${artifact}"

    TMPDIR_CREATED="$(mktemp -d)"
    archive="${TMPDIR_CREATED}/${artifact}"

    info "Downloading ${artifact}..."
    download "$url" "$archive"
    ok "Downloaded $(du -h "$archive" | cut -f1 | tr -d '[:space:]') archive"

    info "Extracting..."
    tar -xzf "$archive" -C "${TMPDIR_CREATED}"

    # The tarball contains a single top-level directory
    bundle_dir="${TMPDIR_CREATED}/rcflow-v${version}-linux-worker-${arch}"
    if [ ! -d "$bundle_dir" ]; then
        # Fall back to finding any directory with an rcflow binary
        bundle_dir="$(find "${TMPDIR_CREATED}" -maxdepth 2 -name rcflow -type f -print -quit 2>/dev/null | xargs dirname 2>/dev/null || true)"
        if [ -z "$bundle_dir" ] || [ ! -f "${bundle_dir}/rcflow" ]; then
            fatal "Could not locate rcflow binary in extracted archive."
        fi
    fi

    if [ ! -f "${bundle_dir}/install.sh" ]; then
        fatal "install.sh not found in extracted archive."
    fi

    chmod +x "${bundle_dir}/install.sh"

    # Build argument list for install.sh
    install_args=""
    if [ -n "${INSTALL_DIR:-}" ]; then
        install_args="--prefix ${INSTALL_DIR}"
    fi

    # Detect if stdin is not a terminal (piped) — run unattended
    if [ ! -t 0 ]; then
        install_args="${install_args} --unattended"
    fi

    info "Running installer..."
    # install.sh requires root on Linux
    if [ "$(id -u)" -ne 0 ]; then
        info "Requesting sudo to install system-wide..."
        # shellcheck disable=SC2086
        sudo "${bundle_dir}/install.sh" $install_args "$@"
    else
        # shellcheck disable=SC2086
        "${bundle_dir}/install.sh" $install_args "$@"
    fi
}

# ── macOS install ──────────────────────────────────────────────────────────

install_macos() {
    version="$1"
    arch="$2"
    shift 2

    artifact="rcflow-v${version}-macos-worker-${arch}.dmg"
    url="${GITHUB_DL}/${REPO}/releases/download/v${version}/${artifact}"

    TMPDIR_CREATED="$(mktemp -d)"
    dmg_path="${TMPDIR_CREATED}/${artifact}"

    info "Downloading ${artifact}..."
    download "$url" "$dmg_path"
    ok "Downloaded $(du -h "$dmg_path" | cut -f1 | tr -d '[:space:]') DMG"

    # Mount the DMG
    info "Mounting DMG..."
    DMG_MOUNT="$(hdiutil attach "$dmg_path" -nobrowse -noautoopen -mountrandom /tmp 2>/dev/null \
        | grep '/tmp/' | head -1 | awk '{print $NF}')"
    if [ -z "$DMG_MOUNT" ] || [ ! -d "$DMG_MOUNT" ]; then
        fatal "Failed to mount DMG."
    fi
    ok "Mounted at ${DMG_MOUNT}"

    # Find the .app inside the DMG.  PyInstaller's windowed macOS build is a
    # self-contained .app bundle: the executable lives in Contents/MacOS/ while
    # its runtime libraries (libpython3.12.dylib, extension modules) live in
    # Contents/Frameworks/.  The bootloader only finds those libraries when the
    # binary runs from inside this layout — copying the executable out of
    # Contents/MacOS leaves it unable to load libpython and it dies on startup
    # (the "_internal/libpython3.12.dylib (no such file)" error).  So we install
    # the whole .app bundle intact and run the binary in place.
    app_path="$(find "$DMG_MOUNT" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)"
    if [ -z "$app_path" ]; then
        fatal "No .app bundle found in DMG."
    fi
    if [ ! -f "${app_path}/Contents/MacOS/rcflow" ]; then
        fatal "rcflow binary not found in ${app_path}/Contents/MacOS"
    fi

    install_macos_app "$app_path" "$version" "$@"

    # Unmount DMG
    hdiutil detach "$DMG_MOUNT" -quiet 2>/dev/null || true
    DMG_MOUNT=""
}

install_macos_app() {
    src_app="$1"
    version="$2"
    shift 2

    # The .app is installed whole (Contents/Frameworks must stay beside the
    # executable).  Default to a per-user Applications directory so no sudo is
    # required — the LaunchAgent we register is user-level anyway.
    app_dir="${INSTALL_DIR:-$HOME/Applications}"
    bin_dir="$HOME/.local/bin"
    rcflow_port="53890"
    service_label="com.rcflow.server"
    setup_service=true
    unattended=false

    while [ $# -gt 0 ]; do
        case "$1" in
            --prefix)      app_dir="$2"; shift 2 ;;
            --app-dir)     app_dir="$2"; shift 2 ;;
            --bin-dir)     bin_dir="$2"; shift 2 ;;
            --port)        rcflow_port="$2"; shift 2 ;;
            --no-service)  setup_service=false; shift ;;
            --unattended)  unattended=true; shift ;;
            *)             shift ;;
        esac
    done

    # Detect if piped
    if [ ! -t 0 ]; then
        unattended=true
    fi

    app_name="$(basename "$src_app")"
    dest_app="${app_dir}/${app_name}"

    # Frozen macOS builds resolve config + data from a FIXED location, not the
    # install directory — the .app bundle itself is treated as read-only.  See
    # src/paths.py get_data_dir(): ~/Library/Application Support/rcflow.  The
    # settings.json and SQLite database live here and survive app re-installs.
    data_dir="$HOME/Library/Application Support/rcflow"
    settings_path="${data_dir}/settings.json"

    # Check for existing installation
    upgrading=false
    if [ -f "${dest_app}/Contents/MacOS/rcflow" ]; then
        warn "Existing installation detected at ${dest_app}"
        info "Upgrading to v${version}. Data and configuration will be preserved."
        upgrading=true
    fi

    if [ "$upgrading" = false ] && [ "$unattended" = false ]; then
        printf "${CYAN}Install directory${NC} [${app_dir}]: "
        read -r input
        app_dir="${input:-$app_dir}"
        dest_app="${app_dir}/${app_name}"

        printf "${CYAN}Binary symlink directory${NC} [${bin_dir}]: "
        read -r input
        bin_dir="${input:-$bin_dir}"

        printf "${CYAN}Server port${NC} [${rcflow_port}]: "
        read -r input
        rcflow_port="${input:-$rcflow_port}"
    fi

    macos_dir="${dest_app}/Contents/MacOS"
    rcflow_bin="${macos_dir}/rcflow"
    agent_plist="$HOME/Library/LaunchAgents/${service_label}.plist"

    # Stop any running LaunchAgent before replacing the bundle on disk.
    if [ -f "$agent_plist" ]; then
        launchctl unload "$agent_plist" > /dev/null 2>&1 || true
    fi

    info "Installing ${app_name} to ${app_dir}..."
    mkdir -p "$app_dir"
    rm -rf "$dest_app"
    cp -R "$src_app" "$dest_app"
    chmod 755 "$rcflow_bin"

    # Strip the Gatekeeper quarantine flag so an unsigned download can launch.
    xattr -dr com.apple.quarantine "$dest_app" 2>/dev/null || true
    ok "Application installed at ${dest_app}"

    # Create the user data directories the server writes to at runtime.
    mkdir -p "$data_dir/data" "$data_dir/logs" "$data_dir/certs"

    # Create settings.json if needed.  DATABASE_URL must be an ABSOLUTE path —
    # the binary's built-in default is relative to the process CWD.  TOOLS_DIR
    # is intentionally omitted so the binary uses its bundled tool definitions
    # in Contents/MacOS/tools.
    if [ ! -f "$settings_path" ]; then
        info "Creating default configuration..."

        api_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
            || openssl rand -base64 32 2>/dev/null \
            || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"

        cat > "$settings_path" <<JSONEOF
{
  "RCFLOW_HOST": "0.0.0.0",
  "RCFLOW_PORT": "${rcflow_port}",
  "RCFLOW_API_KEY": "${api_key}",
  "DATABASE_URL": "sqlite+aiosqlite:///${data_dir}/data/rcflow.db",
  "LLM_PROVIDER": "none",
  "ANTHROPIC_API_KEY": "",
  "ANTHROPIC_MODEL": "claude-sonnet-4-6",
  "OPENAI_API_KEY": "",
  "OPENAI_MODEL": "gpt-5.4",
  "STT_PROVIDER": "wispr_flow",
  "STT_API_KEY": "",
  "TTS_PROVIDER": "none",
  "TTS_API_KEY": "",
  "PROJECTS_DIR": "~/Projects",
  "TOOL_AUTO_UPDATE": "true",
  "TOOL_UPDATE_INTERVAL_HOURS": "6",
  "WSS_ENABLED": "true",
  "LOG_LEVEL": "INFO"
}
JSONEOF

        chmod 600 "$settings_path"
        ok "Configuration created with generated API key"
        printf "\n"
        printf "  ${YELLOW}API Key: ${api_key}${NC}\n"
        printf "  ${YELLOW}Save this key — you'll need it to connect clients.${NC}\n"
        printf "  ${YELLOW}Config file: ${settings_path}${NC}\n"
        printf "\n"
    else
        ok "Existing configuration preserved at ${settings_path}"
    fi

    # Run database migrations — run the binary IN PLACE so its libraries
    # resolve from Contents/Frameworks.
    info "Running database migrations..."
    if (cd "$macos_dir" && ./rcflow migrate); then
        ok "Database migrations complete"
    else
        warn "Migration failed. You can retry with: cd \"${macos_dir}\" && ./rcflow migrate"
    fi

    # Create symlink to the in-bundle binary
    mkdir -p "$bin_dir"
    ln -sfn "$rcflow_bin" "$bin_dir/rcflow"
    ok "Symlink installed at ${bin_dir}/rcflow"

    # Check PATH
    case ":$PATH:" in
        *":${bin_dir}:"*) ;;
        *)
            warn "${bin_dir} is not in your \$PATH."
            warn "Add it to your shell profile:"
            warn "  echo 'export PATH=\"${bin_dir}:\$PATH\"' >> ~/.zshrc"
            warn "  source ~/.zshrc"
            ;;
    esac

    # Setup LaunchAgent — delegate to the canonical installer in the in-bundle
    # binary so this curl-install matches the DMG/.pkg and `rcflow install`
    # exactly (crash-only KeepAlive; CLI + GUI control the same registration).
    # Running the in-bundle binary also ensures libpython resolves from
    # Contents/Frameworks.
    if [ "$setup_service" = true ]; then
        info "Registering launchd LaunchAgent via rcflow install..."
        if (cd "$macos_dir" && ./rcflow install --enable); then
            ok "LaunchAgent installed and running"
        else
            warn "Service registration may have failed. Check: launchctl print gui/$(id -u)/${service_label}"
        fi
    fi

    # Done
    printf "\n"
    printf "${GREEN}╔══════════════════════════════════════════╗${NC}\n"
    printf "${GREEN}║         Installation complete!           ║${NC}\n"
    printf "${GREEN}╚══════════════════════════════════════════╝${NC}\n"
    printf "\n"
    printf "  Application:        ${dest_app}\n"
    printf "  Configuration:      ${settings_path}\n"
    printf "  Data directory:     ${data_dir}/data\n"
    printf "  Binary symlink:     ${bin_dir}/rcflow\n"
    printf "\n"
    if [ "$upgrading" = false ]; then
        printf "  ${YELLOW}IMPORTANT: Configure an LLM provider before using the server.${NC}\n"
        printf "  ${YELLOW}Set provider credentials in ${settings_path}${NC}\n"
        printf "  ${YELLOW}or from the client UI (Worker settings).${NC}\n"
        printf "\n"
    fi
}

# ── Main ───────────────────────────────────────────────────────────────────

main() {
    show_banner

    platform="$(detect_platform)"
    arch="$(detect_arch)"
    version="$(resolve_version)"

    info "Platform: ${platform}"
    info "Architecture: ${arch}"
    info "Version: v${version}"

    case "$platform" in
        linux)  install_linux "$version" "$arch" "$@" ;;
        macos)  install_macos "$version" "$arch" "$@" ;;
    esac

    # Read the configured host:port from the installed settings.json
    bind_host="0.0.0.0"
    bind_port="53890"
    if [ "$platform" = "macos" ]; then
        # Frozen macOS builds keep config under ~/Library/Application Support
        # regardless of where the .app bundle is installed (see src/paths.py).
        settings_path="$HOME/Library/Application Support/rcflow/settings.json"
    elif [ -n "${INSTALL_DIR:-}" ]; then
        settings_path="${INSTALL_DIR}/settings.json"
    else
        settings_path="/opt/rcflow/settings.json"
    fi
    if [ -f "$settings_path" ]; then
        host_val="$(grep '"RCFLOW_HOST"' "$settings_path" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || true)"
        port_val="$(grep '"RCFLOW_PORT"' "$settings_path" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || true)"
        wss_val="$(grep '"WSS_ENABLED"' "$settings_path" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || true)"
        [ -n "$host_val" ] && bind_host="$host_val"
        [ -n "$port_val" ] && bind_port="$port_val"
    fi

    if [ "${wss_val:-}" = "true" ]; then
        proto="wss"
    else
        proto="ws"
    fi

    printf "\n${CYAN}✓ RCFlow Worker installed · running on ${proto}://${bind_host}:${bind_port}${NC}\n"
}

main "$@"
