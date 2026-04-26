"""Plugin management API for RCFlow-managed coding agents.

Provides CRUD endpoints for plugins installed under each managed tool's
plugins directory (``<managed_tools_dir>/<tool>/plugins/``).

Tool-scoped endpoints (canonical)
----------------------------------
GET    /api/tools/{tool_name}/plugins
POST   /api/tools/{tool_name}/plugins
DELETE /api/tools/{tool_name}/plugins/{name}
PATCH  /api/tools/{tool_name}/plugins/{name}

Legacy endpoints (deprecated, will be removed in a future MAJOR version)
-------------------------------------------------------------------------
GET    /api/rcflow-plugins          → alias for claude_code plugins list
POST   /api/rcflow-plugins          → alias for claude_code plugin install
DELETE /api/rcflow-plugins/{name}   → alias for claude_code plugin uninstall

Only ``claude_code`` plugins are fully supported.  Requests for ``codex``
return ``422 Unprocessable Entity`` until Codex gains a plugin system.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from src.api.deps import verify_http_api_key
from src.paths import get_managed_cc_plugins_dir, get_tool_plugins_dir

# Canonical paths for the Claude Code native plugin registry.
_INSTALLED_PLUGINS_FILE = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
_CC_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

router = APIRouter(tags=["Plugins"])
logger = logging.getLogger(__name__)

_description_re = re.compile(r"^description\s*:\s*(.+)$", re.MULTILINE)
_hide_re = re.compile(r"^hide-from-slash-command-tool\s*:\s*(.+)$", re.MULTILINE)

# Tools that support the plugin system.
_SUPPORTED_PLUGIN_TOOLS = frozenset({"claude_code"})
# All recognised managed tool names.
_KNOWN_TOOLS = frozenset({"claude_code", "codex"})


# ---------------------------------------------------------------------------
# PluginStateManager — per-tool enable/disable persistence
# ---------------------------------------------------------------------------

_PLUGIN_STATE_FILE = "plugins_state.json"


class PluginStateManager:
    """Reads and writes per-tool plugin enable/disable state.

    State is stored in ``<plugins_dir>/plugins_state.json``::

        {
          "disabled": ["plugin-name-a", "plugin-name-b"]
        }

    All mutations are written atomically via a ``.tmp`` → rename pattern to
    guard against corruption from concurrent requests or abrupt termination.
    """

    def __init__(self, plugins_dir: Path) -> None:
        self._state_file = plugins_dir / _PLUGIN_STATE_FILE
        self._lock = asyncio.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def get_disabled(self) -> set[str]:
        """Return the set of currently disabled plugin names."""
        return set(self._read().get("disabled", []))

    async def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable a plugin by name. Thread-safe."""
        async with self._lock:
            data = self._read()
            disabled: list[str] = data.get("disabled", [])
            disabled_set = set(disabled)
            if enabled:
                disabled_set.discard(name)
            else:
                disabled_set.add(name)
            data["disabled"] = sorted(disabled_set)
            tmp = self._state_file.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                tmp.replace(self._state_file)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _visible_commands(plugin_dir: Path) -> list[str]:
    """Return names of non-hidden commands exposed by *plugin_dir*."""
    commands_dir = plugin_dir / "commands"
    if not commands_dir.is_dir():
        return []
    names: list[str] = []
    for md in sorted(commands_dir.glob("*.md")):
        if md.name.endswith(":Zone.Identifier"):
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    fm = text[3:end]
                    h = _hide_re.search(fm)
                    if h and h.group(1).strip().strip("\"'").lower() == "true":
                        continue
        except OSError:
            continue
        names.append(md.stem)
    return names


def _plugin_description(plugin_dir: Path) -> str:
    """Read description from ``.claude-plugin/plugin.json`` if present."""
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return ""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return data.get("description", "") or ""
    except Exception:
        return ""


def _plugin_info(plugin_dir: Path, disabled: set[str]) -> dict[str, Any]:
    return {
        "name": plugin_dir.name,
        "description": _plugin_description(plugin_dir),
        "commands": _visible_commands(plugin_dir),
        "path": str(plugin_dir),
        "enabled": plugin_dir.name not in disabled,
    }


def _derive_plugin_name(source: str) -> str:
    """Derive a plugin directory name from a git URL or local path."""
    name = source.rstrip("/").split("?")[0].split("#")[0]
    name = name.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    name = re.sub(r"[^\w\-]", "-", name).strip("-") or "plugin"
    return name


