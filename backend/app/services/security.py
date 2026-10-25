"""Password hashing (bcrypt) and JWT issuing/verification."""

import datetime as dt
import uuid

import bcrypt
import jwt
from loguru import logger

from app.config.settings import get_settings

_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the given plaintext password."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time bcrypt comparison of a plaintext password against a hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        logger.warning("Malformed password hash encountered during verification")
        return False


def create_access_token(user_id: uuid.UUID, token_version: int = 0) -> str:
    """Issue a signed JWT whose subject is the user's id.

    `token_version` is checked against the user row on every request. Bumping
    the row (password reset, forced sign-out) invalidates every old token.
    """
    settings = get_settings()
    now = dt.datetime.now(tz=dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "ver": int(token_version or 0),
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expires_minutes),
        "iss": settings.app_name,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token_claims(token: str) -> tuple[uuid.UUID, int]:
    """Validate a JWT and return `(user_id, token_version)`.

    Raises `jwt.InvalidTokenError` (or a subclass) when the token is bad/expired.
    Tokens issued before token versions existed default to version 0.
    """
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise jwt.InvalidTokenError("Token payload is missing a subject")
    version = payload.get("ver", 0)
    if not isinstance(version, int) or version < 0:
        raise jwt.InvalidTokenError("Token payload has an invalid version")
    return uuid.UUID(subject), version


def decode_access_token(token: str) -> uuid.UUID:
    """Backward-compatible helper returning only the user id."""
    return decode_access_token_claims(token)[0]


_EMAIL_VERIFY_PURPOSE = "verify_email"
_PASSWORD_RESET_PURPOSE = "reset_password"


def create_email_verification_token(
    user_id: uuid.UUID, *, expires_minutes: int = 60 * 24
) -> str:
    """Signed, single-purpose token embedded in the verification link."""
    settings = get_settings()
    now = dt.datetime.now(tz=dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "purpose": _EMAIL_VERIFY_PURPOSE,
        "iat": now,
        "exp": now + dt.timedelta(minutes=expires_minutes),
        "iss": settings.app_name,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_email_verification_token(token: str) -> uuid.UUID:
    """Validate a verification token and return the user id it belongs to."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("purpose") != _EMAIL_VERIFY_PURPOSE:
        raise jwt.InvalidTokenError("Not an email-verification token")
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise jwt.InvalidTokenError("Token payload is missing a subject")
    return uuid.UUID(subject)


def create_password_reset_token(
    user_id: uuid.UUID,
    token_version: int,
    *,
    expires_minutes: int | None = None,
) -> str:
    """Signed, single-purpose password-reset token.

    The embedded `ver` makes the link single-use: a successful reset bumps the
    user's token_version, so replaying the same link fails validation.
    """
    settings = get_settings()
    now = dt.datetime.now(tz=dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "ver": int(token_version or 0),
        "purpose": _PASSWORD_RESET_PURPOSE,
        "iat": now,
        "exp": now
        + dt.timedelta(
            minutes=expires_minutes
            if expires_minutes is not None
            else settings.password_reset_token_minutes
        ),
        "iss": settings.app_name,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_password_reset_token(token: str) -> tuple[uuid.UUID, int]:
    """Validate a reset token and return `(user_id, token_version)`."""
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("purpose") != _PASSWORD_RESET_PURPOSE:
        raise jwt.InvalidTokenError("Not a password-reset token")
    subject = payload.get("sub")
    version = payload.get("ver")
    if not isinstance(subject, str) or not isinstance(version, int) or version < 0:
        raise jwt.InvalidTokenError("Token payload is missing a subject or version")
    return uuid.UUID(subject), version
