"""Groq STT: near-real-time transcription via Groq's OpenAI-compatible
`audio/transcriptions` REST endpoint (whisper-large-v3-turbo).

Groq exposes batch transcription rather than a duplex streaming WebSocket, so
this provider buffers the live 16 kHz PCM stream into rolling segments and
ships each one to the REST API as a WAV. Effective cue latency is roughly one
segment plus a single HTTP round-trip. Results are emitted as FINAL transcript
chunks only (no interims — the overlay ticker updates at each segment boundary).

The rolling-segment, silence-gating and retry behaviour lives in
`OpenAIBatchSTT`; this class only supplies Groq's endpoint and auth.
"""

from app.services.stt.base import TranscriptCallback
from app.services.stt.openai_batch import OpenAIBatchSTT

_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class GroqBatchSTT(OpenAIBatchSTT):
    """Rolling-segment transcription against Groq's REST API."""

    def __init__(
        self,
        on_transcript: TranscriptCallback,
        api_key: str,
        model: str = "whisper-large-v3-turbo",
        segment_seconds: float = 3.0,
    ) -> None:
        super().__init__(
            on_transcript,
            url=_GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            provider_label="Groq",
            model=model,
            warmup_url="https://api.groq.com/",
            segment_seconds=segment_seconds,
        )
