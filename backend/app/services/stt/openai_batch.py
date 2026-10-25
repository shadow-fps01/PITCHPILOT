"""Shared batch STT base for OpenAI-compatible `audio/transcriptions` APIs.

Groq and Azure OpenAI expose batch transcription rather than a duplex
streaming WebSocket, so this base buffers the live 16 kHz PCM stream into
rolling segments and ships each one to the REST API as a WAV. Effective cue
latency is roughly one segment plus a single HTTP round-trip. Results are
emitted as FINAL transcript chunks only (no interims).

Shared behaviour:
- silence gating (near-silent segments are never sent, saving API calls),
- continuity `prompt` (the tail of the previous segment guides the decoder),
- buffer cap + in-flight guard so slow networks never duplicate segments,
- two attempts per segment so a transient DNS/TLS blip never drops audio.
"""

import array
import asyncio
import io
import struct

import httpx
from loguru import logger

from app.services.stt.base import STTProvider, TranscriptCallback, TranscriptChunk

# Segments worth of audio we may buffer before trimming the oldest bytes.
_MAX_BUFFER_SEGMENTS = 3
# int16 RMS below this is silence (no speech worth transcribing).
_MIN_RMS = 300


def pcm16_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw mono 16-bit PCM in a canonical 44-byte WAV header."""
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * 2,  # byte rate
        2,  # block align
        16,  # bits per sample
        b"data",
        data_size,
    )
    return header + pcm


class OpenAIBatchSTT(STTProvider):
    """Rolling-segment transcription against an OpenAI-compatible endpoint."""

    def __init__(
        self,
        on_transcript: TranscriptCallback,
        *,
        url: str,
        headers: dict[str, str],
        provider_label: str,
        model: str | None = None,
        params: dict[str, str] | None = None,
        warmup_url: str | None = None,
        segment_seconds: float = 3.0,
        language: str = "en",
    ) -> None:
        super().__init__(on_transcript)
        self._url = url
        self._headers = headers
        self._provider_label = provider_label
        self._model = model
        self._params = params or {}
        self._warmup_url = warmup_url
        self._segment_seconds = segment_seconds
        self._language = language
        self._bytes_per_segment = int(16000 * 2 * segment_seconds)
        self._max_buffer = self._bytes_per_segment * _MAX_BUFFER_SEGMENTS

        self._buffer = bytearray()
        self._prompt = ""  # tail of the last transcript, for continuity
        self._client: httpx.AsyncClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._warmup_task: asyncio.Task[None] | None = None
        self._warm = asyncio.Event()
        self._stop_event = asyncio.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────
    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        self._stop_event.clear()
        self._task = asyncio.create_task(self._flush_loop())
        self._warmup_task = asyncio.create_task(self._warm_up())
        detail = f"model={self._model}" if self._model else "deployment in URL"
        logger.info(
            f"{self._provider_label} STT started "
            f"({detail}, segment={self._segment_seconds:.1f}s)"
        )

    async def _warm_up(self) -> None:
        """Pre-resolve and pre-connect the API host before real audio flows.

        Cold OS resolvers on some links fail the first getaddrinfo for a host
        several times in a row; burning those failures here means the first
        live segment is never silently dropped.
        """
        assert self._client is not None
        if not self._warmup_url:
            self._warm.set()
            return
        for _ in range(10):
            try:
                # Any completed request warms DNS + TLS and pools the socket.
                await self._client.get(self._warmup_url)
                self._warm.set()
                logger.info(f"{self._provider_label} STT connection warmed up")
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — retry until the resolver settles
                await asyncio.sleep(1.0)
        self._warm.set()  # stop blocking the flush loop; retries take over
        logger.debug(
            f"{self._provider_label} warm-up incomplete; relying on per-segment retries"
        )

    async def send_audio(self, pcm16: bytes) -> None:
        self._buffer.extend(pcm16)
        if len(self._buffer) > self._max_buffer:
            # Transcription is lagging — drop the oldest audio to stay live.
            del self._buffer[: len(self._buffer) - self._bytes_per_segment]

    async def stop(self) -> None:
        self._stop_event.set()
        for task in (self._task, self._warmup_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass
        self._task = None
        self._warmup_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Internals ──────────────────────────────────────────────────────
    async def _flush_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._segment_seconds)
            if self._stop_event.is_set():
                return
            if not self._warm.is_set():
                # Give the connection warm-up a moment before the first POST.
                try:
                    await asyncio.wait_for(self._warm.wait(), timeout=6.0)
                except asyncio.TimeoutError:
                    pass
            if len(self._buffer) == 0:
                continue
            pcm = bytes(self._buffer)
            self._buffer.clear()
            if self._is_silent(pcm):
                continue
            text = await self._transcribe(pcm)
            if text:
                await self._on_transcript(TranscriptChunk(text=text, is_final=True))

    def _is_silent(self, pcm: bytes) -> bool:
        """True when the segment has no meaningful speech energy."""
        usable = len(pcm) - (len(pcm) % 2)
        if usable == 0:
            return True
        samples = array.array("h")
        samples.frombytes(pcm[:usable])
        stepped = samples[::4]  # decimate for speed; RMS is scale-invariant
        mean_square = sum(s * s for s in stepped) / len(stepped)
        rms = mean_square**0.5
        return rms < _MIN_RMS

    async def _transcribe(self, pcm: bytes) -> str:
        assert self._client is not None
        wav = pcm16_to_wav(pcm)
        data: dict[str, str] = {
            "response_format": "json",
            "language": self._language,
        }
        if self._model:
            data["model"] = self._model
        if self._prompt:
            # Continuity: Whisper uses the prompt as soft prior context.
            data["prompt"] = self._prompt
        # Two attempts: transient first-connect DNS/network blips are common
        # on residential links and would otherwise silently drop a segment.
        for attempt in range(2):
            try:
                response = await self._client.post(
                    self._url,
                    headers=self._headers,
                    params=self._params,
                    files={"file": ("audio.wav", io.BytesIO(wav), "audio/wav")},
                    data=data,
                )
                response.raise_for_status()
                payload = response.json()
                text = str(payload.get("text", "")).strip()
                if text:
                    self._prompt = text[-200:]
                return text
            except Exception as exc:  # noqa: BLE001 — one bad segment never kills the stream
                if attempt == 0:
                    logger.debug(
                        f"{self._provider_label} transcription attempt 1 failed "
                        f"({exc}); retrying"
                    )
                    await asyncio.sleep(1.5)
                else:
                    logger.warning(
                        f"{self._provider_label} transcription failed: {exc}"
                    )
                    return ""
        return ""
