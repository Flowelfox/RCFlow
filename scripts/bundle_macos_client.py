#!/usr/bin/env python3
"""RCFlow macOS client bundle builder — creates .dmg and optional .pkg distributables.

Usage:
    python scripts/bundle_macos_client.py                # Build .app + .dmg
    python scripts/bundle_macos_client.py --pkg          # Also build .pkg
    python scripts/bundle_macos_client.py --install      # Build and install to /Applications
    python scripts/bundle_macos_client.py --skip-build   # Reuse existing flutter build

Outputs:
    dist/rcflow-v{version}-macos-client-{arch}.dmg   (styled drag-and-drop disk image)
    dist/RCFlow-{version}-macos-{arch}.pkg           (macOS installer package, with --pkg)
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
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
    """Get current machine architecture as a normalized string.

    Uses the release-asset convention (``amd64`` / ``arm64``) so the produced DMG
    name matches what ``get-client.sh`` downloads.
    """
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
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
            f"ERROR: Built app not found at {app_path}\n  Run 'flutter build macos --release' in rcflowclient/ first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return app_path


def _make_dmg_background(icns_path: Path, output_png: Path, width: int = 540, height: int = 380) -> bool:
    """Generate the DMG window background image (gradient + app icon + label).

    Mirrors the worker installer's background so the client DMG matches the house
    style.  Returns ``False`` (and the DMG falls back to a plain background) when
    Pillow is unavailable.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: PLC0415
    except ImportError:
        print("WARNING: Pillow not available — DMG will use a plain background.", file=sys.stderr)
        return False

    dark, mid = (13, 17, 23), (26, 58, 92)
    bg = Image.new("RGBA", (width, height), dark)
    draw = ImageDraw.Draw(bg)
    for x in range(width):
        t = x / (width - 1)
        draw.line(
            [(x, 0), (x, height)],
            fill=(
                int(dark[0] + (mid[0] - dark[0]) * t),
                int(dark[1] + (mid[1] - dark[1]) * t),
                int(dark[2] + (mid[2] - dark[2]) * t),
                255,
            ),
        )

    icon_size, icon_y = 128, 80
    icon_x = (width - icon_size) // 2
    if icns_path.exists():
        try:
            icon = Image.open(str(icns_path)).convert("RGBA").resize((icon_size, icon_size), Image.LANCZOS)
            shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            ImageDraw.Draw(shadow).ellipse(
                [icon_x + 8, icon_y + icon_size - 12, icon_x + icon_size - 8, icon_y + icon_size + 20],
                fill=(0, 0, 0, 120),
            )
            bg = Image.alpha_composite(bg, shadow.filter(ImageFilter.GaussianBlur(radius=10)))
            bg.paste(icon, (icon_x, icon_y), icon)
        except Exception as exc:
            print(f"WARNING: Could not composite app icon into DMG background: {exc}", file=sys.stderr)

    draw = ImageDraw.Draw(bg)
    label = "Drag to Applications"
    font = None
    for font_path in (
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
    ):
        if os.path.exists(font_path):
            try:
                font = ImageFont.truetype(font_path, 13)
                break
            except Exception:  # noqa: S112 — try the next candidate font
                continue
    if font:
        bbox = draw.textbbox((0, 0), label, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) // 2, height - 38), label, fill=(180, 200, 220, 200), font=font)
    else:
        draw.text((width // 2 - 60, height - 38), label, fill=(180, 200, 220, 200))

    output_png.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(str(output_png), "PNG")
    print(f"Generated DMG background: {output_png.name} ({width}x{height})")
    return True


def build_styled_dmg(app_path: Path, out_dmg: Path, volname: str) -> Path:
    """Build a styled drag-to-Applications DMG (background + icon layout).

    Replaces the plain ``hdiutil create -srcfolder`` flow: creates a read-write
    image sized from the .app, lays out the window via Finder AppleScript (icon
    positions, hidden toolbar, background image), then converts to a compressed
    read-only DMG.  The AppleScript step is cosmetic and non-fatal — a build host
    with no Finder session simply yields an unstyled-but-valid DMG.
    """
    out_dmg.parent.mkdir(parents=True, exist_ok=True)
    if out_dmg.exists():
        out_dmg.unlink()

    bg_png = PROJECT_ROOT / "build" / "dmg_background_client.png"
    icns = app_path / "Contents" / "Resources" / "AppIcon.icns"
    has_background = _make_dmg_background(icns, bg_png)

    tmp_dmg = PROJECT_ROOT / "build" / f"{out_dmg.stem}-rw.dmg"
    # Ensure the scratch dir exists. _make_dmg_background creates it as a side
    # effect, but it returns early without doing so when Pillow is unavailable
    # (e.g. CI where pip refuses to install into an externally-managed env), so
    # hdiutil would otherwise fail with "No such file or directory".
    tmp_dmg.parent.mkdir(parents=True, exist_ok=True)
    if tmp_dmg.exists():
        tmp_dmg.unlink()

    # Size the volume from the actual .app footprint plus headroom (a fixed size
    # silently overflows once the app grows — "No space left on device").
    app_bytes = sum(f.stat().st_size for f in app_path.rglob("*") if f.is_file())
    dmg_mb = max(200, int(app_bytes / (1024 * 1024) * 1.4) + 50)

    print(f"Creating styled DMG: {out_dmg.name} ({dmg_mb} MB volume)...")
    subprocess.check_call(
        ["hdiutil", "create", "-size", f"{dmg_mb}m", "-fs", "HFS+", "-volname", volname, "-o", str(tmp_dmg)]
    )

    # Detach any stale volume of this name first, else the new one mounts as
    # "<name> 1" and the AppleScript styles the wrong volume (plain DMG ships).
    for stale in Path("/Volumes").glob(f"{volname}*"):
        subprocess.run(["hdiutil", "detach", str(stale), "-force", "-quiet"], check=False, capture_output=True)
    result = subprocess.run(
        ["hdiutil", "attach", str(tmp_dmg), "-noautoopen", "-nobrowse"],
        capture_output=True,
        text=True,
        check=True,
    )
    mount_point: str | None = None
    for line in result.stdout.splitlines():
        if "/Volumes/" in line:
            mount_point = line.strip().split("\t")[-1].strip()
            break
    if not mount_point or not os.path.isdir(mount_point):
        print("ERROR: Could not determine DMG mount point.", file=sys.stderr)
        sys.exit(1)

    try:
        vol = Path(mount_point)
        print(f"  Copying {app_path.name}...")
        shutil.copytree(app_path, vol / app_path.name, symlinks=True)
        (vol / "Applications").symlink_to("/Applications")
        if has_background:
            bg_dir = vol / ".background"
            bg_dir.mkdir()
            shutil.copy2(bg_png, bg_dir / "background.png")

        bg_line = (
            'set background picture of viewOptions to file ".background:background.png"'
            if has_background
            else "-- no custom background"
        )
        # Use the actual mounted volume name (may be "<name> 1" on a dedup).
        vol_name = vol.name
        applescript = f"""
tell application "Finder"
    tell disk "{vol_name}"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {{200, 200, 740, 580}}
        set viewOptions to the icon view options of container window
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 96
        {bg_line}
        set position of item "{app_path.name}" of container window to {{140, 190}}
        set position of item "Applications" of container window to {{400, 190}}
        close
        open
        update without registering applications
        delay 2
        close
    end tell
end tell
"""
        print("  Setting DMG window layout via AppleScript...")
        subprocess.run(["osascript", "-e", applescript], check=False, capture_output=True)
        subprocess.run(["sync"], check=False)
    finally:
        subprocess.run(["hdiutil", "detach", mount_point, "-quiet"], check=False, capture_output=True)

    print("  Converting to compressed DMG...")
    subprocess.check_call(
        ["hdiutil", "convert", str(tmp_dmg), "-format", "UDZO", "-imagekey", "zlib-level=9", "-o", str(out_dmg)]
    )
    tmp_dmg.unlink()

    size_mb = out_dmg.stat().st_size / (1024 * 1024)
    print(f"DMG created: {out_dmg} ({size_mb:.1f} MB)")
    return out_dmg


def create_dmg(app_path: Path, version: str, arch: str) -> Path:
    """Create the styled client DMG with the release naming convention."""
    dist_dir = PROJECT_ROOT / "dist"
    dmg_path = dist_dir / f"rcflow-v{version}-macos-client-{arch}.dmg"
    return build_styled_dmg(app_path, dmg_path, "RCFlow Client")


def create_pkg(app_path: Path, version: str, arch: str) -> Path:
    """Create a .pkg installer that installs the .app to /Applications."""
    pkgbuild = shutil.which("pkgbuild")
    if not pkgbuild:
        print(
            "ERROR: pkgbuild not found. Install Xcode command line tools:\n  xcode-select --install",
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
        f'#!/bin/bash\nset -e\nxattr -dr com.apple.quarantine "/Applications/{APP_NAME}" 2>/dev/null || true\n'
    )
    os.chmod(postinstall, 0o755)  # noqa: S103 — installer postinstall script must be executable

    print(f"Building {pkg_name}...")
    subprocess.check_call(
        [
            pkgbuild,
            "--root",
            str(pkg_root),
            "--scripts",
            str(scripts_dir),
            "--identifier",
            BUNDLE_ID,
            "--version",
            version,
            "--install-location",
            "/",
            str(pkg_path),
        ]
    )

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
    """CLI entry point: parse args and build the macOS client distributable(s)."""
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
    parser.add_argument(
        "--styled-dmg",
        action="store_true",
        help="Only build a styled DMG from --app to --out (no flutter build); used by the justfile",
    )
    parser.add_argument("--app", help="Path to the built .app (with --styled-dmg)")
    parser.add_argument("--out", help="Output .dmg path (with --styled-dmg)")
    parser.add_argument("--volname", default="RCFlow Client", help="DMG volume name (with --styled-dmg)")
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("ERROR: This script must be run on macOS.", file=sys.stderr)
        sys.exit(1)

    # Styled-DMG-only mode: the justfile owns the build + naming and delegates
    # just the disk-image styling here.
    if args.styled_dmg:
        if not args.app or not args.out:
            print("ERROR: --styled-dmg requires --app and --out.", file=sys.stderr)
            sys.exit(1)
        build_styled_dmg(Path(args.app), Path(args.out), args.volname)
        return

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
