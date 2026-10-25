"""Process-wide getaddrinfo cache with stale-on-error fallback.

This machine's DNS resolver intermittently fails lookups for API hosts
(`[Errno 11002] getaddrinfo failed`) even though the same hosts resolve
seconds earlier — and Python does not cache `getaddrinfo` results, so every
connection re-rolls the dice. One failed lookup inside a live call means a
dropped STT segment or LLM answer.

This module wraps `socket.getaddrinfo` with:
- a small TTL cache (successful lookups are reused for 5 minutes), and
- stale-on-error fallback (if a refresh fails but we resolved the host
  before, the last known-good addresses are served instead of raising).

Installed once from `app.main` at import time; opt out with
`DNS_CACHE_ENABLED=false`. Purely in-process — no system settings change.
"""

import socket
import threading
import time
from collections import OrderedDict

_TTL_SECONDS = 300.0
_MAX_ENTRIES = 512

_original_getaddrinfo = socket.getaddrinfo
_lock = threading.Lock()
# (host, port, family, type, proto) → (expires_at_monotonic, getaddrinfo result)
_cache: "OrderedDict[tuple, tuple[float, list]]" = OrderedDict()

_installed = False


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Drop-in `socket.getaddrinfo` with TTL cache + stale fallback."""
    # Only cache plain forward lookups; anything exotic goes straight through.
    if (
        not _installed
        or not host
        or flags
        or family not in (0, socket.AF_INET, socket.AF_INET6)
    ):
        return _original_getaddrinfo(host, port, family, type, proto, flags)

    key = (host, port, family, type, proto)
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is not None:
            expires_at, results = entry
            _cache.move_to_end(key)
            if now < expires_at:
                return results

    try:
        results = _original_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        with _lock:
            stale = _cache.get(key)
        if stale is not None:
            # Resolver blip — serve the last known-good addresses. The entry
            # is kept (not refreshed) so the next call retries for real.
            return stale[1]
        raise

    with _lock:
        _cache[key] = (now + _TTL_SECONDS, results)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)
    return results


def install() -> None:
    """Make every socket lookup in this process go through the cache."""
    global _installed
    if _installed:
        return
    socket.getaddrinfo = _cached_getaddrinfo
    _installed = True


def cached_hosts() -> int:
    with _lock:
        return len(_cache)
