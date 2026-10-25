"""Copilot dialogue smoke test (Cluely-style copilot).

Part 1 (offline) — trigger heuristics + keyless fallback, no network.
Part 2 (live)    — full WebSocket flow against a running backend:
                   auth → session → WS → `ask` → copilot `answer`.
                 — registers a fresh throwaway user each run (free quota).

Run (backend must be up, e.g. `npm run dev` in frontend/):
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\copilot_test.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows consoles often default to cp1252 — force UTF-safe output (the box-
# drawing glyphs below otherwise raise UnicodeEncodeError).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402
import websockets  # noqa: E402

from app.config.settings import get_settings  # noqa: E402
from app.services.copilot_chat import (  # noqa: E402
    CopilotChat,
    HeuristicCopilot,
    _looks_like_objection,
    _looks_like_question,
)

BASE = "http://127.0.0.1:8000/api"
WS_BASE = "ws://127.0.0.1:8000/api"
# A fresh user per run: free-tier quota is 3 sessions/month, so a reused
# account eventually trips the 402 paywall and blocks the live check.
EMAIL = f"pitchpilot.copilot.{__import__('time').strftime('%Y%m%d%H%M%S')}@example.com"
PASSWORD = "demodemo123"


def part1_offline() -> bool:
    print("── Part 1: offline trigger heuristics + fallback ──")
    checks = [
        # (text, is_question, is_objection) — the two flags are independent:
        # "What is your pricing?" is legitimately both.
        ("What is your pricing?", True, True),
        ("How does the integration work?", True, True),  # "integration" is an objection keyword too
        ("That is too expensive for our budget.", False, True),
        ("We already use a competitor.", False, True),
        ("I will think about it.", False, True),
        ("Sounds good, thanks.", False, False),
    ]
    ok = True
    for text, want_q, want_obj in checks:
        got_q = _looks_like_question(text)
        got_obj = _looks_like_objection(text)
        status = "OK " if (got_q == want_q and got_obj == want_obj) else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  [{status}] {text!r:45} question={got_q} objection={got_obj}")

    fallback = HeuristicCopilot()
    answer = fallback.answer_question(
        "what about pricing", "we spoke about the product a lot", None,
        "1. Problem\n2. Pricing: 15 dollars per seat", ["pricing"],
    )
    print(f"  [{'OK ' if answer else 'FAIL'}] heuristic fallback answer: {answer!r}")
    return ok and answer is not None


async def part2_live() -> bool:
    print("── Part 2: live WS copilot dialogue ──")
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as api:
        r = await api.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
        if r.status_code == 409:
            r = await api.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        r.raise_for_status()
        token: str = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("  [auth] OK")

        r = await api.post("/auth/consent", headers=headers)
        r.raise_for_status()
        print("  [consent] OK")

        r = await api.post("/sessions", json={"script_id": None}, headers=headers)
        r.raise_for_status()
        session_id: str = r.json()["id"]
        print(f"  [session] OK ({session_id})")

        r = await api.post("/auth/ws-ticket", headers=headers)
        r.raise_for_status()
        ticket: str = r.json()["ticket"]

    uri = f"{WS_BASE}/ws/stream/{session_id}?ticket={ticket}"
    got_status = False
    got_answer = False
    deadline = asyncio.get_event_loop().time() + 90.0

    async with websockets.connect(uri, max_size=2**22) as ws:
        await ws.send(json.dumps({"type": "ping", "timestamp": 0.0}))
        await ws.send(
            json.dumps(
                {
                    "type": "ask",
                    "id": "q1",
                    "text": "The customer asked what happens after the free trial — "
                    "what should I tell them?",
                }
            )
        )
        print("  [ws] connected, asked a question — waiting for the copilot…")
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    print("  [FAIL] overall deadline hit before the answer arrived")
                    break
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 30.0))
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                mtype = message.get("type")
                if mtype == "status":
                    got_status = True
                    print(
                        f"  [status] copilot_active={message.get('copilot_active')} "
                        f"vision_active={message.get('vision_active')}"
                    )
                elif mtype == "copilot":
                    kind = message.get("kind")
                    if kind == "answer" and message.get("reply_to") == "q1":
                        got_answer = True
                        print(f"  [copilot answer] {message['text'][:220]}")
                        break
                    if kind == "status":
                        print(f"  [copilot status] {message['text'][:160]}")
                elif mtype == "error":
                    print(f"  [server error] {message.get('message')}")
        except (asyncio.TimeoutError, TimeoutError):
            print("  [FAIL] timed out waiting for the copilot answer")
        finally:
            await ws.send(json.dumps({"type": "stop"}))

    if got_status and got_answer:
        print("  [PASS] copilot dialogue flow works end-to-end")
        return True
    return False


async def main() -> int:
    offline_ok = part1_offline()
    print()
    try:
        live_ok = await part2_live()
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] live part failed: {exc}")
        live_ok = False
    print()
    print(f"Result: offline={'PASS' if offline_ok else 'FAIL'} live={'PASS' if live_ok else 'FAIL'}")
    return 0 if (offline_ok and live_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
