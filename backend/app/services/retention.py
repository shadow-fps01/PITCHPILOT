"""Scheduled data-retention and abandoned-session cleanup.

Two independent jobs run on the same low-frequency loop:

1. **Close abandoned sessions** — a session row is created before the browser
   opens its WebSocket. If the user closes the tab (or the process restarts)
   before a graceful stop, the row can stay open forever and keep showing as
   "Live". Sessions with no in-process state that are older than
   `STALE_SESSION_HOURS` are closed at their last persisted event.
2. **Purge expired transcript excerpts** — coaching events can contain a
   short transcript snippet. Once a session has been closed for
   `DATA_RETENTION_DAYS`, those events are deleted. The session summary
   (duration, counts) is kept so the account's history still works.

The loop is opt-in via `RETENTION_CLEANUP_ENABLED` so a development instance
pointed at a shared database cannot mutate production data by accident.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, func, select

from app.config.settings import Settings
from app.db.session import async_session_factory
from app.models.coaching_session import CoachingSession
from app.models.session_event import SessionEvent
from app.services.session_registry import registry


async def close_stale_sessions(settings: Settings) -> int:
    """Close sessions that were abandoned before the WebSocket connected."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(
        hours=max(1, settings.stale_session_hours)
    )
    closed = 0
    try:
        async with async_session_factory() as db:
            sessions = list(
                await db.scalars(
                    select(CoachingSession).where(
                        CoachingSession.ended_at.is_(None),
                        CoachingSession.started_at.is_not(None),
                        CoachingSession.started_at < cutoff,
                    )
                )
            )
            now = datetime.now(tz=timezone.utc)
            for session in sessions:
                # A live in-process session may legitimately be long. Leave it
                # alone until the process finalizes it or a restart clears it.
                if registry.get(session.id) is not None:
                    continue
                last_event = await db.scalar(
                    select(func.max(SessionEvent.timestamp)).where(
                        SessionEvent.session_id == session.id
                    )
                )
                duration = max(0, int(last_event or 0))
                started = session.started_at or cutoff
                ended = started + timedelta(seconds=duration)
                session.ended_at = min(ended, now)
                session.duration_seconds = duration
                closed += 1
            if closed:
                await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — cleanup must never stop the API
        logger.warning(f"Stale-session cleanup failed: {exc}")
        return 0
    if closed:
        logger.info(f"Closed {closed} abandoned coaching session(s)")
    return closed


async def purge_expired_session_events(settings: Settings) -> int:
    """Delete transcript-bearing events for sessions past the retention window."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(
        days=max(1, settings.data_retention_days)
    )
    try:
        async with async_session_factory() as db:
            expired_sessions = select(CoachingSession.id).where(
                CoachingSession.ended_at.is_not(None),
                CoachingSession.ended_at < cutoff,
            )
            result = await db.execute(
                delete(SessionEvent).where(
                    SessionEvent.session_id.in_(expired_sessions)
                )
            )
            await db.commit()
            deleted = int(result.rowcount or 0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — cleanup must never stop the API
        logger.warning(f"Retention cleanup failed: {exc}")
        return 0
    if deleted:
        logger.info(
            f"Deleted {deleted} expired coaching event(s) "
            f"(retention: {settings.data_retention_days} days)"
        )
    return deleted


async def run_cleanup(settings: Settings) -> tuple[int, int]:
    """Run both cleanup jobs once; returns `(sessions_closed, events_deleted)`."""
    closed = await close_stale_sessions(settings)
    deleted = await purge_expired_session_events(settings)
    return closed, deleted


async def cleanup_loop(settings: Settings) -> None:
    """Run cleanup now, then once per configured interval until cancelled."""
    interval = max(1.0, settings.retention_cleanup_interval_hours) * 3600.0
    while True:
        try:
            await run_cleanup(settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — defensive; loop must survive
            logger.warning(f"Retention loop iteration failed: {exc}")
        await asyncio.sleep(interval)
