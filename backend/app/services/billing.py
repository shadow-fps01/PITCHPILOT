"""Billing service — PitchPilot Plus subscriptions via Paystack.

Free plan: 3 coaching sessions + 3 scripts per calendar month.
Plus plan: ₦22,500/month Paystack subscription → unlimited everything.

Provider resolution follows the app's `auto` convention:
`PAYMENTS_PROVIDER=auto` uses Paystack when `PAYSTACK_SECRET_KEY` is present
and falls back to an instant mock upgrade (demo mode) otherwise, so the
whole flow is testable with zero keys.
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.models.billing_event import BillingEvent, BillingEventType
from app.models.coaching_session import CoachingSession
from app.models.script import Script
from app.models.user import PLAN_FREE, PLAN_PLUS, User
from app.schemas.billing import BillingStatus

PROVIDER_PAYSTACK = "paystack"
PROVIDER_DEMO = "demo"

PAYSTACK_BASE_URL = "https://api.paystack.co"
PLAN_NAME = "PitchPilot Plus (Monthly)"
METADATA_PURPOSE = "pitchpilot_plus"

# Module-level cache for the Paystack plan code, keyed by (amount, currency).
_plan_code_cache: dict[tuple[int, str], str] = {}

# ── Payment reconciliation tuning ─────────────────────────────────────
# How often a user's Paystack history may be re-checked (seconds) and how far
# back an unprocessed successful charge is still honored (days). The lookback
# is deliberately short: a monthly billing period always outlives it, so
# cancelled/expired subscriptions are never resurrected by old charges.
RECONCILE_MIN_INTERVAL_SECONDS = 20.0
RECONCILE_LOOKBACK_DAYS = 7
RECONCILE_MAX_TRANSACTIONS = 10
_reconcile_last_attempt: dict[uuid.UUID, float] = {}


# ── Provider resolution ────────────────────────────────────────────────
def resolve_provider(settings: Settings) -> str:
    """paystack when configured+keyed, demo otherwise (auto convention)."""
    if settings.payments_provider == PROVIDER_DEMO:
        return PROVIDER_DEMO
    if settings.payments_provider == PROVIDER_PAYSTACK:
        return PROVIDER_PAYSTACK
    return PROVIDER_PAYSTACK if settings.paystack_secret_key else PROVIDER_DEMO


def payments_live(settings: Settings) -> bool:
    return resolve_provider(settings) == PROVIDER_PAYSTACK


# ── Monthly quota helpers ──────────────────────────────────────────────
def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(tz=timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def count_month_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    start = _month_start()
    result = await db.scalar(
        select(func.count())
        .select_from(CoachingSession)
        .where(
            CoachingSession.user_id == user_id,
            CoachingSession.started_at >= start,
        )
    )
    return int(result or 0)


async def count_month_scripts(db: AsyncSession, user_id: uuid.UUID) -> int:
    start = _month_start()
    result = await db.scalar(
        select(func.count())
        .select_from(Script)
        .where(Script.user_id == user_id, Script.created_at >= start)
    )
    return int(result or 0)


async def sync_plan_expiry(db: AsyncSession, user: User) -> User:
    """Lazily downgrade Plus members whose cancelled subscription ran out."""
    if (
        user.plan == PLAN_PLUS
        and user.plus_cancel_at_period_end
        and user.plus_renews_at is not None
        and not user.is_plus
    ):
        logger.info(f"Plus subscription expired for {user.email} — downgrading to free")
        user.plan = PLAN_FREE
        user.plus_cancel_at_period_end = False
        db.add(
            BillingEvent(
                user_id=user.id,
                event_type=BillingEventType.SUBSCRIPTION_EXPIRED,
                provider=resolve_provider(_get_settings()),
                message="Plus subscription ended at the period boundary",
            )
        )
        await db.commit()
        await db.refresh(user)
    return user


def _get_settings() -> Settings:
    from app.config.settings import get_settings

    return get_settings()


async def build_status(db: AsyncSession, user: User, settings: Settings) -> BillingStatus:
    user = await sync_plan_expiry(db, user)
    if not user.is_plus:
        # Self-heal: a completed Paystack payment that was never processed
        # (lost redirect callback / unreachable webhook) activates here.
        try:
            await reconcile_payments(db, user, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Payment reconciliation skipped for {user.email}: {exc}")
    is_plus = user.is_plus
    sessions_used = await count_month_sessions(db, user.id)
    scripts_used = await count_month_scripts(db, user.id)
    return BillingStatus(
        plan=user.plan,
        is_plus=is_plus,
        provider=resolve_provider(settings),
        payments_live=payments_live(settings),
        price_display=settings.plus_price_display,
        price_amount=settings.plus_price_amount,
        currency=settings.paystack_currency,
        free_monthly_sessions=settings.free_monthly_sessions,
        free_monthly_scripts=settings.free_monthly_scripts,
        sessions_used_this_month=sessions_used,
        scripts_used_this_month=scripts_used,
        sessions_remaining=None if is_plus else max(0, settings.free_monthly_sessions - sessions_used),
        scripts_remaining=None if is_plus else max(0, settings.free_monthly_scripts - scripts_used),
        unlimited=is_plus,
        plus_started_at=user.plus_started_at,
        plus_renews_at=user.plus_renews_at,
        plus_cancel_at_period_end=user.plus_cancel_at_period_end,
    )


# ── Quota enforcement (402 → paywall) ──────────────────────────────────
async def assert_session_quota(db: AsyncSession, user: User, settings: Settings) -> None:
    user = await sync_plan_expiry(db, user)
    if user.is_plus:
        return
    used = await count_month_sessions(db, user.id)
    if used >= settings.free_monthly_sessions:
        # Last-chance self-heal before the paywall: a paid-but-unprocessed
        # Paystack payment activates the account right here.
        try:
            await reconcile_payments(db, user, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Payment reconciliation skipped for {user.email}: {exc}")
        if user.is_plus:
            return
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Free plan limit reached — you've used all {settings.free_monthly_sessions} "
                f"coaching sessions this month. Upgrade to PitchPilot Plus "
                f"({settings.plus_price_display}/month) for unlimited sessions."
            ),
        )


async def assert_script_quota(db: AsyncSession, user: User, settings: Settings) -> None:
    user = await sync_plan_expiry(db, user)
    if user.is_plus:
        return
    used = await count_month_scripts(db, user.id)
    if used >= settings.free_monthly_scripts:
        # Last-chance self-heal before the paywall (mirrors session quota).
        try:
            await reconcile_payments(db, user, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Payment reconciliation skipped for {user.email}: {exc}")
        if user.is_plus:
            return
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Free plan limit reached — you've used all {settings.free_monthly_scripts} "
                f"script creations this month. Upgrade to PitchPilot Plus "
                f"({settings.plus_price_display}/month) for unlimited scripts."
            ),
        )


# ── Paystack HTTP helpers ──────────────────────────────────────────────
async def _paystack(
    settings: Settings, method: str, path: str, json_body: dict | None = None
) -> dict:
    """Call the Paystack API and return the `data` payload.

    One transparent retry on transport errors — this machine's resolver
    occasionally blips (see README), and checkout must never fail because
    of a single dropped packet.
    """
    url = f"{PAYSTACK_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {settings.paystack_secret_key}",
        "Content-Type": "application/json",
    }
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.request(
                    method, url, headers=headers, json=json_body
                )
            break
        except httpx.TransportError as exc:
            last_error = exc
            logger.warning(f"Paystack call attempt {attempt} failed ({exc}) — retrying")
            if attempt == 2:
                raise PaystackError(f"Could not reach Paystack: {exc}") from exc
            await asyncio.sleep(1.0)
    else:  # pragma: no cover — loop always breaks or raises
        raise PaystackError("Could not reach Paystack")
    body: dict = response.json()
    if not response.is_success or not body.get("status"):
        message = body.get("message") or response.text or "Paystack request failed"
        raise PaystackError(message)
    return body.get("data") or {}


class PaystackError(Exception):
    """Paystack API returned an error."""


async def _ensure_customer(db: AsyncSession, user: User, settings: Settings) -> str:
    """Return the user's Paystack customer code, creating one if needed."""
    if user.paystack_customer_code:
        return user.paystack_customer_code
    try:
        data = await _paystack(
            settings,
            "POST",
            "/customer",
            {
                "email": user.email,
                "metadata": {"user_id": str(user.id), "app": "pitchpilot"},
            },
        )
        code = data.get("customer_code")
    except PaystackError:
        # Email already registered on the merchant account → fetch it.
        data = await _paystack(settings, "GET", f"/customer/{user.email}")
        code = data.get("customer_code")
    if not code:
        raise PaystackError("Paystack did not return a customer code")
    user.paystack_customer_code = code
    await db.commit()
    return code


