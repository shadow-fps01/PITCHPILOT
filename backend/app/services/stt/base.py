"""Speech-to-text provider interface shared by all backends."""

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptChunk:
    """One transcript fragment from the STT engine."""

    text: str
    is_final: bool


# Async callback invoked for every transcript fragment.
TranscriptCallback = Callable[[TranscriptChunk], Awaitable[None]]


class STTProvider(abc.ABC):
    """A duplex live transcription backend.

    Lifecycle: `start()` → many `send_audio(pcm16)` calls → `stop()`.
    Providers must never let downstream API errors escape to the caller —
    log them and keep the socket alive where possible.
    """

    def __init__(self, on_transcript: TranscriptCallback) -> None:
        self._on_transcript = on_transcript

    @abc.abstractmethod
    async def start(self) -> None:
        """Open the downstream transcription connection."""

    @abc.abstractmethod
    async def send_audio(self, pcm16: bytes) -> None:
        """Forward a 16 kHz 16-bit mono PCM frame."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Close the downstream connection and release resources."""
