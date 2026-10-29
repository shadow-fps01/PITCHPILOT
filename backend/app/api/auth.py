"""Registration, login, account lifecycle, consent, WS tickets and current-user."""

import asyncio
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.config.settings import Settings, get_settings
from app.db.session import get_db
from app.models.billing_event import BillingEvent
from app.models.coaching_session import CoachingSession
from app.models.knowledge_card import KnowledgeCard
from app.models.script import Script
from app.models.user import PLAN_PLUS, User
from app.schemas.auth import (
    AccountDeleteRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserPublic,
    VerificationStatus,
    VerifyRequest,
    WsTicketRequest,
    WsTicketResponse,
)
from app.services.billing import PaystackError, cancel_plus
from app.services.email import (
    email_provider_configured,
    send_password_reset_email,
    send_verification_email,
)
from app.services.knowledge import schedule_seed_default_cards
from app.services.passwords import password_contains_identity
from app.services.rate_limit import (
    clear as clear_rate_limit,
    client_ip,
    hash_email,
    hit as rate_limit_hit,
    login_failure_key,
    rate_limit,
    record_login_failure,
    retry_after as rate_limit_retry_after,
)
from app.services.security import (
    create_access_token,
    create_email_verification_token,
    create_password_reset_token,
    decode_email_verification_token,
    decode_password_reset_token,
    hash_password,
    mask_email,
    verify_password,
)
from app.services.session_registry import registry
from app.services.ws_tickets import ws_tickets

router = APIRouter(prefix="/auth", tags=["auth"])

# Used to keep login timing similar for known and unknown email addresses.
_DUMMY_HASH = hash_password("pitchpilot-dummy-password-not-used")

_REGISTRATION_MESSAGE = (
    "If this email is available, your account is ready. Sign in to continue."
)


def _registration_response() -> MessageResponse:
    return MessageResponse(message=_REGISTRATION_MESSAGE)


def _too_many_attempts(retry_after: float) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed sign-in attempts. Try again later.",
        headers={"Retry-After": str(max(1, int(retry_after) + 1))},
    )


def _check_password_identity(email: str, password: str) -> None:
    if password_contains_identity(password, email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a password that does not contain your email address",
        )


def _registration_allowed(email: str, settings: Settings) -> bool:
    """Public signups, an allowlist, or a closed private instance."""
    allowlist = {entry.lower() for entry in settings.signup_allowlist if entry.strip()}
    if allowlist:
        return email.lower() in allowlist
    return settings.signups_enabled


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id, user.token_version),
        user=UserPublic.model_validate(user),
    )


async def _send_verification(user: User) -> None:
    settings = get_settings()
    token = create_email_verification_token(user.id, user.token_version)
    link = f"{settings.frontend_base_url.rstrip('/')}/#verify={token}"
    await send_verification_email(settings, to=user.email, link=link)


