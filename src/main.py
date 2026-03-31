import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.http import router as http_router
from src.api.integrations.linear import router as linear_router
from src.api.ws.input_text import router as input_text_router
from src.api.ws.output_text import router as output_text_router
from src.api.ws.terminal import router as terminal_router
from src.config import get_settings
from src.core.attachment_store import AttachmentStore
from src.core.llm import LLMClient
from src.core.prompt_router import PromptRouter
from src.core.session import SessionManager
from src.db.engine import check_connection, dispose_engine, get_session_factory, init_engine
from src.logs import setup_logging
from src.services.artifact_scanner import ArtifactScanner
from src.services.telemetry_service import TelemetryService
from src.services.tool_manager import ToolManager
from src.services.tool_settings import ToolSettingsManager
from src.terminal.manager import TerminalSessionManager
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan: startup and shutdown logic."""
    settings = get_settings()
    setup_logging(settings)

    logger.info("Starting RCFlow server")

    # Database
    init_engine(settings)
    try:
        await check_connection()
    except Exception as e:
        logger.error("Database connection check failed: %s", e)
        raise
    logger.info("Database engine initialized")

    # Session manager — created early so stale sessions can be reloaded into it.
    session_manager = SessionManager(backend_id=settings.RCFLOW_BACKEND_ID)

    # Reload non-terminal sessions from the previous run directly into memory so
    # clients can continue using them without any explicit restore step.
    db_factory = get_session_factory()
    async with db_factory() as db:
        await session_manager.reload_stale_sessions(db, settings.RCFLOW_BACKEND_ID)

    # Settings
    app.state.settings = settings

    # Attachment store (temporary in-memory store for user-uploaded files)
    app.state.attachment_store = AttachmentStore()

    # Tool manager — detect and install CLI tools
    tool_manager = ToolManager(settings)
    await tool_manager.ensure_tools()
    app.state.tool_manager = tool_manager

    # Tool settings (per-tool isolated config)
    tool_settings = ToolSettingsManager()
    tool_settings.ensure_defaults("claude_code")
    app.state.tool_settings = tool_settings

    # Artifact scanner
    db_session_factory = get_session_factory()
    artifact_scanner = ArtifactScanner(settings, db_session_factory)
    app.state.artifact_scanner = artifact_scanner
    app.state.db_session_factory = db_session_factory

    # Tool registry
    tool_registry = ToolRegistry()
    tool_registry.load_from_directory(settings.TOOLS_DIR)
    app.state.tool_registry = tool_registry
    logger.info("Loaded %d tools from %s", len(tool_registry.list_tools()), settings.TOOLS_DIR)

    app.state.session_manager = session_manager

    # Terminal session manager (PTY terminals, separate from LLM sessions)
    terminal_manager = TerminalSessionManager()
    app.state.terminal_manager = terminal_manager

    # LLM client (None in direct tool mode)
    llm_client: LLMClient | None = None
    if settings.LLM_PROVIDER != "none":
        llm_client = LLMClient(settings, tool_registry)
    app.state.llm_client = llm_client

    # Telemetry service
    telemetry_service = TelemetryService(
        db_factory=db_session_factory,
        backend_id=settings.RCFLOW_BACKEND_ID,
        retention_days=settings.TELEMETRY_RETENTION_DAYS,
    )
    app.state.telemetry_service = telemetry_service

    # Prompt router
    prompt_router = PromptRouter(
        llm_client,
        session_manager,
        tool_registry,
        db_session_factory,
        settings,
        tool_settings,
        tool_manager,
        artifact_scanner,
        telemetry_service,
    )
    app.state.prompt_router = prompt_router

    logger.info(
        "RCFlow ready — listening on %s:%d (backend_id=%s)",
        settings.RCFLOW_HOST,
        settings.RCFLOW_PORT,
        settings.RCFLOW_BACKEND_ID,
    )

    # Start inactivity reaper
    reaper_task = asyncio.create_task(prompt_router.run_inactivity_reaper())

    # Start tool update checker
    update_task = asyncio.create_task(tool_manager.run_update_loop())

    # Start telemetry aggregation (runs every 60 s) + nightly retention cleanup
    async def _run_telemetry_loop() -> None:
        import asyncio as _asyncio  # noqa: PLC0415
        tick = 0
        while True:
            await _asyncio.sleep(60)
            await telemetry_service.aggregate_pending()
            tick += 1
            if tick % 1440 == 0:  # ~once per day (1440 minutes)
                await telemetry_service.cleanup_old_records()

    telemetry_task = asyncio.create_task(_run_telemetry_loop())

    # Background task set — keeps strong references so tasks are not GC'd
    _bg_tasks: set[asyncio.Task[None]] = set()

    # Linear startup sync (non-blocking background task)
    if settings.LINEAR_SYNC_ON_STARTUP and settings.LINEAR_API_KEY and settings.LINEAR_TEAM_ID:
        from src.api.integrations.linear import _issue_to_dict, _upsert_issues  # noqa: PLC0415
        from src.services.linear_service import LinearService as _LinearService  # noqa: PLC0415
        from src.services.linear_service import LinearServiceError as _LinearServiceError  # noqa: PLC0415
        async def _run_startup_sync() -> None:
            try:
                async with _LinearService(settings.LINEAR_API_KEY) as svc:
                    parsed = await svc.fetch_issues(settings.LINEAR_TEAM_ID)
                async with db_session_factory() as db:
                    upserted = await _upsert_issues(db, settings.RCFLOW_BACKEND_ID, parsed)
                for row in upserted:
                    session_manager.broadcast_linear_issue_update(_issue_to_dict(row))
                logger.info("Linear startup sync: %d issues", len(upserted))
            except _LinearServiceError as exc:
                logger.warning("Linear startup sync failed: %s", exc)
            except Exception as exc:
                logger.warning("Linear startup sync unexpected error: %s", exc)
        _t = asyncio.create_task(_run_startup_sync())
        _bg_tasks.add(_t)
        _t.add_done_callback(_bg_tasks.discard)

    yield

    # Shutdown
    telemetry_task.cancel()
    update_task.cancel()
    reaper_task.cancel()
    logger.info("Shutting down RCFlow server")
    await terminal_manager.close_all()
    await prompt_router.cancel_pending_tasks()
    if llm_client is not None:
        await llm_client.close()

    async with db_session_factory() as db:
        await session_manager.save_all_sessions(db)

    await dispose_engine()
    logger.info("RCFlow server stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="RCFlow",
        description="WebSocket action server: natural language prompts to tool executions via LLM",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(http_router)
    app.include_router(linear_router)
    app.include_router(input_text_router)
    app.include_router(output_text_router)
    app.include_router(terminal_router)

    return app


app = create_app()
