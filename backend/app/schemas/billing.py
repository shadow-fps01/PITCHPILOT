"""Billing request/response schemas (PitchPilot Plus)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BillingStatus(BaseModel):
    """Everything the UI needs to render plan state and usage meters."""

    plan: str = Field(examples=["free"])
    is_plus: bool
    provider: str = Field(examples=["paystack"])  # paystack | demo
    payments_live: bool  # False → demo mode (mock upgrades)
    price_display: str = Field(examples=["$15"])
    price_amount: int = Field(examples=[1500])  # minor units
    currency: str = Field(examples=["USD"])
    free_monthly_sessions: int
    free_monthly_scripts: int
    sessions_used_this_month: int
    scripts_used_this_month: int
    # null when unlimited (Plus)
    sessions_remaining: int | None
    scripts_remaining: int | None
    unlimited: bool
    plus_started_at: datetime | None = None
    plus_renews_at: datetime | None = None
    plus_cancel_at_period_end: bool = False


class CheckoutResponse(BaseModel):
    mode: str = Field(examples=["paystack"])  # paystack | demo | already_plus
    authorization_url: str | None = None  # paystack hosted checkout page
    status: BillingStatus


class VerifyRequest(BaseModel):
    """Client-driven confirmation after returning from Paystack checkout."""

    reference: str = Field(min_length=4, max_length=100)


class VerifyResponse(BaseModel):
    verified: bool
    status: str
    message: str
    billing: BillingStatus


class BillingEventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    provider: str
    reference: str | None
    amount: int | None
    currency: str | None
    message: str | None
    created_at: datetime


class BillingEventListResponse(BaseModel):
    events: list[BillingEventPublic]


class WebhookAccepted(BaseModel):
    received: bool = True
    message: str | None = None
