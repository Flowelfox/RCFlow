import asyncio
import hmac
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import APIKeyHeader

from src.api.deps import verify_http_api_key
from src.config import Settings
from src.core.llm import LLMClient
from src.core.prompt_router import PromptRouter
from src.core.session import SessionManager
from src.main import create_app
from src.tools.registry import ToolRegistry


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        RCFLOW_HOST="127.0.0.1",
        RCFLOW_PORT=8765,
        RCFLOW_API_KEY="test-api-key",
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/rcflow_test",
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="test-anthropic-key",
        ANTHROPIC_MODEL="claude-sonnet-4-20250514",
        STT_PROVIDER="wispr_flow",
        STT_API_KEY="test-stt-key",
        TTS_PROVIDER="none",
        TOOLS_DIR=Path(__file__).parent.parent / "tools",
    )


@pytest.fixture
def tool_registry(test_settings: Settings) -> ToolRegistry:
    registry = ToolRegistry()
    registry.load_from_directory(test_settings.TOOLS_DIR)
    return registry


@pytest.fixture
def session_manager() -> SessionManager:
    return SessionManager("test-backend")


@pytest.fixture
def test_app(test_settings: Settings, tool_registry: ToolRegistry, session_manager: SessionManager) -> FastAPI:
    app = create_app()
    app.state.tool_registry = tool_registry
    app.state.session_manager = session_manager
    app.state.db_session_factory = None
    app.state.prompt_router = PromptRouter(
        LLMClient(test_settings, tool_registry),
        session_manager,
        tool_registry,
        db_session_factory=None,
    )

    # Override the HTTP API key dependency to use test settings
    _header = APIKeyHeader(name="X-API-Key")

    async def _test_verify_http_api_key(api_key: str = Depends(_header)) -> str:
        if not hmac.compare_digest(api_key, test_settings.RCFLOW_API_KEY):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        return api_key

    app.dependency_overrides[verify_http_api_key] = _test_verify_http_api_key

    return app
