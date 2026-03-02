from typing import Protocol

from src.speech.stt.base import BaseSTTProvider, TranscriptionResult
from src.speech.stt.wispr_flow import WisprFlowSTTProvider


class _STTProviderConstructor(Protocol):
    def __call__(self, api_key: str) -> BaseSTTProvider: ...


_PROVIDERS: dict[str, _STTProviderConstructor] = {}


def _load_providers() -> None:
    _PROVIDERS["wispr_flow"] = WisprFlowSTTProvider


def create_stt_provider(provider_name: str, api_key: str) -> BaseSTTProvider:
    """Factory function to create an STT provider by name."""
    if not _PROVIDERS:
        _load_providers()

    provider_class = _PROVIDERS.get(provider_name)
    if provider_class is None:
        available = ", ".join(_PROVIDERS.keys())
        raise ValueError(f"Unknown STT provider '{provider_name}'. Available: {available}")

    return provider_class(api_key=api_key)


__all__ = ["BaseSTTProvider", "TranscriptionResult", "create_stt_provider"]