def _require_supported_tool(tool_name: str) -> None:
    """Raise appropriate HTTP errors for unknown or unsupported tool names."""
    if tool_name not in _KNOWN_TOOLS:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name!r}")
    if tool_name not in _SUPPORTED_PLUGIN_TOOLS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Plugin support is not yet available for {tool_name!r}. Currently only 'claude_code' supports plugins."
            ),
        )


# ---------------------------------------------------------------------------
# Tool-scoped plugin endpoints (canonical)
# ---------------------------------------------------------------------------


@router.get(
    "/tools/{tool_name}/plugins",
    summary="List plugins for a managed tool",
    description=(
        "Returns all plugins installed in the managed tool's plugins directory "
        "(``<managed_tools_dir>/<tool>/plugins/``). "
        "Each entry includes the plugin name, optional description, the list of "
        "visible slash command names it contributes, and whether the plugin is "
        "currently enabled. "
        "Only ``claude_code`` is supported; requesting ``codex`` returns 422."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_tool_plugins(tool_name: str) -> dict[str, Any]:
    """Return installed plugins for the given managed tool."""
    _require_supported_tool(tool_name)
    plugins_dir = get_tool_plugins_dir(tool_name)
    state = PluginStateManager(plugins_dir)
    disabled = state.get_disabled()
    plugins: list[dict[str, Any]] = []
    if plugins_dir.is_dir():
        for entry in sorted(plugins_dir.iterdir()):
            if entry.is_dir() and entry.name != _PLUGIN_STATE_FILE.replace(".json", ""):
                plugins.append(_plugin_info(entry, disabled))
    return {"plugins": plugins}


class InstallPluginRequest(BaseModel):
    source: str
    """Git URL or local filesystem path to clone/copy from."""
    name: str | None = None
    """Override the plugin directory name (defaults to the last URL/path segment)."""


@router.post(
    "/tools/{tool_name}/plugins",
    summary="Install a plugin for a managed tool",
    description=(
        "Installs a plugin into the managed tool's plugins directory by cloning a "
        "git repository or copying a local directory. The plugin name defaults to "
        "the last segment of the source URL/path (without .git). Returns the "
        "installed plugin info on success. "
        "Only ``claude_code`` is supported; requesting ``codex`` returns 422."
    ),
    dependencies=[Depends(verify_http_api_key)],
    status_code=201,
)
async def install_tool_plugin(tool_name: str, body: InstallPluginRequest) -> dict[str, Any]:
    """Install a plugin from a git URL or local path for the given managed tool."""
    _require_supported_tool(tool_name)

    source = body.source.strip()
    if not source:
        raise HTTPException(status_code=422, detail="'source' must not be empty")

    plugin_name = (body.name or _derive_plugin_name(source)).strip()
    if not plugin_name:
        raise HTTPException(status_code=422, detail="Could not derive a plugin name from source")

    plugins_dir = get_tool_plugins_dir(tool_name)
    dest = plugins_dir / plugin_name

    if dest.exists():
        raise HTTPException(
            status_code=409,
            detail=(f"Plugin '{plugin_name}' already exists at {dest}. Uninstall it first or choose a different name."),
        )

    source_path = Path(source).expanduser()

    if source_path.exists() and source_path.is_dir():
        try:
            shutil.copytree(str(source_path), str(dest))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to copy plugin: {exc}") from exc
    else:
        git = shutil.which("git")
        if not git:
            raise HTTPException(
                status_code=503,
                detail="'git' is not available on PATH. Install git to install plugins from URLs.",
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                git,
                "clone",
                "--depth",
                "1",
                source,
                str(dest),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
            if proc.returncode != 0:
                error_text = stderr.decode("utf-8", errors="replace").strip()
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                raise HTTPException(status_code=500, detail=f"git clone failed: {error_text}")
        except TimeoutError:
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(status_code=504, detail="git clone timed out after 120 s") from None

    logger.info("Installed %s plugin '%s' from %s", tool_name, plugin_name, source)
    state = PluginStateManager(plugins_dir)
    disabled = state.get_disabled()
    return {"plugin": _plugin_info(dest, disabled)}


@router.delete(
    "/tools/{tool_name}/plugins/{name}",
    summary="Uninstall a plugin for a managed tool",
    description=(
        "Removes the named plugin from the managed tool's plugins directory, "
        "deleting its directory and all contained files. "
        "Only ``claude_code`` is supported; requesting ``codex`` returns 422."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def uninstall_tool_plugin(tool_name: str, name: str) -> dict[str, str]:
    """Remove an installed plugin by name for the given managed tool."""
    _require_supported_tool(tool_name)

    plugins_dir = get_tool_plugins_dir(tool_name)
    plugin_dir = plugins_dir / name
    if not plugin_dir.exists():
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' is not installed")
    if not plugin_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"'{name}' is not a directory")
    try:
        plugin_dir.resolve().relative_to(plugins_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plugin name") from None

    shutil.rmtree(plugin_dir, ignore_errors=False)

    # Remove from disabled set if present (clean up state file)
    state = PluginStateManager(plugins_dir)
    await state.set_enabled(name, enabled=True)

    logger.info("Uninstalled %s plugin '%s'", tool_name, name)
    return {"status": "uninstalled", "name": name}


class SetPluginEnabledRequest(BaseModel):
    enabled: bool
    """Whether the plugin should be enabled (True) or disabled (False)."""


@router.patch(
    "/tools/{tool_name}/plugins/{name}",
    summary="Enable or disable a plugin",
    description=(
        "Toggles a plugin on or off without uninstalling it. "
        "Disabled plugins are excluded from the ``GET /api/slash-commands`` response "
        "so their commands no longer appear in the autocomplete overlay. "
        "The plugin directory is preserved and the plugin can be re-enabled at any time. "
        "Only ``claude_code`` is supported; requesting ``codex`` returns 422."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def set_tool_plugin_enabled(
    tool_name: str,
    name: str,
    body: SetPluginEnabledRequest,
) -> dict[str, Any]:
    """Enable or disable a plugin for the given managed tool."""
    _require_supported_tool(tool_name)

    plugins_dir = get_tool_plugins_dir(tool_name)
    plugin_dir = plugins_dir / name
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' is not installed")
    try:
        plugin_dir.resolve().relative_to(plugins_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid plugin name") from None

    state = PluginStateManager(plugins_dir)
    await state.set_enabled(name, enabled=body.enabled)
    disabled = state.get_disabled()
    logger.info("%s plugin '%s' %s", tool_name, name, "enabled" if body.enabled else "disabled")
    return {"plugin": _plugin_info(plugin_dir, disabled)}


# ---------------------------------------------------------------------------
# Legacy / deprecated endpoints  (aliases → claude_code)
# ---------------------------------------------------------------------------

_DEPRECATION_NOTICE = "This endpoint is deprecated. Use /api/tools/claude_code/plugins instead."


@router.get(
    "/rcflow-plugins",
    summary="[Deprecated] List RCFlow-managed plugins",
    description=("Deprecated alias for ``GET /api/tools/claude_code/plugins``. Use the tool-scoped endpoint instead."),
    deprecated=True,
    dependencies=[Depends(verify_http_api_key)],
)
async def list_rcflow_plugins_deprecated(response: Response) -> dict[str, Any]:
    """Deprecated — use GET /api/tools/claude_code/plugins."""
    response.headers["X-RCFlow-Deprecated"] = _DEPRECATION_NOTICE
    plugins_dir = get_managed_cc_plugins_dir()
    state = PluginStateManager(plugins_dir)
    disabled = state.get_disabled()
    plugins: list[dict[str, Any]] = []
    if plugins_dir.is_dir():
        for entry in sorted(plugins_dir.iterdir()):
            if entry.is_dir():
                plugins.append(_plugin_info(entry, disabled))
    return {"plugins": plugins}


class _LegacyInstallRequest(BaseModel):
    source: str
    name: str | None = None


@router.post(
    "/rcflow-plugins",
    summary="[Deprecated] Install an RCFlow-managed plugin",
    description=("Deprecated alias for ``POST /api/tools/claude_code/plugins``. Use the tool-scoped endpoint instead."),
    deprecated=True,
    dependencies=[Depends(verify_http_api_key)],
    status_code=201,
)
async def install_rcflow_plugin_deprecated(
    body: _LegacyInstallRequest,
    response: Response,
) -> dict[str, Any]:
    """Deprecated — use POST /api/tools/claude_code/plugins."""
    response.headers["X-RCFlow-Deprecated"] = _DEPRECATION_NOTICE
    return await install_tool_plugin(
        "claude_code",
        InstallPluginRequest(source=body.source, name=body.name),
    )


@router.delete(
    "/rcflow-plugins/{name}",
    summary="[Deprecated] Uninstall an RCFlow-managed plugin",
    description=(
        "Deprecated alias for ``DELETE /api/tools/claude_code/plugins/{name}``. Use the tool-scoped endpoint instead."
    ),
    deprecated=True,
    dependencies=[Depends(verify_http_api_key)],
)
async def uninstall_rcflow_plugin_deprecated(name: str, response: Response) -> dict[str, str]:
    """Deprecated — use DELETE /api/tools/claude_code/plugins/{name}."""
    response.headers["X-RCFlow-Deprecated"] = _DEPRECATION_NOTICE
    return await uninstall_tool_plugin("claude_code", name)
