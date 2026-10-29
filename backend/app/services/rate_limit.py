"""Shared rate limiting, login lockouts and daily AI budgets.

The primary store is a Postgres table (`rate_limit_buckets`) so limits hold
across API instances and restarts. A small in-process store is used only when
the database is temporarily unreachable, so a DB incident degrades rather than
silently disables abuse protection.

Client IPs come from the rightmost `X-Forwarded-For` entry (the one appended by
the nearest trusted proxy). The raw leftmost entry is never trusted.
"""

import hashlib
import ipaddress
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from loguru import logger
from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config.settings import get_settings
from app.db.session import async_session_factory
from app.models.rate_limit import RateLimitBucket


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of a counter hit."""

    allowed: bool
    retry_after: float
    count: int
    locked: bool = False


class _MemoryStore:
    """Per-process fallback; never the primary production store."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[float, int, float | None]] = {}

    def hit(
        self,
        key: str,
        limit: int,
        window_seconds: float,
        *,
        lock_after: int = 0,
        lock_seconds: float = 0.0,
        lock_max_seconds: float | None = None,
    ) -> RateLimitResult:
        now = time.monotonic()
        start, count, locked_until = self._rows.get(key, (now, 0, None))
        if now - start >= window_seconds:
            start, count, locked_until = now, 0, None
        count += 1

        if lock_after and count >= lock_after:
            exponent = min(max(0, count - lock_after), 6)
            duration = min(
                lock_seconds * (2**exponent),
                lock_max_seconds if lock_max_seconds is not None else lock_seconds,
            )
            candidate = now + duration
            if locked_until is None or locked_until < candidate:
                locked_until = candidate

        self._rows[key] = (start, count, locked_until)

        if locked_until is not None:
            remaining = locked_until - now
            if remaining > 0:
                return RateLimitResult(False, remaining, count, True)
        if count > limit:
            return RateLimitResult(
                False, max(1.0, window_seconds - (now - start)), count
            )
        return RateLimitResult(True, 0.0, count)

    def retry_after(self, key: str) -> float:
        now = time.monotonic()
        row = self._rows.get(key)
        if row is None or row[2] is None:
            return 0.0
        return max(0.0, row[2] - now)

    def clear(self, key: str) -> None:
        self._rows.pop(key, None)

    def purge(self) -> None:
        now = time.monotonic()
        for key, (start, _, locked_until) in list(self._rows.items()):
            if locked_until is not None and locked_until > now:
                continue
            if now - start > 2 * 24 * 3600:
                self._rows.pop(key, None)


_memory = _MemoryStore()
_db_failure_until = 0.0
_ops_since_cleanup = 0
_DB_RETRY_SECONDS = 60.0


def _db_enabled() -> bool:
    return (
        get_settings().rate_limit_backend.strip().lower() != "memory"
        and time.monotonic() >= _db_failure_until
    )


def _mark_db_failure(exc: Exception) -> None:
    global _db_failure_until
    _db_failure_until = time.monotonic() + _DB_RETRY_SECONDS
    logger.warning(
        f"Shared rate-limit store unavailable ({exc}); using per-process "
        "fallback for up to 60s"
    )


def _window_start(now: datetime, window_seconds: float) -> datetime:
    """Epoch-aligned fixed window, so every instance agrees on the boundary."""
    remainder = now.timestamp() % window_seconds
    return now - timedelta(seconds=remainder)


async def hit(
    key: str,
    limit: int,
    window_seconds: float,
    *,
    lock_after: int = 0,
    lock_seconds: float = 0.0,
    lock_max_seconds: float | None = None,
) -> RateLimitResult:
    """Increment `key` and report whether the caller is still within budget."""
    global _ops_since_cleanup

    if not _db_enabled():
        return _memory.hit(
            key,
            limit,
            window_seconds,
            lock_after=lock_after,
            lock_seconds=lock_seconds,
            lock_max_seconds=lock_max_seconds,
        )

    now = datetime.now(tz=timezone.utc)
    window_start = _window_start(now, window_seconds)
    try:
        async with async_session_factory() as db:
            statement = (
                pg_insert(RateLimitBucket)
                .values(
                    key=key,
                    count=1,
                    window_start=window_start,
                    locked_until=None,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[RateLimitBucket.key],
                    set_={
                        "count": case(
                            (
                                RateLimitBucket.window_start < window_start,
                                1,
                            ),
                            else_=RateLimitBucket.count + 1,
                        ),
                        "window_start": case(
                            (
                                RateLimitBucket.window_start < window_start,
                                window_start,
                            ),
                            else_=RateLimitBucket.window_start,
                        ),
                        "updated_at": now,
                    },
                )
                .returning(
                    RateLimitBucket.count,
                    RateLimitBucket.window_start,
                    RateLimitBucket.locked_until,
                )
            )
            row = (await db.execute(statement)).one()
            count = int(row[0])
            stored_window_start = row[1]
            locked_until = row[2]

            if lock_after and count >= lock_after:
                exponent = min(max(0, count - lock_after), 6)
                duration = min(
                    lock_seconds * (2**exponent),
                    lock_max_seconds
                    if lock_max_seconds is not None
                    else lock_seconds,
                )
                candidate = now + timedelta(seconds=duration)
                if locked_until is None or locked_until < candidate:
                    await db.execute(
                        update(RateLimitBucket)
                        .where(RateLimitBucket.key == key)
                        .values(locked_until=candidate, updated_at=now)
                    )
                    locked_until = candidate

            await db.commit()

        if locked_until is not None:
            remaining = (locked_until - now).total_seconds()
            if remaining > 0:
                return RateLimitResult(False, remaining, count, True)
        if count > limit:
            window_end = stored_window_start + timedelta(seconds=window_seconds)
            return RateLimitResult(
                False,
                max(1.0, (window_end - now).total_seconds()),
                count,
            )
        _ops_since_cleanup += 1
        if _ops_since_cleanup >= 1000:
            _ops_since_cleanup = 0
            await cleanup_rate_limits()
        return RateLimitResult(True, 0.0, count)
    except Exception as exc:  # noqa: BLE001  -  DB incident must not disable limits
        _mark_db_failure(exc)
        return _memory.hit(
            key,
            limit,
            window_seconds,
            lock_after=lock_after,
            lock_seconds=lock_seconds,
            lock_max_seconds=lock_max_seconds,
        )


