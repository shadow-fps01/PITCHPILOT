"""Billing endpoints — plan status, Paystack checkout, cancel & webhooks."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config.settings import get_settings
from app.db.session import get_db
from app.models.billing_event import BillingEvent
from app.models.user import User
from app.schemas.billing import (
    BillingEventListResponse,
    BillingEventPublic,
    BillingStatus,
    CheckoutResponse,
    VerifyRequest,
    VerifyResponse,
    WebhookAccepted,
)
from app.services.billing import (
    PaystackError,
    build_status,
    cancel_plus,
    process_webhook,
    resolve_provider,
    start_checkout,
    verify_payment,
)
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/billing", tags=["billing"])
settings = get_settings()


@router.get("/usage", response_model=BillingStatus)
async def billing_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BillingStatus:
    """Plan, quotas and usage for the signed-in user."""
    return await build_status(db, current_user, settings)


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    dependencies=[Depends(rate_limit("billing"))],
)
async def billing_checkout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CheckoutResponse:
    """Start a Plus upgrade.

    Paystack mode returns an `authorization_url` to redirect the user to the
    hosted checkout; demo mode (no key configured) upgrades instantly so the
    end-to-end flow is testable without a merchant account.
    """
    try:
        result = await start_checkout(db, current_user, settings)
    except PaystackError as exc:
        logger.error(f"Paystack checkout failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="We couldn't start checkout just now. Please try again in a moment.",
        )
    status_data = await build_status(db, current_user, settings)
    return CheckoutResponse(
        mode=result["mode"],
        authorization_url=result["authorization_url"],
        status=status_data,
    )


@router.post("/cancel", response_model=BillingStatus)
async def billing_cancel(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BillingStatus:
    """Cancel the Plus subscription (Paystack: at period end; demo: now)."""
    try:
        current_user = await cancel_plus(db, current_user, settings)
    except PaystackError as exc:
        logger.warning(f"Subscription cancellation failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "We couldn't cancel your subscription just now. "
                "Please try again in a moment."
            ),
        )
    return await build_status(db, current_user, settings)


@router.post("/verify", response_model=VerifyResponse)
async def billing_verify(
    payload: VerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VerifyResponse:
    """Confirm a Paystack payment client-side.

    Fallback for when Paystack webhooks cannot reach the host (localhost dev,
    tunneling): verifies the transaction reference directly with Paystack and
    runs the same idempotent Plus-activation as the `charge.success` webhook.
    """
    try:
        result = await verify_payment(db, current_user, settings, payload.reference)
    except PaystackError as exc:
        logger.warning(f"Payment verify failed: {exc}")
        return VerifyResponse(
            verified=False,
            status="error",
            message=(
                "We couldn't confirm that payment just now. "
                "Please try again in a moment."
            ),
            billing=await build_status(db, current_user, settings),
        )
    status_data = await build_status(db, current_user, settings)
    return VerifyResponse(billing=status_data, **result)


@router.get("/events", response_model=BillingEventListResponse)
async def billing_events(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BillingEventListResponse:
    """Payment & plan-change history for the signed-in user."""
    limit = max(1, min(limit, 200))
    result = await db.scalars(
        select(BillingEvent)
        .where(BillingEvent.user_id == current_user.id)
        .order_by(desc(BillingEvent.created_at))
        .limit(limit)
    )
    return BillingEventListResponse(
        events=[BillingEventPublic.model_validate(e) for e in result]
    )


@router.post("/webhook", response_model=WebhookAccepted)
async def billing_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> JSONResponse | WebhookAccepted:
    """Paystack webhook receiver.

    Reads the raw body (signature verification is HMAC-SHA512 over the exact
    bytes), processes payment/subscription events, and always answers 2xx for
    well-signed requests so Paystack does not retry forever.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-paystack-signature")
    accepted, message = await process_webhook(db, settings, raw_body, signature)
    if not accepted:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": message},
        )
    return WebhookAccepted(received=True, message=message)
