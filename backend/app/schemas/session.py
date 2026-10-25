"""Coaching session + event request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.session_event import EventType


class SessionCreate(BaseModel):
    script_id: uuid.UUID | None = None


class SessionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    script_id: uuid.UUID | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None


class SessionEventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: uuid.UUID
    timestamp: float
    event_type: EventType
    message: str
    raw_transcript_snippet: str | None


class SessionSummary(BaseModel):
    total_filler_words: int
    pacing_alerts: int
    script_misses: int
    context_cards: int = 0
    event_count: int
    duration_seconds: int | None = Field(default=None)


class SessionDetail(SessionPublic):
    events: list[SessionEventPublic]
    summary: SessionSummary


class SessionListResponse(BaseModel):
    sessions: list[SessionPublic]


class SessionEndResponse(BaseModel):
    session: SessionPublic
    summary: SessionSummary
