# RCFlow project commands

set shell := ["bash", "-cu"]
set windows-shell := ["powershell", "-NoLogo", "-Command"]

# Show available recipes
@default:
    just --list

# Install production dependencies
install:
    uv sync

# Install with dev dependencies
dev:
    uv sync --extra dev
    pre-commit install

# Linting
lint:
    uv run ruff check src/ tests/

# Format and fix code
format:
    uv run ruff format src/ tests/
    uv run ruff check --fix src/ tests/

# Type checking
typecheck:
    ty check src/

# Run all tests (Python + Flutter)
test:
    uv run pytest tests/ -v
    cd rcflowclient && flutter test

# Run tests with coverage
coverage:
    uv run pytest tests/ -v --cov=src --cov-report=term-missing

# Run all static checks (ruff + ty + flutter analyze)
check:
    uv run ruff check src/ tests/
    ty check src/
    cd rcflowclient && flutter analyze

# Run the server
run:
    uv run rcflow

# Generate a new Alembic migration
migrate-gen msg:
    uv run alembic revision --autogenerate -m "{{ msg }}"

# Apply migrations
migrate:
    uv run alembic upgrade head

# Rollback last migration
migrate-down:
    uv run alembic downgrade -1

# Build distributable package for current platform
bundle *FLAGS:
    uv run python scripts/bundle.py {{ FLAGS }}

# Build Linux backend .deb package (must be on Linux)
bundle-linux-backend *FLAGS:
    uv run --extra bundle python scripts/bundle.py --platform linux --installer {{ FLAGS }}

# Build and install Linux backend .deb package (must be on Linux)
bundle-linux-backend-install:
    uv run --extra bundle python scripts/bundle.py --platform linux --install

# Build macOS backend DMG (.app bundle, must be on macOS)
# --extra tray installs pystray + Pillow for the menu bar UI and DMG background
[macos]
bundle-macos-backend *FLAGS:
    uv run --extra bundle --extra tray python scripts/bundle.py --platform macos --installer {{ FLAGS }}

# Uninstall RCFlow backend worker from macOS (app bundle + CLI install + LaunchAgents + data)
[macos]
uninstall-macos *FLAGS:
    bash scripts/uninstall_macos.sh {{ FLAGS }}

# Build and install macOS backend DMG (.app bundle, must be on macOS)
[macos]
bundle-macos-backend-install:
    uv run --extra bundle --extra tray python scripts/bundle.py --platform macos --install

