"""STT provider factory: picks the configured transcription backend."""

from loguru import logger

from app.config.settings import Settings, get_settings
from app.services.stt.azure import AzureBatchSTT
from app.services.stt.base import STTProvider, TranscriptCallback, TranscriptChunk
from app.services.stt.deepgram import DeepgramLiveSTT
from app.services.stt.groq import GroqBatchSTT
from app.services.stt.mock import MockSTT
from app.services.stt.openai_realtime import OpenAIRealtimeSTT


def azure_stt_configured(settings: Settings) -> bool:
    """True when the shared Azure key/endpoint and an STT deployment are set."""
    return bool(
        settings.azure_openai_api_key.strip()
        and settings.azure_openai_endpoint.strip()
        and settings.azure_openai_stt_deployment.strip()
    )


def resolve_stt_choice(settings: Settings) -> str:
    """Decide which provider name is in effect.

    Explicit choices win (`azure` | `deepgram` | `openai` | `groq` | `mock`);
    otherwise `auto` picks the first provider with a configured key —
    Deepgram → Groq → Azure OpenAI → OpenAI Realtime — and falls back to the
    mock demo speaker only when NO transcription key is available.
    """
    choice = settings.stt_provider.strip().lower()
    if choice in ("azure", "deepgram", "openai", "groq", "mock"):
        return choice
    if settings.deepgram_api_key:
        return "deepgram"
    if settings.groq_api_key:
        return "groq"
    if azure_stt_configured(settings):
        return "azure"
    if settings.openai_api_key:
        return "openai"
    return "mock"


def build_stt_provider(
    on_transcript: TranscriptCallback,
) -> tuple[STTProvider, str]:
    """Return `(provider, name)` for the resolved choice (see above)."""
    settings = get_settings()
    name = resolve_stt_choice(settings)

    if name == "azure":
        if not azure_stt_configured(settings):
            logger.warning(
                "Azure OpenAI STT selected but AZURE_OPENAI_API_KEY, "
                "AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_STT_DEPLOYMENT is "
                "missing — using the mock provider for this session"
            )
            return MockSTT(on_transcript), "mock"
        return (
            AzureBatchSTT(
                on_transcript,
                api_key=settings.azure_openai_api_key,
                endpoint=settings.azure_openai_endpoint,
                deployment=settings.azure_openai_stt_deployment,
                api_version=(
                    settings.azure_openai_stt_api_version.strip()
                    or settings.azure_openai_api_version
                ),
                segment_seconds=settings.azure_openai_stt_segment_seconds,
            ),
            name,
        )
    if name == "deepgram":
        return DeepgramLiveSTT(on_transcript, settings.deepgram_api_key), name
    if name == "openai":
        return OpenAIRealtimeSTT(on_transcript, settings.openai_api_key), name
    if name == "groq":
        return (
            GroqBatchSTT(
                on_transcript,
                api_key=settings.groq_api_key,
                model=settings.groq_stt_model,
                segment_seconds=settings.groq_stt_segment_seconds,
            ),
            name,
        )
    return MockSTT(on_transcript), name


__all__ = [
    "STTProvider",
    "TranscriptCallback",
    "TranscriptChunk",
    "AzureBatchSTT",
    "build_stt_provider",
    "azure_stt_configured",
    "resolve_stt_choice",
    "DeepgramLiveSTT",
    "GroqBatchSTT",
    "MockSTT",
    "OpenAIRealtimeSTT",
]
