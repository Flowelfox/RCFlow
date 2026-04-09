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

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
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

    info "Resolving latest release..." >&2

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

    # Find the .app inside the DMG
    app_path="$(find "$DMG_MOUNT" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)"
    if [ -z "$app_path" ]; then
        fatal "No .app bundle found in DMG."
    fi

    macos_dir="${app_path}/Contents/MacOS"
    if [ ! -f "${macos_dir}/rcflow" ]; then
        fatal "rcflow binary not found in ${macos_dir}"
    fi

    # Copy Contents/MacOS/* to a writable temp dir (DMG is read-only)
    bundle_dir="${TMPDIR_CREATED}/bundle"
    mkdir -p "$bundle_dir"
    cp -R "${macos_dir}/"* "$bundle_dir/"
    chmod +x "$bundle_dir/rcflow"

    # Check if install.sh is bundled inside the .app
    if [ -f "${bundle_dir}/install.sh" ]; then
        chmod +x "${bundle_dir}/install.sh"

        install_args=""
        if [ -n "${INSTALL_DIR:-}" ]; then
            install_args="--prefix ${INSTALL_DIR}"
        fi
        if [ ! -t 0 ]; then
            install_args="${install_args} --unattended"
        fi

        info "Running installer..."
        # shellcheck disable=SC2086
        "${bundle_dir}/install.sh" $install_args "$@"
    else
        # No bundled install.sh — perform inline install
        install_macos_inline "$bundle_dir" "$version" "$@"
    fi

    # Unmount DMG
    hdiutil detach "$DMG_MOUNT" -quiet 2>/dev/null || true
    DMG_MOUNT=""
}

