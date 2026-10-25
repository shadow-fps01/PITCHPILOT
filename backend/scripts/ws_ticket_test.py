"""Verify the WebSocket handshake only accepts short-lived tickets.

Run from `backend/` with the API on 127.0.0.1:8000:

    $env:PYTHONPATH='.'; .\\.venv\\Scripts\\python.exe scripts\\ws_ticket_test.py
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import websockets

BASE = "http://127.0.0.1:8000/api"
WS_BASE = "ws://127.0.0.1:8000/api"
PASSWORD = "WsTicketTest123!"


def check(response: httpx.Response, expected: int, label: str) -> httpx.Response:
    if response.status_code != expected:
        raise AssertionError(
            f"{label}: expected {expected}, got {response.status_code}: {response.text}"
        )
    print(f"  ok  {label} ({response.status_code})")
    return response


async def receive_until(ws, wanted: str, timeout: float = 20.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - loop.time()))
        message = json.loads(raw)
        if message.get("type") == wanted:
            return message
    raise AssertionError(f"timed out waiting for {wanted!r}")


async def main() -> int:
    email = f"pilot-ws-{uuid.uuid4().hex[:12]}@example.com"
    token: str | None = None
    headers: dict[str, str] = {}
    deleted = False

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as api:
        try:
            r = check(
                await api.post(
                    "/auth/register", json={"email": email, "password": PASSWORD}
                ),
                201,
                "register",
            )
            token = r.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            check(await api.post("/auth/consent", headers=headers), 200, "consent")
            r = check(
                await api.post("/sessions", headers=headers, json={"script_id": None}),
                201,
                "create session",
            )
            session_id = r.json()["id"]

            # The long-lived JWT is not a valid WebSocket credential.
            closed_code: int | None = None
            try:
                async with websockets.connect(
                    f"{WS_BASE}/ws/stream/{session_id}?token={token}"
                ) as ws:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    message = json.loads(raw)
                    assert message.get("type") == "error", message
                    assert "ticket" in message.get("message", "").lower(), message
                    await ws.wait_closed()
                    closed_code = ws.close_code
            except websockets.ConnectionClosed as exc:
                closed_code = exc.code
            assert closed_code == 4401, f"expected close 4401, got {closed_code}"
            print("  ok  JWT query rejected (4401)")

            r = check(
                await api.post("/auth/ws-ticket", headers=headers),
                200,
                "mint WS ticket",
            )
            ticket = r.json()["ticket"]
            async with websockets.connect(
                f"{WS_BASE}/ws/stream/{session_id}?ticket={ticket}",
                max_size=2**22,
            ) as ws:
                started = await receive_until(ws, "session_started")
                assert started["session_id"] == session_id, started
                print("  ok  ticket handshake + session_started")
                await ws.send(json.dumps({"type": "stop"}))
                await receive_until(ws, "session_ended", timeout=30.0)
                print("  ok  graceful stop + session_ended")

            check(
                await api.request(
                    "DELETE",
                    "/auth/account",
                    headers=headers,
                    json={"password": PASSWORD},
                ),
                204,
                "delete test account",
            )
            deleted = True
            print("\nWS TICKET TEST PASSED")
            return 0
        finally:
            if not deleted and token is not None:
                try:
                    await api.request(
                        "DELETE",
                        "/auth/account",
                        headers=headers,
                        json={"password": PASSWORD},
                    )
                except Exception:
                    pass


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 — CLI test surface
        print(f"\nWS TICKET TEST FAILED: {exc}")
        raise SystemExit(1)
