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

# Run tests
test:
    uv run pytest tests/ -v

# Run tests with coverage
coverage:
    uv run pytest tests/ -v --cov=src --cov-report=term-missing

# Run all checks (lint + typecheck + test)
check: lint typecheck test

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
bundle:
    uv run python scripts/bundle.py

# Build Linux backend .deb package (must be on Linux)
bundle-linux-backend:
    uv run --extra bundle python scripts/bundle.py --platform linux --installer

# Build and install Linux backend .deb package (must be on Linux)
bundle-linux-backend-install:
    uv run --extra bundle python scripts/bundle.py --platform linux --install

# Build macOS backend installer (.pkg, must be on macOS)
[macos]
bundle-macos-backend:
    uv run --extra bundle python scripts/bundle.py --platform macos --installer

# Build and install macOS backend (.pkg, must be on macOS)
[macos]
bundle-macos-backend-install:
    uv run --extra bundle python scripts/bundle.py --platform macos --install

# Build Linux Flutter client distributable (must be on Linux)
[unix]
bundle-linux-client:
    cd rcflowclient && flutter build linux --release
    mkdir -p dist
    tar -czf dist/rcflowclient-linux-$(uname -m).tar.gz -C rcflowclient/build/linux/x64/release bundle

# Build and install Linux Flutter client (must be on Linux)
[unix]
bundle-linux-client-install: bundle-linux-client
    @echo "Installing Linux Flutter client..."
    mkdir -p ~/.local/bin ~/.local/lib/rcflowclient
    tar -xzf dist/rcflowclient-linux-$(uname -m).tar.gz -C ~/.local/lib/rcflowclient --strip-components=1
    ln -sfn ~/.local/lib/rcflowclient/rcflowclient ~/.local/bin/rcflowclient
    @echo "Installed to ~/.local/lib/rcflowclient"

# Build macOS Flutter client distributable (must be on macOS)
[macos]
bundle-macos-client:
    cd rcflowclient && flutter build macos --release
    mkdir -p dist
    tar -czf dist/rcflowclient-macos-$(uname -m).tar.gz -C rcflowclient/build/macos/Build/Products/Release RCFlow.app

# Build and install macOS Flutter client (must be on macOS)
[macos]
bundle-macos-client-install: bundle-macos-client
    @echo "Installing macOS Flutter client..."
    mkdir -p ~/Applications
    tar -xzf dist/rcflowclient-macos-$(uname -m).tar.gz -C ~/Applications
    @echo "Installed to ~/Applications/RCFlow.app"

# Build Windows Flutter client distributable (must be on Windows)
[windows]
bundle-windows-client:
    Set-Location rcflowclient; flutter build windows --release
    if (-not (Test-Path dist)) { New-Item -ItemType Directory -Path dist | Out-Null }
    Compress-Archive -Force -Path 'rcflowclient\build\windows\x64\runner\Release\*' -DestinationPath 'dist\rcflowclient-windows-x64.zip'

# Build and install Windows Flutter client (must be on Windows)
[windows]
bundle-windows-client-install: bundle-windows-client
    $dest = "$env:LOCALAPPDATA\RCFlowClient"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Expand-Archive -Force -Path 'dist\rcflowclient-windows-x64.zip' -DestinationPath $dest
    Write-Host "Installed to $dest"

# Build Windows backend installer (setup.exe, must be on Windows)
[windows]
bundle-windows-backend:
    uv run --extra tray --extra bundle python scripts/bundle.py --platform windows --installer

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

# Run Flutter app in hot reload mode (WSL2 — connects to Windows emulator)
[unix]
flutter-run:
    cd rcflowclient && flutter run -d $(grep nameserver /etc/resolv.conf | awk '{print $2}'):15555

# Build Flutter debug APK
[unix]
flutter-build:
    cd rcflowclient && flutter build apk --debug
    @mkdir -p build/artifacts
    cp rcflowclient/build/app/outputs/flutter-apk/app-debug.apk build/artifacts/

# Build Flutter release APK (split per ABI)
[unix]
flutter-release:
    cd rcflowclient && flutter build apk --release --split-per-abi
    @mkdir -p build/artifacts
    cp rcflowclient/build/app/outputs/flutter-apk/app-*.apk build/artifacts/

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
