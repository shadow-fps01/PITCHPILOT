"""Coaching-session endpoints: create, list, detail (with events) and end."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, require_verified_email
from app.config.settings import get_settings
from app.db.session import get_db
from app.models.coaching_session import CoachingSession
from app.models.script import Script
from app.models.session_event import EventType, SessionEvent
from app.models.user import User
from app.schemas.session import (
    SessionCreate,
    SessionDetail,
    SessionEndResponse,
    SessionEventPublic,
    SessionListResponse,
    SessionPublic,
    SessionSummary,
)
from app.services.billing import assert_session_quota
from app.services.session_registry import registry

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _build_summary(
    events: list[SessionEvent], session: CoachingSession
) -> SessionSummary:
    counts = {event_type: 0 for event_type in EventType}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    duration = session.duration_seconds
    if duration is None and session.started_at is not None:
        end = session.ended_at or datetime.now(tz=timezone.utc)
        duration = int((end - session.started_at).total_seconds())
    return SessionSummary(
        total_filler_words=counts.get(EventType.FILLER_WORD, 0),
        pacing_alerts=counts.get(EventType.PACING_ALERT, 0),
        script_misses=counts.get(EventType.SCRIPT_MISS, 0),
        context_cards=counts.get(EventType.CONTEXT_CARD, 0),
        event_count=len(events),
        duration_seconds=duration,
    )


@router.post("", response_model=SessionPublic, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate,
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
) -> CoachingSession:
    # Plus-only quota: free members get 3 coaching sessions per month (402 → paywall)
    await assert_session_quota(db, current_user, get_settings())

    script_id: uuid.UUID | None = None
    if payload.script_id is not None:
        script = await db.scalar(
            select(Script).where(
                Script.id == payload.script_id, Script.user_id == current_user.id
            )
        )
        if script is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Script not found"
            )
        script_id = script.id

    # started_at is stamped here (not at WS connect) to keep the live
    # stream handshake down to two DB round-trips.
    coaching_session = CoachingSession(
        user_id=current_user.id,
        script_id=script_id,
        started_at=datetime.now(tz=timezone.utc),
    )
    db.add(coaching_session)
    await db.commit()
    await db.refresh(coaching_session)
    return coaching_session


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    limit = max(1, min(limit, 200))
    result = await db.scalars(
        select(CoachingSession)
        .where(CoachingSession.user_id == current_user.id)
        .order_by(desc(CoachingSession.started_at))
        .limit(limit)
    )
    return SessionListResponse(sessions=[SessionPublic.model_validate(s) for s in result])


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionDetail:
    coaching_session = await db.scalar(
        select(CoachingSession)
        .where(CoachingSession.id == session_id, CoachingSession.user_id == current_user.id)
        .options(selectinload(CoachingSession.events))
    )
    if coaching_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    events = sorted(coaching_session.events, key=lambda e: (e.timestamp, e.id))
    summary = _build_summary(events, coaching_session)
    return SessionDetail(
        **SessionPublic.model_validate(coaching_session).model_dump(),
        events=[SessionEventPublic.model_validate(e) for e in events],
        summary=summary,
    )


@router.post("/{session_id}/end", response_model=SessionEndResponse)
async def end_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionEndResponse:
    """Force-close a session (fallback when the client vanished mid-call)."""
    coaching_session = await db.scalar(
        select(CoachingSession)
        .where(CoachingSession.id == session_id, CoachingSession.user_id == current_user.id)
        .options(selectinload(CoachingSession.events))
    )
    if coaching_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if coaching_session.ended_at is None:
        # Finalize live state if this session is still in the registry.
        summary = await registry.finalize(session_id)
        if summary is None:
            await db.refresh(coaching_session)
            if coaching_session.ended_at is None:
                end = datetime.now(tz=timezone.utc)
                started = coaching_session.started_at or end
                coaching_session.ended_at = end
                coaching_session.duration_seconds = int((end - started).total_seconds())
                await db.commit()
                await db.refresh(coaching_session)
        else:
            await db.refresh(coaching_session)
    events = sorted(coaching_session.events, key=lambda e: (e.timestamp, e.id))
    return SessionEndResponse(
        session=SessionPublic.model_validate(coaching_session),
        summary=_build_summary(events, coaching_session),
    )
