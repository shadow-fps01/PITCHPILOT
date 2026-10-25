"""Live verification of the Azure OpenAI STT provider with a speech WAV.

Prerequisites:
1. In the Azure OpenAI resource, create an audio deployment (Whisper or
   gpt-4o-transcribe) and note its **deployment name** (not the model name).
2. Set in `backend/.env` (or the environment):
       AZURE_OPENAI_API_KEY=...
       AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
       AZURE_OPENAI_STT_DEPLOYMENT=<deployment-name>
       AZURE_OPENAI_STT_API_VERSION=2024-06-01
3. Generate a 16 kHz / 16-bit / mono speech WAV (PowerShell):
       Add-Type -AssemblyName System.Speech
       $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
       $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
         16000,
         [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
         [System.Speech.AudioFormat.AudioChannel]::Mono)
       $s.SetOutputToWaveFile("$env:TEMP\\azure_test.wav", $f)
       $s.Speak("Hello, this is a test of the PitchPilot transcription service.")
       $s.Dispose()

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\azure_stt_test.py [wav path]
"""

import asyncio
import sys
import wave

from app.config.settings import get_settings
from app.services.stt.azure import AzureBatchSTT
from app.services.stt.base import TranscriptChunk

DEFAULT_WAV = r"C:\Users\USER\AppData\Local\Temp\opencode\azure_test.wav"


async def main() -> int:
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("AZURE_OPENAI_API_KEY", settings.azure_openai_api_key),
            ("AZURE_OPENAI_ENDPOINT", settings.azure_openai_endpoint),
            ("AZURE_OPENAI_STT_DEPLOYMENT", settings.azure_openai_stt_deployment),
        )
        if not value.strip()
    ]
    if missing:
        print(f"Missing Azure OpenAI STT config: {', '.join(missing)}")
        print("Create an audio deployment in Azure, then set its name in backend/.env.")
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
                f"{wav.getframerate()} Hz / {wav.getsampwidth()*8}-bit / "
                f"{wav.getnchannels()}ch"
            )
            return 2
        pcm = wav.readframes(wav.getnframes())

    print(
        f"Azure deployment: {settings.azure_openai_stt_deployment} "
        f"(API {settings.azure_openai_stt_api_version})"
    )
    print(f"Loaded {len(pcm):,} bytes of PCM ({len(pcm) / 32000:.1f}s) from {wav_path}")

    chunks: list[TranscriptChunk] = []

    async def on_transcript(chunk: TranscriptChunk) -> None:
        chunks.append(chunk)
        print(f"[azure] {chunk.text}")

    provider = AzureBatchSTT(
        on_transcript,
        api_key=settings.azure_openai_api_key,
        endpoint=settings.azure_openai_endpoint,
        deployment=settings.azure_openai_stt_deployment,
        api_version=settings.azure_openai_stt_api_version,
        segment_seconds=settings.azure_openai_stt_segment_seconds,
    )
    await provider.start()

    # Feed the audio in 100 ms frames; the provider segments it internally.
    frame = 3200
    for offset in range(0, len(pcm), frame):
        await provider.send_audio(pcm[offset : offset + frame])
        await asyncio.sleep(0.02)

    drain_seconds = settings.azure_openai_stt_segment_seconds * 2 + 4
    print(f"Waiting {drain_seconds:.0f}s for transcription segments…")
    await asyncio.sleep(drain_seconds)
    await provider.stop()

    print(f"\nReceived {len(chunks)} transcript segment(s).")
    if chunks:
        print("AZURE STT TEST PASSED [OK]")
        return 0
    print(
        "No transcripts returned — check the deployment name, API version, "
        "model availability in the resource region, and network access."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