# Build Linux Flutter client .deb (must be on Linux)
# Requires: cmake, ninja, clang, pkg-config, libgtk-3-dev, dpkg-deb
# Install missing deps with: sudo apt-get install cmake ninja-build clang pkg-config libgtk-3-dev dpkg
[unix]
bundle-linux-client:
    #!/usr/bin/env bash
    set -euo pipefail
    missing=()
    for cmd in cmake ninja clang pkg-config dpkg-deb; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if (( ${#missing[@]} > 0 )); then
        printf '\nERROR: Missing Linux build dependencies: %s\n\n' "${missing[*]}"
        printf 'Install them on Debian/Ubuntu with:\n'
        printf '  sudo apt-get install cmake ninja-build clang pkg-config libgtk-3-dev dpkg\n\n'
        exit 1
    fi
    (cd rcflowclient && flutter build linux --release)
    mkdir -p dist
    CLIENT_VERSION=$(grep '^version:' rcflowclient/pubspec.yaml | sed 's/version: //' | sed 's/+.*//')
    PKG_NAME="rcflow-v${CLIENT_VERSION}-linux-client-amd64"
    DEB_ROOT=$(mktemp -d)
    trap "rm -rf '$DEB_ROOT'" EXIT
    install -Dm755 rcflowclient/build/linux/x64/release/bundle/rcflow \
      "$DEB_ROOT/opt/rcflowclient/rcflowclient"
    cp -r rcflowclient/build/linux/x64/release/bundle/data "$DEB_ROOT/opt/rcflowclient/"
    cp -r rcflowclient/build/linux/x64/release/bundle/lib "$DEB_ROOT/opt/rcflowclient/"
    mkdir -p "$DEB_ROOT/usr/bin"
    ln -s /opt/rcflowclient/rcflowclient "$DEB_ROOT/usr/bin/rcflowclient"
    mkdir -p "$DEB_ROOT/DEBIAN"
    printf 'Package: rcflow-client\nVersion: %s\nArchitecture: amd64\nMaintainer: RCFlow <rcflow@localhost>\nDescription: RCFlow Desktop Client\n Self-contained RCFlow Flutter desktop client.\nSection: net\nPriority: optional\n' \
      "$CLIENT_VERSION" > "$DEB_ROOT/DEBIAN/control"
    dpkg-deb --build --root-owner-group "$DEB_ROOT" "dist/${PKG_NAME}.deb"
    printf 'Built dist/%s.deb\n' "$PKG_NAME"

# Build and install Linux Flutter client .deb (must be on Linux)
[unix]
bundle-linux-client-install: bundle-linux-client
    #!/usr/bin/env bash
    set -euo pipefail
    CLIENT_VERSION=$(grep '^version:' rcflowclient/pubspec.yaml | sed 's/version: //' | sed 's/+.*//')
    sudo dpkg -i "dist/rcflow-v${CLIENT_VERSION}-linux-client-amd64.deb"
    echo "Installed rcflow-client to /opt/rcflowclient"

# Build macOS Flutter client .dmg (must be on macOS)
[macos]
bundle-macos-client:
    #!/usr/bin/env bash
    set -euo pipefail
    (cd rcflowclient && flutter build macos --release)
    mkdir -p dist
    CLIENT_VERSION=$(grep '^version:' rcflowclient/pubspec.yaml | sed 's/version: //' | sed 's/+.*//')
    CLIENT_ARCH=$(uname -m | sed 's/x86_64/amd64/')
    DMG_NAME="rcflow-v${CLIENT_VERSION}-macos-client-${CLIENT_ARCH}"
    APP_PATH="rcflowclient/build/macos/Build/Products/Release/RCFlow.app"
    STAGE=$(mktemp -d)
    cp -R "$APP_PATH" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"
    hdiutil create -srcfolder "$STAGE" -volname "RCFlow Client" -fs HFS+ -format UDZO \
      -o "dist/${DMG_NAME}.dmg"
    rm -rf "$STAGE"
    printf 'Built dist/%s.dmg\n' "$DMG_NAME"

# Build and install macOS Flutter client (must be on macOS)
[macos]
bundle-macos-client-install: bundle-macos-client
    #!/usr/bin/env bash
    set -euo pipefail
    CLIENT_VERSION=$(grep '^version:' rcflowclient/pubspec.yaml | sed 's/version: //' | sed 's/+.*//')
    CLIENT_ARCH=$(uname -m | sed 's/x86_64/amd64/')
    DMG="dist/rcflow-v${CLIENT_VERSION}-macos-client-${CLIENT_ARCH}.dmg"
    MOUNT=$(hdiutil attach "$DMG" -nobrowse | awk '/\/Volumes\//{print $NF}')
    mkdir -p ~/Applications
    cp -R "$MOUNT/RCFlow.app" ~/Applications/
    hdiutil detach "$MOUNT" -quiet
    echo "Installed to ~/Applications/RCFlow.app"

# Build Windows Flutter client .exe installer (must be on Windows)
# Requires Inno Setup 6 (iscc.exe on PATH or at default install location)
[windows]
bundle-windows-client:
    Set-Location rcflowclient; flutter build windows --release
    $appVersion = ((Get-Content rcflowclient/pubspec.yaml | Select-String '^version:').Line -replace 'version: ', '' -replace '\+.*', ''); $bundleDir = (Resolve-Path 'rcflowclient\build\windows\x64\runner\Release').Path; $outputFilename = "rcflow-v${appVersion}-windows-client-amd64"; if (-not (Test-Path dist)) { New-Item -ItemType Directory -Path dist | Out-Null }; iscc scripts\inno_setup_client.iss "/DBundleDir=$bundleDir" "/DAppVersion=$appVersion" "/DArch=amd64" "/DOutputDir=dist" "/DOutputFilename=$outputFilename"

# Build and install Windows Flutter client (must be on Windows)
[windows]
bundle-windows-client-install: bundle-windows-client
    $appVersion = ((Get-Content rcflowclient/pubspec.yaml | Select-String '^version:').Line -replace 'version: ', '' -replace '\+.*', ''); $installer = "dist\rcflow-v${appVersion}-windows-client-amd64.exe"; Start-Process -FilePath $installer -Wait

# Build Windows backend installer (setup.exe, must be on Windows)
[windows]
bundle-windows-backend *FLAGS:
    uv run --extra tray --extra bundle python scripts/bundle.py --platform windows --installer {{ FLAGS }}

# Build and install Windows backend (setup.exe, must be on Windows)
[windows]
bundle-windows-backend-install:
    uv run --extra tray --extra bundle python scripts/bundle.py --platform windows --install

# Start Windows Android emulator (cold boot)
[unix]
start-emulator:
    ./scripts/start-emulator.sh

# Setup WSL2 ADB connection to Windows emulator
[unix]
setup-emulator:
    ./scripts/setup-emulator.sh

# Run Flutter app on Android emulator in hot reload mode (WSL2 — connects to Windows emulator)
[unix]
run-android:
    cd rcflowclient && flutter run -d $(grep nameserver /etc/resolv.conf | awk '{print $2}'):15555

# Build Flutter debug APK
[unix]
flutter-build:
    cd rcflowclient && flutter build apk --debug
    @mkdir -p build/artifacts
    cp rcflowclient/build/app/outputs/flutter-apk/app-debug.apk build/artifacts/

# Build Flutter release APK (arm64 only — fastest for local testing)
# CI builds all ABIs directly via: flutter build apk --release --split-per-abi
[unix]
flutter-release:
    cd rcflowclient && flutter build apk --release --target-platform android-arm64
    @mkdir -p build/artifacts
    cp rcflowclient/build/app/outputs/flutter-apk/app-release.apk build/artifacts/

# Build Flutter Windows desktop app (release)
[windows]
flutter-windows:
    Set-Location rcflowclient; flutter build windows --release
    if (-not (Test-Path build/artifacts)) { New-Item -ItemType Directory -Path build/artifacts | Out-Null }
    Copy-Item -Recurse -Force rcflowclient\build\windows\x64\runner\Release build\artifacts\windows

# Clean build artifacts
[unix]
clean:
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
    rm -rf dist/ build/ *.egg-info/ htmlcov/ .coverage

# Clean build artifacts (Windows)
[windows]
clean:
    if (Test-Path dist) { Remove-Item -Recurse -Force dist }
    if (Test-Path build) { Remove-Item -Recurse -Force build }
    if (Test-Path htmlcov) { Remove-Item -Recurse -Force htmlcov }
    if (Test-Path .coverage) { Remove-Item -Force .coverage }
    Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    Get-ChildItem -Recurse -Directory -Filter .pytest_cache | Remove-Item -Recurse -Force
    Get-ChildItem -Recurse -Directory -Filter .ruff_cache | Remove-Item -Recurse -Force
