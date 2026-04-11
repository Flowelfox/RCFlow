"""Tests for caveman mode in LLMClient._system_prompt."""

from unittest.mock import MagicMock, patch

from src.core.llm import _CAVEMAN_PROMPTS, LLMClient, _caveman_instruction


def _make_llm_client(
    caveman_mode: bool = False,
    caveman_level: str = "full",
    global_prompt: str = "",
) -> LLMClient:
    settings = MagicMock()
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_MODEL = "claude-sonnet-4-6"
    settings.OPENAI_MODEL = ""
    settings.AWS_REGION = "us-east-1"
    settings.AWS_ACCESS_KEY_ID = ""
    settings.AWS_SECRET_ACCESS_KEY = ""
    settings.ANTHROPIC_API_KEY = "test"
    settings.OPENAI_API_KEY = ""
    settings.TITLE_MODEL = ""
    settings.TASK_MODEL = ""
    settings.GLOBAL_PROMPT = global_prompt
    settings.CAVEMAN_MODE = caveman_mode
    settings.CAVEMAN_LEVEL = caveman_level
    settings.projects_dirs = []

    tool_registry = MagicMock()
    tool_registry.to_anthropic_tools.return_value = []

    with (
        patch("src.core.llm.anthropic.AsyncAnthropic"),
        patch("src.core.llm.anthropic.AsyncAnthropicBedrock"),
        patch("src.core.llm.openai.AsyncOpenAI"),
        patch("src.core.llm.PromptBuilder") as mock_builder,
    ):
        mock_builder.return_value.build.return_value = "BASE_PROMPT"
        return LLMClient(settings, tool_registry)


class TestCavemanInstruction:
    def test_full_level(self) -> None:
        text = _caveman_instruction("full")
        assert "smart caveman" in text
        assert "Drop: articles" in text

    def test_lite_level(self) -> None:
        text = _caveman_instruction("lite")
        assert "Lite mode" in text

    def test_ultra_level(self) -> None:
        text = _caveman_instruction("ultra")
        assert "Ultra mode" in text

    def test_unknown_level_falls_back_to_full(self) -> None:
        text = _caveman_instruction("nonexistent")
        assert text == _caveman_instruction("full")

    def test_all_levels_include_auto_clarity(self) -> None:
        for level in _CAVEMAN_PROMPTS:
            text = _caveman_instruction(level)
            assert "Auto-Clarity" in text
            assert "security warnings" in text


class TestSystemPromptCaveman:
    def test_disabled_no_caveman_text(self) -> None:
        client = _make_llm_client(caveman_mode=False)
        assert "caveman" not in client._system_prompt.lower()

    def test_enabled_appends_caveman_after_base(self) -> None:
        client = _make_llm_client(caveman_mode=True, caveman_level="full")
        prompt = client._system_prompt
        assert "BASE_PROMPT" in prompt
        assert "smart caveman" in prompt
        base_pos = prompt.index("BASE_PROMPT")
        caveman_pos = prompt.index("smart caveman")
        assert base_pos < caveman_pos

    def test_caveman_before_global_prompt(self) -> None:
        client = _make_llm_client(
            caveman_mode=True,
            caveman_level="full",
            global_prompt="CUSTOM GLOBAL",
        )
        prompt = client._system_prompt
        caveman_pos = prompt.index("smart caveman")
        global_pos = prompt.index("CUSTOM GLOBAL")
        assert caveman_pos < global_pos

    def test_each_level_produces_different_text(self) -> None:
        prompts = {}
        for level in ("lite", "full", "ultra"):
            client = _make_llm_client(caveman_mode=True, caveman_level=level)
            prompts[level] = client._system_prompt
        assert prompts["lite"] != prompts["full"]
        assert prompts["full"] != prompts["ultra"]

    def test_disabled_with_global_prompt(self) -> None:
        client = _make_llm_client(caveman_mode=False, global_prompt="MY CUSTOM")
        prompt = client._system_prompt
        assert "MY CUSTOM" in prompt
        assert "caveman" not in prompt.lower()
