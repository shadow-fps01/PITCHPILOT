"""Live verification of the Groq STT provider using a synthetic speech WAV.

Feeds real speech audio (16 kHz / 16-bit / mono) through GroqBatchSTT and
prints every transcript segment Groq returns. Requires GROQ_API_KEY in
backend/.env (or the environment).

Generate the default test WAV first (PowerShell):
    Add-Type -AssemblyName System.Speech;
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer;
    $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);
    $s.SetOutputToWaveFile("$env:TEMP\\groq_test.wav", $f); $s.Speak("..."); $s.Dispose()

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\groq_stt_test.py [wav path]
"""

import asyncio
import sys
import wave

from app.config.settings import get_settings
from app.services.stt.base import TranscriptChunk
from app.services.stt.groq import GroqBatchSTT

DEFAULT_WAV = r"C:\Users\USER\AppData\Local\Temp\opencode\groq_test.wav"


async def main() -> int:
    settings = get_settings()
    if not settings.groq_api_key:
        print("GROQ_API_KEY is not set.")
        print("Add it to backend/.env (GROQ_API_KEY=gsk_...) and run this again.")
        return 2

    wav_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WAV
    with wave.open(wav_path, "rb") as wav:
        if (
            wav.getframerate() != 16000
            or wav.getnchannels() != 1
            or wav.getsampwidth() != 2
        ):
            print(
                f"WAV must be 16 kHz / 16-bit / mono, got "
                f"{wav.getframerate()} Hz / {wav.getsampwidth()*8}-bit / {wav.getnchannels()}ch"
            )
            return 2
        pcm = wav.readframes(wav.getnframes())
    print(f"Loaded {len(pcm):,} bytes of PCM ({len(pcm) / 32000:.1f}s) from {wav_path}")

    chunks: list[TranscriptChunk] = []

    async def on_transcript(chunk: TranscriptChunk) -> None:
        chunks.append(chunk)
        print(f"[groq] {chunk.text}")

    provider = GroqBatchSTT(
        on_transcript,
        api_key=settings.groq_api_key,
        model=settings.groq_stt_model,
        segment_seconds=settings.groq_stt_segment_seconds,
    )
    await provider.start()

    # Feed the audio in 100 ms frames (faster than real time; the provider's
    # internal timer segments it for transcription).
    frame = 3200
    for offset in range(0, len(pcm), frame):
        await provider.send_audio(pcm[offset : offset + frame])
        await asyncio.sleep(0.02)

    # Let the flush cycles drain the buffered segments.
    drain_seconds = settings.groq_stt_segment_seconds * 2 + 4
    print(f"Waiting {drain_seconds:.0f}s for transcription segments…")
    await asyncio.sleep(drain_seconds)
    await provider.stop()

    print(f"\nReceived {len(chunks)} transcript segment(s).")
    if chunks:
        joined = " ".join(chunk.text for chunk in chunks).lower()
        filler_hits = [w for w in ("um", "uh", "like", "so") if w in joined]
        print(f"Filler words detected in transcript: {filler_hits}")
        print("GROQ STT TEST PASSED [OK]")
        return 0
    print("No transcripts returned — check the API key, model name and network.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
