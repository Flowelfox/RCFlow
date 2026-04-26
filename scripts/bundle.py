#!/usr/bin/env python3
"""RCFlow bundle builder — creates distributable packages using PyInstaller.

Usage:
    python scripts/bundle.py                              # Build for current platform
    python scripts/bundle.py --platform linux              # Explicit platform
    python scripts/bundle.py --platform linux --installer  # Build .deb package
    python scripts/bundle.py --platform windows --installer # Build setup.exe
    python scripts/bundle.py --platform macos --installer   # Build .pkg installer
    python scripts/bundle.py --sign                        # Build and sign for current platform

Outputs:
    dist/rcflow-v{version}-{platform}-worker-{arch}.tar.gz   (Linux archive)
    dist/rcflow-v{version}-linux-worker-{arch}.deb            (Linux .deb package)
    dist/rcflow-v{version}-{platform}-worker-{arch}.zip       (Windows archive)
    dist/rcflow-v{version}-windows-worker-{arch}.exe          (Windows installer)
    dist/rcflow-v{version}-macos-worker-{arch}.dmg            (macOS DMG)

Code signing (--sign):
    Signing is optional and controlled by the --sign flag. When enabled, the
    appropriate platform signing tool is invoked after each artifact is produced.
    Required environment variables per platform:

    Windows (Authenticode via signtool.exe):
        SIGN_CERT_PATH        Path to .pfx certificate file
        SIGN_CERT_PASSWORD    PFX password
        SIGN_TIMESTAMP_URL    Timestamp server (default: http://timestamp.digicert.com)

    macOS (codesign + notarization):
        SIGN_IDENTITY         Developer ID Application identity (e.g. "Developer ID Application: ...")
        SIGN_INSTALLER_IDENTITY  Developer ID Installer identity (for .pkg)
        APPLE_ID              Apple account email (for notarization)
        APPLE_TEAM_ID         Developer team ID
        APPLE_APP_PASSWORD    App-specific password for notarytool

    Linux (GPG detached signatures):
        GPG_KEY_ID            GPG key ID or fingerprint for signing
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

# Project root is parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_version() -> str:
    """Extract version from pyproject.toml."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    for line in pyproject.read_text().splitlines():
        if line.strip().startswith("version"):
            # version = "0.1.0"
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def get_arch() -> str:
    """Get current machine architecture as a normalized string."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def get_deb_arch() -> str:
    """Map machine architecture to Debian architecture name."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("armv7l", "armhf"):
        return "armhf"
    return machine


def detect_platform() -> str:
    """Detect current platform."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def ensure_pyinstaller() -> None:
    """Ensure PyInstaller is available."""
    try:
        import PyInstaller  # noqa: F401, PLC0415
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def create_alembic_ini_for_bundle() -> Path:
    """Create a modified alembic.ini for the bundled distribution.

    The bundled version uses paths relative to the install directory
    rather than the source tree.
    """
    output = PROJECT_ROOT / "build" / "bundle" / "alembic.ini"
    output.parent.mkdir(parents=True, exist_ok=True)

    content = """\
[alembic]
script_location = %(here)s/migrations
prepend_sys_path = .
path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    output.write_text(content)
    return output


def run_pyinstaller(target_platform: str, *, windowed: bool = False) -> Path:
    """Run PyInstaller and return the path to the output directory.

    Args:
        target_platform: Target OS ("linux", "windows", "macos").
        windowed: If True, use --windowed (no console window). Used for the
                  Windows tray build so the app runs as a background GUI process.
    """
    build_dir = PROJECT_ROOT / "build" / "pyinstaller"
    dist_dir = PROJECT_ROOT / "build" / "pyinstaller_dist"
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    # Collect hidden imports that PyInstaller may miss
    hidden_imports = [
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "aiosqlite",
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        "alembic",
        "alembic.command",
        "alembic.config",
        "src",
        "src.main",
        "src.config",
        "src.paths",
        "src.__main__",
        "src.api",
        "src.api.http",
        "src.api.ws",
        "src.api.ws.input_text",
        "src.api.ws.output_text",
        "src.core",
        "src.core.buffer",
        "src.core.llm",
        "src.core.permissions",
        "src.core.prompt_router",
        "src.core.session",
        "src.database",
        "src.database.engine",
        "src.executors",
        "src.executors.claude_code",
        "src.executors.codex",
        "src.logs",
        "src.models",
        "src.database.models",
        "src.prompts",
        "src.prompts.builder",
        "src.services",
        "src.services.tool_manager",
        "src.services.tool_settings",
        "src.tools",
        "src.tools.loader",
        "src.tools.registry",
        "jinja2",
        "pydantic",
        "pydantic_settings",
        "httpx",
        "anthropic",
        "aiohttp",
    ]

    # Windows tray build needs pystray + PIL, terminal needs pywinpty
    if target_platform == "windows":
        hidden_imports.extend(
            [
                "src.tray",
                "src.gui",
                "src.gui.windows",
                "src.gui.core",
                "src.gui.theme",
                "src.gui.updater",
                "pystray",
                "pystray._win32",
                "PIL",
                "PIL.Image",
                "PIL.ImageDraw",
                "winpty",
                "customtkinter",
            ]
        )

    # macOS menu bar build needs src.gui.macos, PyObjC AppKit bridge, and PIL
    if target_platform == "macos":
        hidden_imports.extend(
            [
                "src.gui",
                "src.gui.macos",
                "src.gui.core",
                "src.gui.theme",
                "src.gui.updater",
                "AppKit",
                "Foundation",
                "objc",
                "PIL",
                "PIL.Image",
                "PIL.ImageDraw",
                "customtkinter",
            ]
        )

    # Linux GUI dashboard + tray reuses src.gui.windows; needs pystray (X/AppIndicator backend) + PIL
    if target_platform == "linux":
        hidden_imports.extend(
            [
                "src.gui",
                "src.gui.windows",
                "src.gui.core",
                "src.gui.theme",
                "src.gui.updater",
                "pystray",
                "pystray._appindicator",
                "pystray._gtk",
                "pystray._xorg",
                "PIL",
                "PIL.Image",
                "PIL.ImageDraw",
                "customtkinter",
            ]
        )

    # Data files to include inside the PyInstaller bundle (_MEIPASS)
    # Templates need to be in _MEIPASS so Path(__file__)-based resolution works
    datas = [
        (str(PROJECT_ROOT / "src" / "prompts" / "templates"), "templates"),
    ]

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "rcflow",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(PROJECT_ROOT),
        "--noconfirm",
        "--clean",
    ]

    for imp in hidden_imports:
        cmd.extend(["--hidden-import", imp])

    for src_path, dest_path in datas:
        cmd.extend(["--add-data", f"{src_path}{os.pathsep}{dest_path}"])

    # Windows: --windowed hides the console, --icon sets the exe icon
    if target_platform == "windows":
        if windowed:
            cmd.append("--windowed")
        icon_path = PROJECT_ROOT / "src" / "gui" / "assets" / "tray_icon.ico"
        if icon_path.exists():
            cmd.extend(["--icon", str(icon_path)])

    # Linux: keep console so `rcflow run` from a terminal still prints output;
    # the .desktop launcher uses `Terminal=false` and the GUI takes over the
    # window.  Collect customtkinter assets so the bundled CTk theme JSON files
    # are present at runtime; PyInstaller's built-in tkinter hook handles
    # tcl/tk shared libraries automatically once tkinter is reachable from the
    # import graph.
    if target_platform == "linux":
        cmd.extend(["--collect-data", "customtkinter"])

    # macOS: always windowed (needed for NSStatusBar / LSUIElement app)
    if target_platform == "macos":
        cmd.append("--windowed")
        cmd.extend(["--osx-bundle-identifier", "com.rcflow.worker"])
        icns_path = PROJECT_ROOT / "src" / "gui" / "assets" / "tray_icon.icns"
        if icns_path.exists():
            # --icon copies tray_icon.icns to Contents/Resources/ automatically;
            # do NOT also add it via --add-data or PyInstaller will try to create
            # a directory with the same name and conflict with the already-placed file.
            cmd.extend(["--icon", str(icns_path)])
        cmd.extend(["--collect-all", "objc"])
        cmd.extend(["--collect-all", "AppKit"])
        cmd.extend(["--collect-all", "Foundation"])

    # Collect all submodules of src
    cmd.extend(["--collect-submodules", "src"])
    cmd.extend(["--collect-submodules", "uvicorn"])

    # pywinpty ships native .pyd/.dll files that PyInstaller must bundle
    if target_platform == "windows":
        cmd.extend(["--collect-all", "winpty"])

    # Entry point
    cmd.append(str(PROJECT_ROOT / "src" / "__main__.py"))

    print(f"Running PyInstaller: {' '.join(cmd[-5:])}")
    subprocess.check_call(cmd, cwd=str(PROJECT_ROOT))

    # macOS --windowed produces a .app bundle; other platforms produce a flat dir
    output_dir = dist_dir / "rcflow.app" if target_platform == "macos" else dist_dir / "rcflow"

    if not output_dir.exists():
        print(f"ERROR: Expected PyInstaller output at {output_dir}", file=sys.stderr)
        sys.exit(1)

    return output_dir


