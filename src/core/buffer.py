import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of messages retained in the text history buffer (F11).
# When the limit is reached the oldest message is evicted to cap memory usage.
_MAX_BUFFER_MESSAGES = 2000


class MessageType(StrEnum):
    TEXT_CHUNK = "text_chunk"
    TOOL_START = "tool_start"
    TOOL_OUTPUT = "tool_output"
    ERROR = "error"
    SESSION_END = "session_end"
    SESSION_END_ASK = "session_end_ask"
    SUMMARY = "summary"
    NOTIFICATION = "notification"
    AGENT_SESSION_START = "agent_session_start"
    AGENT_GROUP_START = "agent_group_start"
    AGENT_GROUP_END = "agent_group_end"
    SESSION_PAUSED = "session_paused"
    SESSION_RESUMED = "session_resumed"
    PLAN_MODE_ASK = "plan_mode_ask"
    PLAN_REVIEW_ASK = "plan_review_ask"
    PERMISSION_REQUEST = "permission_request"
    SESSION_RESTORED = "session_restored"
    TODO_UPDATE = "todo_update"
    THINKING = "thinking"
    AGENT_LOG = "agent_log"
    SESSION_UPDATE = "session_update"  # For broadcasting session metadata updates
    SUBPROCESS_STATUS = "subprocess_status"  # Ephemeral — not archived to DB


@dataclass
class BufferedMessage:
    """A single buffered output message with its sequence number."""

    sequence: int
    message_type: MessageType
    data: dict[str, Any]


class SessionBuffer:
    """Buffers full session output history for replay and live streaming.

    Subscribers receive the full history on subscribe, then live updates.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._text_messages: deque[BufferedMessage] = deque(maxlen=_MAX_BUFFER_MESSAGES)
        self._text_sequence: int = 0
        self._text_subscribers: dict[str, asyncio.Queue[BufferedMessage | None]] = {}
        self._closed: bool = False

    @property
    def text_history(self) -> list[BufferedMessage]:
        return list(self._text_messages)

    def push_text(self, message_type: MessageType, data: dict[str, Any]) -> BufferedMessage:
        """Push a text message to the buffer and notify all subscribers.

        When the buffer exceeds ``_MAX_BUFFER_MESSAGES`` the oldest entry is
        evicted so that long-running sessions cannot exhaust process memory
        (F11 remediation).
        """
        self._text_sequence += 1
        msg = BufferedMessage(sequence=self._text_sequence, message_type=message_type, data=data)
        self._text_messages.append(msg)

        for queue in self._text_subscribers.values():
            queue.put_nowait(msg)

        return msg

    def push_ephemeral(self, message_type: MessageType, data: dict[str, Any]) -> None:
        """Push a message to live subscribers only — NOT archived to text_history.

        Use for transient UI updates (e.g. subprocess_status) that should not
        be replayed on reconnect or persisted to the database.
        """
        self._text_sequence += 1
        msg = BufferedMessage(sequence=self._text_sequence, message_type=message_type, data=data)
        for queue in self._text_subscribers.values():
            queue.put_nowait(msg)

    def subscribe_text(self, subscriber_id: str) -> asyncio.Queue[BufferedMessage | None]:
        """Subscribe to text output. Returns a queue that receives the full history then live updates."""
        queue: asyncio.Queue[BufferedMessage | None] = asyncio.Queue()

        # Replay full history
        for msg in self._text_messages:
            queue.put_nowait(msg)

        if self._closed:
            # Session already ended — put sentinel so stream_session exits cleanly
            # instead of blocking on queue.get() forever.
            queue.put_nowait(None)
        else:
            self._text_subscribers[subscriber_id] = queue
        return queue

    def unsubscribe_text(self, subscriber_id: str) -> None:
        queue = self._text_subscribers.pop(subscriber_id, None)
        if queue:
            queue.put_nowait(None)  # Signal end

    def close(self) -> None:
        """Signal all subscribers that the session is done."""
        self._closed = True
        for queue in self._text_subscribers.values():
            queue.put_nowait(None)
        self._text_subscribers.clear()
