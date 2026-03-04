#!/usr/bin/env python3
"""RCFlow bundle builder — creates distributable packages using PyInstaller.

Usage:
    python scripts/bundle.py              # Build for current platform
    python scripts/bundle.py --platform linux    # Explicit platform
    python scripts/bundle.py --platform windows  # Explicit platform

Outputs:
    dist/rcflow-{version}-{platform}-{arch}.tar.gz   (Linux)
    dist/rcflow-{version}-{platform}-{arch}.zip       (Windows)
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


def run_pyinstaller(target_platform: str) -> Path:
    """Run PyInstaller and return the path to the output directory."""
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
        "poml",
        "pydantic",
        "pydantic_settings",
        "httpx",
        "anthropic",
        "aiohttp",
    ]

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

    # Collect all submodules of src
    cmd.extend(["--collect-submodules", "src"])
    cmd.extend(["--collect-submodules", "uvicorn"])
    cmd.extend(["--collect-submodules", "poml"])

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

    # 5. Copy .env.example
    env_example = PROJECT_ROOT / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, bundle_dir / ".env.example")

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

    # Make the main executable executable on Linux
    if target_platform == "linux":
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
    if args.skip_pyinstaller:
        pyinstaller_dir = PROJECT_ROOT / "build" / "pyinstaller_dist" / "rcflow"
        if not pyinstaller_dir.exists():
            print("ERROR: No existing PyInstaller build found. Run without --skip-pyinstaller.", file=sys.stderr)
            sys.exit(1)
        print("Skipping PyInstaller (using existing build)")
    else:
        print("=== Step 1: Running PyInstaller ===")
        pyinstaller_dir = run_pyinstaller(target_platform)
    print()

    # Step 3: Assemble bundle
    print("=== Step 2: Assembling bundle ===")
    bundle_dir = assemble_bundle(pyinstaller_dir, target_platform, version, arch)
    print()

    # Step 4: Create archive
    print("=== Step 3: Creating archive ===")
    archive_path = create_archive(bundle_dir, target_platform, version, arch)
    print()

    print("=== Build complete ===")
    print(f"  Archive: {archive_path}")
    print(f"  Bundle:  {bundle_dir}")
    print()
    print("To test locally:")
    if target_platform == "linux":
        print(f"  cd {bundle_dir}")
        print("  sudo ./install.sh")
    else:
        print(f"  cd {bundle_dir}")
        print("  .\\install.ps1")


if __name__ == "__main__":
    main()
