"""Tests for LLMClient.supports_vision model-capability detection.

Verifies that each model in the configured catalog is correctly classified
as supporting or not supporting image/vision attachments, per the
2026-03-16 model catalog.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.llm import LLMClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_client(provider: str, model: str) -> LLMClient:
    """Construct an LLMClient with mocked SDK clients and the given provider/model."""
    settings = MagicMock()
    settings.LLM_PROVIDER = provider
    settings.ANTHROPIC_MODEL = model
    settings.OPENAI_MODEL = model
    settings.AWS_REGION = "us-east-1"
    settings.AWS_ACCESS_KEY_ID = ""
    settings.AWS_SECRET_ACCESS_KEY = ""
    settings.ANTHROPIC_API_KEY = "test"
    settings.OPENAI_API_KEY = "test"
    settings.TITLE_MODEL = ""
    settings.TASK_MODEL = ""
    settings.GLOBAL_PROMPT = ""
    settings.projects_dirs = []

    tool_registry = MagicMock()

    with (
        patch("src.core.llm.anthropic.AsyncAnthropic"),
        patch("src.core.llm.anthropic.AsyncAnthropicBedrock"),
        patch("src.core.llm.openai.AsyncOpenAI"),
        patch("src.core.llm.PromptBuilder"),
    ):
        return LLMClient(settings, tool_registry)


# ---------------------------------------------------------------------------
# Anthropic / Bedrock — vision support
# ---------------------------------------------------------------------------


class TestAnthropicVisionSupport:
    """All Claude 3.x and Claude 4.x models support vision."""

    @pytest.mark.parametrize(
        "model",
        [
            # Anthropic direct
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            # Older 3.x family
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
            "claude-3-5-sonnet-20241022",
        ],
    )
    def test_anthropic_vision_true(self, model: str) -> None:
        client = _make_llm_client("anthropic", model)
        assert client.supports_vision is True

    @pytest.mark.parametrize(
        "model",
        [
            # Hypothetical legacy text-only Claude 2 — should not be flagged as vision
            "claude-2.1",
            "claude-instant-1.2",
        ],
    )
    def test_anthropic_legacy_no_vision(self, model: str) -> None:
        client = _make_llm_client("anthropic", model)
        assert client.supports_vision is False


class TestBedrockVisionSupport:
    """Bedrock Claude 4.x models support vision."""

    @pytest.mark.parametrize(
        "model",
        [
            "us.anthropic.claude-opus-4-5-20251101-v1:0",
            "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            # 3.x family on Bedrock
            "us.anthropic.claude-3-sonnet-20240229-v1:0",
            "us.anthropic.claude-3-haiku-20240307-v1:0",
        ],
    )
    def test_bedrock_vision_true(self, model: str) -> None:
        client = _make_llm_client("bedrock", model)
        assert client.supports_vision is True


# ---------------------------------------------------------------------------
# OpenAI — vision support
# ---------------------------------------------------------------------------


class TestOpenAIVisionSupport:
    """GPT-4.x, GPT-4o, and GPT-5.x all support vision; o-series does not."""

    @pytest.mark.parametrize(
        "model",
        [
            # GPT-5 family (multimodal per 2026-03-16 catalog)
            "gpt-5.4",
            "gpt-5-mini",
            "gpt-5",
            # GPT-4.1 family
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            # GPT-4o family
            "gpt-4o",
            "gpt-4o-mini",
            # GPT-4 turbo / vision
            "gpt-4-turbo",
            "gpt-4-turbo-2024-04-09",
            "gpt-4-vision-preview",
        ],
    )
    def test_openai_vision_true(self, model: str) -> None:
        client = _make_llm_client("openai", model)
        assert client.supports_vision is True

    @pytest.mark.parametrize(
        "model",
        [
            # o-series: reasoning-only, no image input
            "o3",
            "o3-mini",
            "o4-mini",
            "o1",
            "o1-mini",
            "o1-preview",
        ],
    )
    def test_openai_reasoning_no_vision(self, model: str) -> None:
        client = _make_llm_client("openai", model)
        assert client.supports_vision is False


# ---------------------------------------------------------------------------
# attachment_capabilities dict
# ---------------------------------------------------------------------------


class TestAttachmentCapabilities:
    """attachment_capabilities should reflect supports_vision and always allow text."""

    def test_vision_model_has_images_true(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")
        caps = client.attachment_capabilities
        assert caps["images"] is True
        assert caps["text_files"] is True

    def test_reasoning_model_has_images_false(self) -> None:
        client = _make_llm_client("openai", "o3")
        caps = client.attachment_capabilities
        assert caps["images"] is False
        assert caps["text_files"] is True

    def test_gpt5_has_images_true(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")
        caps = client.attachment_capabilities
        assert caps["images"] is True
        assert caps["text_files"] is True
