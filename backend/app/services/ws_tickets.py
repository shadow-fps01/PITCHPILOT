"""Short-lived, user-bound WebSocket tickets.

Browsers cannot set headers on a WebSocket handshake, so the credential has to
travel in the query string. To keep a long-lived JWT out of URLs (and server
access logs), clients fetch a short-lived ticket first.

Tickets are reusable within their TTL so client reconnects keep working, and
are revoked on password reset and account deletion. In-process — consistent
with the single-worker deployment model.
"""

import secrets
import time

from app.config.settings import get_settings


class WsTicketStore:
    def __init__(self) -> None:
        self._tickets: dict[str, tuple[str, float]] = {}

    def issue(self, user_id: str) -> tuple[str, int]:
        settings = get_settings()
        ttl = max(30, settings.ws_ticket_ttl_seconds)
        self._purge()
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (user_id, time.monotonic() + ttl)
        return ticket, ttl

    def resolve(self, ticket: str) -> str | None:
        """Return the user id for a live ticket, or None if invalid/expired."""
        entry = self._tickets.get(ticket)
        if entry is None:
            return None
        user_id, expires_at = entry
        if time.monotonic() > expires_at:
            self._tickets.pop(ticket, None)
            return None
        return user_id

    def revoke(self, ticket: str) -> None:
        self._tickets.pop(ticket, None)

    def revoke_user(self, user_id: str) -> None:
        """Invalidate every outstanding ticket for a user (reset/delete)."""
        for ticket, (owner, _) in list(self._tickets.items()):
            if owner == user_id:
                self._tickets.pop(ticket, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for ticket in [t for t, (_, exp) in self._tickets.items() if exp <= now]:
            self._tickets.pop(ticket, None)


ws_tickets = WsTicketStore()
