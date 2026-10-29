"""Core service-layer exports."""

from app.services.ai_coach import AICoach
from app.services.audio_processor import TranscriptAnalytics
from app.services.llm_client import (
    AIUnavailable,
    LLMClient,
    ScriptAuditRequest,
    ScriptAuditResult,
    get_llm_client,
)
from app.services.security import (
    create_access_token,
    create_password_reset_token,
    decode_access_token,
    decode_access_token_claims,
    decode_password_reset_token,
    hash_password,
    verify_password,
)
from app.services.session_registry import LiveSessionRegistry, LiveSessionState, registry
from app.services.stt import (
    DeepgramLiveSTT,
    GroqBatchSTT,
    MockSTT,
    OpenAIRealtimeSTT,
    STTProvider,
    TranscriptCallback,
    TranscriptChunk,
    build_stt_provider,
    resolve_stt_choice,
)

__all__ = [
    "AICoach",
    "DeepgramLiveSTT",
    "GroqBatchSTT",
    "AIUnavailable",
    "LLMClient",
    "LiveSessionRegistry",
    "LiveSessionState",
    "MockSTT",
    "OpenAIRealtimeSTT",
    "STTProvider",
    "ScriptAuditRequest",
    "ScriptAuditResult",
    "TranscriptAnalytics",
    "TranscriptCallback",
    "TranscriptChunk",
    "build_stt_provider",
    "resolve_stt_choice",
    "create_access_token",
    "create_password_reset_token",
    "decode_access_token",
    "decode_access_token_claims",
    "decode_password_reset_token",
    "get_llm_client",
    "hash_password",
    "registry",
    "verify_password",
]
