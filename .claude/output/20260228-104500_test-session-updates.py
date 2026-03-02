#!/usr/bin/env python3
"""Test script to verify session update broadcasting is working."""

import asyncio
from src.core.session import SessionManager, SessionType


async def test_session_updates():
    """Test that session updates are broadcast to subscribers."""
    manager = SessionManager()

    # Subscribe to updates
    subscriber_id = "test-subscriber"
    queue = manager.subscribe_updates(subscriber_id)

    # Create a new session (should trigger an update)
    print("Creating session...")
    session = manager.create_session(SessionType.CONVERSATIONAL)
    print(f"Created session: {session.id}")

    # Get the creation update
    update = await asyncio.wait_for(queue.get(), timeout=1.0)
    print(f"Received creation update: {update}")
    assert update["type"] == "session_update"
    assert update["session_id"] == session.id
    assert update["status"] == "created"
    assert update["title"] is None

    # Change status (should trigger an update)
    print("\nChanging status to active...")
    session.set_active()

    update = await asyncio.wait_for(queue.get(), timeout=1.0)
    print(f"Received status update: {update}")
    assert update["status"] == "active"

    # Change title (should trigger an update)
    print("\nChanging title...")
    session.title = "Test Session Title"

    update = await asyncio.wait_for(queue.get(), timeout=1.0)
    print(f"Received title update: {update}")
    assert update["title"] == "Test Session Title"

    # Change status to executing
    print("\nChanging status to executing...")
    session.set_executing()

    update = await asyncio.wait_for(queue.get(), timeout=1.0)
    print(f"Received executing update: {update}")
    assert update["status"] == "executing"

    # Complete the session
    print("\nCompleting session...")
    session.complete()

    update = await asyncio.wait_for(queue.get(), timeout=1.0)
    print(f"Received completion update: {update}")
    assert update["status"] == "completed"

    # Unsubscribe
    manager.unsubscribe_updates(subscriber_id)

    print("\n✅ All tests passed!")


if __name__ == "__main__":
    asyncio.run(test_session_updates())