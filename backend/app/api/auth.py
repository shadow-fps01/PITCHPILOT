"""Registration, login, account lifecycle, consent, WS tickets and current-user."""

import asyncio
from datetime import datetime, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import select
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
    EmailAvailability,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserPublic,
    VerificationStatus,
    VerifyRequest,
    WsTicketResponse,
)
from app.services.billing import PaystackError, cancel_plus
from app.services.email import (
    email_provider_configured,
    send_password_reset_email,
    send_verification_email,
)
from app.services.knowledge import schedule_seed_default_cards
from app.services.rate_limit import rate_limit
from app.services.security import (
    create_access_token,
    create_email_verification_token,
    create_password_reset_token,
    decode_email_verification_token,
    decode_password_reset_token,
    hash_password,
    verify_password,
)
from app.services.session_registry import registry
from app.services.ws_tickets import ws_tickets

router = APIRouter(prefix="/auth", tags=["auth"])


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
    token = create_email_verification_token(user.id)
    link = f"{settings.frontend_base_url.rstrip('/')}/?verify={token}"
    await send_verification_email(settings, to=user.email, link=link)


async def _send_password_reset(user: User) -> None:
    settings = get_settings()
    token = create_password_reset_token(
        user.id,
        user.token_version,
        expires_minutes=settings.password_reset_token_minutes,
    )
    link = f"{settings.frontend_base_url.rstrip('/')}/?reset={token}"
    await send_password_reset_email(
        settings,
        to=user.email,
        link=link,
        expires_minutes=settings.password_reset_token_minutes,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth"))],
)
async def register(
    payload: RegisterRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    settings = get_settings()
    email = payload.email.lower()
    if not _registration_allowed(email, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign-ups are currently invite-only.",
        )
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    require_verification = settings.require_email_verification
    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        email_verified=not require_verification,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info(f"New user registered: {email}")
    schedule_seed_default_cards(user.id)  # F3: default RAG deck (background)
    if require_verification:
        asyncio.create_task(_send_verification(user))
    return _token_response(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit("auth"))],
)
async def login(
    payload: LoginRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    email = payload.email.lower()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    logger.info(f"User logged in: {email}")
    return _token_response(user)


@router.get("/email-available", response_model=EmailAvailability)
async def email_available() -> EmailAvailability:
    """Public capability check for the sign-in screen.

    Password recovery is hidden when this deployment cannot send email yet.
    The response deliberately says nothing about which provider is configured.
    """
    return EmailAvailability(available=email_provider_configured(get_settings()))


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limit("auth"))],
)
async def forgot_password(
    payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)
) -> MessageResponse:
    """Send a reset link when the account exists.

    The response is identical for known and unknown addresses so this endpoint
    cannot be used to enumerate accounts.
    """
    settings = get_settings()
    if not email_provider_configured(settings):
        logger.warning("Password reset requested but no email provider is configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password recovery is temporarily unavailable. Please contact support.",
        )
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
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
    user.hashed_password = hash_password(payload.password)
    user.token_version += 1
    await db.commit()
    ws_tickets.revoke_user(str(user.id))
    logger.info(f"Password reset for {user.email}")
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

    current_user.hashed_password = hash_password(payload.new_password)
    current_user.token_version += 1
    await db.commit()
    await db.refresh(current_user)
    ws_tickets.revoke_user(str(current_user.id))
    logger.info(f"Password changed for {current_user.email}")
    return _token_response(current_user)


@router.get("/me", response_model=UserPublic)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/verify", response_model=UserPublic)
async def verify_email(
    payload: VerifyRequest, db: AsyncSession = Depends(get_db)
) -> User:
    """Confirm an email address from the link sent at registration."""
    try:
        user_id = decode_email_verification_token(payload.token)
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
    if not user.email_verified:
        user.email_verified = True
        await db.commit()
        await db.refresh(user)
        logger.info(f"Email verified for {user.email}")
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
    logger.info(f"Recording consent accepted by {current_user.email}")
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
        except Exception as exc:  # noqa: BLE001 — never delete with a live charge
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
    ws_tickets.revoke_user(str(user_id))
    logger.info(f"Account deleted: {email}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/ws-ticket",
    response_model=WsTicketResponse,
    dependencies=[Depends(rate_limit("ws-ticket"))],
)
async def ws_ticket(current_user: User = Depends(get_current_user)) -> WsTicketResponse:
    """Mint a short-lived credential for the WebSocket handshake (keeps the
    JWT out of URLs and access logs)."""
    ticket, ttl = ws_tickets.issue(str(current_user.id))
    return WsTicketResponse(ticket=ticket, expires_in=ttl)
