"""Dependency-free in-process rate limiting for public endpoints.

Fixed-window counters keyed by (scope, client IP). This is intentionally
simple: PitchPilot already runs as a single process (live sessions keep state
in memory), so an in-process limiter is sufficient. For a multi-worker or
multi-node deployment, back this with Redis instead.
"""

import ipaddress
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status

from app.config.settings import get_settings


class _FixedWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)

    def too_many(self, key: tuple[str, str], limit: int, window: float) -> float:
        """Record a hit; return seconds-to-wait when the limit is exceeded."""
        now = time.monotonic()
        cutoff = now - window
        hits = [t for t in self._hits.get(key, []) if t >= cutoff]
        if len(hits) >= limit:
            self._hits[key] = hits
            return max(1.0, window - (now - hits[0]))
        hits.append(now)
        self._hits[key] = hits
        # Keep the store from growing without bound on a busy host.
        if len(self._hits) > 20_000:
            self._hits = defaultdict(
                list, {k: v for k, v in self._hits.items() if v and v[-1] >= cutoff}
            )
        return 0.0


_limiter = _FixedWindowLimiter()


def _client_key(request: Request) -> str:
    """Client IP, trusting the rightmost X-Forwarded-For entry.

    The leftmost entry can be supplied by the client; the nearest trusted
    proxy appends the real address on the right. Only read the header when
    the request came from a private/loopback peer or when the deployment
    explicitly opts in via TRUST_PROXY_HEADERS (API behind a trusted proxy).
    """
    peer = request.client.host if request.client is not None else ""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        settings = get_settings()
        if settings.trust_proxy_headers or _is_private(peer):
            candidate = forwarded.split(",")[-1].strip()
            if candidate:
                return candidate
    return peer or "unknown"


def _is_private(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


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
        retry_after = _limiter.too_many((scope, _client_key(request)), limit, 60.0)
        if retry_after > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — please slow down and try again.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return dependency
