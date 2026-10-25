"""OpenAI Realtime transcription backend (gpt-4o-transcribe over WebSockets).

Implements the standard OpenAI Realtime protocol: a `session.update` with
server VAD + whisper transcription, `input_audio_buffer.append` frames, and
`conversation.item.input_audio_transcription.completed` events for finals.
"""

import asyncio
import base64
import json

import websockets
from loguru import logger

from app.services.stt.base import STTProvider, TranscriptCallback, TranscriptChunk

_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-transcribe"

_SESSION_UPDATE = {
    "type": "session.update",
    "session": {
        "modalities": ["text"],
        "input_audio_format": "pcm16",
        "input_audio_transcription": {"model": "gpt-4o-transcribe", "language": "en"},
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 600,
        },
    },
}


async def _connect(url: str, headers: dict[str, str]):
    """Connect with the kwarg name that matches the installed websockets version."""
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=2**22)
    except TypeError:  # websockets < 14 uses `extra_headers`
        return await websockets.connect(url, extra_headers=headers, max_size=2**22)


class OpenAIRealtimeSTT(STTProvider):
    """Streams base64 PCM frames to the OpenAI Realtime transcription socket."""

    def __init__(self, on_transcript: TranscriptCallback, api_key: str) -> None:
        super().__init__(on_transcript)
        self._api_key = api_key
        # An open `websockets` client connection (typing varies across versions).
        self._ws = None
        self._recv_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._ws is not None:
            return
        self._ws = await _connect(
            _URL,
            {
                "Authorization": f"Bearer {self._api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
        )
        await self._ws.send(json.dumps(_SESSION_UPDATE))
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("OpenAI Realtime STT connected")

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    event: dict = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                event_type = event.get("type", "")
                if event_type == "conversation.item.input_audio_transcription.completed":
                    text = str(event.get("transcript", "")).strip()
                    if text:
                        await self._on_transcript(TranscriptChunk(text=text, is_final=True))
                elif event_type == "response.audio_transcript.delta":
                    delta = str(event.get("delta", "")).strip()
                    if delta:
                        await self._on_transcript(TranscriptChunk(text=delta, is_final=False))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OpenAI Realtime recv loop ended: {exc}")

    async def send_audio(self, pcm16: bytes) -> None:
        if self._ws is None:
            return
        frame = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm16).decode("ascii"),
        }
        try:
            await self._ws.send(json.dumps(frame))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OpenAI Realtime send failed: {exc}")

    async def stop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            self._recv_task = None