def assemble_bundle(pyinstaller_dir: Path, target_platform: str, version: str, arch: str) -> Path:
    """Assemble the final distributable bundle directory."""
    bundle_name = f"rcflow-v{version}-{target_platform}-worker-{arch}"
    bundle_dir = PROJECT_ROOT / "build" / "bundle" / bundle_name
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)

    # 1. Copy PyInstaller output (executable + _internal/)
    print("Copying PyInstaller output...")
    for item in pyinstaller_dir.iterdir():
        dest = bundle_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    # 2. Copy tool definitions
    tools_src = PROJECT_ROOT / "tools"
    tools_dest = bundle_dir / "tools"
    if tools_src.exists():
        shutil.copytree(tools_src, tools_dest)
        print(f"Copied tools/ ({len(list(tools_dest.glob('*.json')))} JSON files)")

    # 3. Copy alembic migrations
    migrations_src = PROJECT_ROOT / "src" / "database" / "migrations"
    migrations_dest = bundle_dir / "migrations"
    if migrations_src.exists():
        shutil.copytree(migrations_src, migrations_dest)
        # Remove __pycache__ from copied migrations
        for cache_dir in migrations_dest.rglob("__pycache__"):
            shutil.rmtree(cache_dir)
        print("Copied migrations/")

    # 4. Copy bundled alembic.ini
    bundled_ini = create_alembic_ini_for_bundle()
    shutil.copy2(bundled_ini, bundle_dir / "alembic.ini")
    print("Created bundled alembic.ini")

    # 5. (Removed — settings.json is generated at runtime, no .env.example needed)

    # 6. Copy systemd service template (Linux only)
    if target_platform == "linux":
        service_src = PROJECT_ROOT / "systemd" / "rcflow.service"
        if service_src.exists():
            shutil.copy2(service_src, bundle_dir / "rcflow.service")

    # 7. Copy install/uninstall scripts
    scripts_dir = PROJECT_ROOT / "scripts"
    if target_platform == "linux":
        for script in ("install.sh", "uninstall.sh"):
            src = scripts_dir / script
            if src.exists():
                shutil.copy2(src, bundle_dir / script)
                os.chmod(bundle_dir / script, 0o755)
    elif target_platform == "macos":
        for source_name, target_name in (
            ("install_macos.sh", "install.sh"),
            ("uninstall_macos.sh", "uninstall.sh"),
        ):
            src = scripts_dir / source_name
            if src.exists():
                shutil.copy2(src, bundle_dir / target_name)
                os.chmod(bundle_dir / target_name, 0o755)
    elif target_platform == "windows":
        for script in ("install.ps1", "uninstall.ps1"):
            src = scripts_dir / script
            if src.exists():
                shutil.copy2(src, bundle_dir / script)

    # 8. Write VERSION file
    (bundle_dir / "VERSION").write_text(version + "\n")

    # 9. Copy LICENSE if exists
    license_file = PROJECT_ROOT / "LICENSE"
    if license_file.exists():
        shutil.copy2(license_file, bundle_dir / "LICENSE")

    # 10. Copy tray icon (Windows + Linux)
    if target_platform in {"windows", "linux"}:
        tray_icon_src = PROJECT_ROOT / "src" / "gui" / "assets" / "tray_icon.ico"
        if tray_icon_src.exists():
            shutil.copy2(tray_icon_src, bundle_dir / "tray_icon.ico")
            print("Copied tray_icon.ico")

    # Linux additionally needs a PNG copy of the tray icon: Tk's iconphoto
    # accepts PNG (via the Img extension that ships with modern Tk) but does
    # not accept Windows .ico, and pystray's AppIndicator backend wants a
    # path-on-disk PNG/SVG to hand to the indicator.
    if target_platform == "linux":
        tray_png_dest = bundle_dir / "tray_icon.png"
        ensure_tray_png(tray_png_dest)
        # Ship the GTK + WebKit launcher script alongside the binary so
        # `rcflow gui` can spawn it under the system Python interpreter
        # (the frozen interpreter cannot load the C-based PyGObject /
        # WebKit2 GIR bindings).
        gui_dir = bundle_dir / "gui"
        gui_dir.mkdir(parents=True, exist_ok=True)
        gui_launcher_src = PROJECT_ROOT / "scripts" / "linux_gui_window.py"
        if gui_launcher_src.exists():
            shutil.copy2(gui_launcher_src, gui_dir / "linux_gui_window.py")
            os.chmod(gui_dir / "linux_gui_window.py", 0o755)
            print("Copied scripts/linux_gui_window.py → gui/")

    # Make the main executable executable on Linux
    if target_platform in {"linux", "macos"}:
        exe = bundle_dir / "rcflow"
        if exe.exists():
            os.chmod(exe, 0o755)

    return bundle_dir


def install_hicolor_icon(source_png: Path, pkg_root: Path, icon_name: str) -> None:
    """Render *source_png* into the hicolor icon theme under ``pkg_root``.

    The standard sizes are 48, 64, 128, 256, 512.  If Pillow is available
    we resize the source to each size for crisp renderings; otherwise we
    drop a single copy at the largest hicolor directory and rely on
    desktop environments to scale.
    """
    sizes = (48, 64, 128, 256, 512)
    try:
        from PIL import Image as _pil_image  # noqa: PLC0415, N813
    except ImportError:
        _pil_image = None  # ty:ignore[invalid-assignment]

    for size in sizes:
        icons_dir = pkg_root / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        icons_dir.mkdir(parents=True, exist_ok=True)
        dest = icons_dir / f"{icon_name}.png"
        if _pil_image is None:
            shutil.copy2(source_png, dest)
            continue
        with _pil_image.open(source_png) as img:
            img.convert("RGBA").resize((size, size), _pil_image.LANCZOS).save(dest, "PNG")
    print(f"Installed {icon_name}.png into hicolor icon theme ({len(sizes)} sizes)")


