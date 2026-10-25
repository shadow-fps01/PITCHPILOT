"""Registry of live coaching sessions, with reconnect grace + finalization.

When a WebSocket drops mid-call, the session's coach state (WPM timeline,
filler hits, audit coverage) stays alive in this registry for a grace
period so a reconnecting client resumes the same session seamlessly. If no
client returns before the grace period expires, the session is finalized
and persisted.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from app.services.vision import ScreenVisionWorker

from app.config.settings import get_settings
from app.db.session import async_session_factory
from app.models.coaching_session import CoachingSession
from app.models.script import Script
from app.schemas.stream import SessionSummarySchema
from app.services.ai_coach import AICoach
from app.services.copilot_chat import CopilotChat, build_copilot_llm
from app.services.llm_client import get_llm_client


@dataclass(slots=True)
class LiveSessionState:
    """Everything that must survive a client reconnect."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    coach: AICoach
    script: Script | None
    started_at: datetime
    connect_count: int = field(default=0)
    anticipation: "NextTopicAnticipator | None" = None  # F3 RAG
    context_cards: int = 0  # surfaced cards this session (for the end summary)
    copilot: CopilotChat | None = None  # Cluely-style dialogue engine
    screen_worker: "ScreenVisionWorker | None" = None  # client-shared screen frames


class LiveSessionRegistry:
    def __init__(self) -> None:
        self._states: dict[uuid.UUID, LiveSessionState] = {}
        self._finalize_tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    # ── Lookup / creation ─────────────────────────────────────────────
    def get(self, session_id: uuid.UUID) -> LiveSessionState | None:
        return self._states.get(session_id)

    def get_or_create(
        self, session: CoachingSession, script: Script | None
    ) -> tuple[LiveSessionState, bool]:
        """Return the live state, creating it on first connect.

        Returns `(state, created)`; on reconnect the grace timer is cancelled.
        """
        self.cancel_finalize(session.id)
        state = self._states.get(session.id)
        if state is not None:
            state.connect_count += 1
            return state, False

        settings = get_settings()
        from app.services.anticipation import NextTopicAnticipator
        from app.services.copilot_chat import CopilotChat, build_copilot_llm
        from app.services.embedding import get_shared_embedding_client
        from app.services.vision import shared_worker

        anticipation = (
            NextTopicAnticipator(
                settings=settings,
                user_id=session.user_id,
                embedding_client=get_shared_embedding_client(),
                on_card=None,  # rebound per (re)connection by the WS handler
            )
            if settings.anticipation_enabled
            else None
        )
        vision_worker = shared_worker()
        coach = AICoach(
            settings=settings,
            target_wpm=script.target_wpm if script else settings.target_wpm_default,
            outline=script.content_outline if script else "",
            keywords=list(script.keywords) if script else [],
            llm=get_llm_client(settings),
            anticipation=anticipation,
            screen_context_provider=(
                vision_worker.latest_context if vision_worker is not None else None
            ),
        )
        copilot = (
            CopilotChat(
                settings=settings,
                outline=script.content_outline if script else "",
                keywords=list(script.keywords) if script else [],
                llm=build_copilot_llm(settings),
                screen_provider=(
                    vision_worker.latest_context if vision_worker is not None else None
                ),
                fresh_screen_provider=(
                    vision_worker.describe_now if vision_worker is not None else None
                ),
                on_message=None,  # rebound per (re)connection by the WS handler
            )
            if settings.copilot_enabled
            else None
        )
        state = LiveSessionState(
            session_id=session.id,
            user_id=session.user_id,
            coach=coach,
            script=script,
            started_at=session.started_at or datetime.now(tz=timezone.utc),
            anticipation=anticipation,
            copilot=copilot,
        )
        state.connect_count = 1
        self._states[session.id] = state
        return state, True

    # ── Finalization ─────────────────────────────────────────────────
    def schedule_finalize(self, session_id: uuid.UUID, delay: float | None = None) -> None:
        """Start (or restart) the grace timer before the session is closed out."""
        self.cancel_finalize(session_id)
        if delay is None:
            delay = get_settings().ws_reconnect_grace_seconds
        self._finalize_tasks[session_id] = asyncio.create_task(
            self._finalize_after(session_id, delay)
        )
        logger.debug(f"Session {session_id} finalize scheduled in {delay:.0f}s")

    def cancel_finalize(self, session_id: uuid.UUID) -> None:
        task = self._finalize_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()

    async def _finalize_after(self, session_id: uuid.UUID, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self.finalize(session_id)

    async def finalize(self, session_id: uuid.UUID) -> SessionSummarySchema | None:
        """Close out a live session: stop the coach and persist the end state."""
        self.cancel_finalize(session_id)
        state = self._states.pop(session_id, None)
        if state is None:
            return None

        await state.coach.aclose()
        if state.copilot is not None:
            await state.copilot.aclose()
        if state.screen_worker is not None:
            await state.screen_worker.stop()
            state.screen_worker = None
        duration_seconds = (
            int(state.coach.elapsed()) if state.coach.started_at is not None else 0
        )
        counters = state.coach.counters
        summary = SessionSummarySchema(
            total_filler_words=state.coach.analytics.filler_count,
            pacing_alerts=counters.pacing_cues,
            script_misses=counters.script_cues,
            context_cards=state.context_cards,
            event_count=(
                counters.filler_cues
                + counters.pacing_cues
                + counters.script_cues
                + state.context_cards
            ),
            duration_seconds=duration_seconds,
        )

        try:
            async with async_session_factory() as db:
                db_session = await db.get(CoachingSession, session_id)
                if db_session is not None and db_session.ended_at is None:
                    db_session.ended_at = datetime.now(tz=timezone.utc)
                    db_session.duration_seconds = duration_seconds
                    await db.commit()
        except Exception as exc:  # noqa: BLE001 — persistence must not crash finalize
            logger.warning(f"Failed to persist session end for {session_id}: {exc}")

        logger.info(
            f"Session {session_id} finalized — {duration_seconds}s, "
            f"{summary.total_filler_words} fillers, {summary.pacing_alerts} pacing alerts"
        )
        return summary

    async def finalize_user(self, user_id: uuid.UUID) -> None:
        """Close every live session owned by a user (account deletion)."""
        session_ids = [
            session_id
            for session_id, state in self._states.items()
            if state.user_id == user_id
        ]
        for session_id in session_ids:
            await self.finalize(session_id)

    def active_count(self) -> int:
        return len(self._states)


# Module-level singleton used by the stream endpoint.
registry = LiveSessionRegistry()
