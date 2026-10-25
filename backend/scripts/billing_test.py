"""End-to-end billing test: free-plan quotas (3 sessions + 3 scripts per
month), the 402 paywall, Paystack checkout initialization and the signed
webhook that flips an account to Plus (unlimited).

Run from backend/ while the API is running:

    $env:PYTHONPATH='.'; .\\.venv\\Scripts\\python scripts\\billing_test.py
"""

import hashlib
import hmac
import json
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8000/api"
EMAIL = f"billing-test-{uuid.uuid4().hex[:8]}@example.com"
PASSWORD = "test-password-123"

# Windows consoles often default to cp1252 — force UTF-safe output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def main() -> None:
    from app.config.settings import get_settings

    settings = get_settings()
    client = httpx.Client(base_url=BASE, timeout=90.0)

    # 1 ── Register: every new account starts on the free plan.
    r = client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    check("register test user", r.status_code == 201, f"HTTP {r.status_code}")
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    check("new user starts on free plan", r.json()["user"]["plan"] == "free")

    # 1b ── Recording consent is a server-side gate before any session.
    r = client.post("/auth/consent", headers=headers)
    check("recording consent recorded", r.status_code == 200, f"HTTP {r.status_code}")

    # 2 ── Usage: 3 + 3 remaining on the free plan.
    usage = client.get("/billing/usage", headers=headers).json()
    check(
        "usage meters: 3 sessions + 3 scripts remaining",
        usage["plan"] == "free"
        and usage["sessions_remaining"] == 3
        and usage["scripts_remaining"] == 3,
        json.dumps(usage),
    )

    # 3 ── Session quota: 3 OK, the 4th is a 402 paywall.
    for i in range(1, 4):
        r = client.post("/sessions", json={"script_id": None}, headers=headers)
        check(f"coaching session {i}/3 created", r.status_code == 201, f"HTTP {r.status_code}")
    r = client.post("/sessions", json={"script_id": None}, headers=headers)
    check("4th session blocked -> HTTP 402", r.status_code == 402, r.json().get("detail", "")[:90])

    # 4 ── Script quota: 3 OK, the 4th is a 402 paywall.
    script_payload = {
        "title": "Billing test outline",
        "content_outline": "1. Problem 2. Solution 3. The ask",
        "target_wpm": 140,
        "keywords": ["pricing"],
    }
    for i in range(1, 4):
        r = client.post("/scripts", json=script_payload, headers=headers)
        check(f"script {i}/3 created", r.status_code == 201, f"HTTP {r.status_code}")
    r = client.post("/scripts", json=script_payload, headers=headers)
    check("4th script blocked -> HTTP 402", r.status_code == 402, r.json().get("detail", "")[:90])

    # 5 ── Usage reflects exhausted quotas.
    usage = client.get("/billing/usage", headers=headers).json()
    check(
        "usage meters: quotas exhausted",
        usage["sessions_remaining"] == 0 and usage["scripts_remaining"] == 0,
    )

    # 6 ── Checkout: Paystack returns a hosted authorization URL (or demo
    #      mode upgrades instantly when no key is configured).
    r = client.post("/billing/checkout", headers=headers)
    body = r.json()
    check("checkout initialized", r.status_code == 200, f"mode={body.get('mode')}")
    if r.status_code != 200:
        print(f"    checkout error: {r.text[:200]}")
    if r.status_code == 200 and body.get("mode") == "demo":
        # No Paystack key — the demo provider already upgraded the account.
        return finish_after_demo_upgrade(client, headers)
    url = body.get("authorization_url") or ""
    check(
        "Paystack authorization_url returned",
        isinstance(url, str) and url.startswith("https://"),
        url[:60],
    )

    # 6b ── Client-side verify fallback: an unknown reference must NOT
    #      activate anything (and must not crash the endpoint).
    r = client.post(
        "/billing/verify", json={"reference": "does-not-exist-000"}, headers=headers
    )
    verification = r.json() if r.status_code == 200 else {}
    check(
        "verify with unknown reference is a no-op",
        r.status_code == 200
        and verification.get("verified") is False
        and verification.get("billing", {}).get("is_plus") is False,
        f"HTTP {r.status_code} verified={verification.get('verified')}",
    )

    # 7 ── Webhook: signed charge.success flips the account to Plus.
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    payload = {
        "event": "charge.success",
        "data": {
            "reference": f"e2e-{uuid.uuid4().hex[:10]}",
            "amount": settings.plus_price_amount,
            "currency": settings.paystack_currency,
            "metadata": {"user_id": user_id, "purpose": "pitchpilot_plus"},
            "customer": {"email": EMAIL, "customer_code": f"CUS_{uuid.uuid4().hex[:8]}"},
            "plan": {"name": "PitchPilot Plus (Monthly)"},
        },
    }
    raw = json.dumps(payload).encode()
    signature = hmac.new(
        settings.paystack_secret_key.encode(), raw, hashlib.sha512
    ).hexdigest()
    r = client.post(
        "/billing/webhook", content=raw, headers={"x-paystack-signature": signature}
    )
    check("signed webhook accepted (200)", r.status_code == 200, r.text[:80])

    r = client.post(
        "/billing/webhook", content=raw, headers={"x-paystack-signature": "bad"}
    )
    check("bad signature rejected (401)", r.status_code == 401)

    # 8 ── Plus = unlimited: quotas are gone.
    usage = client.get("/billing/usage", headers=headers).json()
    check(
        "plan flipped to plus (unlimited)",
        usage["is_plus"] and usage["unlimited"] and usage["sessions_remaining"] is None,
        json.dumps(usage),
    )
    r = client.post("/sessions", json={"script_id": None}, headers=headers)
    check("unlimited sessions after upgrade", r.status_code == 201, f"HTTP {r.status_code}")
    r = client.post("/scripts", json=script_payload, headers=headers)
    check("unlimited scripts after upgrade", r.status_code == 201, f"HTTP {r.status_code}")

    # 9 ── Idempotency: replaying the same webhook changes nothing.
    r = client.post(
        "/billing/webhook", content=raw, headers={"x-paystack-signature": signature}
    )
    check("replayed webhook ignored", r.status_code == 200 and "Duplicate" in r.text, r.text[:80])

    # 10 ── Billing history is recorded.
    events = client.get("/billing/events", headers=headers).json()["events"]
    check("billing history recorded", len(events) >= 2, f"{len(events)} events")

    print()
    if failures:
        print(f"{len(failures)} billing check(s) FAILED:")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("All billing e2e checks passed ✔")


def finish_after_demo_upgrade(client: httpx.Client, headers: dict) -> None:
    """Demo mode: checkout already upgraded the account — verify unlimited."""
    usage = client.get("/billing/usage", headers=headers).json()
    check(
        "demo checkout upgraded to plus (unlimited)",
        usage["is_plus"] and usage["unlimited"],
        json.dumps(usage),
    )
    r = client.post("/sessions", json={"script_id": None}, headers=headers)
    check("unlimited sessions after upgrade", r.status_code == 201, f"HTTP {r.status_code}")
    print()
    if failures:
        print(f"{len(failures)} billing check(s) FAILED:")
        for name in failures:
            print(f"  - {name}")
        sys.exit(1)
    print("All billing e2e checks passed (demo mode) ✔")


if __name__ == "__main__":
    main()
