import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator

import websockets
import websockets.asyncio.client

from src.speech.stt.base import BaseSTTProvider, TranscriptionResult

logger = logging.getLogger(__name__)

WISPR_FLOW_WS_URL = "wss://platform-api.wisprflow.ai/api/v1/dash/ws"


class WisprFlowSTTProvider(BaseSTTProvider):
    """Wispr Flow Speech-to-Text provider using their WebSocket API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._ws: websockets.asyncio.client.ClientConnection | None = None

    async def connect(self) -> None:
        url = f"{WISPR_FLOW_WS_URL}?api_key=Bearer%20{self._api_key}"
        self._ws = await websockets.asyncio.client.connect(url)
        logger.info("Connected to Wispr Flow STT")

    async def transcribe(self, audio_chunks: AsyncIterator[bytes]) -> AsyncGenerator[TranscriptionResult, None]:
        if self._ws is None:
            raise RuntimeError("Not connected. Call connect() first.")

        # Send auth message
        auth_msg = {
            "type": "auth",
            "access_token": self._api_key,
        }
        await self._ws.send(json.dumps(auth_msg))

        # Wait for auth response
        auth_response = await self._ws.recv()
        auth_data = json.loads(auth_response)
        if auth_data.get("status") == "error":
            raise RuntimeError(f"Wispr Flow auth failed: {auth_data}")

        logger.info("Wispr Flow STT authenticated")

        # Start sending audio and receiving transcriptions concurrently
        send_done = asyncio.Event()

        async def send_audio() -> None:
            position = 0
            async for chunk in audio_chunks:
                encoded = base64.b64encode(chunk).decode("ascii")
                append_msg = {
                    "type": "append",
                    "data": encoded,
                    "position": position,
                }
                await self._ws.send(json.dumps(append_msg))  # type: ignore[union-attr]
                position += 1

            # Send commit
            commit_msg = {
                "type": "commit",
                "position": position,
            }
            await self._ws.send(json.dumps(commit_msg))  # type: ignore[union-attr]
            send_done.set()

        send_task = asyncio.create_task(send_audio())

        try:
            while True:
                try:
                    response = await self._ws.recv()
                    data = json.loads(response)

                    if data.get("status") == "text":
                        text = data.get("text", "")
                        is_final = data.get("final", False)
                        if text:
                            yield TranscriptionResult(text=text, is_final=is_final)

                        if is_final:
                            break

                    elif data.get("status") == "error":
                        logger.error("Wispr Flow STT error: %s", data)
                        break

                    elif data.get("status") == "info":
                        # Commit acknowledgement or other info
                        if send_done.is_set() and data.get("message") == "commit":
                            # Wait a bit more for final transcription
                            continue

                except websockets.ConnectionClosed:
                    break
        finally:
            if not send_task.done():
                send_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await send_task

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("Disconnected from Wispr Flow STT")
