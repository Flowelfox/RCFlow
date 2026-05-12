#!/usr/bin/env python3
"""RCFlow macOS client bundle builder — creates .dmg and optional .pkg distributables.

Usage:
    python scripts/bundle_macos_client.py                # Build .app + .dmg
    python scripts/bundle_macos_client.py --pkg          # Also build .pkg
    python scripts/bundle_macos_client.py --install      # Build and install to /Applications
    python scripts/bundle_macos_client.py --skip-build   # Reuse existing flutter build

Outputs:
    dist/RCFlow-{version}-macos-{arch}.dmg   (Drag-and-drop disk image)
    dist/RCFlow-{version}-macos-{arch}.pkg   (macOS installer package, with --pkg)
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Project root is parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = PROJECT_ROOT / "rcflowclient"
PUBSPEC = CLIENT_DIR / "pubspec.yaml"
APP_BUILD_DIR = CLIENT_DIR / "build" / "macos" / "Build" / "Products" / "Release"
APP_NAME = "RCFlow.app"
BUNDLE_ID = "com.rcflow.rcflowclient"


def get_version() -> str:
    """Extract version from rcflowclient/pubspec.yaml (strips build number)."""
    for line in PUBSPEC.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            # "version: 1.26.1+44" → "1.26.1"
            raw = stripped.split(":", 1)[1].strip()
            return raw.split("+")[0]
    return "0.0.0"


def get_arch() -> str:
    """Get current machine architecture as a normalized string."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def run_flutter_build() -> None:
    """Run flutter build macos --release."""
    print("Building Flutter macOS client...")
    subprocess.check_call(
        ["flutter", "build", "macos", "--release"],
        cwd=str(CLIENT_DIR),
    )


def get_app_path() -> Path:
    """Return the path to the built .app bundle, or exit if missing."""
    app_path = APP_BUILD_DIR / APP_NAME
    if not app_path.exists():
        print(
            f"ERROR: Built app not found at {app_path}\n"
            "  Run 'flutter build macos --release' in rcflowclient/ first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return app_path


def create_dmg(app_path: Path, version: str, arch: str) -> Path:
    """Create a .dmg disk image with the .app and an Applications symlink."""
    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    dmg_name = f"RCFlow-{version}-macos-{arch}.dmg"
    dmg_path = dist_dir / dmg_name

    # Clean previous output
    if dmg_path.exists():
        dmg_path.unlink()

    # Staging directory
    staging_dir = PROJECT_ROOT / "build" / "dmg-staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    # Copy .app into staging
    dest_app = staging_dir / APP_NAME
    print(f"Copying {APP_NAME} to staging directory...")
    shutil.copytree(app_path, dest_app, symlinks=True)

    # Create Applications symlink for drag-and-drop install
    os.symlink("/Applications", staging_dir / "Applications")

    # Create DMG
    print(f"Creating {dmg_name}...")
    subprocess.check_call([
        "hdiutil", "create",
        "-volname", "RCFlow",
        "-srcfolder", str(staging_dir),
        "-ov",
        "-format", "UDZO",
        str(dmg_path),
    ])

    # Clean up staging
    shutil.rmtree(staging_dir)

    size_mb = dmg_path.stat().st_size / (1024 * 1024)
    print(f"DMG created: {dmg_path} ({size_mb:.1f} MB)")
    return dmg_path


def create_pkg(app_path: Path, version: str, arch: str) -> Path:
    """Create a .pkg installer that installs the .app to /Applications."""
    pkgbuild = shutil.which("pkgbuild")
    if not pkgbuild:
        print(
            "ERROR: pkgbuild not found. Install Xcode command line tools:\n"
            "  xcode-select --install",
            file=sys.stderr,
        )
        sys.exit(1)

    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    pkg_name = f"RCFlow-{version}-macos-{arch}.pkg"
    pkg_path = dist_dir / pkg_name

    if pkg_path.exists():
        pkg_path.unlink()

    # Create a temporary root with the .app inside an Applications directory
    # so pkgbuild installs it to /Applications/RCFlow.app
    pkg_root = PROJECT_ROOT / "build" / "macos-client-pkg"
    if pkg_root.exists():
        shutil.rmtree(pkg_root)

    apps_dir = pkg_root / "Applications"
    apps_dir.mkdir(parents=True)
    shutil.copytree(app_path, apps_dir / APP_NAME, symlinks=True)

    # Create postinstall script to remove quarantine attribute
    scripts_dir = PROJECT_ROOT / "build" / "macos-client-pkg-scripts"
    if scripts_dir.exists():
        shutil.rmtree(scripts_dir)
    scripts_dir.mkdir(parents=True)

    postinstall = scripts_dir / "postinstall"
    postinstall.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        f'xattr -dr com.apple.quarantine "/Applications/{APP_NAME}" 2>/dev/null || true\n'
    )
    os.chmod(postinstall, 0o755)

    print(f"Building {pkg_name}...")
    subprocess.check_call([
        pkgbuild,
        "--root", str(pkg_root),
        "--scripts", str(scripts_dir),
        "--identifier", BUNDLE_ID,
        "--version", version,
        "--install-location", "/",
        str(pkg_path),
    ])

    # Clean up
    shutil.rmtree(pkg_root)
    shutil.rmtree(scripts_dir)

    if not pkg_path.exists():
        print(f"ERROR: Expected .pkg at {pkg_path} but it was not found.", file=sys.stderr)
        sys.exit(1)

    size_mb = pkg_path.stat().st_size / (1024 * 1024)
    print(f"PKG created: {pkg_path} ({size_mb:.1f} MB)")
    return pkg_path


