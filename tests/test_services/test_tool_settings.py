from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from src.services.tool_settings import ToolSettingsManager, _mask_secret


@pytest.fixture
def manager(tmp_path: Path) -> ToolSettingsManager:
    return ToolSettingsManager(base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Schema filtering by managed status
# ---------------------------------------------------------------------------


class TestSchemaFiltering:
    def test_returns_all_fields(self, manager: ToolSettingsManager):
        """All schema fields are returned for an installed (managed) tool."""
        result = manager.get_settings_with_schema("claude_code")
        keys = {f["key"] for f in result["fields"]}
        assert "permissions.allow" in keys
        assert "model" in keys
        assert "default_permission_mode" in keys
        assert "max_turns" in keys
        assert "timeout" in keys

    def test_codex_returns_all_fields(self, manager: ToolSettingsManager):
        result = manager.get_settings_with_schema("codex")
        keys = {f["key"] for f in result["fields"]}
        assert "model" in keys
        assert "approval_mode" in keys
        assert "timeout" in keys


# ---------------------------------------------------------------------------
# Update validation
# ---------------------------------------------------------------------------


class TestUpdateValidation:
    def test_update_known_key(self, manager: ToolSettingsManager):
        result = manager.update_settings("claude_code", {"model": "claude-sonnet-4-5-20250514"})
        model_field = next(f for f in result["fields"] if f["key"] == "model")
        assert model_field["value"] == "claude-sonnet-4-5-20250514"

    def test_update_unknown_key_rejected(self, manager: ToolSettingsManager):
        with pytest.raises(ValueError, match="Unknown settings keys"):
            manager.update_settings("claude_code", {"nonexistent_key": "value"})


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------


class TestSettingsPersistence:
    def test_round_trip(self, manager: ToolSettingsManager):
        """Settings should survive write → read cycle."""
        manager.update_settings("claude_code", {"max_turns": "100"})
        settings = manager.get_settings("claude_code")
        assert settings["max_turns"] == "100"

    def test_unknown_tool_raises(self, manager: ToolSettingsManager):
        with pytest.raises(ValueError, match="Unknown tool"):
            manager.get_settings_with_schema("nonexistent")


# ---------------------------------------------------------------------------
# Provider settings — schema fields
# ---------------------------------------------------------------------------


class TestProviderSettings:
    def test_provider_fields_present_when_managed(self, manager: ToolSettingsManager):
        result = manager.get_settings_with_schema("claude_code")
        keys = {f["key"] for f in result["fields"]}
        assert "provider" in keys
        assert "anthropic_api_key" in keys
        assert "aws_region" in keys
        assert "aws_access_key_id" in keys
        assert "aws_secret_access_key" in keys

    def test_visible_when_present_on_conditional_fields(self, manager: ToolSettingsManager):
        result = manager.get_settings_with_schema("claude_code")
        fields_by_key = {f["key"]: f for f in result["fields"]}

        assert "visible_when" in fields_by_key["anthropic_api_key"]
        assert fields_by_key["anthropic_api_key"]["visible_when"] == {
            "key": "provider",
            "value": "anthropic",
        }

        assert "visible_when" in fields_by_key["aws_region"]
        assert fields_by_key["aws_region"]["visible_when"] == {
            "key": "provider",
            "value": "bedrock",
        }

    def test_provider_field_has_no_visible_when(self, manager: ToolSettingsManager):
        result = manager.get_settings_with_schema("claude_code")
        provider = next(f for f in result["fields"] if f["key"] == "provider")
        assert "visible_when" not in provider

    @pytest.mark.parametrize("tool_name", ["claude_code", "codex", "opencode"])
    def test_provider_options_no_global_choice(self, manager: ToolSettingsManager, tool_name: str):
        """The empty-value 'Global' option was removed — coding agents need an explicit pick."""
        result = manager.get_settings_with_schema(tool_name)
        provider = next(f for f in result["fields"] if f["key"] == "provider")
        values = {opt["value"] for opt in provider["options"]}
        assert "" not in values, f"Provider options for {tool_name} still include the empty 'Global' value: {values}"

    def test_update_provider_to_invalid_value_rejected(self, manager: ToolSettingsManager):
        """PATCH validates select fields against their options."""
        with pytest.raises(ValueError, match="Invalid value for provider"):
            manager.update_settings("claude_code", {"provider": "not_a_provider"})

    def test_update_provider_empty_string_allowed_for_back_compat(self, manager: ToolSettingsManager):
        """Empty string remains the unconfigured sentinel (legacy settings.json files)."""
        # Should not raise — empty is the unset state, not a regular option.
        manager.update_settings("claude_code", {"provider": ""})


# ---------------------------------------------------------------------------
# Provider env sync
# ---------------------------------------------------------------------------


class TestProviderEnvSync:
    def test_anthropic_sets_env_api_key(self, manager: ToolSettingsManager):
        manager.update_settings(
            "claude_code",
            {"provider": "anthropic", "anthropic_api_key": "sk-ant-test1234"},
        )
        settings = manager.get_settings("claude_code")
        assert settings["env"] == {"ANTHROPIC_API_KEY": "sk-ant-test1234"}

    def test_bedrock_sets_env_vars(self, manager: ToolSettingsManager):
        manager.update_settings(
            "claude_code",
            {
                "provider": "bedrock",
                "aws_region": "us-west-2",
                "aws_access_key_id": "AKID1234",
                "aws_secret_access_key": "SECRET5678",
            },
        )
        settings = manager.get_settings("claude_code")
        assert settings["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert settings["env"]["AWS_REGION"] == "us-west-2"
        assert settings["env"]["AWS_ACCESS_KEY_ID"] == "AKID1234"
        assert settings["env"]["AWS_SECRET_ACCESS_KEY"] == "SECRET5678"

    def test_global_removes_env_section(self, manager: ToolSettingsManager):
        # First set a provider with env.
        manager.update_settings(
            "claude_code",
            {"provider": "anthropic", "anthropic_api_key": "sk-ant-test1234"},
        )
        assert "env" in manager.get_settings("claude_code")

        # Switch back to global.
        manager.update_settings("claude_code", {"provider": ""})
        settings = manager.get_settings("claude_code")
        assert "env" not in settings

    def test_bedrock_without_optional_creds(self, manager: ToolSettingsManager):
        """Bedrock with only region should still set CLAUDE_CODE_USE_BEDROCK."""
        manager.update_settings(
            "claude_code",
            {"provider": "bedrock", "aws_region": "eu-west-1"},
        )
        settings = manager.get_settings("claude_code")
        assert settings["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert settings["env"]["AWS_REGION"] == "eu-west-1"
        assert "AWS_ACCESS_KEY_ID" not in settings["env"]


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Undercover setting
# ---------------------------------------------------------------------------


class TestUndercoverSetting:
    def test_undercover_in_schema(self, manager: ToolSettingsManager):
        result = manager.get_settings_with_schema("claude_code")
        keys = {f["key"] for f in result["fields"]}
        assert "undercover" in keys

    def test_undercover_default_is_false(self, manager: ToolSettingsManager):
        result = manager.get_settings_with_schema("claude_code")
        field = next(f for f in result["fields"] if f["key"] == "undercover")
        assert field["value"] is False
        assert field["default"] is False
        assert field["type"] == "boolean"

    def test_undercover_marked_coming_soon(self, manager: ToolSettingsManager):
        result = manager.get_settings_with_schema("claude_code")
        field = next(f for f in result["fields"] if f["key"] == "undercover")
        assert field.get("coming_soon") is True

    def test_undercover_update_rejected(self, manager: ToolSettingsManager):
        with pytest.raises(ValueError, match="coming-soon"):
            manager.update_settings("claude_code", {"undercover": True})


# ---------------------------------------------------------------------------
# Secret masking
# ---------------------------------------------------------------------------


class TestSecretMasking:
    def test_mask_secret_long_value(self):
        assert _mask_secret("sk-ant-abcdefgh1234") == "***************1234"

    def test_mask_secret_short_value(self):
        assert _mask_secret("abc") == "***"

    def test_mask_secret_empty(self):
        assert _mask_secret("") == ""

    def test_secret_masked_in_schema_output(self, manager: ToolSettingsManager):
        manager.update_settings(
            "claude_code",
            {"provider": "anthropic", "anthropic_api_key": "sk-ant-real-key-9999"},
        )
        result = manager.get_settings_with_schema("claude_code")
        api_key_field = next(f for f in result["fields"] if f["key"] == "anthropic_api_key")
        assert api_key_field["value"] == "****************9999"
        assert "sk-ant" not in api_key_field["value"]

    def test_masked_value_not_overwritten_on_update(self, manager: ToolSettingsManager):
        """Sending the masked value back should preserve the original secret."""
        manager.update_settings(
            "claude_code",
            {"provider": "anthropic", "anthropic_api_key": "sk-ant-real-key-9999"},
        )
        # Get the masked value.
        result = manager.get_settings_with_schema("claude_code")
        masked = next(f for f in result["fields"] if f["key"] == "anthropic_api_key")["value"]

        # Send the masked value back as an update.
        manager.update_settings("claude_code", {"anthropic_api_key": masked})

        # The original value should be preserved.
        raw = manager.get_settings("claude_code")
        assert raw["anthropic_api_key"] == "sk-ant-real-key-9999"

    def test_new_secret_value_overwrites(self, manager: ToolSettingsManager):
        """Sending a genuinely new value should overwrite."""
        manager.update_settings(
            "claude_code",
            {"provider": "anthropic", "anthropic_api_key": "sk-ant-old-key-1111"},
        )
        manager.update_settings(
            "claude_code",
            {"anthropic_api_key": "sk-ant-new-key-2222"},
        )
        raw = manager.get_settings("claude_code")
        assert raw["anthropic_api_key"] == "sk-ant-new-key-2222"
