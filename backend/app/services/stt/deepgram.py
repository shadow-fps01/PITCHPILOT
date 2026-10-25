"""Deepgram live WebSocket STT backend (linear16 @ 16 kHz)."""

import asyncio
import json

import websockets
from loguru import logger

from app.services.stt.base import STTProvider, TranscriptCallback

_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=linear16"
    "&sample_rate=16000"
    "&channels=1"
    "&punctuate=true"
    "&smart_format=true"
    "&interim_results=true"
    "&model=nova-2"
)


async def _connect(url: str, headers: dict[str, str]):
    """Connect with the kwarg name that matches the installed websockets version."""
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=2**22)
    except TypeError:  # websockets < 14 uses `extra_headers`
        return await websockets.connect(url, extra_headers=headers, max_size=2**22)


class DeepgramLiveSTT(STTProvider):
    """Streams raw PCM frames to Deepgram and parses interim/final results."""

    def __init__(self, on_transcript: TranscriptCallback, api_key: str) -> None:
        super().__init__(on_transcript)
        self._api_key = api_key
        # An open `websockets` client connection (typing varies across versions).
        self._ws = None
        self._recv_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._ws is not None:
            return
        self._ws = await _connect(_URL, {"Authorization": f"Token {self._api_key}"})
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("Deepgram live STT connected")

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    message: dict = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if message.get("type") != "Results":
                    continue
                channel = message.get("channel", {})
                alternatives = channel.get("alternatives", [])
                if not alternatives:
                    continue
                text = str(alternatives[0].get("transcript", "")).strip()
                if not text:
                    continue
                is_final = bool(message.get("is_final", False))
                await self._on_transcript_chunk(text, is_final)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a dead provider must not kill the WS
            logger.warning(f"Deepgram recv loop ended: {exc}")

    async def _on_transcript_chunk(self, text: str, is_final: bool) -> None:
        from app.services.stt.base import TranscriptChunk

        await self._on_transcript(TranscriptChunk(text=text, is_final=is_final))

    async def send_audio(self, pcm16: bytes) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(pcm16)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Deepgram send failed: {exc}")

    async def stop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:  # noqa: BLE001 — best effort close
                pass
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
