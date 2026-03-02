#!/usr/bin/env python3
"""Test script to verify real-time session updates.

This script:
1. Connects to the WebSocket output endpoint
2. Creates a session via the HTTP API
3. Updates the session title
4. Verifies that session_update messages are received
"""

import asyncio
import json
import sys
import uuid
from datetime import datetime

import aiohttp
import websockets


API_KEY = "key"  # Update with your actual API key from .env
HOST = "localhost:8765"
BASE_URL = f"http://{HOST}"
WS_URL = f"ws://{HOST}"


async def test_realtime_updates():
    print(f"[{datetime.now().isoformat()}] Starting real-time session update test...")

    # Step 1: Connect to WebSocket output endpoint
    print(f"[{datetime.now().isoformat()}] Connecting to WebSocket...")
    ws = await websockets.connect(f"{WS_URL}/ws/output/text?api_key={API_KEY}")

    # Collect session_update messages
    update_messages = []

    async def receive_updates():
        """Background task to receive WebSocket messages."""
        try:
            while True:
                msg_raw = await ws.recv()
                msg = json.loads(msg_raw)
                if msg.get("type") == "session_update":
                    print(f"[{datetime.now().isoformat()}] Received session_update: {json.dumps(msg, indent=2)}")
                    update_messages.append(msg)
        except websockets.exceptions.ConnectionClosed:
            pass

    # Start receiving updates in background
    receive_task = asyncio.create_task(receive_updates())

    # Give it a moment to establish connection
    await asyncio.sleep(0.5)

    # Step 2: Create a test session via HTTP API
    print(f"[{datetime.now().isoformat()}] Creating test session via HTTP API...")
    async with aiohttp.ClientSession() as session:
        # First, let's list current sessions
        headers = {"X-API-Key": API_KEY}

        async with session.get(f"{BASE_URL}/api/sessions", headers=headers) as resp:
            if resp.status != 200:
                print(f"Failed to list sessions: {resp.status}")
                await ws.close()
                return
            sessions = await resp.json()
            print(f"[{datetime.now().isoformat()}] Current sessions: {len(sessions.get('sessions', []))}")

        # Find or create a session to test with
        test_session_id = None
        existing_sessions = sessions.get("sessions", [])

        if existing_sessions:
            # Use the first session for testing
            test_session_id = existing_sessions[0]["session_id"]
            print(f"[{datetime.now().isoformat()}] Using existing session: {test_session_id}")
        else:
            print(f"[{datetime.now().isoformat()}] No existing sessions. Please create one via the client app.")
            await ws.close()
            return

        # Clear any initial updates
        await asyncio.sleep(0.5)
        update_messages.clear()

        # Step 3: Update the session title
        new_title = f"Test Update {datetime.now().strftime('%H:%M:%S')}"
        print(f"[{datetime.now().isoformat()}] Updating session title to: {new_title}")

        async with session.patch(
            f"{BASE_URL}/api/sessions/{test_session_id}/title",
            headers=headers,
            json={"title": new_title}
        ) as resp:
            if resp.status != 200:
                print(f"Failed to update session title: {resp.status}")
                body = await resp.text()
                print(f"Response: {body}")
            else:
                result = await resp.json()
                print(f"[{datetime.now().isoformat()}] Title updated successfully: {result}")

        # Step 4: Wait for session_update message
        print(f"[{datetime.now().isoformat()}] Waiting for session_update message...")
        await asyncio.sleep(1.0)

        # Check if we received the update
        if update_messages:
            print(f"\n✅ SUCCESS: Received {len(update_messages)} session_update message(s)")
            for msg in update_messages:
                if msg.get("session_id") == test_session_id:
                    if msg.get("title") == new_title:
                        print(f"✅ Title update confirmed: {new_title}")
                    else:
                        print(f"⚠️ Title mismatch: expected '{new_title}', got '{msg.get('title')}'")
        else:
            print("\n❌ FAILURE: No session_update messages received")

        # Step 5: Test status update by pausing/resuming
        print(f"\n[{datetime.now().isoformat()}] Testing status updates...")
        update_messages.clear()

        # Pause the session
        print(f"[{datetime.now().isoformat()}] Pausing session...")
        async with session.post(
            f"{BASE_URL}/api/sessions/{test_session_id}/pause",
            headers=headers
        ) as resp:
            if resp.status == 200:
                print(f"[{datetime.now().isoformat()}] Session paused")
            else:
                print(f"Failed to pause session: {resp.status}")

        await asyncio.sleep(0.5)

        if update_messages:
            for msg in update_messages:
                if msg.get("status") == "paused":
                    print(f"✅ Status update confirmed: paused")
                    break

        # Resume the session
        update_messages.clear()
        print(f"[{datetime.now().isoformat()}] Resuming session...")
        async with session.post(
            f"{BASE_URL}/api/sessions/{test_session_id}/resume",
            headers=headers
        ) as resp:
            if resp.status == 200:
                print(f"[{datetime.now().isoformat()}] Session resumed")
            else:
                print(f"Failed to resume session: {resp.status}")

        await asyncio.sleep(0.5)

        if update_messages:
            for msg in update_messages:
                if msg.get("status") == "active":
                    print(f"✅ Status update confirmed: active")
                    break

    # Clean up
    receive_task.cancel()
    await ws.close()
    print(f"\n[{datetime.now().isoformat()}] Test complete!")


if __name__ == "__main__":
    asyncio.run(test_realtime_updates())