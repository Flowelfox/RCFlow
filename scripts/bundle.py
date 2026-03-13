#!/usr/bin/env python3
"""RCFlow bundle builder — creates distributable packages using PyInstaller.

Usage:
    python scripts/bundle.py                              # Build for current platform
    python scripts/bundle.py --platform linux              # Explicit platform
    python scripts/bundle.py --platform linux --installer  # Build .deb package
    python scripts/bundle.py --platform windows --installer # Build setup.exe
    python scripts/bundle.py --platform macos --installer   # Build .pkg installer

Outputs:
    dist/rcflow-{version}-{platform}-{arch}.tar.gz   (Linux archive)
    dist/rcflow_{version}_{deb_arch}.deb              (Linux .deb package)
    dist/rcflow-{version}-{platform}-{arch}.zip       (Windows archive)
    dist/rcflow-{version}-{arch}-setup.exe            (Windows installer)
    dist/rcflow-{version}-macos-{arch}.pkg            (macOS installer)
"""

from __future__ import annotations

import argparse
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
        return "x64"
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
        "src.api.ws.input_audio",
        "src.api.ws.output_text",
        "src.api.ws.output_audio",
        "src.core",
        "src.core.buffer",
        "src.core.llm",
        "src.core.permissions",
        "src.core.prompt_router",
        "src.core.session",
        "src.db",
        "src.db.engine",
        "src.executors",
        "src.executors.claude_code",
        "src.executors.codex",
        "src.logs",
        "src.models",
        "src.models.db",
        "src.prompts",
        "src.prompts.builder",
        "src.services",
        "src.services.tool_manager",
        "src.services.tool_settings",
        "src.speech",
        "src.speech.stt",
        "src.speech.tts",
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
        hidden_imports.extend([
            "src.tray",
            "pystray",
            "pystray._win32",
            "PIL",
            "PIL.Image",
            "PIL.ImageDraw",
            "winpty",
        ])

    # Data files to include inside the PyInstaller bundle (_MEIPASS)
    # Templates need to be in _MEIPASS so Path(__file__)-based resolution works
    datas = [
        (str(PROJECT_ROOT / "src" / "prompts" / "templates"), "templates"),
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "rcflow",
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(PROJECT_ROOT),
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
        icon_path = PROJECT_ROOT / "assets" / "tray_icon.ico"
        if icon_path.exists():
            cmd.extend(["--icon", str(icon_path)])

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

    output_dir = dist_dir / "rcflow"
    if not output_dir.exists():
        print(f"ERROR: Expected PyInstaller output at {output_dir}", file=sys.stderr)
        sys.exit(1)

    return output_dir


def assemble_bundle(pyinstaller_dir: Path, target_platform: str, version: str, arch: str) -> Path:
    """Assemble the final distributable bundle directory."""
    bundle_name = f"rcflow-{version}-{target_platform}-{arch}"
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
    migrations_src = PROJECT_ROOT / "src" / "db" / "migrations"
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

    # 10. Copy tray icon (Windows)
    if target_platform == "windows":
        tray_icon_src = PROJECT_ROOT / "assets" / "tray_icon.ico"
        if tray_icon_src.exists():
            shutil.copy2(tray_icon_src, bundle_dir / "tray_icon.ico")
            print("Copied tray_icon.ico")

    # Make the main executable executable on Linux
    if target_platform in {"linux", "macos"}:
        exe = bundle_dir / "rcflow"
        if exe.exists():
            os.chmod(exe, 0o755)

    return bundle_dir


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
    output_filename = f"rcflow-{version}-{arch}-setup"

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
            "ERROR: dpkg-deb not found. Install dpkg:\n"
            "  sudo apt-get install dpkg",
            file=sys.stderr,
        )
        return None

    deb_arch = get_deb_arch()
    pkg_name = f"rcflow_{version}_{deb_arch}"
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

    # Create systemd service unit
    systemd_dir = pkg_root / "lib" / "systemd" / "system"
    systemd_dir.mkdir(parents=True)
    (systemd_dir / "rcflow.service").write_text(
        f"""\
[Unit]
Description=RCFlow Action Server
After=network.target

[Service]
Type=simple
User=rcflow
WorkingDirectory=/opt/rcflow
# Settings loaded from /opt/rcflow/settings.json by the application
ExecStart=/opt/rcflow/rcflow
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/rcflow/data /opt/rcflow/logs /opt/rcflow/certs
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""
    )

    # Create DEBIAN control files
    debian_dir = pkg_root / "DEBIAN"
    debian_dir.mkdir(parents=True)

    (debian_dir / "control").write_text(
        f"""\
Package: rcflow
Version: {version}
Architecture: {deb_arch}
Maintainer: RCFlow <rcflow@localhost>
Description: RCFlow Action Server
 Self-contained RCFlow backend server with all dependencies bundled.
Section: net
Priority: optional
Installed-Size: {sum(f.stat().st_size for f in install_dir.rglob('*') if f.is_file()) // 1024}
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

case "$1" in
    start)
        echo "Starting $NAME..."
        start-stop-daemon --start --background --make-pidfile \\
            --pidfile "$PIDFILE" --chuid "$USER" --chdir "$WORKDIR" \\
            --startas /bin/bash -- -c "exec $DAEMON >> $LOGFILE 2>&1"
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

echo ""
echo "============================================"
echo "  RCFlow installed successfully!"
echo "  Run 'rcflow info' to view server details."
echo "  Run 'rcflow api-key' to view your API key."
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
    """Build a macOS .pkg installer from the assembled bundle directory."""
    pkgbuild = shutil.which("pkgbuild")
    if not pkgbuild:
        print(
            "ERROR: pkgbuild not found. Install Xcode command line tools:\n"
            "  xcode-select --install",
            file=sys.stderr,
        )
        return None

    pkg_scripts_dir = PROJECT_ROOT / "build" / "macos-pkg" / "scripts"
    if pkg_scripts_dir.exists():
        shutil.rmtree(pkg_scripts_dir)
    pkg_scripts_dir.mkdir(parents=True, exist_ok=True)

    postinstall = pkg_scripts_dir / "postinstall"
    postinstall.write_text(
        """#!/bin/bash
set -euo pipefail
/usr/local/lib/rcflow/install.sh --prefix /usr/local/lib/rcflow --bin-dir /usr/local/bin --unattended
"""
    )
    os.chmod(postinstall, 0o755)

    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = dist_dir / f"rcflow-{version}-macos-{arch}.pkg"

    if pkg_path.exists():
        pkg_path.unlink()

    print(f"Building {pkg_path.name}...")
    subprocess.check_call([
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
        "/usr/local/lib/rcflow",
        str(pkg_path),
    ])

    if pkg_path.exists():
        size_mb = pkg_path.stat().st_size / (1024 * 1024)
        print(f"Package created: {pkg_path} ({size_mb:.1f} MB)")
        return pkg_path

    print(f"WARNING: Expected .pkg at {pkg_path} but it was not found.", file=sys.stderr)
    return None


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
    args = parser.parse_args()

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

    # Step 4: Create archive
    print("=== Step 3: Creating archive ===")
    archive_path = create_archive(bundle_dir, target_platform, version, arch)
    print()

    # Step 5: Build platform installer (optional)
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
        elif target_platform == "macos":
            print("=== Step 4: Building .pkg package ===")
            installer_path = build_macos_pkg(bundle_dir, version, arch)
            print()
        else:
            print(f"WARNING: --installer is not supported on {target_platform}. Skipping.", file=sys.stderr)

    print("=== Build complete ===")
    print(f"  Archive: {archive_path}")
    print(f"  Bundle:  {bundle_dir}")
    if installer_path:
        print(f"  Installer: {installer_path}")
    print()
    print("To test locally:")
    if installer_path and target_platform == "linux":
        print(f"  sudo dpkg -i {installer_path}")
    elif target_platform == "linux":
        print(f"  cd {bundle_dir}")
        print("  sudo ./install.sh")
    elif installer_path and target_platform == "windows":
        print(f"  Run: {installer_path}")
    elif installer_path and target_platform == "macos":
        print(f"  sudo installer -pkg {installer_path} -target /")
    elif target_platform == "macos":
        print(f"  cd {bundle_dir}")
        print("  sudo ./install.sh")
    else:
        print(f"  cd {bundle_dir}")
        print("  .\\install.ps1")


if __name__ == "__main__":
    main()