async def _ensure_plan(settings: Settings) -> str:
    """Return the recurring plan code for PitchPilot Plus, caching per config."""
    cache_key = (settings.plus_price_amount, settings.paystack_currency)
    if cache_key in _plan_code_cache:
        return _plan_code_cache[cache_key]
    plan_code: str | None = None
    create_error: str | None = None
    try:
        data = await _paystack(
            settings,
            "POST",
            "/plan",
            {
                "name": PLAN_NAME,
                "amount": settings.plus_price_amount,
                "interval": "monthly",
                "currency": settings.paystack_currency,
            },
        )
        plan_code = data.get("plan_code")
    except PaystackError as exc:
        create_error = str(exc)
        # Plan name already exists — locate it via the list endpoint.
        page = 1
        while plan_code is None and page <= 10:
            data = await _paystack(settings, "GET", f"/plan?perPage=100&page={page}")
            for plan in data.get("data") or []:
                if plan.get("name") == PLAN_NAME:
                    plan_code = plan.get("plan_code")
                    break
            if len(data.get("data") or []) < 100:
                break
            page += 1
    if not plan_code:
        raise PaystackError(
            f"Could not resolve the PitchPilot Plus plan on Paystack ({create_error})"
        )
    _plan_code_cache[cache_key] = plan_code
    return plan_code


