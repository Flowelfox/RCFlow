from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator


class BaseTTSProvider(ABC):
    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the TTS service."""

    @abstractmethod
    def synthesize(self, text: str) -> AsyncGenerator[bytes, None]:
        """Synthesize text into streaming audio chunks (Opus/OGG frames)."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the connection to the TTS service."""
