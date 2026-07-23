"""``register_artifact`` (exposed to agents as ``rcflow_register_artifact``) — mark a produced file as an artifact."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.native_tools import NativeToolContext


async def run(ctx: NativeToolContext, params: dict[str, Any]) -> str:
    """Register *file_path* as an artifact of the current session."""
    file_path = str(params.get("file_path", "")).strip()
    if not file_path:
        raise ValueError("register_artifact requires a 'file_path'")
    scanner = ctx.router._artifact_scanner
    if scanner is None:
        raise RuntimeError("Artifact scanning is not enabled on this worker")

    project = ctx.session.main_project_path
    new_count, updated_count = await scanner.register_paths(
        ctx.session.id,
        [file_path],
        Path(project) if project else None,
    )
    if new_count == 0 and updated_count == 0:
        return (
            f"No artifact registered for '{file_path}' — the file must exist on disk "
            f"and match the artifact include/exclude patterns."
        )
    ctx.router._fire_realtime_artifact_scan(ctx.session)
    verb = "registered" if new_count else "updated"
    return f"Artifact {verb}: {file_path}"
