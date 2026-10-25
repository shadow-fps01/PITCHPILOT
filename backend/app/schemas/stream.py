"""Typed WebSocket message protocol between the browser overlay and the backend.

Every message travelling over `/api/ws/stream/{session_id}` is one of the
models below, discriminated by the `type` literal. The frontend mirrors
these shapes in `src/types/index.ts`.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.models.session_event import EventType


# ── Server → Client ──────────────────────────────────────────────────────
class SessionStartedMessage(BaseModel):
    type: Literal["session_started"] = "session_started"
    session_id: uuid.UUID
    started_at: datetime


class TranscriptMessage(BaseModel):
    type: Literal["transcript"] = "transcript"
    text: str
    is_final: bool


class MetricsMessage(BaseModel):
    type: Literal["metrics"] = "metrics"
    wpm: float
    filler_word_count: int
    elapsed_seconds: float
    target_wpm: int
    mic_level: float = 0.0


CueSeverity = Literal["info", "warn", "alert"]


class CueMessage(BaseModel):
    type: Literal["cue"] = "cue"
    cue_type: EventType
    message: str
    severity: CueSeverity
    raw_transcript_snippet: str | None = None


class SessionEndedMessage(BaseModel):
    type: Literal["session_ended"] = "session_ended"
    duration_seconds: int
    summary: "SessionSummarySchema"


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"
    timestamp: float


# ── Copilot upgrades ─────────────────────────────────────────────────────
class SessionStatusMessage(BaseModel):
    """Sent right after session_started: which copilot subsystems are live."""

    type: Literal["status"] = "status"
    stt_provider: str
    capture_available: bool = False
    capture_active: bool = False
    vision_available: bool = False
    vision_active: bool = False
    anticipation_active: bool = False
    copilot_active: bool = False


class CaptureStatusMessage(BaseModel):
    """Native dual-audio capture lifecycle updates (F1)."""

    type: Literal["capture_status"] = "capture_status"
    active: bool
    mode: Literal["off", "mic-only", "dual"] = "off"
    mic: str | None = None
    loopback: str | None = None
    error: str | None = None


class ContextCardMessage(BaseModel):
    """A pre-fetched next-topic answer card surfaced by the RAG anticipator (F3)."""

    type: Literal["context_card"] = "context_card"
    title: str
    content: str
    score: float
    keywords: list[str] = Field(default_factory=list)


class CopilotMessage(BaseModel):
    """One message in the Cluely-style copilot dialogue feed.

    `kind="suggestion"`  → proactive ready-to-speak suggestion (call + screen aware)
    `kind="answer"`      → reply to a user question asked from the dialogue box
    `kind="status"`      → transient copilot status note (e.g. screen on/off)
    """

    type: Literal["copilot"] = "copilot"
    id: str
    kind: Literal["suggestion", "answer", "status"] = "suggestion"
    text: str
    trigger: str | None = None
    reply_to: str | None = None
    screen_used: bool = False


class CopilotDeltaMessage(BaseModel):
    """Incremental token stream while the copilot composes an answer.

    `text` is the full answer so far (not a delta), so the client can simply
    replace the streaming bubble's contents. Followed by the final
    `CopilotMessage` carrying the same `id`.
    """

    type: Literal["copilot_delta"] = "copilot_delta"
    id: str
    reply_to: str
    text: str


class DebriefMessage(BaseModel):
    """End-of-session written review (generated once the session closes)."""

    type: Literal["debrief"] = "debrief"
    summary: str
    tips: list[str] = Field(default_factory=list)
    source: str = "heuristic"  # azure | openrouter | heuristic


class SessionSummarySchema(BaseModel):
    total_filler_words: int
    pacing_alerts: int
    script_misses: int
    context_cards: int = 0
    event_count: int
    duration_seconds: int | None = None


ServerMessage = Annotated[
    Union[
        SessionStartedMessage,
        TranscriptMessage,
        MetricsMessage,
        CueMessage,
        SessionEndedMessage,
        ErrorMessage,
        PongMessage,
        SessionStatusMessage,
        CaptureStatusMessage,
        ContextCardMessage,
        CopilotMessage,
        CopilotDeltaMessage,
        DebriefMessage,
    ],
    Field(discriminator="type"),
]


# ── Client → Server (JSON; binary audio frames are sent alongside these) ─
class ClientPing(BaseModel):
    type: Literal["ping"] = "ping"
    timestamp: float


class ClientStop(BaseModel):
    type: Literal["stop"] = "stop"


class CaptureCommand(BaseModel):
    """Ask the backend to start/stop native dual-audio capture (F1)."""

    type: Literal["capture"] = "capture"
    action: Literal["start", "stop"] = "stop"


class ClientAsk(BaseModel):
    """A question typed into the copilot dialogue box (Cluely-style)."""

    type: Literal["ask"] = "ask"
    id: str
    text: str


class ScreenCommand(BaseModel):
    """Toggle screen vision for the live copilot (F5)."""

    type: Literal["screen"] = "screen"
    action: Literal["start", "stop"] = "start"


class ClientScreenFrame(BaseModel):
    """One browser-shared screen frame (base64 JPEG), sent sparingly.

    The frame is analyzed for the live copilot and then discarded; it is
    never written to the database or logs.
    """

    type: Literal["screen_frame"] = "screen_frame"
    image: str = Field(min_length=16, max_length=4_000_000)


class ClientTune(BaseModel):
    """Per-session coaching sensitivity (cue frequency / thresholds)."""

    type: Literal["tune"] = "tune"
    sensitivity: Literal["calm", "balanced", "coach"] = "balanced"


ClientMessage = Annotated[
    Union[
        ClientPing,
        ClientStop,
        CaptureCommand,
        ClientAsk,
        ScreenCommand,
        ClientScreenFrame,
        ClientTune,
    ],
    Field(discriminator="type"),
]


SessionEndedMessage.model_rebuild()
