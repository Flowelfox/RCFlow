"""Path resolution for both normal Python and PyInstaller frozen environments."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_bundle_dir() -> Path:
    """Return the directory containing bundled data files.

    When frozen (PyInstaller), this is ``sys._MEIPASS``.
    When running from source, this is the project root (parent of ``src/``).
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))  # noqa: B009 - private attr set by PyInstaller at runtime
    return Path(__file__).resolve().parent.parent


def get_install_dir() -> Path:
    """Return the installation directory (where the executable lives).

    When frozen, this is the directory containing the ``rcflow`` executable.
    When running from source, this is the project root.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_default_tools_dir() -> Path:
    """Return the default tools directory path.

    When frozen, tools are next to the executable in the install directory.
    When running from source, tools are at ``./tools`` in the project root.
    """
    return get_install_dir() / "tools"


def get_migrations_dir() -> Path:
    """Return the path to alembic migration scripts.

    When frozen, migrations are bundled in the install directory.
    When running from source, they're at ``src/db/migrations``.
    """
    if is_frozen():
        return get_install_dir() / "migrations"
    return get_bundle_dir() / "src" / "db" / "migrations"


def get_alembic_ini() -> Path:
    """Return the path to alembic.ini.

    When frozen, it's in the install directory.
    When running from source, it's in the project root.
    """
    if is_frozen():
        return get_install_dir() / "alembic.ini"
    return get_bundle_dir() / "alembic.ini"


def get_templates_dir() -> Path:
    """Return the path to Jinja2 prompt templates.

    When frozen, templates are bundled via PyInstaller data files.
    When running from source, they're at ``src/prompts/templates``.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")) / "templates"  # noqa: B009
    return Path(__file__).resolve().parent / "prompts" / "templates"


_TOOL_DIR_MAP: dict[str, str] = {
    "claude_code": "claude-code",
    "codex": "codex",
}


def get_tool_plugins_dir(tool_name: str) -> Path:
    """Return the plugins directory for a RCFlow-managed tool.

    Args:
        tool_name: One of ``"claude_code"`` or ``"codex"``.

    Returns:
        A :class:`~pathlib.Path` pointing to
        ``<managed_tools_dir>/<tool_dir>/plugins/``.
        The directory is created on first access.

    Raises:
        ValueError: If *tool_name* is not a recognised managed tool.
    """
    tool_dir = _TOOL_DIR_MAP.get(tool_name)
    if tool_dir is None:
        raise ValueError(f"Unknown tool: {tool_name!r}. Must be one of: {sorted(_TOOL_DIR_MAP)}")
    candidate = get_managed_tools_dir() / tool_dir / "plugins"
    with contextlib.suppress(OSError):
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def get_managed_cc_plugins_dir() -> Path:
    """Return the directory where RCFlow stores its own managed Claude Code plugins.

    Each sub-directory under this path is treated as a single plugin and may
    contain a ``commands/`` folder with ``.md`` skill files, following the same
    format used by Claude Code's own plugin system.

    Layout::

        <managed_tools_dir>/claude-code/plugins/
            my-plugin/
                commands/
                    do-thing.md
            another-plugin/
                commands/
                    other-skill.md

    Commands found here are returned with ``"source": "rcflow_plugin"`` by the
    ``GET /api/slash-commands`` endpoint.

    The directory is created on first access (mirroring :func:`get_managed_tools_dir`)
    and is also explicitly created during managed Claude Code installation so that
    it exists on every machine where RCFlow manages the Claude Code binary.
    """
    candidate = get_managed_tools_dir() / "claude-code" / "plugins"
    with contextlib.suppress(OSError):
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def get_managed_tools_dir() -> Path:
    """Return the base directory for RCFlow-managed CLI tool binaries and config.

    Resolution order:
    1. ``~/.local/share/rcflow/tools`` (Linux) or ``%LOCALAPPDATA%/rcflow/tools``
       (Windows) — if the home directory exists and is writable.
    2. ``<install_dir>/managed-tools`` — fallback for service accounts or
       environments where the home directory is absent or read-only (e.g. a
       system user running from ``/opt/rcflow``).
    """
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = Path(local_app_data) / "rcflow" / "tools"
        else:
            candidate = Path.home() / "AppData" / "Local" / "rcflow" / "tools"
    else:
        candidate = Path.home() / ".local" / "share" / "rcflow" / "tools"

    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return get_install_dir() / "managed-tools"