def ensure_tray_png(dest: Path) -> None:
    """Materialise a square PNG of the worker icon at *dest*.

    Prefers a pre-rendered ``src/gui/assets/tray_icon.png`` if present;
    otherwise rasterises the largest frame of ``tray_icon.ico`` via Pillow.
    """
    src_png = PROJECT_ROOT / "src" / "gui" / "assets" / "tray_icon.png"
    if src_png.exists():
        shutil.copy2(src_png, dest)
        print("Copied tray_icon.png")
        return
    src_ico = PROJECT_ROOT / "src" / "gui" / "assets" / "tray_icon.ico"
    if not src_ico.exists():
        print("WARNING: no tray_icon.ico to derive tray_icon.png from", file=sys.stderr)
        return
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(src_ico) as ico:
            sizes = ico.info.get("sizes") or [ico.size]
            largest = max(sizes, key=lambda s: s[0])
            ico.size = largest
            ico.load()
            ico.convert("RGBA").save(dest, "PNG")
        print(f"Derived tray_icon.png ({largest[0]}x{largest[1]}) from tray_icon.ico")
    except (OSError, ValueError, ImportError) as exc:
        print(f"WARNING: failed to render tray_icon.png: {exc}", file=sys.stderr)


def create_archive(bundle_dir: Path, target_platform: str, version: str, arch: str) -> Path:
    """Create the final distributable archive."""
    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    bundle_name = bundle_dir.name

    if target_platform == "windows":
        archive_path = dist_dir / f"{bundle_name}.zip"
        print(f"Creating {archive_path.name}...")
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(bundle_dir.rglob("*")):
                if file_path.is_file():
                    arcname = f"{bundle_name}/{file_path.relative_to(bundle_dir)}"
                    zf.write(file_path, arcname)
    else:
        archive_path = dist_dir / f"{bundle_name}.tar.gz"
        print(f"Creating {archive_path.name}...")
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(bundle_dir, arcname=bundle_name)

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print(f"Archive created: {archive_path} ({size_mb:.1f} MB)")
    return archive_path


def build_windows_installer(bundle_dir: Path, version: str, arch: str) -> Path | None:
    """Compile the Inno Setup script to produce setup.exe.

    Requires Inno Setup 6 to be installed (iscc.exe on PATH or at the
    default install location).
    """
    iss_path = PROJECT_ROOT / "scripts" / "inno_setup.iss"
    if not iss_path.exists():
        print(f"ERROR: Inno Setup script not found at {iss_path}", file=sys.stderr)
        return None

    # Find iscc.exe
    iscc = shutil.which("iscc")
    if not iscc:
        # Check default Inno Setup install locations
        for candidate in [
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ]:
            if os.path.isfile(candidate):
                iscc = candidate
                break

    if not iscc:
        print(
            "ERROR: Inno Setup compiler (iscc.exe) not found.\n"
            "  Install Inno Setup 6 from https://jrsoftware.org/isinfo.php\n"
            "  or add its directory to PATH.",
            file=sys.stderr,
        )
        return None

    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    output_filename = f"rcflow-v{version}-windows-worker-{arch}"

    cmd = [
        iscc,
        str(iss_path),
        f"/DBundleDir={bundle_dir}",
        f"/DAppVersion={version}",
        f"/DArch={arch}",
        f"/DOutputDir={dist_dir}",
        f"/DOutputFilename={output_filename}",
    ]

    print(f"Running Inno Setup compiler: {os.path.basename(iscc)}")
    subprocess.check_call(cmd)

    output_path = dist_dir / f"{output_filename}.exe"
    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"Installer created: {output_path} ({size_mb:.1f} MB)")
        return output_path
    else:
        print(f"WARNING: Expected installer at {output_path} but it was not found.", file=sys.stderr)
        return None


