import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import select

from src.api.http import router as http_router
from src.api.ws.input_audio import router as input_audio_router
from src.api.ws.input_text import router as input_text_router
from src.api.ws.output_audio import router as output_audio_router
from src.api.ws.output_text import router as output_text_router
from src.config import get_settings
from src.core.llm import LLMClient
from src.core.prompt_router import PromptRouter
from src.core.session import SessionManager
from src.db.engine import check_connection, dispose_engine, get_session_factory, init_engine
from src.logs import setup_logging
from src.models.db import Session as SessionModel
from src.services.tool_manager import ToolManager
from src.services.tool_settings import ToolSettingsManager
from src.speech.stt import create_stt_provider
from src.speech.tts import create_tts_provider
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

    # Mark stale non-terminal sessions as failed (leftover from previous shutdown)
    # Only affect sessions owned by this backend instance.
    db_factory = get_session_factory()
    async with db_factory() as db:
        stale_statuses = ("created", "active", "executing", "paused")
        stale_rows = (
            await db.execute(
                select(SessionModel).where(
                    SessionModel.status.in_(stale_statuses),
                    SessionModel.backend_id == settings.RCFLOW_BACKEND_ID,
                )
            )
        ).scalars().all()
        for row in stale_rows:
            row.status = "failed"
            row.ended_at = datetime.now(UTC)
            meta = row.metadata_ or {}
            row.metadata_ = {**meta, "error": "Server restarted while session was active"}
        if stale_rows:
            await db.commit()
            logger.info("Marked %d stale sessions as failed after restart", len(stale_rows))

    # Settings
    app.state.settings = settings

    # Tool manager — detect and install CLI tools
    tool_manager = ToolManager(settings)
    await tool_manager.ensure_tools()
    app.state.tool_manager = tool_manager

    # Tool settings (per-tool isolated config)
    tool_settings = ToolSettingsManager()
    app.state.tool_settings = tool_settings

    # Tool registry
    tool_registry = ToolRegistry()
    tool_registry.load_from_directory(settings.TOOLS_DIR)
    app.state.tool_registry = tool_registry
    logger.info("Loaded %d tools from %s", len(tool_registry.list_tools()), settings.TOOLS_DIR)

    # Session manager
    session_manager = SessionManager(backend_id=settings.RCFLOW_BACKEND_ID)
    app.state.session_manager = session_manager

    # LLM client
    llm_client = LLMClient(settings, tool_registry)
    app.state.llm_client = llm_client

    # Prompt router
    db_session_factory = get_session_factory()
    app.state.db_session_factory = db_session_factory
    prompt_router = PromptRouter(
        llm_client,
        session_manager,
        tool_registry,
        db_session_factory,
        settings,
        tool_settings,
        tool_manager,
    )
    app.state.prompt_router = prompt_router

    # STT provider
    stt_provider = create_stt_provider(settings.STT_PROVIDER, settings.STT_API_KEY)
    app.state.stt_provider = stt_provider

    # TTS provider
    tts_provider = create_tts_provider(settings.TTS_PROVIDER, settings.TTS_API_KEY)
    app.state.tts_provider = tts_provider

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

    yield

    # Shutdown
    update_task.cancel()
    reaper_task.cancel()
    logger.info("Shutting down RCFlow server")
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
    app.include_router(input_text_router)
    app.include_router(input_audio_router)
    app.include_router(output_text_router)
    app.include_router(output_audio_router)

    return app


app = create_app()
