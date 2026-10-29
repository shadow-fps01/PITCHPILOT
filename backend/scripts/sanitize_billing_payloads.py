"""Remove card/customer PII from historical Paystack billing payloads.

New webhook writes are sanitized at the source (see
``app.services.billing.sanitize_payload``), but older ``billing_events`` rows
may still contain the full Paystack ``authorization`` object (card bin, last4,
reusable signature) and customer email/phone/address.

This script is a dry run by default. Pass ``--apply`` to update rows. Always
take a database backup and get approval before running it against production.

Usage (from backend/, with the production DATABASE_URL loaded):
    .venv\\Scripts\\python.exe scripts/sanitize_billing_payloads.py
    .venv\\Scripts\\python.exe scripts/sanitize_billing_payloads.py --apply
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.session import async_session_factory, engine
from app.models.billing_event import BillingEvent
from app.services.billing import sanitize_payload


async def _run(apply: bool) -> int:
    changed = 0
    scanned = 0
    async with async_session_factory() as db:
        rows = (
            await db.scalars(
                select(BillingEvent).where(BillingEvent.raw_payload.is_not(None))
            )
        ).all()
        for row in rows:
            scanned += 1
            safe = sanitize_payload(row.raw_payload)
            if safe == row.raw_payload:
                continue
            changed += 1
            if apply:
                row.raw_payload = safe
        if apply and changed:
            await db.commit()
    await engine.dispose()
    verb = "updated" if apply else "would update"
    print(f"scanned={scanned} {verb}={changed}")
    if not apply and changed:
        print("Dry run only. Re-run with --apply after reviewing the count.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the sanitized payloads (default: dry run)",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
