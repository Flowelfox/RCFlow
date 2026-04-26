"""Tests for :mod:`src.services.model_catalog`."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.services.model_catalog import (
    _BEDROCK_REGION_PREFIXES,
    _OPENAI_DENY_RE,
    _OPENAI_KEEP_RE,
    MODEL_CACHE_FILE_NAME,
    BedrockFetcher,
    CatalogResult,
    Credentials,
    ModelCatalog,
    ModelEntry,
    OpenAIFetcher,
    _fingerprint,
    _prettify_openai_label,
)


class _StubFetcher:
    """In-memory fetcher used by the catalog tests.

    Records every call and returns either a configured payload or raises
    a configured exception so tests can drive both success and failure
    paths through :class:`ModelCatalog.get`.
    """

    name = "anthropic"

    def __init__(self, options: list[ModelEntry] | None = None, error: Exception | None = None) -> None:
        self.options = options or []
        self.error = error
        self.calls: list[Credentials] = []

    async def fetch(self, creds: Credentials) -> list[ModelEntry]:
        self.calls.append(creds)
        if self.error is not None:
            raise self.error
        return list(self.options)


@pytest.fixture
def stub_fetcher() -> _StubFetcher:
    return _StubFetcher(
        options=[
            ModelEntry(value="claude-opus-test", label="Claude Opus Test"),
            ModelEntry(value="claude-sonnet-test", label="Claude Sonnet Test"),
        ]
    )


def _make_catalog(tmp_path, fetcher: _StubFetcher, ttl: int = 600) -> ModelCatalog:
    return ModelCatalog(tmp_path, ttl_seconds=ttl, fetchers={"anthropic": fetcher})


@pytest.mark.asyncio
async def test_first_call_fetches_live(tmp_path, stub_fetcher: _StubFetcher) -> None:
    catalog = _make_catalog(tmp_path, stub_fetcher)
    result = await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    assert isinstance(result, CatalogResult)
    assert result.source == "live"
    assert [opt.value for opt in result.options] == ["claude-opus-test", "claude-sonnet-test"]
    assert result.error is None
    assert len(stub_fetcher.calls) == 1


@pytest.mark.asyncio
async def test_second_call_returns_cached(tmp_path, stub_fetcher: _StubFetcher) -> None:
    catalog = _make_catalog(tmp_path, stub_fetcher)
    await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    second = await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    assert second.source == "cached"
    assert len(stub_fetcher.calls) == 1


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(tmp_path, stub_fetcher: _StubFetcher) -> None:
    catalog = _make_catalog(tmp_path, stub_fetcher)
    await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    await catalog.get("anthropic", "global", Credentials(api_key="key-1"), force_refresh=True)
    assert len(stub_fetcher.calls) == 2


@pytest.mark.asyncio
async def test_ttl_expiry(tmp_path, stub_fetcher: _StubFetcher, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _make_catalog(tmp_path, stub_fetcher, ttl=60)
    clock = {"now": 100.0}

    def fake_time() -> float:
        return clock["now"]

    monkeypatch.setattr("src.services.model_catalog.time.time", fake_time)
    await catalog.get("anthropic", "global", Credentials(api_key="key-1"))

    clock["now"] = 200.0  # advance past TTL (delta = 100, ttl = 60)
    second = await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    assert second.source == "live"  # cache expired
    assert len(stub_fetcher.calls) == 2


@pytest.mark.asyncio
async def test_fetch_failure_returns_fallback(tmp_path) -> None:
    failing = _StubFetcher(error=RuntimeError("upstream 500"))
    catalog = ModelCatalog(tmp_path, fetchers={"anthropic": failing})
    result = await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    assert result.source == "fallback"
    assert result.error == "upstream 500"
    # Fallback comes from PROVIDER_MODELS, which is non-empty for "anthropic".
    assert len(result.options) > 0


@pytest.mark.asyncio
async def test_invalidate_provider(tmp_path, stub_fetcher: _StubFetcher) -> None:
    catalog = _make_catalog(tmp_path, stub_fetcher)
    await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    removed = catalog.invalidate(provider="anthropic")
    assert removed == 1
    second = await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    assert second.source == "live"


@pytest.mark.asyncio
async def test_invalidate_scope_only(tmp_path, stub_fetcher: _StubFetcher) -> None:
    catalog = _make_catalog(tmp_path, stub_fetcher)
    await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    await catalog.get("anthropic", "claude_code", Credentials(api_key="key-1"))
    assert catalog.invalidate(scope="claude_code") == 1
    # Global entry is still cached.
    third = await catalog.get("anthropic", "global", Credentials(api_key="key-1"))
    assert third.source == "cached"


@pytest.mark.asyncio
async def test_disk_persistence_round_trip(tmp_path, stub_fetcher: _StubFetcher) -> None:
    catalog = _make_catalog(tmp_path, stub_fetcher)
    await catalog.get("anthropic", "global", Credentials(api_key="key-1"))

    # Sanity: file written.
    cache_file = tmp_path / MODEL_CACHE_FILE_NAME
    assert cache_file.is_file()
    payload = json.loads(cache_file.read_text())
    assert any("claude-opus-test" in json.dumps(entry) for entry in payload.values())

    # New catalog instance with the same dir reads cache from disk.
    second_fetcher = _StubFetcher()  # would return [] if hit
    second = ModelCatalog(tmp_path, fetchers={"anthropic": second_fetcher})
    result = await second.get("anthropic", "global", Credentials(api_key="key-1"))
    assert result.source == "cached"
    assert [opt.value for opt in result.options] == ["claude-opus-test", "claude-sonnet-test"]
    assert second_fetcher.calls == []


@pytest.mark.asyncio
async def test_unknown_provider_raises(tmp_path) -> None:
    catalog = ModelCatalog(tmp_path, fetchers={})
    with pytest.raises(ValueError, match="Unknown provider"):
        await catalog.get("not-a-provider", "global", Credentials())


def test_fingerprint_distinct_for_distinct_keys() -> None:
    a = _fingerprint("sk-ant-key-aaa")
    b = _fingerprint("sk-ant-key-bbb")
    assert a != b
    assert _fingerprint(None) == "anon"
    # Stable across calls.
    assert _fingerprint("sk-ant-key-aaa") == a


def test_openai_filter_keeps_chat_only() -> None:
    keep = ["gpt-4o", "gpt-4.1-mini", "gpt-5", "o3", "o4-mini", "chatgpt-4o-latest"]
    drop = [
        "text-embedding-3-small",
        "tts-1",
        "whisper-1",
        "dall-e-3",
        "babbage-002",
        "davinci-002",
        "omni-moderation-latest",
        "gpt-4o-audio-preview",
        "gpt-4o-realtime-preview",
        "gpt-4o-search-preview",
        "gpt-image-1",
    ]
    for model_id in keep:
        assert _OPENAI_KEEP_RE.match(model_id), model_id
        assert not _OPENAI_DENY_RE.search(model_id), model_id
    for model_id in drop:
        kept = bool(_OPENAI_KEEP_RE.match(model_id) and not _OPENAI_DENY_RE.search(model_id))
        assert not kept, f"{model_id} should be dropped"


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("gpt-4o", "GPT-4o"),
        ("gpt-4o-mini", "GPT-4o Mini"),
        ("gpt-4.1", "GPT-4.1"),
        ("gpt-4.1-mini", "GPT-4.1 Mini"),
        ("gpt-5", "GPT-5"),
        ("gpt-5-mini", "GPT-5 Mini"),
        ("gpt-5.5", "GPT-5.5"),
        ("o3", "o3"),
        ("o4-mini", "o4 Mini"),
        ("chatgpt-4o-latest", "ChatGPT-4o (latest)"),
        ("gpt-4o-2024-08-06", "GPT-4o (2024-08-06)"),
        ("o1-2024-12-17", "o1 (2024-12-17)"),
        ("gpt-4o-20240806", "GPT-4o (2024-08-06)"),
        ("gpt-4.1-mini-preview", "GPT-4.1 Mini (preview)"),
    ],
)
def test_prettify_openai_label(model_id: str, expected: str) -> None:
    assert _prettify_openai_label(model_id) == expected


def test_prettify_openai_label_unknown_shape_passthrough() -> None:
    # Anything that doesn't match the GPT/O families is left untouched so we
    # never silently rewrite a label we don't understand.
    assert _prettify_openai_label("davinci-003") == "davinci-003"


def test_bedrock_region_prefix_map_has_known_regions() -> None:
    assert _BEDROCK_REGION_PREFIXES["us-east-1"] == "us."
    assert _BEDROCK_REGION_PREFIXES["eu-west-1"] == "eu."
    assert _BEDROCK_REGION_PREFIXES["ap-southeast-2"] == "apac."
    # Unknown regions fall back to no prefix when looked up via .get(...).
    assert _BEDROCK_REGION_PREFIXES.get("zz-fake-1") is None


@pytest.mark.asyncio
async def test_eviction_caps_memory(tmp_path) -> None:
    fetcher = _StubFetcher(options=[ModelEntry(value="a", label="A")])
    catalog = ModelCatalog(tmp_path, fetchers={"anthropic": fetcher}, max_entries=2)
    await catalog.get("anthropic", "global", Credentials(api_key="k1"))
    await catalog.get("anthropic", "claude_code", Credentials(api_key="k2"))
    await catalog.get("anthropic", "codex", Credentials(api_key="k3"))
    assert len(list(catalog._entries())) == 2


@pytest.mark.asyncio
async def test_concurrent_fetches_dedup(tmp_path) -> None:
    """Two callers asking for the same key should share one fetch."""
    call_count = 0

    class _SlowFetcher:
        name = "anthropic"

        async def fetch(self, creds: Credentials) -> list[ModelEntry]:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return [ModelEntry(value="x", label="x")]

    catalog = ModelCatalog(tmp_path, fetchers={"anthropic": _SlowFetcher()})
    creds = Credentials(api_key="k1")
    a, b = await asyncio.gather(
        catalog.get("anthropic", "global", creds),
        catalog.get("anthropic", "global", creds),
    )
    # Both calls succeed; only one upstream fetch should fire.
    assert a.options == b.options
    assert call_count == 1


@pytest.mark.asyncio
async def test_openai_fetcher_requires_key() -> None:
    fetcher = OpenAIFetcher()
    with pytest.raises(ValueError, match="OpenAI API key"):
        await fetcher.fetch(Credentials(api_key=None))


@pytest.mark.asyncio
async def test_bedrock_fetcher_uses_region_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """BedrockFetcher prepends the regional inference-profile prefix."""

    class _StubBedrockClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def list_foundation_models(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "modelSummaries": [
                    {
                        "modelId": "anthropic.claude-opus-4-5-20251101-v1:0",
                        "modelName": "Claude Opus 4.5",
                        "providerName": "Anthropic",
                    },
                    {
                        "modelId": "amazon.titan-text-lite",
                        "modelName": "Titan Lite",
                        "providerName": "Amazon",
                    },
                ]
            }

    class _StubSession:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def client(self, name: str, region_name: str) -> _StubBedrockClient:
            assert name == "bedrock"
            return _StubBedrockClient()

    fake_module = type("aioboto3", (), {"Session": _StubSession})
    monkeypatch.setitem(__import__("sys").modules, "aioboto3", fake_module)

    fetcher = BedrockFetcher()
    entries = await fetcher.fetch(Credentials(aws_region="us-east-1"))
    assert [e.value for e in entries] == ["us.anthropic.claude-opus-4-5-20251101-v1:0"]


@pytest.mark.asyncio
async def test_bedrock_fetcher_unknown_region_no_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubBedrockClient:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def list_foundation_models(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "modelSummaries": [
                    {
                        "modelId": "anthropic.claude-haiku-4-5-20251001-v1:0",
                        "modelName": "Claude Haiku 4.5",
                        "providerName": "Anthropic",
                    },
                ]
            }

    class _StubSession:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def client(self, name: str, region_name: str) -> _StubBedrockClient:
            return _StubBedrockClient()

    fake_module = type("aioboto3", (), {"Session": _StubSession})
    monkeypatch.setitem(__import__("sys").modules, "aioboto3", fake_module)

    fetcher = BedrockFetcher()
    entries = await fetcher.fetch(Credentials(aws_region="zz-fake-1"))
    assert entries[0].value == "anthropic.claude-haiku-4-5-20251001-v1:0"  # no prefix