install_macos_inline() {
    bundle_dir="$1"
    version="$2"
    shift 2

    # Parse passthrough arguments
    install_prefix="${INSTALL_DIR:-$HOME/.local/lib/rcflow}"
    bin_dir="$HOME/.local/bin"
    rcflow_port="53890"
    service_label="com.rcflow.server"
    setup_service=true
    unattended=false

    while [ $# -gt 0 ]; do
        case "$1" in
            --prefix)      install_prefix="$2"; shift 2 ;;
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

    # Check for existing installation
    upgrading=false
    if [ -d "$install_prefix" ] && [ -f "$install_prefix/rcflow" ]; then
        existing_version="unknown"
        if [ -f "$install_prefix/VERSION" ]; then
            existing_version="$(tr -d '[:space:]' < "$install_prefix/VERSION")"
        fi
        warn "Existing installation detected: v${existing_version} at ${install_prefix}"
        info "Upgrading to v${version}. Data and configuration will be preserved."
        upgrading=true
    fi

    if [ "$upgrading" = false ] && [ "$unattended" = false ]; then
        printf "${CYAN}Install directory${NC} [${install_prefix}]: "
        read -r input
        install_prefix="${input:-$install_prefix}"

        printf "${CYAN}Binary symlink directory${NC} [${bin_dir}]: "
        read -r input
        bin_dir="${input:-$bin_dir}"

        printf "${CYAN}Server port${NC} [${rcflow_port}]: "
        read -r input
        rcflow_port="${input:-$rcflow_port}"
    fi

    info "Installing to ${install_prefix}..."
    mkdir -p "$install_prefix"

    # Copy files
    cp -f "$bundle_dir/rcflow" "$install_prefix/rcflow"
    chmod 755 "$install_prefix/rcflow"

    if [ -d "$bundle_dir/_internal" ]; then
        rm -rf "$install_prefix/_internal"
        cp -R "$bundle_dir/_internal" "$install_prefix/_internal"
    fi

    for sub in tools migrations templates; do
        if [ -d "$bundle_dir/$sub" ]; then
            rm -rf "$install_prefix/$sub"
            cp -R "$bundle_dir/$sub" "$install_prefix/$sub"
        fi
    done

    for f in alembic.ini VERSION LICENSE; do
        if [ -f "$bundle_dir/$f" ]; then
            cp -f "$bundle_dir/$f" "$install_prefix/$f"
        fi
    done

    ok "Files installed"

    # Create data directories
    mkdir -p "$install_prefix/data" "$install_prefix/logs" "$install_prefix/certs"

    # Create settings.json if needed
    if [ ! -f "$install_prefix/settings.json" ]; then
        info "Creating default configuration..."

        api_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
            || openssl rand -base64 32 2>/dev/null \
            || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"

        cat > "$install_prefix/settings.json" <<JSONEOF
{
  "RCFLOW_HOST": "0.0.0.0",
  "RCFLOW_PORT": "${rcflow_port}",
  "RCFLOW_API_KEY": "${api_key}",
  "DATABASE_URL": "sqlite+aiosqlite:///${install_prefix}/data/rcflow.db",
  "LLM_PROVIDER": "anthropic",
  "ANTHROPIC_API_KEY": "",
  "ANTHROPIC_MODEL": "claude-sonnet-4-6",
  "OPENAI_API_KEY": "",
  "OPENAI_MODEL": "gpt-5.4",
  "STT_PROVIDER": "wispr_flow",
  "STT_API_KEY": "",
  "TTS_PROVIDER": "none",
  "TTS_API_KEY": "",
  "PROJECTS_DIR": "~/Projects",
  "TOOLS_DIR": "${install_prefix}/tools",
  "TOOL_AUTO_UPDATE": "true",
  "TOOL_UPDATE_INTERVAL_HOURS": "6",
  "LOG_LEVEL": "INFO"
}
JSONEOF

        chmod 600 "$install_prefix/settings.json"
        ok "Configuration created with generated API key"
        printf "\n"
        printf "  ${YELLOW}API Key: ${api_key}${NC}\n"
        printf "  ${YELLOW}Save this key — you'll need it to connect clients.${NC}\n"
        printf "  ${YELLOW}Config file: ${install_prefix}/settings.json${NC}\n"
        printf "\n"
    else
        ok "Existing configuration preserved at ${install_prefix}/settings.json"
    fi

    # Run database migrations
    info "Running database migrations..."
    if (cd "$install_prefix" && ./rcflow migrate); then
        ok "Database migrations complete"
    else
        warn "Migration failed. You can retry with: cd ${install_prefix} && ./rcflow migrate"
    fi

    # Create symlink
    mkdir -p "$bin_dir"
    ln -sfn "$install_prefix/rcflow" "$bin_dir/rcflow"
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

    # Setup LaunchAgent
    if [ "$setup_service" = true ]; then
        info "Setting up launchd LaunchAgent..."
        mkdir -p "$HOME/Library/LaunchAgents"
        plist_path="$HOME/Library/LaunchAgents/${service_label}.plist"

        # Unload existing
        if [ -f "$plist_path" ]; then
            launchctl unload "$plist_path" > /dev/null 2>&1 || true
        fi

        cat > "$plist_path" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${service_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${install_prefix}/rcflow</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${install_prefix}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>${install_prefix}/logs/service-stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${install_prefix}/logs/service-stderr.log</string>
</dict>
</plist>
PLISTEOF

        chmod 644 "$plist_path"
        launchctl load -w "$plist_path"

        if launchctl list "$service_label" > /dev/null 2>&1; then
            ok "LaunchAgent installed and running"
        else
            warn "Service registration may have failed. Check: launchctl list ${service_label}"
        fi
    fi

    # Done
    printf "\n"
    printf "${GREEN}╔══════════════════════════════════════════╗${NC}\n"
    printf "${GREEN}║         Installation complete!           ║${NC}\n"
    printf "${GREEN}╚══════════════════════════════════════════╝${NC}\n"
    printf "\n"
    printf "  Install directory:  ${install_prefix}\n"
    printf "  Configuration:      ${install_prefix}/settings.json\n"
    printf "  Data directory:     ${install_prefix}/data\n"
    printf "  Binary symlink:     ${bin_dir}/rcflow\n"
    printf "\n"
    if [ "$upgrading" = false ]; then
        printf "  ${YELLOW}IMPORTANT: Edit ${install_prefix}/settings.json to set your ANTHROPIC_API_KEY${NC}\n"
        printf "  ${YELLOW}before using the server.${NC}\n"
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
    if [ -n "${INSTALL_DIR:-}" ]; then
        settings_path="${INSTALL_DIR}/settings.json"
    elif [ "$platform" = "macos" ]; then
        settings_path="$HOME/.local/lib/rcflow/settings.json"
    else
        settings_path="/opt/rcflow/settings.json"
    fi
    if [ -f "$settings_path" ]; then
        host_val="$(grep '"RCFLOW_HOST"' "$settings_path" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || true)"
        port_val="$(grep '"RCFLOW_PORT"' "$settings_path" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || true)"
        [ -n "$host_val" ] && bind_host="$host_val"
        [ -n "$port_val" ] && bind_port="$port_val"
    fi

    printf "\n${CYAN}✓ RCFlow Worker installed · running on ${bind_host}:${bind_port}${NC}\n"
}

main "$@"
