import logging
from collections.abc import AsyncGenerator
from typing import Protocol

from src.speech.tts.base import BaseTTSProvider

logger = logging.getLogger(__name__)


class _TTSProviderConstructor(Protocol):
    def __call__(self, api_key: str) -> BaseTTSProvider: ...


_PROVIDERS: dict[str, _TTSProviderConstructor] = {}


class NoopTTSProvider(BaseTTSProvider):
    """Placeholder TTS provider that produces no audio."""

    async def connect(self) -> None:
        logger.info("NoopTTSProvider: no TTS configured")

    async def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        return
        yield  # Make this a valid async generator

    async def close(self) -> None:
        pass


def create_tts_provider(provider_name: str, api_key: str = "") -> BaseTTSProvider:
    """Factory function to create a TTS provider by name."""
    if provider_name == "none" or not provider_name:
        return NoopTTSProvider()

    provider_class = _PROVIDERS.get(provider_name)
    if provider_class is None:
        available = ", ".join(_PROVIDERS.keys()) or "(none registered)"
        raise ValueError(f"Unknown TTS provider '{provider_name}'. Available: {available}")

    return provider_class(api_key=api_key)


__all__ = ["BaseTTSProvider", "create_tts_provider"]