def install_app(app_path: Path) -> None:
    """Copy the .app to /Applications."""
    dest = Path("/Applications") / APP_NAME
    if dest.exists():
        print(f"Removing existing {dest}...")
        shutil.rmtree(dest)
    print(f"Installing {APP_NAME} to /Applications...")
    shutil.copytree(app_path, dest, symlinks=True)
    # Remove quarantine attribute
    subprocess.run(
        ["xattr", "-dr", "com.apple.quarantine", str(dest)],
        check=False,
    )
    print(f"Installed to {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RCFlow macOS client distributable")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip flutter build (use existing build)",
    )
    parser.add_argument(
        "--pkg",
        action="store_true",
        help="Also build a .pkg installer",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install the .app to /Applications after building",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("ERROR: This script must be run on macOS.", file=sys.stderr)
        sys.exit(1)

    version = get_version()
    arch = get_arch()

    print(f"Building RCFlow Client {version} for macos-{arch}")
    print(f"Project root: {PROJECT_ROOT}")
    print()

    # Step 1: Flutter build
    if args.skip_build:
        print("Skipping flutter build (using existing build)")
    else:
        print("=== Step 1: Building Flutter macOS client ===")
        run_flutter_build()
    print()

    # Verify .app exists
    app_path = get_app_path()
    print(f"Found {APP_NAME} at {app_path}")
    print()

    # Step 2: Create DMG
    print("=== Step 2: Creating DMG ===")
    dmg_path = create_dmg(app_path, version, arch)
    print()

    # Step 3: Create PKG (optional)
    pkg_path = None
    if args.pkg:
        print("=== Step 3: Creating PKG ===")
        pkg_path = create_pkg(app_path, version, arch)
        print()

    # Summary
    print("=== Build complete ===")
    print(f"  DMG: {dmg_path}")
    if pkg_path:
        print(f"  PKG: {pkg_path}")
    print()

    # Step 4: Install (optional)
    if args.install:
        print("=== Installing ===")
        install_app(app_path)
        return

    print("To install manually:")
    print(f"  Open {dmg_path} and drag RCFlow to Applications")
    if pkg_path:
        print(f"  Or run: sudo installer -pkg {pkg_path} -target /")


if __name__ == "__main__":
    main()
