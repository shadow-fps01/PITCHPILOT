"""Shared FastAPI dependencies (DB session, current user, owned resources)."""

import uuid

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.session import get_db
from app.models.coaching_session import CoachingSession
from app.models.user import User
from app.services.security import decode_access_token_claims

_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the JWT bearer token to a fully-loaded user row."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Not authenticated")
    try:
        user_id, token_version = decode_access_token_claims(credentials.credentials)
    except pyjwt.InvalidTokenError:
        raise _unauthorized("Invalid or expired token")
    user = await db.get(User, user_id)
    if user is None:
        raise _unauthorized("User no longer exists")
    if token_version != user.token_version:
        raise _unauthorized("Your session has expired — please sign in again")
    return user


async def require_verified_email(
    current_user: User = Depends(get_current_user),
) -> User:
    """Enforce the gates required before a coaching session can start.

    Email verification is opt-in (`REQUIRE_EMAIL_VERIFICATION`); recording
    consent is on by default because the app captures and sends audio.
    """
    settings = get_settings()
    if settings.require_email_verification and not current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address to start coaching.",
        )
    if settings.require_recording_consent and not current_user.consent_current:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Please confirm that you have permission to record this call "
                "before starting a coaching session."
            ),
        )
    return current_user


async def get_owned_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CoachingSession:
    """Fetch a coaching session that belongs to the current user (404 otherwise)."""
    coaching_session = await db.get(CoachingSession, session_id)
    if coaching_session is None or coaching_session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return coaching_session
