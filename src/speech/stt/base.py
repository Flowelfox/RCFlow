from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    """A transcription result from the STT provider."""

    text: str
    is_final: bool


class BaseSTTProvider(ABC):
    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the STT service."""

    @abstractmethod
    def transcribe(self, audio_chunks: AsyncIterator[bytes]) -> AsyncGenerator[TranscriptionResult, None]:
        """Transcribe streaming audio chunks into text results.

        Yields partial results (is_final=False) and final results (is_final=True).
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the connection to the STT service."""
