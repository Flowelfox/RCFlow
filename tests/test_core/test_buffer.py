import asyncio

import pytest

from src.core.buffer import MessageType, SessionBuffer


class TestSessionBuffer:
    def test_push_text(self):
        buffer = SessionBuffer("test-session")
        msg = buffer.push_text(MessageType.TEXT_CHUNK, {"content": "hello"})
        assert msg.sequence == 1
        assert msg.message_type == MessageType.TEXT_CHUNK
        assert buffer.text_history == [msg]

    def test_push_audio(self):
        buffer = SessionBuffer("test-session")
        chunk = buffer.push_audio(b"\x00\x01\x02")
        assert chunk.sequence == 1
        assert chunk.data == b"\x00\x01\x02"
        assert buffer.audio_history == [chunk]

    @pytest.mark.asyncio
    async def test_text_subscriber_receives_history_and_live(self):
        buffer = SessionBuffer("test-session")
        buffer.push_text(MessageType.TEXT_CHUNK, {"content": "history"})

        queue = buffer.subscribe_text("sub-1")

        # Should have the history message
        msg = queue.get_nowait()
        assert msg is not None
        assert msg.data["content"] == "history"

        # Push a live message
        buffer.push_text(MessageType.TEXT_CHUNK, {"content": "live"})
        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert msg is not None
        assert msg.data["content"] == "live"

    @pytest.mark.asyncio
    async def test_unsubscribe_sends_none(self):
        buffer = SessionBuffer("test-session")
        queue = buffer.subscribe_text("sub-1")
        buffer.unsubscribe_text("sub-1")

        msg = queue.get_nowait()
        assert msg is None

    def test_close_signals_all_subscribers(self):
        buffer = SessionBuffer("test-session")
        q1 = buffer.subscribe_text("sub-1")
        q2 = buffer.subscribe_audio("sub-2")

        buffer.close()

        assert q1.get_nowait() is None
        assert q2.get_nowait() is None

    @pytest.mark.asyncio
    async def test_push_ephemeral_notifies_subscribers_but_not_history(self):
        """push_ephemeral must reach live subscribers but never appear in text_history."""
        buffer = SessionBuffer("test-session")

        # Push one archived message so the subscriber gets replayed history first
        buffer.push_text(MessageType.TEXT_CHUNK, {"content": "archived"})

        queue = buffer.subscribe_text("sub-1")

        # Consume the replayed history entry
        hist_msg = queue.get_nowait()
        assert hist_msg is not None
        assert hist_msg.data["content"] == "archived"

        # Now push an ephemeral message
        buffer.push_ephemeral(MessageType.SUBPROCESS_STATUS, {"subprocess_type": "claude_code"})

        # The subscriber queue must receive it
        live_msg = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert live_msg is not None
        assert live_msg.message_type == MessageType.SUBPROCESS_STATUS
        assert live_msg.data["subprocess_type"] == "claude_code"

        # But it must NOT appear in text_history
        status_msgs = [m for m in buffer.text_history if m.message_type == MessageType.SUBPROCESS_STATUS]
        assert len(status_msgs) == 0

    def test_push_ephemeral_increments_sequence(self):
        """push_ephemeral must still advance the sequence counter."""
        buffer = SessionBuffer("test-session")
        archived = buffer.push_text(MessageType.TEXT_CHUNK, {"content": "first"})
        assert archived.sequence == 1

        # Subscribe so push_ephemeral has a destination (avoids dead-letter drop)
        buffer.subscribe_text("sub-1")
        buffer.push_ephemeral(MessageType.SUBPROCESS_STATUS, {"subprocess_type": None})

        # Next archived message should have sequence 3 (ephemeral consumed seq 2)
        next_archived = buffer.push_text(MessageType.TEXT_CHUNK, {"content": "third"})
        assert next_archived.sequence == 3