# ── Checkout ───────────────────────────────────────────────────────────
async def start_checkout(
    db: AsyncSession, user: User, settings: Settings
) -> dict[str, Any]:
    """Begin a Plus upgrade.

    Returns {"mode": "paystack", "authorization_url": …} (redirect the user to
    Paystack's hosted checkout) or {"mode": "demo", …} when no Paystack key is
    configured — the demo mode upgrades instantly so the flow is testable.
    """
    provider = resolve_provider(settings)

    if provider == PROVIDER_DEMO:
        now = datetime.now(tz=timezone.utc)
        user.plan = PLAN_PLUS
        user.plus_started_at = user.plus_started_at or now
        user.plus_renews_at = now + timedelta(days=30)
        user.plus_cancel_at_period_end = False
        db.add(
            BillingEvent(
                user_id=user.id,
                event_type=BillingEventType.SUBSCRIPTION_ACTIVATED,
                provider=PROVIDER_DEMO,
                reference=f"plan-{uuid.uuid4().hex[:12]}",
                amount=settings.plus_price_amount,
                currency=settings.paystack_currency,
                message="Plan upgraded",
            )
        )
        await db.commit()
        await db.refresh(user)
        logger.info(f"Demo-mode Plus upgrade for {user.email}")
        return {"mode": PROVIDER_DEMO, "authorization_url": None}

    # Double-charge guard: if a previous Paystack payment was never applied
    # (lost callback / unreachable webhook), activate it instead of taking
    # another payment.
    try:
        await reconcile_payments(db, user, settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Pre-checkout reconciliation skipped for {user.email}: {exc}")
    if user.is_plus:
        logger.info(f"Checkout skipped — {user.email} is already Plus")
        return {"mode": "already_plus", "authorization_url": None}

    await _ensure_customer(db, user, settings)
    plan_code = await _ensure_plan(settings)
    reference = f"ppplus-{uuid.uuid4().hex[:16]}"
    data = await _paystack(
        settings,
        "POST",
        "/transaction/initialize",
        {
            "email": user.email,
            "amount": settings.plus_price_amount,
            "currency": settings.paystack_currency,
            "plan": plan_code,
            "reference": reference,
            "callback_url": f"{settings.frontend_base_url.rstrip('/')}/?view=dashboard&billing=success",
            "metadata": {
                "user_id": str(user.id),
                "purpose": METADATA_PURPOSE,
                "custom_data": {"app": "pitchpilot", "plan": "plus"},
            },
        },
    )
    authorization_url = data.get("authorization_url")
    if not authorization_url:
        raise PaystackError("Paystack did not return an authorization URL")
    db.add(
        BillingEvent(
            user_id=user.id,
            event_type=BillingEventType.CHECKOUT_STARTED,
            provider=PROVIDER_PAYSTACK,
            reference=data.get("reference") or reference,
            amount=settings.plus_price_amount,
            currency=settings.paystack_currency,
            message="Checkout started",
        )
    )
    await db.commit()
    logger.info(f"Paystack checkout initialized for {user.email}")
    return {"mode": PROVIDER_PAYSTACK, "authorization_url": authorization_url}


async def cancel_plus(db: AsyncSession, user: User, settings: Settings) -> User:
    """Cancel Plus. Paystack subscriptions stay active until the period ends;
    demo/manual upgrades are downgraded immediately."""
    # A stale subscription code can outlive a local downgrade; never leave it
    # active (especially during account deletion).
    if user.plan != PLAN_PLUS and not user.paystack_subscription_code:
        return user

    provider = resolve_provider(settings)
    if provider == PROVIDER_PAYSTACK:
        # A first Paystack charge sometimes omits the subscription object.
        # Recover the active subscription before deciding it is safe to
        # downgrade/delete the account.
        if not user.paystack_subscription_code:
            await _backfill_subscription(db, user, settings)
        if user.paystack_subscription_code:
            await _paystack(
                settings,
                "POST",
                "/subscription/disable",
                {
                    "code": user.paystack_subscription_code,
                    "email_token": user.paystack_email_token,
                },
            )
            user.plus_cancel_at_period_end = True
            message = "Plus subscription cancelled — stays active until the period ends"
        else:
            logger.warning(
                f"No active Paystack subscription found for {user.email} — "
                "downgrading locally"
            )
            user.plan = PLAN_FREE
            user.plus_cancel_at_period_end = False
            user.plus_started_at = None
            user.plus_renews_at = None
            message = "Plus cancelled — no active subscription found"
    else:
        user.plan = PLAN_FREE
        user.plus_cancel_at_period_end = False
        user.plus_started_at = None
        user.plus_renews_at = None
        message = "Plus cancelled — downgraded immediately"

    db.add(
        BillingEvent(
            user_id=user.id,
            event_type=BillingEventType.SUBSCRIPTION_CANCELLED,
            provider=provider,
            reference=user.paystack_subscription_code,
            amount=settings.plus_price_amount,
            currency=settings.paystack_currency,
            message=message,
        )
    )
    await db.commit()
    await db.refresh(user)
    logger.info(f"Plus cancelled for {user.email}: {message}")
    return user


# ── Webhook processing ─────────────────────────────────────────────────
def verify_webhook_signature(settings: Settings, raw_body: bytes, signature: str | None) -> bool:
    if not settings.paystack_secret_key or not signature:
        return False
    expected = hmac.new(
        settings.paystack_secret_key.encode(), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _activate_from_charge(
    db: AsyncSession,
    settings: Settings,
    user: User,
    data: dict,
    payload: dict | None = None,
) -> str:
    """Flip an account to Plus after a successful charge (idempotent).

    Shared by the `charge.success` webhook and the client-driven
    `POST /billing/verify` fallback (webhooks cannot reach localhost).
    Returns "activated", "renewed" or "duplicate".
    """
    reference = data.get("reference")
    if await _event_seen(db, BillingEventType.PAYMENT_SUCCEEDED, reference):
        return "duplicate"
    was_plus = user.is_plus
    now = datetime.now(tz=timezone.utc)
    customer_code = (data.get("customer") or {}).get("customer_code")
    if customer_code:
        user.paystack_customer_code = customer_code
    user.plan = PLAN_PLUS
    user.plus_started_at = user.plus_started_at or now
    user.plus_cancel_at_period_end = False
    sub = data.get("subscription")
    if isinstance(sub, dict):
        user.paystack_subscription_code = sub.get(
            "subscription_code", user.paystack_subscription_code
        )
        user.paystack_email_token = sub.get("email_token", user.paystack_email_token)
    db.add(
        BillingEvent(
            user_id=user.id,
            event_type=BillingEventType.PAYMENT_SUCCEEDED,
            provider=PROVIDER_PAYSTACK,
            reference=reference,
            amount=data.get("amount"),
            currency=data.get("currency"),
            message="Plus payment received" if not was_plus else "Plus subscription renewed",
            raw_payload=payload,
        )
    )
    await db.commit()
    await db.refresh(user)
    await _refresh_subscription(db, user, settings)
    await _backfill_subscription(db, user, settings)
    logger.info(f"Plus payment received from {user.email}")
    return "renewed" if was_plus else "activated"


async def verify_payment(
    db: AsyncSession, user: User, settings: Settings, reference: str
) -> dict[str, Any]:
    """Client-driven payment confirmation (fallback for when the Paystack
    webhook cannot reach the host — e.g. localhost dev).

    Calls Paystack's transaction verify endpoint; on success the same
    activation logic as the `charge.success` webhook runs (idempotent).
    """
    data = await _paystack(settings, "GET", f"/transaction/verify/{reference}")
    status_value = data.get("status")
    purpose = _metadata(data).get("purpose")
    is_plus_payment = purpose == METADATA_PURPOSE or data.get("plan") is not None
    customer_email = ((data.get("customer") or {}).get("email") or "").lower()
    belongs_to_user = (
        str(_metadata(data).get("user_id")) == str(user.id) or customer_email == user.email
    )
    if status_value != "success" or not is_plus_payment or not belongs_to_user:
        return {"verified": False, "status": status_value or "unknown",
                "message": "Payment has not been confirmed yet"}
    result = await _activate_from_charge(db, settings, user, data)
    if result == "duplicate":
        return {"verified": True, "status": "success", "message": "Already processed"}
    return {"verified": True, "status": "success", "message": "Plus activated"}


# ── Payment reconciliation (self-healing Plus activation) ──────────────
async def _fetch_customer_id(
    db: AsyncSession, user: User, settings: Settings
) -> int | None:
    """Numeric Paystack customer id (list endpoints filter by id, not code)."""
    try:
        if user.paystack_customer_code:
            data = await _paystack(
                settings, "GET", f"/customer/{user.paystack_customer_code}"
            )
        else:
            data = await _paystack(settings, "GET", f"/customer/{user.email}")
    except PaystackError:
        return None
    code = data.get("customer_code")
    if code and code != user.paystack_customer_code:
        user.paystack_customer_code = code
        await db.commit()
    return data.get("id")


async def _backfill_subscription(db: AsyncSession, user: User, settings: Settings) -> None:
    """Store subscription details the charge payload omitted.

    The first payment of a Paystack plan subscription often carries no
    `subscription` object in the transaction payload, which would leave
    `plus_renews_at` unset and break period-end cancellation — pull the
    customer's active subscription directly instead.
    """
    if user.paystack_subscription_code:
        return
    customer_id = await _fetch_customer_id(db, user, settings)
    if customer_id is None:
        return
    data = await _paystack(
        settings, "GET", f"/subscription?customer={customer_id}&perPage=10"
    )
    subscriptions = data if isinstance(data, list) else []
    active = next((s for s in subscriptions if s.get("status") == "active"), None)
    if active is None:
        return
    user.paystack_subscription_code = active.get(
        "subscription_code", user.paystack_subscription_code
    )
    user.paystack_email_token = active.get("email_token", user.paystack_email_token)
    next_payment = _parse_datetime(active.get("next_payment_date"))
    if next_payment:
        user.plus_renews_at = next_payment
    await db.commit()
    await db.refresh(user)
    logger.info(f"Paystack subscription details backfilled for {user.email}")


async def reconcile_payments(db: AsyncSession, user: User, settings: Settings) -> bool:
    """Self-healing Plus activation for unprocessed Paystack payments.

    Scans the user's recent successful Paystack charges and runs the standard
    activation for any PitchPilot Plus payment that was never processed —
    covering a lost checkout redirect (stripped params, dev server down,
    deployed frontend behind another URL) and webhooks that cannot reach the
    host. Rate-limited per user; returns True when a payment was applied.
    """
    if resolve_provider(settings) != PROVIDER_PAYSTACK or user.is_plus:
        return False
    now = time.monotonic()
    last_attempt = _reconcile_last_attempt.get(user.id)
    if last_attempt is not None and now - last_attempt < RECONCILE_MIN_INTERVAL_SECONDS:
        return False
    _reconcile_last_attempt[user.id] = now

    customer_id = await _fetch_customer_id(db, user, settings)
    if customer_id is None:
        logger.debug(f"Reconciliation skipped — no Paystack customer for {user.email}")
        return False
    data = await _paystack(
        settings,
        "GET",
        f"/transaction?customer={customer_id}&status=success&perPage=25",
    )
    transactions = data if isinstance(data, list) else []
    if not transactions:
        return False
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=RECONCILE_LOOKBACK_DAYS)
    applied = False
    for tx in transactions[:RECONCILE_MAX_TRANSACTIONS]:
        if _metadata(tx).get("purpose") != METADATA_PURPOSE:
            continue  # not a PitchPilot Plus checkout charge
        paid_at = _parse_datetime(tx.get("paid_at")) or _parse_datetime(
            tx.get("created_at")
        )
        if paid_at is not None and paid_at < cutoff:
            continue
        if await _event_seen(db, BillingEventType.PAYMENT_SUCCEEDED, tx.get("reference")):
            continue
        await _activate_from_charge(db, settings, user, tx)
        applied = True
    return applied


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _metadata(data: dict) -> dict:
    meta = data.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            return {}
    return meta if isinstance(meta, dict) else {}


async def _find_user(db: AsyncSession, data: dict) -> User | None:
    meta = _metadata(data)
    user_id = meta.get("user_id")
    if user_id:
        try:
            user = await db.get(User, uuid.UUID(str(user_id)))
            if user is not None:
                return user
        except ValueError:
            pass
    customer = data.get("customer") or {}
    email = customer.get("email")
    if email:
        return await db.scalar(select(User).where(User.email == email.lower()))
    return None


async def _event_seen(db: AsyncSession, event_type: str, reference: str | None) -> bool:
    if not reference:
        return False
    existing = await db.scalar(
        select(func.count())
        .select_from(BillingEvent)
        .where(BillingEvent.event_type == event_type, BillingEvent.reference == reference)
    )
    return int(existing or 0) > 0


async def _refresh_subscription(db: AsyncSession, user: User, settings: Settings) -> None:
    """Pull the authoritative subscription state from Paystack (when keyed)."""
    if not user.paystack_subscription_code:
        return
    try:
        data = await _paystack(
            settings, "GET", f"/subscription/{user.paystack_subscription_code}"
        )
    except PaystackError as exc:
        logger.warning(f"Subscription refresh failed: {exc}")
        return
    sub_status = data.get("status")
    next_payment = _parse_datetime(data.get("next_payment_date"))
    if next_payment:
        user.plus_renews_at = next_payment
    if sub_status == "active":
        user.plan = PLAN_PLUS
        user.plus_cancel_at_period_end = False
    elif sub_status in ("cancelled", "completed", "expired", "non-renewing"):
        if sub_status == "non-renewing":
            user.plus_cancel_at_period_end = True
        elif next_payment is None or next_payment <= datetime.now(tz=timezone.utc):
            user.plan = PLAN_FREE
            user.plus_cancel_at_period_end = False
    await db.commit()
    await db.refresh(user)


async def process_webhook(
    db: AsyncSession, settings: Settings, raw_body: bytes, signature: str | None
) -> tuple[bool, str]:
    """Verify + process a Paystack webhook. Returns (accepted, message)."""
    if not verify_webhook_signature(settings, raw_body, signature):
        return False, "Invalid or missing x-paystack-signature"

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return False, "Malformed JSON body"
    event = payload.get("event")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return True, f"Ignored {event}: no data object"

    provider = PROVIDER_PAYSTACK
    purpose = _metadata(data).get("purpose")
    is_plus_payment = purpose == METADATA_PURPOSE or data.get("plan")

    if event == "charge.success" and is_plus_payment:
        user = await _find_user(db, data)
        if user is None:
            logger.warning("charge.success webhook: no matching PitchPilot user")
            return True, "No matching user"
        result = await _activate_from_charge(db, settings, user, data, payload)
        if result == "duplicate":
            return True, "Duplicate charge.success — already processed"

    elif event in ("subscription.create", "subscription.enable"):
        sub = data.get("subscription") or {}
        reference = sub.get("subscription_code")
        if await _event_seen(db, BillingEventType.SUBSCRIPTION_ACTIVATED, reference):
            return True, "Duplicate subscription event — already processed"
        user = await _find_user(db, data)
        if user is None:
            logger.warning(f"{event} webhook: no matching PitchPilot user")
            return True, "No matching user"
        now = datetime.now(tz=timezone.utc)
        user.plan = PLAN_PLUS
        user.plus_started_at = user.plus_started_at or now
        user.plus_cancel_at_period_end = False
        user.paystack_subscription_code = sub.get(
            "subscription_code", user.paystack_subscription_code
        )
        user.paystack_email_token = sub.get("email_token", user.paystack_email_token)
        next_payment = _parse_datetime(sub.get("next_payment_date"))
        if next_payment:
            user.plus_renews_at = next_payment
        db.add(
            BillingEvent(
                user_id=user.id,
                event_type=(
                    BillingEventType.SUBSCRIPTION_ACTIVATED
                    if event == "subscription.create"
                    else BillingEventType.SUBSCRIPTION_RENEWED
                ),
                provider=provider,
                reference=reference,
                amount=sub.get("amount"),
                currency=(data.get("plan") or {}).get("currency"),
                message="Plus subscription updated",
                raw_payload=payload,
            )
        )
        await db.commit()
        await db.refresh(user)
        logger.info(f"{event} processed for {user.email}")

    elif event == "subscription.disable":
        sub = data.get("subscription") or {}
        reference = sub.get("subscription_code")
        if await _event_seen(db, BillingEventType.SUBSCRIPTION_CANCELLED, reference):
            return True, "Duplicate subscription.disable — already processed"
        user = await _find_user(db, data)
        if user is None:
            logger.warning("subscription.disable webhook: no matching PitchPilot user")
            return True, "No matching user"
        user.plus_cancel_at_period_end = True
        next_payment = _parse_datetime(sub.get("next_payment_date"))
        if next_payment:
            user.plus_renews_at = next_payment
        db.add(
            BillingEvent(
                user_id=user.id,
                event_type=BillingEventType.SUBSCRIPTION_CANCELLED,
                provider=provider,
                reference=reference,
                message="Plus subscription disabled — ends at the period boundary",
                raw_payload=payload,
            )
        )
        await db.commit()
        logger.info(f"subscription.disable processed for {user.email}")

    else:
        return True, f"Ignored event: {event}"

    return True, "Processed"