def build_deb(bundle_dir: Path, version: str, arch: str) -> Path | None:
    """Build a .deb package from the assembled bundle directory.

    Requires dpkg-deb to be available (standard on Debian/Ubuntu).
    Installs to /opt/rcflow with a systemd service.
    """
    if not shutil.which("dpkg-deb"):
        print(
            "ERROR: dpkg-deb not found. Install dpkg:\n  sudo apt-get install dpkg",
            file=sys.stderr,
        )
        return None

    deb_arch = get_deb_arch()
    pkg_name = f"rcflow-v{version}-linux-worker-{deb_arch}"
    pkg_root = PROJECT_ROOT / "build" / "deb" / pkg_name
    install_dir = pkg_root / "opt" / "rcflow"

    # Clean previous build
    if pkg_root.exists():
        shutil.rmtree(pkg_root)

    # Copy bundle contents to /opt/rcflow
    shutil.copytree(bundle_dir, install_dir)

    # Remove install/uninstall scripts (handled by dpkg)
    for script in ("install.sh", "uninstall.sh"):
        s = install_dir / script
        if s.exists():
            s.unlink()

    # Create /usr/bin/rcflow symlink so the CLI is on PATH
    usr_bin = pkg_root / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    (usr_bin / "rcflow").symlink_to("/opt/rcflow/rcflow")

    # Install XDG desktop entry so the worker GUI shows up in the application
    # menu / GNOME Activities search alongside the rcflow-client entry.
    apps_dir = pkg_root / "usr" / "share" / "applications"
    apps_dir.mkdir(parents=True)
    (apps_dir / "rcflow-worker.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=RCFlow Worker\n"
        "GenericName=RCFlow Worker\n"
        "Comment=RCFlow background worker — dashboard and tray\n"
        "Exec=/opt/rcflow/rcflow gui\n"
        "Icon=rcflow-worker\n"
        "Terminal=false\n"
        "Categories=Network;Development;Utility;\n"
        # Matches the WM_CLASS instance set by scripts/linux_gui_window.py
        # (Gtk.Window.set_wmclass("rcflow", "RCFlow Worker")).
        "StartupWMClass=rcflow\n"
        "Keywords=rcflow;worker;automation;\n"
    )

    # Install hicolor icon so the .desktop entry renders properly in app
    # launchers and GNOME Activities.  We render one PNG per common size so
    # the launcher picks the closest match without scaling artefacts.
    tray_png_src = bundle_dir / "tray_icon.png"
    if tray_png_src.exists():
        install_hicolor_icon(tray_png_src, pkg_root, "rcflow-worker")

    # Create systemd service unit
    systemd_dir = pkg_root / "lib" / "systemd" / "system"
    systemd_dir.mkdir(parents=True)
    (systemd_dir / "rcflow.service").write_text(
        """\
[Unit]
Description=RCFlow Action Server
After=network.target

[Service]
Type=simple
User=rcflow
WorkingDirectory=/opt/rcflow
# Settings loaded from /opt/rcflow/settings.json by the application
ExecStart=/opt/rcflow/rcflow run
Restart=on-failure
RestartSec=5

# Allow rcflow to read git repos owned by other users (git >= 2.35.2 safe.directory check)
Environment="GIT_CONFIG_COUNT=1"
Environment="GIT_CONFIG_KEY_0=safe.directory"
Environment="GIT_CONFIG_VALUE_0=*"
# SSH key and other optional overrides (written by installer when an owner SSH key is found)
EnvironmentFile=-/opt/rcflow/env

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
# Whole install dir is writable so settings.json (resolved by paths.get_data_dir)
# can be created/updated by the service user.  Subpaths data/, logs/, certs/
# are inside this tree and inherit the same access.
ReadWritePaths=/opt/rcflow
# HOME points at the install dir so XDG fallbacks (used when /opt/rcflow is
# not writable, e.g. interactive `rcflow` invocations) land somewhere the
# service can actually write to instead of hitting a missing /home/rcflow.
Environment="HOME=/opt/rcflow"
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""
    )

    # Create DEBIAN control files
    debian_dir = pkg_root / "DEBIAN"
    debian_dir.mkdir(parents=True)

    # Headless `rcflow run` (the systemd service) only needs the bundled
    # binary and standard glibc.  The optional GUI mode (`rcflow gui`,
    # invoked by the .desktop launcher) opens a GTK + WebKit window via
    # the system Python interpreter — the bundled tcl/tk path is unsafe
    # on modern libxcb (Ubuntu 25.04+).  Headless installs (servers,
    # containers, WSL without X) can ignore the recommends; desktop
    # installs already pull them in via the default GNOME / KDE
    # meta-packages.
    recommends = [
        # Native GTK dashboard launcher (Linux GUI)
        "python3 (>= 3.10)",
        "python3-gi",
        "gir1.2-gtk-3.0",
        "gir1.2-webkit2-4.1 | gir1.2-webkit-6.0",
        # Browser-fallback dashboard
        "xdg-utils",
        # Tk parity dashboard (kept as fallback for non-Ubuntu-25.04 hosts)
        "libtcl9.0 | libtcl8.6",
        "libtk9.0 | libtk8.6",
        "libxcb1",
        "libxft2",
        "libxss1",
        "libfontconfig1",
        "gir1.2-ayatanaappindicator3-0.1",
    ]
    (debian_dir / "control").write_text(
        f"""\
Package: rcflow
Version: {version}
Architecture: {deb_arch}
Maintainer: RCFlow <rcflow@localhost>
Description: RCFlow Action Server
 Self-contained RCFlow backend server with all dependencies bundled.
 Run headless via the bundled systemd service or interactively with the
 GUI dashboard (`rcflow gui`, also available from the application menu).
Section: net
Priority: optional
Recommends: {", ".join(recommends)}
Installed-Size: {sum(f.stat().st_size for f in install_dir.rglob("*") if f.is_file()) // 1024}
"""
    )

    # Note: settings.json is created by postinst, not shipped in the package,
    # so it must NOT be listed in conffiles. dpkg conffiles only covers files
    # that are part of the archive itself.

    (debian_dir / "postinst").write_text(
        """\
#!/bin/bash
set -e

# Create service user if it doesn't exist
if ! id rcflow &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin rcflow
fi

# Create data directories
mkdir -p /opt/rcflow/data /opt/rcflow/logs /opt/rcflow/certs

# settings.json is created automatically on first server start

# Grant rcflow read access to the installing user's Projects directory.
# SUDO_USER is set when the user runs: sudo dpkg -i rcflow_*.deb
OWNER_USER="${SUDO_USER:-}"
if [ -n "$OWNER_USER" ] && id "$OWNER_USER" &>/dev/null; then
    usermod -aG "$OWNER_USER" rcflow
    chmod 710 "/home/$OWNER_USER" 2>/dev/null || true
    if [ -d "/home/$OWNER_USER/Projects" ]; then
        chmod 750 "/home/$OWNER_USER/Projects"
    fi
fi

# Copy owner user's SSH key so the service can authenticate git push operations
SSH_KEY=""
if [ -n "$OWNER_USER" ]; then
    for key_file in id_ed25519 id_ecdsa id_rsa; do
        if [ -f "/home/$OWNER_USER/.ssh/$key_file" ]; then
            SSH_KEY="/home/$OWNER_USER/.ssh/$key_file"
            break
        fi
    done
fi
if [ -n "$SSH_KEY" ]; then
    mkdir -p /opt/rcflow/ssh
    cp "$SSH_KEY" /opt/rcflow/ssh/id
    chmod 700 /opt/rcflow/ssh
    chmod 600 /opt/rcflow/ssh/id
    echo 'GIT_SSH_COMMAND="ssh -i /opt/rcflow/ssh/id -o StrictHostKeyChecking=accept-new"' \
        > /opt/rcflow/env
fi

# Fix ownership
chown -R rcflow:rcflow /opt/rcflow

# Run database migrations
echo "Running database migrations..."
if su -s /bin/bash rcflow -c "cd /opt/rcflow && ./rcflow migrate"; then
    echo "Database migrations complete."
else
    echo "WARNING: Migration failed. Check your DATABASE_URL in /opt/rcflow/settings.json" >&2
    echo "You can retry with: cd /opt/rcflow && sudo -u rcflow ./rcflow migrate" >&2
fi

# Enable and start service
if pidof systemd &>/dev/null; then
    systemctl daemon-reload
    systemctl enable rcflow
    systemctl start rcflow || true
else
    # Fallback for non-systemd environments (e.g. WSL)
    # Install an init.d script so "sudo service rcflow start" works
    cat > /etc/init.d/rcflow <<'INITEOF'
#!/bin/bash
### BEGIN INIT INFO
# Provides:          rcflow
# Required-Start:    $network $local_fs
# Required-Stop:     $network $local_fs
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: RCFlow Action Server
### END INIT INFO

NAME=rcflow
DAEMON=/opt/rcflow/rcflow
PIDFILE=/var/run/rcflow.pid
LOGFILE=/opt/rcflow/logs/rcflow.log
USER=rcflow
WORKDIR=/opt/rcflow

# Allow rcflow to read git repos owned by other users (git >= 2.35.2 safe.directory check)
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0='*'

# Load optional overrides (SSH key, etc.) written by the installer
# shellcheck disable=SC1091
[ -f /opt/rcflow/env ] && set -a && . /opt/rcflow/env && set +a

case "$1" in
    start)
        echo "Starting $NAME..."
        start-stop-daemon --start --background --make-pidfile \\
            --pidfile "$PIDFILE" --chuid "$USER" --chdir "$WORKDIR" \\
            --startas /bin/bash -- -c "exec $DAEMON run >> $LOGFILE 2>&1"
        # Brief pause to check if the process survived startup
        sleep 1
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "$NAME started (PID $(cat "$PIDFILE"))"
        else
            echo "$NAME failed to start. Check $LOGFILE for details." >&2
            exit 1
        fi
        ;;
    stop)
        echo "Stopping $NAME..."
        start-stop-daemon --stop --pidfile "$PIDFILE" --retry 10
        rm -f "$PIDFILE"
        ;;
    restart)
        $0 stop
        $0 start
        ;;
    status)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "$NAME is running (PID $(cat "$PIDFILE"))"
        else
            echo "$NAME is not running"
            if [ -f "$LOGFILE" ]; then
                echo "Last log lines:"
                tail -5 "$LOGFILE"
            fi
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
INITEOF
    chmod 755 /etc/init.d/rcflow
    update-rc.d rcflow defaults 2>/dev/null || true
    service rcflow start || true
fi

# Refresh desktop entry / icon caches so the worker GUI shows up immediately
# in app launchers and GNOME Activities search.  Both tools are best-effort:
# they may be absent on minimal server installs.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi

echo ""
echo "============================================"
echo "  RCFlow installed successfully!"
echo "  Run 'rcflow info' to view server details."
echo "  Run 'rcflow api-key' to view your API key."
echo "  Launch the GUI dashboard from your app menu"
echo "  ('RCFlow Worker') or run 'rcflow gui'."
echo "============================================"
echo ""
"""
    )
    os.chmod(debian_dir / "postinst", 0o755)

    (debian_dir / "prerm").write_text(
        """\
#!/bin/bash
set -e

if pidof systemd &>/dev/null; then
    if systemctl is-active --quiet rcflow 2>/dev/null; then
        systemctl stop rcflow
    fi
elif [ -x /etc/init.d/rcflow ]; then
    service rcflow stop 2>/dev/null || true
fi
"""
    )
    os.chmod(debian_dir / "prerm", 0o755)

    (debian_dir / "postrm").write_text(
        """\
#!/bin/bash
set -e

if [ "$1" = "purge" ]; then
    # Remove data, config, and user on purge
    rm -rf /opt/rcflow
    userdel rcflow 2>/dev/null || true
    rm -f /etc/init.d/rcflow
    update-rc.d rcflow remove 2>/dev/null || true
fi

if pidof systemd &>/dev/null; then
    systemctl daemon-reload
fi

