#!/usr/bin/env python3
"""Test script to verify session update broadcasting works correctly."""

import asyncio
import sys
import uuid
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.session import SessionManager, SessionType


async def test_session_updates():
    """Test that all session updates are properly broadcast."""
    print("Testing session update broadcasting...")

    # Create manager
    manager = SessionManager()

    # Subscribe two clients
    client1_id = str(uuid.uuid4())
    client2_id = str(uuid.uuid4())

    queue1 = manager.subscribe_updates(client1_id)
    queue2 = manager.subscribe_updates(client2_id)

    print("✓ Created manager and subscribed 2 clients")

    # Test 1: Session creation broadcast
    session = manager.create_session(SessionType.CONVERSATIONAL)

    # Both clients should receive creation
    update1 = queue1.get_nowait()
    update2 = queue2.get_nowait()

    assert update1["type"] == "session_update"
    assert update1["session_id"] == session.id
    assert update1["status"] == "created"
    assert update1["title"] is None
    assert update2 == update1

    print(f"✓ Session creation broadcast to all clients")

    # Test 2: Title update
    session.title = "Test Session"

    update1 = queue1.get_nowait()
    update2 = queue2.get_nowait()

    assert update1["title"] == "Test Session"
    assert update2["title"] == "Test Session"

    print(f"✓ Title update broadcast: '{update1['title']}'")

    # Test 3: Title clear (set to None)
    session.title = None

    update1 = queue1.get_nowait()
    update2 = queue2.get_nowait()

    assert update1["title"] is None
    assert update2["title"] is None

    print(f"✓ Title clear broadcast (set to null)")

    # Test 4: Status changes
    statuses = [
        ("set_active", "active"),
        ("set_executing", "executing"),
        ("pause", "paused"),
        ("resume", "active"),
        ("complete", "completed"),
    ]

    for method, expected_status in statuses:
        if method == "complete":
            # Skip if already completed
            if session.status == "completed":
                break

        # Call the method
        getattr(session, method)()

        # Check broadcast
        update1 = queue1.get_nowait()
        assert update1["status"] == expected_status
        queue2.get_nowait()  # Just consume from queue2

        print(f"✓ Status change broadcast: {method}() → {expected_status}")

    # Test 5: Unsubscribe one client
    manager.unsubscribe_updates(client1_id)

    # Verify queue1 gets None (unsubscribe signal)
    signal = await asyncio.wait_for(queue1.get(), timeout=1.0)
    assert signal is None

    print("✓ Client unsubscribe works")

    # Test 6: Create another session (only client2 should receive)
    session2 = manager.create_session(SessionType.ONE_SHOT)

    update2 = queue2.get_nowait()
    assert update2["session_id"] == session2.id

    # queue1 should be empty (unsubscribed)
    assert queue1.empty()

    print("✓ Unsubscribed client doesn't receive updates")

    # Clean up
    manager.unsubscribe_updates(client2_id)

    print("\n✅ All session update broadcast tests passed!")
    print("\nSummary:")
    print("- Session creation broadcasts to all subscribers")
    print("- Title updates broadcast (including null for cleared titles)")
    print("- Status changes broadcast for all transitions")
    print("- Multiple clients receive the same updates")
    print("- Unsubscribed clients stop receiving updates")


if __name__ == "__main__":
    asyncio.run(test_session_updates())