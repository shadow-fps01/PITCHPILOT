"""Mock STT backend: simulates a live speaker so the whole app runs key-less.

Streams a scripted pitch (~180 WPM, deliberately filler-heavy) as interim +
final transcript chunks, exercising the pacing engine, filler tracker and
script auditor exactly like a real provider would.
"""

import asyncio
import random

from loguru import logger

from app.services.stt.base import STTProvider, TranscriptCallback, TranscriptChunk

# Word gap ≈ 0.33 s → ~180 WPM, comfortably above the 140 default target so
# "Slow down" pacing cues appear during the demo.
_WORD_GAP_SECONDS = 0.33
_SENTENCE_PAUSE_SECONDS = 0.45

_SENTENCES: tuple[str, ...] = (
    "So, um, thanks everyone for joining the call today.",
    "Uh, let me walk you through what we have been building.",
    "PitchPilot is, like, a live speaking coach that sits quietly on your desktop.",
    "Um, it listens to your microphone and, you know, transcribes everything in real time.",
    "Basically, the overlay shows coaching cues that only you can see, so screen shares stay clean.",
    "We built the analytics engine to track pacing, filler words, and, uh, missed talking points.",
    "So, right now this demo is speaking at around one hundred and eighty words per minute.",
    "Like, the biggest risk for presenters is rambling, and our pacing engine catches that instantly.",
    "Uh, our customers are sales teams and founders who pitch on Zoom every single week.",
    "The pricing is forty nine dollars per seat per month.",
    "We also offer a twenty percent discount for annual billing.",
    "So, um, that brings us to the roadmap for next quarter.",
    "We are adding support for more languages and, uh, deeper CRM integrations.",
    "Like I mentioned before, the team has shipped weekly for two straight years.",
    "Um, let me wrap up with our ask, which is, uh, five hundred thousand dollars.",
    "So, that is the pitch, and I am happy to take questions now.",
)


class MockSTT(STTProvider):
    """Deterministic-ish simulated speaker for demo mode."""

    def __init__(self, on_transcript: TranscriptCallback) -> None:
        super().__init__(on_transcript)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._rng = random.Random(42)
        self._bytes_seen = 0

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        logger.info("Mock STT started (demo mode — simulated live speaker)")

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            for sentence in _SENTENCES:
                if self._stop_event.is_set():
                    return
                words = sentence.split()
                for i in range(1, len(words) + 1):
                    if self._stop_event.is_set():
                        return
                    interim = " ".join(words[:i])
                    is_final = i == len(words)
                    await self._on_transcript(
                        TranscriptChunk(text=interim, is_final=False)
                    )
                    await asyncio.sleep(
                        _WORD_GAP_SECONDS + self._rng.uniform(-0.04, 0.04)
                    )
                await self._on_transcript(
                    TranscriptChunk(text=sentence, is_final=True)
                )
                await asyncio.sleep(_SENTENCE_PAUSE_SECONDS)

    async def send_audio(self, pcm16: bytes) -> None:
        # Audio is ignored in demo mode, but we track volume for realism.
        self._bytes_seen += len(pcm16)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            self._task = None
