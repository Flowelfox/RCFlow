import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MessageType(StrEnum):
    TEXT_CHUNK = "text_chunk"
    TOOL_START = "tool_start"
    TOOL_OUTPUT = "tool_output"
    ERROR = "error"
    SESSION_END = "session_end"
    SESSION_END_ASK = "session_end_ask"
    SUMMARY = "summary"
    NOTIFICATION = "notification"
    AGENT_GROUP_START = "agent_group_start"
    AGENT_GROUP_END = "agent_group_end"
    SESSION_PAUSED = "session_paused"
    SESSION_RESUMED = "session_resumed"
    PLAN_MODE_ASK = "plan_mode_ask"
    PLAN_REVIEW_ASK = "plan_review_ask"
    SESSION_RESTORED = "session_restored"
    SESSION_UPDATE = "session_update"  # For broadcasting session metadata updates


@dataclass
class BufferedMessage:
    """A single buffered output message with its sequence number."""

    sequence: int
    message_type: MessageType
    data: dict[str, Any]


@dataclass
class AudioChunk:
    """A buffered audio chunk with its sequence number."""

    sequence: int
    data: bytes


class SessionBuffer:
    """Buffers full session output history for replay and live streaming.

    Subscribers receive the full history on subscribe, then live updates.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._text_messages: list[BufferedMessage] = []
        self._audio_chunks: list[AudioChunk] = []
        self._text_sequence: int = 0
        self._audio_sequence: int = 0
        self._text_subscribers: dict[str, asyncio.Queue[BufferedMessage | None]] = {}
        self._audio_subscribers: dict[str, asyncio.Queue[AudioChunk | None]] = {}

    @property
    def text_history(self) -> list[BufferedMessage]:
        return list(self._text_messages)

    @property
    def audio_history(self) -> list[AudioChunk]:
        return list(self._audio_chunks)

    def push_text(self, message_type: MessageType, data: dict[str, Any]) -> BufferedMessage:
        """Push a text message to the buffer and notify all subscribers."""
        self._text_sequence += 1
        msg = BufferedMessage(sequence=self._text_sequence, message_type=message_type, data=data)
        self._text_messages.append(msg)

        for queue in self._text_subscribers.values():
            queue.put_nowait(msg)

        return msg

    def push_audio(self, data: bytes) -> AudioChunk:
        """Push an audio chunk to the buffer and notify all subscribers."""
        self._audio_sequence += 1
        chunk = AudioChunk(sequence=self._audio_sequence, data=data)
        self._audio_chunks.append(chunk)

        for queue in self._audio_subscribers.values():
            queue.put_nowait(chunk)

        return chunk

    def subscribe_text(self, subscriber_id: str) -> asyncio.Queue[BufferedMessage | None]:
        """Subscribe to text output. Returns a queue that receives the full history then live updates."""
        queue: asyncio.Queue[BufferedMessage | None] = asyncio.Queue()

        # Replay full history
        for msg in self._text_messages:
            queue.put_nowait(msg)

        self._text_subscribers[subscriber_id] = queue
        return queue

    def unsubscribe_text(self, subscriber_id: str) -> None:
        queue = self._text_subscribers.pop(subscriber_id, None)
        if queue:
            queue.put_nowait(None)  # Signal end

    def subscribe_audio(self, subscriber_id: str) -> asyncio.Queue[AudioChunk | None]:
        """Subscribe to audio output. Returns a queue that receives the full history then live updates."""
        queue: asyncio.Queue[AudioChunk | None] = asyncio.Queue()

        # Replay full history
        for chunk in self._audio_chunks:
            queue.put_nowait(chunk)

        self._audio_subscribers[subscriber_id] = queue
        return queue

    def unsubscribe_audio(self, subscriber_id: str) -> None:
        queue = self._audio_subscribers.pop(subscriber_id, None)
        if queue:
            queue.put_nowait(None)  # Signal end

    def close(self) -> None:
        """Signal all subscribers that the session is done."""
        for queue in self._text_subscribers.values():
            queue.put_nowait(None)
        for queue in self._audio_subscribers.values():
            queue.put_nowait(None)
        self._text_subscribers.clear()
        self._audio_subscribers.clear()
