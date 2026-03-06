"""Path resolution for both normal Python and PyInstaller frozen environments."""

from __future__ import annotations

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
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
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
        return Path(sys._MEIPASS) / "templates"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / "prompts" / "templates"