# Keep desktop / icon caches in sync after removal so stale RCFlow launchers
# don't linger in the app menu.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
"""
    )
    os.chmod(debian_dir / "postrm", 0o755)

    # Ensure correct permissions
    os.chmod(install_dir / "rcflow", 0o755)

    # Build the .deb
    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    deb_path = dist_dir / f"{pkg_name}.deb"

    print(f"Building {deb_path.name}...")
    subprocess.check_call(["dpkg-deb", "--build", "--root-owner-group", str(pkg_root), str(deb_path)])

    if deb_path.exists():
        size_mb = deb_path.stat().st_size / (1024 * 1024)
        print(f"Package created: {deb_path} ({size_mb:.1f} MB)")
        return deb_path
    else:
        print(f"WARNING: Expected .deb at {deb_path} but it was not found.", file=sys.stderr)
        return None


def build_macos_pkg(bundle_dir: Path, version: str, arch: str) -> Path | None:
    """Build a macOS .pkg installer from the assembled bundle directory.

    The .pkg postinstall runs as root, so we detect the console user and
    run the actual install.sh as that user to set up a user-level LaunchAgent.
    Files are staged to a temp system location by pkgbuild, then the
    postinstall moves them to the user's ~/.local/lib/rcflow.
    """
    pkgbuild = shutil.which("pkgbuild")
    if not pkgbuild:
        print(
            "ERROR: pkgbuild not found. Install Xcode command line tools:\n  xcode-select --install",
            file=sys.stderr,
        )
        return None

    pkg_scripts_dir = PROJECT_ROOT / "build" / "macos-pkg" / "scripts"
    if pkg_scripts_dir.exists():
        shutil.rmtree(pkg_scripts_dir)
    pkg_scripts_dir.mkdir(parents=True, exist_ok=True)

    # The pkg installs files to /tmp/rcflow-pkg-stage, then postinstall
    # runs install.sh as the console user to place them under ~/.local/lib/rcflow.
    pkg_stage = "/tmp/rcflow-pkg-stage"

    postinstall = pkg_scripts_dir / "postinstall"
    postinstall.write_text(
        f"""#!/bin/bash
set -euo pipefail

CONSOLE_USER=$(stat -f '%Su' /dev/console)
CONSOLE_HOME=$(dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory | awk '{{print $2}}')
SERVICE_LABEL="com.rcflow.server"

# --- Clean up old LaunchDaemon install (we are root here) ---
OLD_PLIST="/Library/LaunchDaemons/$SERVICE_LABEL.plist"
if [ -f "$OLD_PLIST" ]; then
    launchctl bootout system "$OLD_PLIST" 2>/dev/null || true
    rm -f "$OLD_PLIST"
fi

OLD_PREFIX="/usr/local/lib/rcflow"
if [ -d "$OLD_PREFIX" ]; then
    # Migrate settings and data to staging dir so install.sh picks them up
    if [ -f "$OLD_PREFIX/settings.json" ]; then
        cp "$OLD_PREFIX/settings.json" {pkg_stage}/settings.json.migrated
        # Fix paths in migrated settings
        sed -i '' "s|$OLD_PREFIX|$CONSOLE_HOME/.local/lib/rcflow|g" {pkg_stage}/settings.json.migrated
    fi
    if [ -d "$OLD_PREFIX/data" ]; then
        cp -R "$OLD_PREFIX/data" {pkg_stage}/data.migrated
    fi
    rm -rf "$OLD_PREFIX"
fi

if [ -L "/usr/local/bin/rcflow" ]; then
    rm -f "/usr/local/bin/rcflow"
fi

# Remove old service user
if dscl . -read "/Users/rcflow" &>/dev/null 2>&1; then
    dscl . -delete "/Users/rcflow" 2>/dev/null || true
fi

# --- Run user-level install as the console user ---
# chown staging dir so the user can read/move all files (including migrated ones)
chown -R "$CONSOLE_USER:staff" {pkg_stage}

# Use su -l to get a login shell with correct HOME
su -l "$CONSOLE_USER" -c "cd {pkg_stage} && ./install.sh --skip-migration --unattended"

