"""Dynamic LLM model catalog with on-disk + in-memory TTL cache.

Replaces the static :data:`src.config.PROVIDER_MODELS` lookup as the
runtime source for ``model_select`` schema dropdowns. Each provider has a
fetcher that calls the upstream ``models.list`` endpoint; results are
cached for :data:`MODEL_CACHE_TTL_SECONDS` keyed by ``(provider, scope,
api_key_fingerprint)``. Fetch failures fall back to the seed list shipped
in :data:`src.config.PROVIDER_MODELS` so the UI stays usable offline or
without an API key.

OpenAI's ``models.list`` returns embeddings, audio, image, and deprecated
IDs alongside chat models, so the OpenAI fetcher applies a conservative
allow/deny regex pair to keep only chat-capable IDs (see
:data:`_OPENAI_KEEP_RE` and :data:`_OPENAI_DENY_RE`).

Bedrock listing uses ``aioboto3`` (``bedrock`` client →
``list_foundation_models``) and prepends the regional inference-profile
prefix from :data:`_BEDROCK_REGION_PREFIXES` so the value matches the
``us.anthropic.…`` / ``eu.anthropic.…`` IDs accepted by the Anthropic
SDK's Bedrock client.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx

from src.config import PROVIDER_MODELS

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)


MODEL_CACHE_TTL_SECONDS = 600
MODEL_CACHE_FILE_NAME = "model_cache.json"
MODEL_CACHE_MAX_ENTRIES = 64

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# OpenAI: keep only chat-capable model ids. Allow gpt-N-…, oN-…, chatgpt-…
# explicitly; reject anything that smells like embeddings/audio/image/etc.
_OPENAI_KEEP_RE = re.compile(r"^(gpt-[0-9]|o[0-9]|chatgpt-)")
_OPENAI_DENY_RE = re.compile(
    r"(audio|tts|whisper|embedding|moderation|dall-e|davinci|babbage|search|realtime|transcribe|image)",
    re.IGNORECASE,
)

# OpenAI doesn't return a display name from ``models.list`` — every entry
# is just a raw id like ``gpt-4o-mini-2024-07-18``. Build a human label
# from the id so the dropdown matches the polish of Anthropic's
# ``display_name`` and OpenRouter's ``name`` fields.
_OPENAI_GPT_RE = re.compile(r"^(chat)?gpt-(\d+(?:\.\d+)?o?)(?:-(.+))?$", re.IGNORECASE)
_OPENAI_O_RE = re.compile(r"^(o\d+)(?:-(.+))?$", re.IGNORECASE)
_OPENAI_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_OPENAI_TITLE_TOKENS = {
    "mini": "Mini",
    "nano": "Nano",
    "turbo": "Turbo",
    "instruct": "Instruct",
    "vision": "Vision",
    "omni": "Omni",
}
_OPENAI_PAREN_TOKENS = {"latest", "preview", "beta"}


def _prettify_openai_label(model_id: str) -> str:
    """Return a readable label for an OpenAI model id.

    Examples:
        ``gpt-4o-mini`` → ``"GPT-4o Mini"``
        ``gpt-5.5`` → ``"GPT-5.5"``
        ``chatgpt-4o-latest`` → ``"ChatGPT-4o (latest)"``
        ``o4-mini`` → ``"o4 Mini"``
        ``gpt-4o-2024-08-06`` → ``"GPT-4o (2024-08-06)"``
    """
    m = _OPENAI_GPT_RE.match(model_id)
    if m:
        prefix_name = "ChatGPT" if m.group(1) else "GPT"
        prefix = f"{prefix_name}-{m.group(2)}"
        suffix = m.group(3)
    else:
        m_o = _OPENAI_O_RE.match(model_id)
        if not m_o:
            return model_id
        prefix = m_o.group(1).lower()
        suffix = m_o.group(2)

    if not suffix:
        return prefix

    tokens = suffix.split("-")
    pretty: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # ISO date split across three dash-separated tokens.
        if (
            i + 2 < len(tokens)
            and re.fullmatch(r"\d{4}", tok)
            and re.fullmatch(r"\d{2}", tokens[i + 1])
            and re.fullmatch(r"\d{2}", tokens[i + 2])
        ):
            pretty.append(f"({tok}-{tokens[i + 1]}-{tokens[i + 2]})")
            i += 3
            continue
        # YYYYMMDD as a single compact token.
        d = _OPENAI_COMPACT_DATE_RE.match(tok)
        if d:
            pretty.append(f"({d.group(1)}-{d.group(2)}-{d.group(3)})")
            i += 1
            continue
        lower = tok.lower()
        if lower in _OPENAI_PAREN_TOKENS:
            pretty.append(f"({lower})")
        elif lower in _OPENAI_TITLE_TOKENS:
            pretty.append(_OPENAI_TITLE_TOKENS[lower])
        else:
            pretty.append(tok[:1].upper() + tok[1:] if tok else tok)
        i += 1

    return f"{prefix} {' '.join(pretty)}".strip()


# Bedrock returns bare model IDs (e.g. ``anthropic.claude-opus-4-5-20251101-v1:0``)
# but the Anthropic SDK and most callers want the regional inference-profile
# prefix (``us.anthropic.…``).  This map covers the regions Anthropic publishes
# inference profiles for; any other region falls back to the bare ID.
_BEDROCK_REGION_PREFIXES: dict[str, str] = {
    "us-east-1": "us.",
    "us-east-2": "us.",
    "us-west-2": "us.",
    "eu-central-1": "eu.",
    "eu-west-1": "eu.",
    "eu-west-3": "eu.",
    "ap-northeast-1": "apac.",
    "ap-northeast-2": "apac.",
    "ap-south-1": "apac.",
    "ap-southeast-1": "apac.",
    "ap-southeast-2": "apac.",
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelEntry:
    """One entry in a model dropdown."""

    value: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label}


@dataclass(frozen=True)
class Credentials:
    """Credentials passed to a provider fetcher.

    Each fetcher only consumes the fields it needs; missing fields are
    represented as :class:`None` (or the empty string for AWS region).
    OpenRouter ignores the credentials entirely — its public model list
    is unauthenticated.
    """

    api_key: str | None = None
    aws_region: str | None = None
    aws_access_key: str | None = None
    aws_secret_key: str | None = None


CatalogSource = Literal["live", "cached", "fallback"]


@dataclass
class CatalogResult:
    """Result returned by :meth:`ModelCatalog.get`."""

    options: list[ModelEntry]
    source: CatalogSource
    fetched_at: datetime | None
    error: str | None
    ttl_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "options": [opt.to_dict() for opt in self.options],
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "error": self.error,
            "ttl_seconds": self.ttl_seconds,
        }


# ---------------------------------------------------------------------------
# Fetcher protocol + concrete implementations
# ---------------------------------------------------------------------------


class ProviderFetcher(Protocol):
    """Async fetcher for a single provider."""

    name: str

    async def fetch(self, creds: Credentials) -> list[ModelEntry]: ...


class AnthropicFetcher:
    """Fetch via ``anthropic.AsyncAnthropic.models.list``."""

    name = "anthropic"

    async def fetch(self, creds: Credentials) -> list[ModelEntry]:
        if not creds.api_key:
            raise ValueError("Anthropic API key is required to list models")
        import anthropic  # noqa: PLC0415

        client = anthropic.AsyncAnthropic(api_key=creds.api_key)
        try:
            entries: list[ModelEntry] = []
            async for model in client.models.list(limit=1000):
                value = getattr(model, "id", None)
                if not value:
                    continue
                label = getattr(model, "display_name", None) or value
                entries.append(ModelEntry(value=value, label=label))
            return entries
        finally:
            await client.close()


class OpenAIFetcher:
    """Fetch via ``openai.AsyncOpenAI.models.list`` with chat-only filter."""

    name = "openai"

    async def fetch(self, creds: Credentials) -> list[ModelEntry]:
        if not creds.api_key:
            raise ValueError("OpenAI API key is required to list models")
        import openai  # noqa: PLC0415

        client = openai.AsyncOpenAI(api_key=creds.api_key)
        try:
            response = await client.models.list()
            entries: list[ModelEntry] = []
            for model in response.data:
                model_id = getattr(model, "id", None)
                if not model_id:
                    continue
                if not _OPENAI_KEEP_RE.match(model_id):
                    continue
                if _OPENAI_DENY_RE.search(model_id):
                    continue
                entries.append(ModelEntry(value=model_id, label=_prettify_openai_label(model_id)))
            entries.sort(key=lambda e: e.value, reverse=True)
            return entries
        finally:
            await client.close()


class BedrockFetcher:
    """Fetch via ``aioboto3`` ``bedrock.list_foundation_models``.

    Filters to text-only ``ON_DEMAND`` models from Anthropic (the only
    family RCFlow supports through Bedrock today). Adds the regional
    inference-profile prefix per :data:`_BEDROCK_REGION_PREFIXES`.
    """

    name = "bedrock"

    async def fetch(self, creds: Credentials) -> list[ModelEntry]:
        region = creds.aws_region or "us-east-1"
        prefix = _BEDROCK_REGION_PREFIXES.get(region, "")

        try:
            import aioboto3  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("aioboto3 is required to list Bedrock models") from exc

        session_kwargs: dict[str, Any] = {}
        if creds.aws_access_key and creds.aws_secret_key:
            session_kwargs["aws_access_key_id"] = creds.aws_access_key
            session_kwargs["aws_secret_access_key"] = creds.aws_secret_key
        session = aioboto3.Session(**session_kwargs)

        async with session.client("bedrock", region_name=region) as client:
            response = await client.list_foundation_models(byOutputModality="TEXT", byInferenceType="ON_DEMAND")

        entries: list[ModelEntry] = []
        seen: set[str] = set()
        for summary in response.get("modelSummaries", []):
            if summary.get("providerName") != "Anthropic":
                continue
            model_id = summary.get("modelId")
            if not model_id:
                continue
            value = f"{prefix}{model_id}" if prefix else model_id
            if value in seen:
                continue
            seen.add(value)
            label = summary.get("modelName") or model_id
            entries.append(ModelEntry(value=value, label=label))
        return entries


class OpenRouterFetcher:
    """Fetch the public OpenRouter model catalog (unauthenticated)."""

    name = "openrouter"

    async def fetch(self, creds: Credentials) -> list[ModelEntry]:
        async with httpx.AsyncClient(timeout=_OPENROUTER_TIMEOUT) as client:
            response = await client.get(_OPENROUTER_MODELS_URL)
            response.raise_for_status()
            payload = response.json()
        entries: list[ModelEntry] = []
        for model in payload.get("data", []):
            value = model.get("id")
            if not value:
                continue
            label = model.get("name") or value
            entries.append(ModelEntry(value=value, label=label))
        entries.sort(key=lambda e: e.value)
        return entries


_DEFAULT_FETCHERS: dict[str, ProviderFetcher] = {
    "anthropic": AnthropicFetcher(),
    "openai": OpenAIFetcher(),
    "bedrock": BedrockFetcher(),
    "openrouter": OpenRouterFetcher(),
}


# ---------------------------------------------------------------------------
# Static fallback derived from PROVIDER_MODELS
# ---------------------------------------------------------------------------


def _static_fallback(provider: str) -> list[ModelEntry]:
    """Return the bundled hardcoded list for *provider* (or empty)."""
    seed = PROVIDER_MODELS.get(provider)
    if not seed:
        return []
    return [ModelEntry(value=opt["value"], label=opt["label"]) for opt in seed.get("options", []) if opt.get("value")]


# ---------------------------------------------------------------------------
# Cache primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CacheKey:
    provider: str
    scope: str
    fingerprint: str

    def serialise(self) -> str:
        return f"{self.provider}:{self.scope}:{self.fingerprint}"

    @classmethod
    def parse(cls, raw: str) -> _CacheKey | None:
        parts = raw.split(":")
        if len(parts) != 3:
            return None
        return cls(provider=parts[0], scope=parts[1], fingerprint=parts[2])


@dataclass
class _CacheEntry:
    options: list[ModelEntry]
    fetched_at: float  # unix seconds (monotonic-clock-friendly via time.time)


def _fingerprint(api_key: str | None) -> str:
    """Compute a short fingerprint of *api_key* for cache keying.

    Prevents cached results for one user's key being served when the key
    changes. The raw key is never persisted; only the first 8 hex chars
    of its sha256 hit disk.
    """
    if not api_key:
        return "anon"
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# ModelCatalog
# ---------------------------------------------------------------------------


@dataclass
class _LockMap:
    """Per-key asyncio.Lock map with lazy creation."""

    locks: dict[_CacheKey, asyncio.Lock] = field(default_factory=dict)

    def get(self, key: _CacheKey) -> asyncio.Lock:
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock


class ModelCatalog:
    """In-memory + on-disk cache of provider model lists.

    Each entry is keyed by ``(provider, scope, fingerprint(api_key))`` so
    that swapping API keys produces fresh fetches without leaking the
    previous user's catalog. Concurrent fetches for the same key are
    deduplicated via per-key locks.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        ttl_seconds: int = MODEL_CACHE_TTL_SECONDS,
        fetchers: dict[str, ProviderFetcher] | None = None,
        max_entries: int = MODEL_CACHE_MAX_ENTRIES,
    ) -> None:
        self._data_dir = data_dir
        self._cache_path = data_dir / MODEL_CACHE_FILE_NAME
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._fetchers = fetchers if fetchers is not None else _DEFAULT_FETCHERS
        self._memory: dict[_CacheKey, _CacheEntry] = {}
        self._locks = _LockMap()
        self._persist_lock = asyncio.Lock()
        self._load_disk()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(
        self,
        provider: str,
        scope: str,
        credentials: Credentials,
        *,
        force_refresh: bool = False,
    ) -> CatalogResult:
        """Return the catalog for *(provider, scope)* with TTL semantics.

        On fetch failure, returns a :class:`CatalogResult` with
        ``source='fallback'`` and ``error`` populated.
        """
        if provider not in self._fetchers:
            raise ValueError(f"Unknown provider: {provider!r}")

        key = _CacheKey(provider=provider, scope=scope, fingerprint=_fingerprint(credentials.api_key))

        # TTL hit (skip when force_refresh).
        if not force_refresh:
            entry = self._memory.get(key)
            if entry and self._is_fresh(entry):
                return CatalogResult(
                    options=list(entry.options),
                    source="cached",
                    fetched_at=_to_datetime(entry.fetched_at),
                    error=None,
                    ttl_seconds=self._ttl,
                )

        async with self._locks.get(key):
            # Re-check inside the lock to dedupe concurrent fetches.
            if not force_refresh:
                entry = self._memory.get(key)
                if entry and self._is_fresh(entry):
                    return CatalogResult(
                        options=list(entry.options),
                        source="cached",
                        fetched_at=_to_datetime(entry.fetched_at),
                        error=None,
                        ttl_seconds=self._ttl,
                    )

            try:
                options = await self._fetchers[provider].fetch(credentials)
            except Exception as exc:
                # Fetch failures are surfaced as ``CatalogResult(error=...)``
                # so the UI can render an "offline" badge — never bubble up
                # as an HTTP 5xx.
                logger.warning("Model catalog fetch failed for %s/%s: %s", provider, scope, exc)
                fallback = _static_fallback(provider)
                return CatalogResult(
                    options=fallback,
                    source="fallback",
                    fetched_at=None,
                    error=str(exc),
                    ttl_seconds=self._ttl,
                )

            now = time.time()
            self._memory[key] = _CacheEntry(options=list(options), fetched_at=now)
            self._evict_if_needed()
            await self._persist()

            return CatalogResult(
                options=list(options),
                source="live",
                fetched_at=_to_datetime(now),
                error=None,
                ttl_seconds=self._ttl,
            )

    def invalidate(self, *, provider: str | None = None, scope: str | None = None) -> int:
        """Drop cached entries matching *provider* and/or *scope*.

        Passing both ``provider=None`` and ``scope=None`` clears the
        entire cache. Returns the number of entries removed.
        """
        to_drop = [
            key
            for key in self._memory
            if (provider is None or key.provider == provider) and (scope is None or key.scope == scope)
        ]
        for key in to_drop:
            self._memory.pop(key, None)
        if to_drop:
            # Persist inline; a sync write is fine here since invalidations
            # are infrequent and run outside hot paths.
            self._write_disk_sync()
        return len(to_drop)

    # ------------------------------------------------------------------
    # Disk persistence
    # ------------------------------------------------------------------

    def _load_disk(self) -> None:
        if not self._cache_path.is_file():
            return
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read %s; ignoring stale model cache", self._cache_path)
            return
        for serialised, payload in raw.items():
            key = _CacheKey.parse(serialised)
            if key is None:
                continue
            options = [
                ModelEntry(value=opt["value"], label=opt.get("label", opt["value"]))
                for opt in payload.get("options", [])
                if opt.get("value")
            ]
            fetched_at = float(payload.get("fetched_at", 0.0))
            self._memory[key] = _CacheEntry(options=options, fetched_at=fetched_at)

    async def _persist(self) -> None:
        async with self._persist_lock:
            await asyncio.to_thread(self._write_disk_sync)

    def _write_disk_sync(self) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Cannot create model cache dir %s", self._data_dir)
            return
        payload = {
            key.serialise(): {
                "options": [asdict(opt) for opt in entry.options],
                "fetched_at": entry.fetched_at,
            }
            for key, entry in self._memory.items()
        }
        tmp = self._cache_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self._cache_path)
        except OSError as exc:
            logger.warning("Failed to persist model cache: %s", exc)
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_fresh(self, entry: _CacheEntry) -> bool:
        return (time.time() - entry.fetched_at) < self._ttl

    def _evict_if_needed(self) -> None:
        if len(self._memory) <= self._max_entries:
            return
        # Drop the oldest entries until we're back under the cap.
        ordered = sorted(self._memory.items(), key=lambda kv: kv[1].fetched_at)
        for key, _ in ordered[: len(self._memory) - self._max_entries]:
            self._memory.pop(key, None)

    # Test/debug helpers — not part of the public surface.
    def _entries(self) -> Iterable[tuple[_CacheKey, _CacheEntry]]:
        return self._memory.items()


def _to_datetime(unix_seconds: float) -> datetime:
    return datetime.fromtimestamp(unix_seconds, tz=UTC)