async def _send_password_reset(user: User) -> None:
    settings = get_settings()
    token = create_password_reset_token(
        user.id,
        user.token_version,
        expires_minutes=settings.password_reset_token_minutes,
    )
    link = f"{settings.frontend_base_url.rstrip('/')}/#reset={token}"
    await send_password_reset_email(
        settings,
        to=user.email,
        link=link,
        expires_minutes=settings.password_reset_token_minutes,
    )


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("auth"))],
)
async def register(
    payload: RegisterRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Create an account without revealing whether the email already exists."""
    settings = get_settings()
    email = payload.email.lower()
    if not _registration_allowed(email, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign-ups are currently invite-only.",
        )
    _check_password_identity(email, payload.password)
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        logger.info(
            f"Registration attempted for an existing email: {mask_email(email)}"
        )
        return _registration_response()
    require_verification = settings.require_email_verification
    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        email_verified=not require_verification,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent registration for the same address: return the same
        # generic response instead of leaking the race as a 409/500.
        await db.rollback()
        logger.info(f"Registration raced for an existing email: {mask_email(email)}")
        return _registration_response()
    await db.refresh(user)
    logger.info(f"New user registered: {mask_email(email)}")
    schedule_seed_default_cards(user.id)  # F3: default RAG deck (background)
    if require_verification:
        asyncio.create_task(_send_verification(user))
    return _registration_response()


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("auth"))],
)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email = payload.email.lower()
    failure_key = login_failure_key(email)
    retry = await rate_limit_retry_after(failure_key)
    if retry > 0:
        logger.warning(
            f"Locked login attempt for {mask_email(email)} from {client_ip(request)}"
        )
        raise _too_many_attempts(retry)

    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        # Run a real bcrypt comparison for unknown addresses so response timing
        # does not reveal whether the account exists.
        verify_password(payload.password, _DUMMY_HASH)
        failure = await record_login_failure(failure_key)
        logger.warning(
            f"Failed login for {mask_email(email)} from {client_ip(request)}"
        )
        if failure.retry_after > 0:
            raise _too_many_attempts(failure.retry_after)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not verify_password(payload.password, user.hashed_password):
        failure = await record_login_failure(failure_key)
        logger.warning(
            f"Failed login for {mask_email(email)} from {client_ip(request)}"
        )
        if failure.retry_after > 0:
            raise _too_many_attempts(failure.retry_after)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    await clear_rate_limit(failure_key)
    logger.info(f"User logged in: {mask_email(email)}")
    return _token_response(user)


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit("auth"))],
)
async def forgot_password(
    payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Send a reset link when the account exists.

    The response is identical for known and unknown addresses, and when email
    delivery is not configured. A per-email budget prevents reset-email
    bombing once SMTP/Resend is enabled.
    """
    settings = get_settings()
    email = payload.email.lower()
    reset_budget = await rate_limit_hit(
        f"reset:email:{hash_email(email)}", 3, 3600
    )
    if reset_budget.allowed:
        if not email_provider_configured(settings):
            logger.warning(
                f"Password reset requested for {mask_email(email)} but no email "
                "provider is configured"
            )
        else:
            user = await db.scalar(select(User).where(User.email == email))
            if user is not None:
                asyncio.create_task(_send_password_reset(user))
    return MessageResponse(
        message="If that email has an account, we've sent a reset link."
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit("auth"))],
)
async def reset_password(
    payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Set a new password from a single-use reset link."""
    try:
        user_id, token_version = decode_password_reset_token(payload.token)
    except pyjwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired",
        )
    user = await db.get(User, user_id)
    if user is None or user.token_version != token_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired",
        )
    _check_password_identity(user.email, payload.password)
    user.hashed_password = hash_password(payload.password)
    user.token_version += 1
    await db.commit()
    await ws_tickets.revoke_user(str(user.id))
    try:
        await registry.finalize_user(user.id)
    except Exception as exc:  # noqa: BLE001  -  logout must not 500
        logger.warning(f"Failed to finalize live sessions after password reset: {exc}")
    logger.info(f"Password reset for {mask_email(user.email)}")
    return MessageResponse(message="Password updated. Sign in with your new password.")


@router.post(
    "/change-password",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("auth"))],
)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Change the password from an authenticated session.

    The password change bumps `token_version`, which invalidates every older
    access token and all outstanding WebSocket tickets. A fresh token is
    returned so this browser stays signed in; other devices must sign in again.
    """
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect",
        )
    if verify_password(payload.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a password you haven't used for this account",
        )
    _check_password_identity(current_user.email, payload.new_password)

    current_user.hashed_password = hash_password(payload.new_password)
    current_user.token_version += 1
    await db.commit()
    await db.refresh(current_user)
    await ws_tickets.revoke_user(str(current_user.id))
    try:
        await registry.finalize_user(current_user.id)
    except Exception as exc:  # noqa: BLE001  -  password change must not 500
        logger.warning(f"Failed to finalize live sessions after password change: {exc}")
    logger.info(f"Password changed for {mask_email(current_user.email)}")
    return _token_response(current_user)


async def _revoke_all_sessions(
    current_user: User, db: AsyncSession
) -> MessageResponse:
    """Bump token_version (revoking every access token) and close live sockets."""
    current_user.token_version += 1
    await db.commit()
    await ws_tickets.revoke_user(str(current_user.id))
    try:
        await registry.finalize_user(current_user.id)
    except Exception as exc:  # noqa: BLE001  -  logout must not 500
        logger.warning(f"Failed to finalize live sessions on logout: {exc}")
    logger.info(f"Signed out all sessions for {mask_email(current_user.email)}")
    return MessageResponse(message="Signed out.")


@router.post(
    "/logout",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit("logout"))],
)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Sign out this device and every other session for the account."""
    return await _revoke_all_sessions(current_user, db)


@router.post(
    "/logout-all",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit("logout"))],
)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Explicit alias for revoking every session on every device."""
    return await _revoke_all_sessions(current_user, db)


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/verify", response_model=UserPublic)
async def verify_email(
    payload: VerifyRequest, db: AsyncSession = Depends(get_db)
) -> User:
    """Confirm an email address from the link sent at registration."""
    try:
        user_id, token_version = decode_email_verification_token(payload.token)
    except pyjwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired",
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account not found"
        )
    if token_version != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired",
        )
    if not user.email_verified:
        user.email_verified = True
        await db.commit()
        await db.refresh(user)
        logger.info(f"Email verified for {mask_email(user.email)}")
    return user


@router.post(
    "/resend-verification",
    response_model=VerificationStatus,
    dependencies=[Depends(rate_limit("auth"))],
)
async def resend_verification(
    current_user: User = Depends(get_current_user),
) -> VerificationStatus:
    settings = get_settings()
    if not settings.require_email_verification or current_user.email_verified:
        return VerificationStatus(verified=current_user.email_verified, sent=False)
    if not email_provider_configured(settings):
        logger.warning("Verification email requested but no email provider is configured")
        return VerificationStatus(verified=False, sent=False)
    asyncio.create_task(_send_verification(current_user))
    return VerificationStatus(verified=False, sent=True)