# Clean up the staging directory
rm -rf {pkg_stage}
"""
    )
    os.chmod(postinstall, 0o755)

    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = dist_dir / f"rcflow-v{version}-macos-worker-{arch}.pkg"

    if pkg_path.exists():
        pkg_path.unlink()

    print(f"Building {pkg_path.name}...")
    subprocess.check_call(
        [
            pkgbuild,
            "--root",
            str(bundle_dir),
            "--scripts",
            str(pkg_scripts_dir),
            "--identifier",
            "com.rcflow.backend",
            "--version",
            version,
            "--install-location",
            pkg_stage,
            str(pkg_path),
        ]
    )

    if pkg_path.exists():
        size_mb = pkg_path.stat().st_size / (1024 * 1024)
        print(f"Package created: {pkg_path} ({size_mb:.1f} MB)")
        return pkg_path

    print(f"WARNING: Expected .pkg at {pkg_path} but it was not found.", file=sys.stderr)
    return None


def assemble_macos_app(pyinstaller_app: Path, version: str, arch: str) -> Path:
    """Copy the PyInstaller .app, inject Info.plist keys, and bundle data files.

    Handles the macOS-specific assembly path.  The output is a stand-alone
    ``.app`` bundle that can be signed and wrapped in a DMG.

    Args:
        pyinstaller_app: Path to the raw ``.app`` produced by PyInstaller
                         (``build/pyinstaller_dist/rcflow.app``).
        version: Release version string (e.g. ``"0.32.0"``).
        arch: Architecture string (e.g. ``"arm64"`` or ``"x64"``).

    Returns:
        Path to the finished ``.app`` bundle in ``build/bundle/``.
    """
    import plistlib  # noqa: PLC0415

    app_name = "RCFlow Worker.app"
    dest = PROJECT_ROOT / "build" / "bundle" / app_name
    if dest.exists():
        shutil.rmtree(dest)

    print(f"Copying .app bundle to {dest.name}...")
    shutil.copytree(pyinstaller_app, dest)

    # The macOS executable and data files live in Contents/MacOS/
    contents_macos = dest / "Contents" / "MacOS"
    contents_resources = dest / "Contents" / "Resources"
    contents_resources.mkdir(parents=True, exist_ok=True)

    # 1. Copy tray_icon.icns to Contents/Resources/ (standard .app location)
    icns_src = PROJECT_ROOT / "src" / "gui" / "assets" / "tray_icon.icns"
    if icns_src.exists():
        shutil.copy2(icns_src, contents_resources / "tray_icon.icns")
        print("Copied tray_icon.icns → Contents/Resources/")

    # 2. Copy tool definitions, migrations, alembic.ini next to the executable
    for src_rel, dest_name in (
        ("tools", "tools"),
        ("src/database/migrations", "migrations"),
    ):
        src = PROJECT_ROOT / src_rel
        if src.exists():
            dest_sub = contents_macos / dest_name
            if dest_sub.exists():
                shutil.rmtree(dest_sub)
            shutil.copytree(src, dest_sub)
            # Remove __pycache__ from copied Python source trees
            for cache in dest_sub.rglob("__pycache__"):
                shutil.rmtree(cache)
            print(f"Copied {src_rel}/ → Contents/MacOS/{dest_name}/")

    bundled_ini = create_alembic_ini_for_bundle()
    shutil.copy2(bundled_ini, contents_macos / "alembic.ini")
    print("Created bundled alembic.ini")

    (contents_macos / "VERSION").write_text(version + "\n")

    license_file = PROJECT_ROOT / "LICENSE"
    if license_file.exists():
        shutil.copy2(license_file, contents_macos / "LICENSE")

    # 3. Patch Info.plist — inject keys that make it a proper LSUIElement app.
    #    PyInstaller's default Info.plist omits these; they may already be set
    #    if the rcflow.spec BUNDLE() was used, but we set them here regardless.
    info_plist_path = dest / "Contents" / "Info.plist"
    if info_plist_path.exists():
        with info_plist_path.open("rb") as fh:
            info = plistlib.load(fh)
    else:
        info = {}

    info.update(
        {
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "CFBundleName": "RCFlow Worker",
            "CFBundleDisplayName": "RCFlow Worker",
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "CFBundleIdentifier": "com.rcflow.worker",
            "CFBundleIconFile": "tray_icon",
        }
    )

    with info_plist_path.open("wb") as fh:
        plistlib.dump(info, fh)
    print("Patched Contents/Info.plist (LSUIElement, version, icon, bundle-id)")

    # Make the main executable executable
    exe = contents_macos / "rcflow"
    if exe.exists():
        os.chmod(exe, 0o755)

    print(f"macOS app assembled: {dest}")
    return dest


def _make_dmg_background(icns_path: Path, output_png: Path, width: int = 540, height: int = 380) -> bool:
    """Generate the DMG window background image using Pillow.

    Produces a gradient from dark navy to steel blue with the app icon
    composited near the top-center and subtle "Drag to Applications" text
    at the bottom.  Saved as a PNG at *output_png*.

    Args:
        icns_path: Path to the app icon (``.icns`` or ``.png``).
        output_png: Where to write the output PNG.
        width: Canvas width in pixels (default 540).
        height: Canvas height in pixels (default 380).

    Returns:
        ``True`` if the background was written successfully, ``False`` if
        Pillow is unavailable or an error occurred.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: PLC0415
    except ImportError:
        print("WARNING: Pillow not available — DMG will use plain white background.", file=sys.stderr)
        return False

    # ── Gradient background ──────────────────────────────────────────
    # Horizontal linear gradient: dark navy (#0d1117) → steel blue (#1a3a5c)
    dark = (13, 17, 23)
    mid = (26, 58, 92)
    bg = Image.new("RGBA", (width, height), dark)
    draw = ImageDraw.Draw(bg)

    for x in range(width):
        t = x / (width - 1)
        r = int(dark[0] + (mid[0] - dark[0]) * t)
        g = int(dark[1] + (mid[1] - dark[1]) * t)
        b = int(dark[2] + (mid[2] - dark[2]) * t)
        draw.line([(x, 0), (x, height)], fill=(r, g, b, 255))

    # ── App icon ──────────────────────────────────────────────────────
    icon_size = 128
    icon_x = (width - icon_size) // 2
    icon_y = 80

    if icns_path.exists():
        try:
            icon = Image.open(str(icns_path)).convert("RGBA")
            icon = icon.resize((icon_size, icon_size), Image.LANCZOS)

            # Soft drop shadow: blurred dark ellipse behind the icon
            shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow)
            shadow_draw.ellipse(
                [icon_x + 8, icon_y + icon_size - 12, icon_x + icon_size - 8, icon_y + icon_size + 20],
                fill=(0, 0, 0, 120),
            )
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=10))
            bg = Image.alpha_composite(bg, shadow)

            bg.paste(icon, (icon_x, icon_y), icon)
        except Exception as exc:
            print(f"WARNING: Could not composite app icon into DMG background: {exc}", file=sys.stderr)

    # ── "Drag to Applications" label ────────────────────────────────
    draw = ImageDraw.Draw(bg)
    label = "Drag to Applications"
    label_color = (180, 200, 220, 200)

    # Use a default font — ImageFont.load_default() gives a basic bitmap font.
    # On macOS we attempt to load the system Helvetica Neue.
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
            except Exception:
                continue

    if font:
        bbox = draw.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_x = (width - text_w) // 2
        draw.text((text_x, height - 38), label, fill=label_color, font=font)
    else:
        draw.text((width // 2 - 60, height - 38), label, fill=label_color)

    # ── Save ─────────────────────────────────────────────────────────
    output_png.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(str(output_png), "PNG")
    print(f"Generated DMG background: {output_png.name} ({width}x{height})")
    return True


def build_macos_dmg(app_path: Path, version: str, arch: str) -> Path | None:
    """Build a styled macOS ``.dmg`` from the assembled ``.app`` bundle.

    Creates a read-write DMG, sets a custom gradient background with the app
    icon, positions the app and an /Applications symlink, then converts to a
    compressed read-only UDZO DMG.

    Requires ``hdiutil`` and ``osascript`` (both standard on macOS).

    Args:
        app_path: Path to the finished ``.app`` bundle (output of
                  :func:`assemble_macos_app`).
        version: Release version string used in the output filename.
        arch: Architecture string used in the output filename.

    Returns:
        Path to the created ``.dmg``, or ``None`` on failure.
    """
    if not shutil.which("hdiutil"):
        print("ERROR: hdiutil not found (are you on macOS?)", file=sys.stderr)
        return None
    if not shutil.which("osascript"):
        print("ERROR: osascript not found (are you on macOS?)", file=sys.stderr)
        return None

    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    dmg_name = f"rcflow-v{version}-macos-worker-{arch}"
    final_dmg = dist_dir / f"{dmg_name}.dmg"
    tmp_dmg = PROJECT_ROOT / "build" / f"{dmg_name}-rw.dmg"

    # Clean up any leftover temp DMG
    if tmp_dmg.exists():
        tmp_dmg.unlink()
    if final_dmg.exists():
        final_dmg.unlink()

    # ── Generate background image ────────────────────────────────────
    bg_png = PROJECT_ROOT / "build" / "dmg_background.png"
    icns_src = PROJECT_ROOT / "src" / "gui" / "assets" / "tray_icon.icns"
    _make_dmg_background(icns_src, bg_png)

    # ── Create a blank read-write DMG ────────────────────────────────
    print(f"Creating DMG: {final_dmg.name}...")
    subprocess.check_call(
        [
            "hdiutil",
            "create",
            "-size",
            "400m",
            "-fs",
            "HFS+",
            "-volname",
            "RCFlow Worker",
            "-o",
            str(tmp_dmg),
        ]
    )

    # ── Mount the DMG ────────────────────────────────────────────────
    result = subprocess.run(
        ["hdiutil", "attach", str(tmp_dmg), "-noautoopen", "-nobrowse"],
        capture_output=True,
        text=True,
        check=True,
    )
    # Parse the mount point from hdiutil output (last tab-separated column of the /dev/diskN line)
    mount_point: str | None = None
    for line in result.stdout.splitlines():
        if "/Volumes/" in line:
            mount_point = line.strip().split("\t")[-1].strip()
            break

    if not mount_point or not os.path.isdir(mount_point):
        print("ERROR: Could not determine DMG mount point from hdiutil output.", file=sys.stderr)
        return None

    try:
        vol_path = Path(mount_point)

        # ── Copy .app into volume ────────────────────────────────────
        app_dest = vol_path / app_path.name
        print(f"  Copying {app_path.name}...")
        shutil.copytree(app_path, app_dest)

        # ── Create /Applications symlink ─────────────────────────────
        applications_link = vol_path / "Applications"
        applications_link.symlink_to("/Applications")

        # ── Copy background image into hidden .background/ folder ────
        has_background = bg_png.exists()
        if has_background:
            bg_dir = vol_path / ".background"
            bg_dir.mkdir()
            shutil.copy2(bg_png, bg_dir / "background.png")

        # ── Set DMG window appearance via AppleScript ─────────────────
        # Window: 540x380, icons at fixed positions, background image, no sidebar.
        app_icon_x, app_icon_y = 140, 190
        apps_icon_x, apps_icon_y = 400, 190
        win_x1, win_y1, win_x2, win_y2 = 200, 200, 740, 580

        bg_line = (
            'set background picture of viewOptions to file ".background:background.png"'
            if has_background
            else "-- no custom background"
        )
        applescript = f"""
tell application "Finder"
    tell disk "RCFlow Worker"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {{{win_x1}, {win_y1}, {win_x2}, {win_y2}}}
        set viewOptions to the icon view options of container window
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 96
        {bg_line}
        set position of item "{app_path.name}" of container window to {{{app_icon_x}, {app_icon_y}}}
        set position of item "Applications" of container window to {{{apps_icon_x}, {apps_icon_y}}}
        close
        open
        update without registering applications
        delay 2
        close
    end tell
end tell
"""
        print("  Setting DMG window layout via AppleScript...")
        subprocess.run(
            ["osascript", "-e", applescript],
            check=False,  # Non-fatal: window layout is cosmetic
            capture_output=True,
        )

        # Sync and unmount
        subprocess.run(["sync"], check=False)

    finally:
        subprocess.run(
            ["hdiutil", "detach", mount_point, "-quiet"],
            check=False,
            capture_output=True,
        )

    # ── Convert to compressed read-only DMG ─────────────────────────
    print("  Converting to compressed DMG...")
    subprocess.check_call(
        [
            "hdiutil",
            "convert",
            str(tmp_dmg),
            "-format",
            "UDZO",
            "-imagekey",
            "zlib-level=9",
            "-o",
            str(final_dmg),
        ]
    )
    tmp_dmg.unlink()

    if final_dmg.exists():
        size_mb = final_dmg.stat().st_size / (1024 * 1024)
        print(f"DMG created: {final_dmg} ({size_mb:.1f} MB)")
        return final_dmg

    print(f"WARNING: Expected DMG at {final_dmg} but it was not found.", file=sys.stderr)
    return None


### Code Signing ###


def _check_sign_env(variables: list[str]) -> dict[str, str]:
    """Verify that required environment variables are set for signing.

    Returns a dict of variable name → value. Exits with an error if any are missing.
    """
    values: dict[str, str] = {}
    missing: list[str] = []
    for var in variables:
        val = os.environ.get(var)
        if val:
            values[var] = val
        else:
            missing.append(var)
    if missing:
        print(
            f"ERROR: Code signing requested but missing environment variables:\n"
            f"  {', '.join(missing)}\n"
            f"Set these variables or omit --sign to build without signing.",
            file=sys.stderr,
        )
        sys.exit(1)
    return values


def sign_windows(path: Path) -> None:
    """Sign a Windows binary or installer with Authenticode (signtool.exe).

    Required env vars: SIGN_CERT_PATH, SIGN_CERT_PASSWORD.
    Optional: SIGN_TIMESTAMP_URL (defaults to http://timestamp.digicert.com).
    """
    env = _check_sign_env(["SIGN_CERT_PATH", "SIGN_CERT_PASSWORD"])
    timestamp_url = os.environ.get("SIGN_TIMESTAMP_URL", "http://timestamp.digicert.com")

    signtool = shutil.which("signtool")
    if not signtool:
        # Check Windows SDK default locations
        sdk_root = os.environ.get("WindowsSdkVerBinPath", "")  # noqa: SIM112
        if sdk_root:
            candidate = os.path.join(sdk_root, "x64", "signtool.exe")
            if os.path.isfile(candidate):
                signtool = candidate
        if not signtool:
            for candidate_path in [
                r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe",
                r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.22000.0\x64\signtool.exe",
            ]:
                if os.path.isfile(candidate_path):
                    signtool = candidate_path
                    break

    if not signtool:
        print(
            "ERROR: signtool.exe not found.\n  Install the Windows SDK or add signtool.exe to PATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = [
        signtool,
        "sign",
        "/f",
        env["SIGN_CERT_PATH"],
        "/p",
        env["SIGN_CERT_PASSWORD"],
        "/tr",
        timestamp_url,
        "/td",
        "sha256",
        "/fd",
        "sha256",
        str(path),
    ]
    print(f"Signing {path.name} with Authenticode...")
    subprocess.check_call(cmd)
    print(f"  Signed: {path.name}")


def sign_macos(path: Path) -> None:
    """Sign a macOS binary or .app bundle with codesign.

    Uses ``scripts/rcflow_macos.entitlements`` (hardened runtime, no sandbox)
    which is appropriate for the backend server app.  The Flutter client uses
    its own entitlements under ``rcflowclient/macos/``.

    Required env var: SIGN_IDENTITY (e.g. "Developer ID Application: Name (TEAMID)").
    """
    env = _check_sign_env(["SIGN_IDENTITY"])

    entitlements = PROJECT_ROOT / "scripts" / "rcflow_macos.entitlements"
    cmd = [
        "codesign",
        "--deep",
        "--force",
        "--options",
        "runtime",
        "--sign",
        env["SIGN_IDENTITY"],
        "--timestamp",
    ]
    if entitlements.exists():
        cmd.extend(["--entitlements", str(entitlements)])
    cmd.append(str(path))

    print(f"Signing {path.name} with codesign...")
    subprocess.check_call(cmd)
    print(f"  Signed: {path.name}")


def sign_macos_pkg(path: Path) -> None:
    """Sign a macOS .pkg installer with productsign.

    Required env var: SIGN_INSTALLER_IDENTITY (e.g. "Developer ID Installer: Name (TEAMID)").
    """
    env = _check_sign_env(["SIGN_INSTALLER_IDENTITY"])

    signed_path = path.with_suffix(".signed.pkg")
    cmd = [
        "productsign",
        "--sign",
        env["SIGN_INSTALLER_IDENTITY"],
        "--timestamp",
        str(path),
        str(signed_path),
    ]

    print(f"Signing {path.name} with productsign...")
    subprocess.check_call(cmd)

    # Replace the unsigned pkg with the signed one
    path.unlink()
    signed_path.rename(path)
    print(f"  Signed: {path.name}")


def notarize_macos(path: Path) -> None:
    """Submit a macOS artifact for Apple notarization and staple the ticket.

    Required env vars: APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD.
    """
    env = _check_sign_env(["APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_PASSWORD"])

    print(f"Submitting {path.name} for notarization...")
    submit_cmd = [
        "xcrun",
        "notarytool",
        "submit",
        str(path),
        "--apple-id",
        env["APPLE_ID"],
        "--team-id",
        env["APPLE_TEAM_ID"],
        "--password",
        env["APPLE_APP_PASSWORD"],
        "--wait",
    ]
    subprocess.check_call(submit_cmd)

    print(f"Stapling notarization ticket to {path.name}...")
    subprocess.check_call(["xcrun", "stapler", "staple", str(path)])
    print(f"  Notarized: {path.name}")


def sign_linux(path: Path) -> None:
    """Create a GPG detached signature for a Linux artifact.

    Required env var: GPG_KEY_ID.
    """
    env = _check_sign_env(["GPG_KEY_ID"])

    sig_path = Path(str(path) + ".asc")
    cmd = [
        "gpg",
        "--batch",
        "--yes",
        "--local-user",
        env["GPG_KEY_ID"],
        "--armor",
        "--detach-sign",
        "--output",
        str(sig_path),
        str(path),
    ]

    print(f"GPG-signing {path.name}...")
    subprocess.check_call(cmd)
    print(f"  Signature: {sig_path.name}")


def generate_checksums(artifacts: list[Path]) -> Path | None:
    """Generate a SHA256SUMS file for all artifacts in dist/.

    If GPG_KEY_ID is set, also signs the checksums file.
    """
    if not artifacts:
        return None

    dist_dir = PROJECT_ROOT / "dist"
    checksums_path = dist_dir / "SHA256SUMS"
    lines: list[str] = []
    for artifact in sorted(artifacts):
        if not artifact.exists():
            continue
        sha256 = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f"{sha256}  {artifact.name}")

    checksums_path.write_text("\n".join(lines) + "\n")
    print(f"Generated {checksums_path.name} ({len(lines)} entries)")

    gpg_key = os.environ.get("GPG_KEY_ID")
    if gpg_key:
        sig_path = Path(str(checksums_path) + ".asc")
        subprocess.check_call(
            [
                "gpg",
                "--batch",
                "--yes",
                "--local-user",
                gpg_key,
                "--armor",
                "--detach-sign",
                "--output",
                str(sig_path),
                str(checksums_path),
            ]
        )
        print(f"  GPG-signed: {sig_path.name}")

    return checksums_path


def run_install(bundle_dir: Path, installer_path: Path | None, target_platform: str) -> None:
    """Run the platform-appropriate installer after a successful build."""
    if target_platform == "linux":
        if installer_path:
            print(f"Installing {installer_path.name}...")
            subprocess.check_call(["sudo", "dpkg", "-i", str(installer_path)])
        else:
            print("Running install.sh...")
            subprocess.check_call(["sudo", "bash", str(bundle_dir / "install.sh")])
    elif target_platform == "macos":
        # bundle_dir is the .app path for macOS GUI builds; installer_path is the DMG.
        # Install by copying the .app to /Applications (no sudo needed for ~/Applications
        # fallback, but /Applications requires it).
        app_path = bundle_dir  # assemble_macos_app returns the .app directly
        dest = Path("/Applications") / app_path.name
        print(f"Installing {app_path.name} → {dest}...")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(app_path, dest)
    elif target_platform == "windows":
        if installer_path:
            print(f"Launching {installer_path.name}...")
            os.startfile(str(installer_path))  # type: ignore[attr-defined]  # Windows-only
        else:
            print("Running install.ps1...")
            subprocess.check_call(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(bundle_dir / "install.ps1")]
            )
    else:
        print(f"Auto-install not supported on {target_platform}", file=sys.stderr)
        sys.exit(1)

    print("Installation complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RCFlow distributable package")
    parser.add_argument(
        "--platform",
        choices=["linux", "windows", "macos"],
        default=None,
        help="Target platform (default: auto-detect)",
    )
    parser.add_argument(
        "--skip-pyinstaller",
        action="store_true",
        help="Skip PyInstaller step (use existing build)",
    )
    parser.add_argument(
        "--installer",
        action="store_true",
        help="Build a platform installer (.deb on Linux, setup.exe on Windows)",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install after building (implies --installer for platforms that use one)",
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Sign artifacts after building (requires platform-specific env vars)",
    )
    args = parser.parse_args()

    # --install implies --installer
    if args.install:
        args.installer = True

    target_platform = args.platform or detect_platform()
    version = get_version()
    arch = get_arch()

    print(f"Building RCFlow {version} for {target_platform}-{arch}")
    print(f"Project root: {PROJECT_ROOT}")
    print()

    # Verify we're building on the right platform
    current = detect_platform()
    if target_platform != current:
        print(
            f"WARNING: Cross-compilation is not supported by PyInstaller.\n"
            f"  You requested --platform {target_platform} but you're on {current}.\n"
            f"  The bundle must be built on the target platform.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 1: Ensure PyInstaller is available
    ensure_pyinstaller()

    # ── macOS: .app bundle → sign → DMG path ────────────────────────
    if target_platform == "macos":
        if args.skip_pyinstaller:
            pyinstaller_app = PROJECT_ROOT / "build" / "pyinstaller_dist" / "rcflow.app"
            if not pyinstaller_app.exists():
                print("ERROR: No existing PyInstaller .app found. Run without --skip-pyinstaller.", file=sys.stderr)
                sys.exit(1)
            print("Skipping PyInstaller (using existing .app build)")
        else:
            print("=== Step 1: Running PyInstaller ===")
            # macOS always builds as a windowed .app
            pyinstaller_app = run_pyinstaller(target_platform, windowed=True)
        print()

        print("=== Step 2: Assembling .app bundle ===")
        app_path = assemble_macos_app(pyinstaller_app, version, arch)
        print()

        if args.sign:
            print("=== Step 3: Signing .app ===")
            sign_macos(app_path)
            print()

        print("=== Step 4: Building DMG ===")
        dmg_path = build_macos_dmg(app_path, version, arch)
        print()

        if args.sign and dmg_path:
            print("=== Step 5: Notarizing DMG ===")
            notarize_macos(dmg_path)
            print()

        produced_artifacts = [a for a in [dmg_path] if a is not None]
        print("=== Checksums ===")
        generate_checksums(produced_artifacts)
        print()

        print("=== Build complete ===")
        print(f"  App:     {app_path}")
        if dmg_path:
            print(f"  DMG:     {dmg_path}")
        if args.sign:
            print("  Signing: enabled")
        print()

        if args.install and dmg_path:
            run_install(app_path, dmg_path, target_platform)

        return

    # ── Linux / Windows path ─────────────────────────────────────────

    # Step 2: Run PyInstaller
    use_windowed = target_platform == "windows" and args.installer
    if args.skip_pyinstaller:
        pyinstaller_dir = PROJECT_ROOT / "build" / "pyinstaller_dist" / "rcflow"
        if not pyinstaller_dir.exists():
            print("ERROR: No existing PyInstaller build found. Run without --skip-pyinstaller.", file=sys.stderr)
            sys.exit(1)
        print("Skipping PyInstaller (using existing build)")
    else:
        print("=== Step 1: Running PyInstaller ===")
        pyinstaller_dir = run_pyinstaller(target_platform, windowed=use_windowed)
    print()

    # Step 3: Assemble bundle
    print("=== Step 2: Assembling bundle ===")
    bundle_dir = assemble_bundle(pyinstaller_dir, target_platform, version, arch)
    print()

    # Step 4: Sign the main executable (before archiving)
    if args.sign:
        exe_name = "rcflow.exe" if target_platform == "windows" else "rcflow"
        exe_path = bundle_dir / exe_name
        if exe_path.exists():
            print("=== Step 3a: Signing executable ===")
            if target_platform == "windows":
                sign_windows(exe_path)
            print()

    # Step 5: Create archive
    print("=== Step 3: Creating archive ===")
    archive_path = create_archive(bundle_dir, target_platform, version, arch)
    print()

    # Step 6: Build platform installer (optional)
    installer_path = None
    if args.installer:
        if target_platform == "windows":
            print("=== Step 4: Building Windows installer ===")
            installer_path = build_windows_installer(bundle_dir, version, arch)
            print()
        elif target_platform == "linux":
            print("=== Step 4: Building .deb package ===")
            installer_path = build_deb(bundle_dir, version, arch)
            print()
        else:
            print(f"WARNING: --installer is not supported on {target_platform}. Skipping.", file=sys.stderr)

    # Step 7: Sign installer artifacts
    if args.sign:
        print("=== Step 5: Signing artifacts ===")
        if target_platform == "windows":
            if installer_path:
                sign_windows(installer_path)
        elif target_platform == "linux":
            sign_linux(archive_path)
            if installer_path:
                sign_linux(installer_path)
        print()

    # Step 8: Generate checksums
    produced_artifacts = [archive_path]
    if installer_path:
        produced_artifacts.append(installer_path)
    print("=== Checksums ===")
    generate_checksums(produced_artifacts)
    print()

    print("=== Build complete ===")
    print(f"  Archive: {archive_path}")
    print(f"  Bundle:  {bundle_dir}")
    if installer_path:
        print(f"  Installer: {installer_path}")
    if args.sign:
        print("  Signing:  enabled")
    print()

    if args.install:
        run_install(bundle_dir, installer_path, target_platform)

    print("To test locally:")
    if installer_path and target_platform == "linux":
        print(f"  sudo dpkg -i {installer_path}")
    elif target_platform == "linux":
        print(f"  cd {bundle_dir}")
        print("  sudo ./install.sh")
    elif installer_path and target_platform == "windows":
        print(f"  Run: {installer_path}")
    elif installer_path and target_platform == "macos":
        print(f"  installer -pkg {installer_path} -target /")
    elif target_platform == "macos":
        print(f"  cd {bundle_dir}")
        print("  ./install.sh")
    else:
        print(f"  cd {bundle_dir}")
        print("  .\\install.ps1")


if __name__ == "__main__":
    main()
