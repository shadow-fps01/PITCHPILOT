"""End-to-end account-lifecycle check against a running local API.

Covers: registration, email verification when required, recording consent,
session creation gate, data export, single-use password reset, JWT revocation,
wrong-password deletion rejection, and permanent account deletion.

Run from `backend/` with the API listening on 127.0.0.1:8000:

    .\\.venv\\Scripts\\python.exe scripts\\account_lifecycle_test.py
"""

from __future__ import annotations

import sys
import uuid

import httpx

from app.services.security import (
    create_email_verification_token,
    create_password_reset_token,
)

BASE = "http://127.0.0.1:8000/api"
PASSWORD = "PilotTest123!"
NEW_PASSWORD = "PilotTest456!"


def check(response: httpx.Response, expected: int, label: str) -> httpx.Response:
    if response.status_code != expected:
        raise AssertionError(
            f"{label}: expected {expected}, got {response.status_code}: {response.text}"
        )
    print(f"  ok  {label} ({response.status_code})")
    return response


def main() -> int:
    email = f"pilot-lifecycle-{uuid.uuid4().hex[:12]}@example.com"
    token: str | None = None
    user_id: str | None = None
    current_password = PASSWORD
    deleted = False

    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        try:
            check(client.get("/ready"), 200, "readiness")
            check(
                client.post(
                    "/auth/forgot-password",
                    json={"email": f"missing-{uuid.uuid4().hex[:8]}@example.com"},
                ),
                200,
                "forgot-password is generic for unknown email",
            )
            check(
                client.post(
                    "/auth/reset-password",
                    json={"token": "not-a-real-token", "password": PASSWORD},
                ),
                400,
                "invalid reset token rejected",
            )

            registered = check(
                client.post(
                    "/auth/register",
                    json={"email": email, "password": PASSWORD},
                ),
                201,
                "register",
            ).json()
            token = registered["access_token"]
            user_id = registered["user"]["id"]
            headers = {"Authorization": f"Bearer {token}"}
            print(f"  ..  created {email}")

            me = check(client.get("/auth/me", headers=headers), 200, "me").json()
            assert me["consent_current"] is False, me

            if not me["email_verified"]:
                verify_token = create_email_verification_token(uuid.UUID(user_id))
                check(
                    client.post("/auth/verify", json={"token": verify_token}),
                    200,
                    "verify email",
                )

            blocked = check(
                client.post("/sessions", headers=headers, json={"script_id": None}),
                403,
                "session blocked before consent",
            )
            assert "permission" in blocked.json()["detail"].lower()

            consented = check(
                client.post("/auth/consent", headers=headers),
                200,
                "accept recording consent",
            ).json()
            assert consented["consent_current"] is True, consented

            session = check(
                client.post("/sessions", headers=headers, json={"script_id": None}),
                201,
                "create session after consent",
            ).json()
            check(
                client.post(f"/sessions/{session['id']}/end", headers=headers),
                200,
                "end session",
            )

            export = check(client.get("/auth/export", headers=headers), 200, "export")
            exported = export.json()
            assert exported["user"]["email"] == email, exported["user"]
            assert exported["user"]["consent_version"], exported["user"]
            assert len(exported["sessions"]) >= 1, exported["sessions"]

            reset_token = create_password_reset_token(uuid.UUID(user_id), 0)
            check(
                client.post(
                    "/auth/reset-password",
                    json={"token": reset_token, "password": NEW_PASSWORD},
                ),
                200,
                "reset password",
            )
            current_password = NEW_PASSWORD
            check(
                client.get("/auth/me", headers=headers),
                401,
                "old access token revoked",
            )
            check(
                client.post(
                    "/auth/reset-password",
                    json={"token": reset_token, "password": PASSWORD},
                ),
                400,
                "reset link is single-use",
            )

            login = check(
                client.post(
                    "/auth/login",
                    json={"email": email, "password": NEW_PASSWORD},
                ),
                200,
                "login with new password",
            ).json()
            token = login["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            check(
                client.request(
                    "DELETE",
                    "/auth/account",
                    headers=headers,
                    json={"password": "wrong-password"},
                ),
                403,
                "delete rejects wrong password",
            )
            check(
                client.request(
                    "DELETE",
                    "/auth/account",
                    headers=headers,
                    json={"password": NEW_PASSWORD},
                ),
                204,
                "delete account",
            )
            deleted = True
            check(
                client.post(
                    "/auth/login",
                    json={"email": email, "password": NEW_PASSWORD},
                ),
                401,
                "deleted account cannot sign in",
            )

            print("\nACCOUNT LIFECYCLE TEST PASSED")
            return 0
        finally:
            # Best-effort cleanup if an assertion failed before deletion.
            if not deleted and token is not None:
                try:
                    client.request(
                        "DELETE",
                        "/auth/account",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"password": current_password},
                    )
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — CLI test surface
        print(f"\nACCOUNT LIFECYCLE TEST FAILED: {exc}")
        sys.exit(1)