@router.post("/consent", response_model=UserPublic)
async def accept_recording_consent(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Record the presenter's permission to record/transcribe the call."""
    settings = get_settings()
    current_user.consented_at = datetime.now(tz=timezone.utc)
    current_user.consent_version = settings.consent_version
    await db.commit()
    await db.refresh(current_user)
    logger.info(f"Recording consent accepted by {mask_email(current_user.email)}")
    return current_user


@router.get("/export", dependencies=[Depends(rate_limit("export"))])
async def export_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Download everything PitchPilot stores for the signed-in account."""
    scripts = list(
        await db.scalars(
            select(Script)
            .where(Script.user_id == current_user.id)
            .order_by(Script.created_at)
        )
    )
    sessions = list(
        await db.scalars(
            select(CoachingSession)
            .where(CoachingSession.user_id == current_user.id)
            .options(selectinload(CoachingSession.events))
            .order_by(CoachingSession.started_at)
        )
    )
    cards = list(
        await db.scalars(
            select(KnowledgeCard)
            .where(KnowledgeCard.user_id == current_user.id)
            .order_by(KnowledgeCard.created_at)
        )
    )
    billing_events = list(
        await db.scalars(
            select(BillingEvent)
            .where(BillingEvent.user_id == current_user.id)
            .order_by(BillingEvent.created_at)
        )
    )
    payload = {
        "exported_at": datetime.now(tz=timezone.utc),
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "created_at": current_user.created_at,
            "plan": current_user.plan,
            "email_verified": current_user.email_verified,
            "consented_at": current_user.consented_at,
            "consent_version": current_user.consent_version,
        },
        "scripts": [
            {
                "id": script.id,
                "title": script.title,
                "content_outline": script.content_outline,
                "target_wpm": script.target_wpm,
                "keywords": script.keywords,
                "created_at": script.created_at,
            }
            for script in scripts
        ],
        "sessions": [
            {
                "id": session.id,
                "script_id": session.script_id,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "duration_seconds": session.duration_seconds,
                "events": [
                    {
                        "id": event.id,
                        "timestamp": event.timestamp,
                        "event_type": event.event_type.value,
                        "message": event.message,
                        "raw_transcript_snippet": event.raw_transcript_snippet,
                    }
                    for event in session.events
                ],
            }
            for session in sessions
        ],
        "knowledge_cards": [
            {
                "id": card.id,
                "script_id": card.script_id,
                "title": card.title,
                "content": card.content,
                "keywords": card.keywords,
                "source": card.source,
                "created_at": card.created_at,
            }
            for card in cards
        ],
        "billing_events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "provider": event.provider,
                "reference": event.reference,
                "amount": event.amount,
                "currency": event.currency,
                "message": event.message,
                "created_at": event.created_at,
            }
            for event in billing_events
        ],
    }
    return JSONResponse(
        content=jsonable_encoder(payload),
        headers={
            "Content-Disposition": 'attachment; filename="pitchpilot-export.json"',
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/account",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("account"))],
)
async def delete_account(
    payload: AccountDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Cancel any live subscription, then permanently delete the account."""
    if not verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password is incorrect",
        )

    email = current_user.email
    user_id = current_user.id
    settings = get_settings()
    if current_user.plan == PLAN_PLUS or current_user.paystack_subscription_code:
        try:
            await cancel_plus(db, current_user, settings)
        except PaystackError as exc:
            logger.warning(f"Account deletion blocked by subscription cancellation: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "We couldn't cancel your subscription just now. "
                    "Please try again in a moment or contact support."
                ),
            )
        except Exception as exc:  # noqa: BLE001  -  never delete with a live charge
            logger.error(f"Unexpected subscription cancellation failure: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "We couldn't cancel your subscription just now. "
                    "Please try again in a moment or contact support."
                ),
            )

    await registry.finalize_user(user_id)
    await db.delete(current_user)
    await db.commit()
    await ws_tickets.revoke_user(str(user_id))
    logger.info(f"Account deleted: {mask_email(email)}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/ws-ticket",
    response_model=WsTicketResponse,
    dependencies=[Depends(rate_limit("ws-ticket"))],
)
async def ws_ticket(
    payload: WsTicketRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WsTicketResponse:
    """Mint a short-lived, single-use ticket bound to one owned session.

    Browsers cannot set WebSocket headers, so the ticket travels in the query
    string instead of the long-lived JWT.
    """
    session = await db.scalar(
        select(CoachingSession).where(
            CoachingSession.id == payload.session_id,
            CoachingSession.user_id == current_user.id,
        )
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    ticket, ttl = await ws_tickets.issue(str(current_user.id), str(session.id))
    return WsTicketResponse(ticket=ticket, expires_in=ttl)
