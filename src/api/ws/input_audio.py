import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from src.api.deps import verify_ws_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/input/audio")
async def ws_input_audio(
    websocket: WebSocket,
    api_key: str = Query(...),
) -> None:
    """WebSocket endpoint for receiving user voice audio.

    Clients send binary audio chunks (16-bit PCM, 16kHz, mono).
    The server transcribes them via the configured STT provider,
    then routes the resulting text through the LLM pipeline.

    Query Parameters:
        api_key: API key for authentication.

    Input: Binary audio frames.

    Response Message Format:
        {
            "type": "transcription",
            "text": "transcribed text",
            "is_final": true
        }
        {
            "type": "ack",
            "session_id": "uuid"
        }
    """
    await verify_ws_api_key(api_key)
    await websocket.accept()

    stt_provider = websocket.app.state.stt_provider
    prompt_router = websocket.app.state.prompt_router

    logger.info("Client connected to /ws/input/audio")

    try:
        # Create an async iterator from WebSocket binary messages
        async def audio_stream() -> AsyncIterator[bytes]:
            while True:
                try:
                    data = await websocket.receive_bytes()
                    yield data
                except WebSocketDisconnect:
                    return

        # Connect to STT and transcribe
        await stt_provider.connect()

        final_text = ""
        try:
            async for result in stt_provider.transcribe(audio_stream()):
                await websocket.send_json(
                    {
                        "type": "transcription",
                        "text": result.text,
                        "is_final": result.is_final,
                    }
                )

                if result.is_final:
                    final_text = result.text
                    break
        finally:
            await stt_provider.close()

        # Route the transcribed text through the LLM pipeline
        if final_text.strip():
            session_id = await prompt_router.handle_prompt(final_text)
            await websocket.send_json({"type": "ack", "session_id": session_id})

    except WebSocketDisconnect:
        logger.info("Client disconnected from /ws/input/audio")
