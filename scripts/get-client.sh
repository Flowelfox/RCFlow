#!/usr/bin/env sh
# ============================================================================
# RCFlow Client — One-line Installer
#
# Install the latest RCFlow desktop client with:
#
#   curl -fsSL https://rcflow.app/get-client.sh | sh
#
# Or install a specific version:
#
#   curl -fsSL https://rcflow.app/get-client.sh | RCFLOW_VERSION=0.35.0 sh
#
# Environment variables:
#   RCFLOW_VERSION    Pin a specific version (e.g. "0.35.0", without the "v" prefix)
#   RCFLOW_REPO       GitHub owner/repo (default: Flowelfox/RCFlow)
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
        echo "${RCFLOW_VERSION#v}"
        return
    fi

    info "Resolving latest release..."

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
    printf "${CYAN}║      RCFlow Client Installer             ║${NC}\n"
    printf "${CYAN}╚══════════════════════════════════════════╝${NC}\n"
    printf "\n"
}

# ── Linux install (.deb) ──────────────────────────────────────────────────

install_linux() {
    version="$1"
    arch="$2"

    artifact="rcflow-v${version}-linux-client-${arch}.deb"
    url="${GITHUB_DL}/${REPO}/releases/download/v${version}/${artifact}"

    TMPDIR_CREATED="$(mktemp -d)"
    deb_path="${TMPDIR_CREATED}/${artifact}"

    info "Downloading ${artifact}..."
    download "$url" "$deb_path"
    ok "Downloaded $(du -h "$deb_path" | cut -f1 | tr -d '[:space:]') package"

    info "Installing .deb package..."
    if [ "$(id -u)" -ne 0 ]; then
        info "Requesting sudo to install system-wide..."
        sudo dpkg -i "$deb_path"
    else
        dpkg -i "$deb_path"
    fi

    ok "RCFlow Client installed"
    printf "\n"
    printf "  Launch with: ${CYAN}rcflowclient${NC}\n"
    printf "\n"
}

# ── macOS install (.dmg) ──────────────────────────────────────────────────

install_macos() {
    version="$1"
    arch="$2"

    artifact="rcflow-v${version}-macos-client-${arch}.dmg"
    url="${GITHUB_DL}/${REPO}/releases/download/v${version}/${artifact}"

    TMPDIR_CREATED="$(mktemp -d)"
    dmg_path="${TMPDIR_CREATED}/${artifact}"

    info "Downloading ${artifact}..."
    download "$url" "$dmg_path"
    ok "Downloaded $(du -h "$dmg_path" | cut -f1 | tr -d '[:space:]') DMG"

    info "Mounting DMG..."
    DMG_MOUNT="$(hdiutil attach "$dmg_path" -nobrowse -noautoopen -mountrandom /tmp 2>/dev/null \
        | grep '/tmp/' | head -1 | awk '{print $NF}')"
    if [ -z "$DMG_MOUNT" ] || [ ! -d "$DMG_MOUNT" ]; then
        fatal "Failed to mount DMG."
    fi

    app_path="$(find "$DMG_MOUNT" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)"
    if [ -z "$app_path" ]; then
        fatal "No .app bundle found in DMG."
    fi

    app_name="$(basename "$app_path")"
    dest="/Applications/${app_name}"

    if [ -d "$dest" ]; then
        warn "Removing existing ${app_name}..."
        rm -rf "$dest"
    fi

    info "Copying ${app_name} to /Applications..."
    cp -R "$app_path" "$dest"

    hdiutil detach "$DMG_MOUNT" -quiet 2>/dev/null || true
    DMG_MOUNT=""

    ok "RCFlow Client installed to /Applications/${app_name}"
    printf "\n"
    printf "  Launch from: ${CYAN}/Applications/${app_name}${NC}\n"
    printf "  Or:          ${CYAN}open -a '${app_name%.app}'${NC}\n"
    printf "\n"
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
    printf "\n"

    case "$platform" in
        linux)  install_linux "$version" "$arch" ;;
        macos)  install_macos "$version" "$arch" ;;
    esac

    printf "\n${CYAN}✓ RCFlow Client installed${NC}\n"
}

main "$@"