async def retry_after(key: str) -> float:
    """Seconds until a login lockout expires (0 when not locked)."""
    if not _db_enabled():
        return _memory.retry_after(key)
    try:
        async with async_session_factory() as db:
            locked_until = await db.scalar(
                select(RateLimitBucket.locked_until).where(
                    RateLimitBucket.key == key
                )
            )
    except Exception as exc:  # noqa: BLE001
        _mark_db_failure(exc)
        return _memory.retry_after(key)
    if locked_until is None:
        return 0.0
    return max(
        0.0, (locked_until - datetime.now(tz=timezone.utc)).total_seconds()
    )


async def clear(key: str) -> None:
    """Reset a counter (successful login, etc.)."""
    if not _db_enabled():
        _memory.clear(key)
        return
    try:
        async with async_session_factory() as db:
            await db.execute(delete(RateLimitBucket).where(RateLimitBucket.key == key))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        _mark_db_failure(exc)
        _memory.clear(key)


async def cleanup_rate_limits() -> None:
    """Delete expired generic buckets and stale lockout rows."""
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=2)
    _memory.purge()
    if not _db_enabled():
        return
    try:
        async with async_session_factory() as db:
            await db.execute(
                delete(RateLimitBucket).where(
                    RateLimitBucket.window_start < cutoff,
                    or_(
                        RateLimitBucket.locked_until.is_(None),
                        RateLimitBucket.locked_until < now,
                    ),
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        _mark_db_failure(exc)


def _valid_ip(value: str) -> str | None:
    candidate = value.strip()
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def client_ip(request: Request) -> str:
    """Client IP, trusting only the rightmost X-Forwarded-For entry."""
    peer = request.client.host if request.client is not None else ""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        settings = get_settings()
        if settings.trust_proxy_headers or _is_private(peer):
            candidate = _valid_ip(forwarded.split(",")[-1])
            if candidate is not None:
                return candidate
    return peer or "unknown"


def _is_private(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def hash_email(email: str) -> str:
    """Stable, non-reversible key for per-email counters."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def login_failure_key(email: str) -> str:
    return f"login:email:{hash_email(email)}"


async def record_login_failure(key: str) -> RateLimitResult:
    """Count a failed login and apply exponential lockout after the threshold."""
    settings = get_settings()
    return await hit(
        key,
        settings.login_max_failures,
        settings.login_failure_window_seconds,
        lock_after=settings.login_max_failures,
        lock_seconds=settings.login_lockout_seconds,
        lock_max_seconds=settings.login_lockout_max_seconds,
    )


async def consume_daily_budget(
    kind: str, user_id: object, limit: int
) -> RateLimitResult:
    """Count one paid-AI action against a user's UTC-day budget."""
    if limit <= 0:
        return RateLimitResult(True, 0.0, 0)
    day = datetime.now(tz=timezone.utc).date().isoformat()
    return await hit(f"budget:{kind}:{user_id}:{day}", limit, 86_400)


def rate_limit(scope: str):
    """FastAPI dependency factory enforcing the per-IP budget for `scope`."""

    async def dependency(request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        limit = (
            settings.auth_rate_limit_per_minute
            if scope == "auth"
            else settings.rate_limit_per_minute
        )
        result = await hit(f"rate:{scope}:{client_ip(request)}", limit, 60.0)
        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests  -  please slow down and try again.",
                headers={"Retry-After": str(int(result.retry_after) + 1)},
            )

    return dependency
