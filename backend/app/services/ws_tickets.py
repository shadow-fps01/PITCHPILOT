"""Single-use, session-bound WebSocket tickets.

Browsers cannot set headers on a WebSocket handshake, so the credential has to
travel in the query string. Clients first mint a short-lived ticket for one
specific session. The database stores only the ticket hash; resolving a ticket
deletes it atomically, so it cannot be replayed or used against another session.

The shared table also makes tickets work across API instances and restarts.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete

from app.config.settings import get_settings
from app.db.session import async_session_factory
from app.models.ws_ticket import WsTicket


def _hash_ticket(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


class WsTicketStore:
    async def issue(self, user_id: str, session_id: str) -> tuple[str, int]:
        """Mint a ticket for one user+session; returns `(ticket, ttl_seconds)`."""
        settings = get_settings()
        ttl = max(30, min(settings.ws_ticket_ttl_seconds, 300))
        ticket = secrets.token_urlsafe(32)
        now = datetime.now(tz=timezone.utc)
        async with async_session_factory() as db:
            # Opportunistic cleanup; the expires_at index keeps this cheap.
            await db.execute(delete(WsTicket).where(WsTicket.expires_at <= now))
            db.add(
                WsTicket(
                    ticket_hash=_hash_ticket(ticket),
                    user_id=uuid.UUID(user_id),
                    session_id=uuid.UUID(session_id),
                    expires_at=now + timedelta(seconds=ttl),
                )
            )
            await db.commit()
        return ticket, ttl

    async def resolve(self, ticket: str, session_id: uuid.UUID) -> str | None:
        """Consume a ticket and return its user id when valid for `session_id`."""
        if not ticket:
            return None
        now = datetime.now(tz=timezone.utc)
        async with async_session_factory() as db:
            result = await db.execute(
                delete(WsTicket)
                .where(
                    WsTicket.ticket_hash == _hash_ticket(ticket),
                    WsTicket.session_id == session_id,
                    WsTicket.expires_at > now,
                )
                .returning(WsTicket.user_id)
            )
            row = result.first()
            await db.commit()
        if row is None:
            return None
        return str(row[0])

    async def revoke(self, ticket: str) -> None:
        async with async_session_factory() as db:
            await db.execute(
                delete(WsTicket).where(WsTicket.ticket_hash == _hash_ticket(ticket))
            )
            await db.commit()

    async def revoke_user(self, user_id: str) -> None:
        """Invalidate every outstanding ticket for a user (logout/reset/delete)."""
        try:
            async with async_session_factory() as db:
                await db.execute(
                    delete(WsTicket).where(WsTicket.user_id == uuid.UUID(user_id))
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001  -  token_version still revokes access
            logger.warning(f"Failed to revoke WebSocket tickets for a user: {exc}")


ws_tickets = WsTicketStore()
