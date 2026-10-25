"""Transactional email sender (verification links).

Two providers are supported:
- `smtp` (default): classic SMTP, works on VPS hosts that allow outbound
  ports 25/465/587.
- `resend`: HTTPS API (`https://api.resend.com/emails`), for hosts that block
  outbound SMTP such as Render Free. Set `RESEND_API_KEY` and a verified
  `EMAIL_FROM` address.

Every failure is swallowed: a broken mailer must never break registration.
"""

import asyncio
import smtplib
from email.message import EmailMessage

import httpx
from loguru import logger

from app.config.settings import Settings

_RESEND_URL = "https://api.resend.com/emails"


def email_provider_configured(settings: Settings) -> bool:
    """True when the selected provider has the credentials it needs."""
    provider = settings.email_provider.strip().lower()
    if provider == "resend":
        return bool(settings.resend_api_key.strip())
    if provider == "smtp":
        return bool(settings.smtp_host.strip())
    return False


def _send_smtp_sync(settings: Settings, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def _send_resend(settings: Settings, to: str, subject: str, body: str) -> None:
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(_RESEND_URL, headers=headers, json=payload)
    if not response.is_success:
        # Keep provider response out of user-facing paths; log only the status.
        logger.warning(
            f"Resend rejected {subject!r} for {to}: "
            f"HTTP {response.status_code}"
        )
        response.raise_for_status()


async def send_email(settings: Settings, *, to: str, subject: str, body: str) -> bool:
    """Best-effort send; returns True when the provider accepted the message."""
    provider = settings.email_provider.strip().lower()
    if provider == "resend":
        if not settings.resend_api_key:
            logger.warning("RESEND_API_KEY missing — not sending verification email")
            return False
        try:
            await _send_resend(settings, to, subject, body)
            logger.info(f"Email sent to {to}: {subject}")
            return True
        except Exception as exc:  # noqa: BLE001 — email must never break the request
            logger.warning(f"Email send failed to {to}: {exc}")
            return False

    if provider != "smtp":
        logger.warning(f"Unknown EMAIL_PROVIDER={provider!r} — not sending email")
        return False
    if not settings.smtp_host:
        logger.warning(f"SMTP not configured — not sending {subject!r} to {to}")
        return False
    try:
        await asyncio.to_thread(_send_smtp_sync, settings, to, subject, body)
        logger.info(f"Email sent to {to}: {subject}")
        return True
    except Exception as exc:  # noqa: BLE001 — email must never break the request
        logger.warning(f"Email send failed to {to}: {exc}")
        return False


async def send_verification_email(settings: Settings, *, to: str, link: str) -> bool:
    body = (
        "Welcome to PitchPilot!\n\n"
        "Confirm your email address to start coaching:\n"
        f"{link}\n\n"
        "If you didn't create this account, you can safely ignore this message."
    )
    return await send_email(
        settings, to=to, subject="Verify your PitchPilot email", body=body
    )


async def send_password_reset_email(
    settings: Settings, *, to: str, link: str, expires_minutes: int
) -> bool:
    body = (
        "We received a request to reset your PitchPilot password.\n\n"
        f"Choose a new password here (the link expires in {expires_minutes} minutes):\n"
        f"{link}\n\n"
        "If you didn't request this, you can safely ignore this message — "
        "your password will not change."
    )
    return await send_email(
        settings, to=to, subject="Reset your PitchPilot password", body=body
    )
