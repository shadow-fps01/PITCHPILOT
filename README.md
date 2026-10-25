# PitchPilot 🎙️

**A just-in-time live public speaking & Zoom pitch coach.**

PitchPilot is an AI-powered SaaS MVP that sits as a lightweight floating overlay
while you pitch on calls. It listens to your microphone, transcribes in real
time, and flashes translucent coaching cues — *"Slow down"*, *"You're saying
'um' too much"*, *"Time to mention the pricing discount"* — that only you can
see. Your screen-shared call window stays completely clean.

```
┌────────────────────────────────────────────────────────────────────┐
│                        ┌──────────────────────┐                    │
│   Zoom / Meet call     │  PitchPilot overlay  │  ← cues only you   │
│   (screen-shared)      │  ┌────────────────┐  │    can see         │
│                        │  │ ⚠ Ease off —    │  │                    │
│   Presenter speaks ───▶│  │ 178 WPM        │  │                    │
│                        │  │ um fillers: 4  │  │                    │
│                        │  │ ⏱ 2:14  ░░░░░  │  │                    │
│                        │  └────────────────┘  │                    │
│                        └──────────▲───────────┘                    │
│                                   │ 16 kHz PCM @160 ms              │
│                                   │ WS (duplex)                     │
│                        ┌──────────┴───────────┐                    │
│                        │  FastAPI backend     │                    │
│                        │  • STT providers     │                    │
│                        │  • Pacing engine     │                    │
│                        │  • Filler tracker    │                    │
│                        │  • LLM script audit  │──▶ PostgreSQL      │
│                        └──────────────────────┘                    │
└────────────────────────────────────────────────────────────────────┘
```

## Tech stack

| Layer     | Tech                                                                 |
| --------- | -------------------------------------------------------------------- |
| Frontend  | React 18 (Vite), TypeScript (strict), Tailwind CSS, Radix UI, Lucide |
| Backend   | Python 3.11+ / FastAPI (async), Loguru, Pydantic v2                  |
| Realtime  | WebSocket duplex stream — Deepgram Live / Groq / Azure OpenAI / OpenAI Realtime / **mock** |
| Database  | PostgreSQL, SQLAlchemy 2.0 (async), Alembic migrations              |
| Analytics | Pacing (WPM/5 s window), filler-word burst tracking, LLM script auditor |

## Quick start

### 0. Prerequisites

- **Node.js ≥ 18** and npm
- **Python ≥ 3.11** (3.14 verified)
- **PostgreSQL** — any local install, **or** your Supabase project (see below)

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# Configure (backend/.env is ALREADY pointed at the verified Supabase
# project; see the Database section below):
notepad .env

# Apply migrations (already applied to the configured Supabase project)
.\.venv\Scripts\alembic upgrade head

# Run the API on :8000 — run.py pins the wsproto WebSocket backend
# (uvicorn's default websockets impl stalls live sockets on
# Python 3.14 + websockets 17; see requirements.txt)
.\.venv\Scripts\python run.py
# …or manually:
# .\.venv\Scripts\uvicorn app.main:app --reload --port 8000 --ws wsproto
```

Health check: <http://localhost:8000/api/health>

### 2. Database — already wired to your Supabase project

`backend/.env` holds the app-specific configuration; your database connection
string lives in the local (git-ignored) `.env`:

```
DATABASE_URL=postgresql+asyncpg://postgres.<project-ref>:<password-url-encoded>@aws-1-<region>.pooler.supabase.com:5432/postgres
```

Notes:

- The direct host `db.<ref>.supabase.co` is IPv6-only and does not resolve on
  this machine — use the **session-mode pooler** URL above (username is
  `postgres.<project-ref>`; the password must be URL-encoded, `#` → `%23`).
- The initial migration (`0001_initial`) has been applied to that project, so
  the four PitchPilot tables (`users`, `scripts`, `coaching_sessions`,
  `session_events`) already exist alongside your other apps' tables
  (`admins`, `businesses`, `invitations`, `payments`) — no name collisions.
- To point at a different Postgres instead, just change `DATABASE_URL` and run
  `alembic upgrade head`.

### 3. Frontend + API together

```powershell
cd frontend
npm install
npm run dev          # boots BOTH: FastAPI on :8000 and Vite on :5173
```

`npm run dev` (or `npm start`) auto-starts the backend from `backend/.venv`
(uvicorn with `--ws wsproto`, `.env` loaded from `backend/`) and stops it when
Vite exits. The frontend links to the API same-origin — Vite proxies
`/api` (REST **and** WebSocket) to `http://127.0.0.1:8000`, so there are no
CORS concerns and the overlay's live stream works out of the box.

- If a backend is **already running** on :8000, the dev server detects and
  reuses it instead of starting a second one.
- To run the frontend alone: `$env:PITCHPILOT_NO_API='1'; npm run dev`.
- Open <http://localhost:5173> — health check via the proxy:
  <http://localhost:5173/api/health>

### 4. Pitch!

1. Run `npm run dev` in `frontend/` (backend + database come up with it).
2. Register an account.
3. *(Optional)* Create a script: outline + target WPM + keywords
   (`pricing`, `discount`, `the ask`…).
4. **Start coaching** — or click **Floating overlay (share-safe)** for an
   always-on-top PiP window you park next to your Zoom/Meet call. It stays
   out of tab/window screen shares; if you must share your entire screen,
   use the Electron shell (`desktop/`) instead.
5. Click **Start speaking**, and PitchPilot cues you live.

> **Zero-API-key demo mode** — with no STT keys configured, the backend's
> mock provider streams a simulated filler-heavy pitch (~180 WPM), so pacing
> alerts, filler bursts and script-miss cues all fire end-to-end. Production
> boots reject the mock provider; set `STT_PROVIDER=groq` and `GROQ_API_KEY`
> for live transcription.

## Real-time transcription providers

Configure in `backend/.env` — API keys may live in the **workspace-root `.env`**
(which the backend loads as its secrets source; `backend/.env` overrides it for
app-specific config):

| `STT_PROVIDER` | Requirement            | Notes                                              |
| -------------- | ---------------------- | -------------------------------------------------- |
| `auto`         | any key below, or none | Deepgram → Groq → Azure OpenAI → OpenAI → mock     |
| `deepgram`     | `DEEPGRAM_API_KEY`     | Linear16 16 kHz over `wss://api.deepgram.com`      |
| `groq`         | `GROQ_API_KEY`         | **Recommended.** Near-real-time rolling segments → `whisper-large-v3-turbo` (Groq's OpenAI-compatible REST API); finals only, no interims |
| `azure`        | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_STT_DEPLOYMENT` | Rolling segments via Azure OpenAI `audio/transcriptions`; deployment name, not model name |
| `openai`       | `OPENAI_API_KEY`       | OpenAI Realtime `gpt-4o-transcribe`                |
| `mock`         | nothing                | Deterministic demo speaker (rejected in production) |

**Groq specifics** — Groq exposes batch transcription rather than a duplex
WebSocket, so `GroqBatchSTT` buffers the live PCM stream into rolling
`GROQ_STT_SEGMENT_SECONDS` (default 3 s) segments, wraps each as a WAV, and
POSTs it to `https://api.groq.com/openai/v1/audio/transcriptions` with a
continuity `prompt` (the previous segment's tail). Effective cue latency ≈
segment + one HTTP round-trip. Near-silent segments are skipped (no wasted
calls), the buffer is capped so slow networks drop the oldest audio, and one
failed segment never breaks the stream.

**Azure OpenAI specifics (optional alternative)** — this provider reuses the same
`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` as the chat client, so one
Azure resource key covers both. It needs an **audio deployment** in that
resource: in Azure AI Foundry → Deployments, create a `whisper` (or
`gpt-4o-transcribe`) deployment and put the **deployment name** in
`AZURE_OPENAI_STT_DEPLOYMENT` — not the model name. The default
`AZURE_OPENAI_STT_API_VERSION=2024-06-01` works for Whisper; use the version
required by your region/model if you choose a newer transcribe model. The
provider uses the same rolling-segment, silence-gating and retry behaviour as
Groq. Verify with:

```powershell
cd backend
$env:PYTHONPATH='.'; .\.venv\Scripts\python scripts\azure_stt_test.py
```

If Azure replies `DeploymentNotFound`, the deployment name is wrong (or the
deployment has not finished creating); if it replies `OperationNotSupported`,
the deployment is a text-only model such as `gpt-4o`, not an audio model.

The recommended production setting is **`STT_PROVIDER=groq`** with
`GROQ_API_KEY` set. With `STT_PROVIDER=auto`, Deepgram wins when configured,
then Groq, then Azure OpenAI, then OpenAI Realtime. Verify Groq with:

```powershell
cd backend
$env:PYTHONPATH='.'; .\.venv\Scripts\python scripts\groq_stt_test.py
```

The browser captures the mic via `AudioWorklet`, downsamples to 16 kHz 16-bit
mono PCM, and streams frames every ~160 ms.

## Script-auditor LLM

`LLM_PROVIDER=auto` picks the first available backend for the Contextual
Script Auditor (which periodically compares your recent transcript against
your outline/keywords):

1. **Azure OpenAI** — set `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`,
   `AZURE_OPENAI_CHAT_DEPLOYMENT`
2. **OpenRouter** — set `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
3. **Heuristic** — keyword-matching fallback; always works, no keys

## The Cluely-style Live Copilot dialogue 💬

The overlay is a **dialogue box** — think Cluely, built into PitchPilot:

- **Proactive suggestions** — while you speak, a copilot engine watches the
  rolling call transcript (both sides with native capture) *and* the live
  screen-vision feed. When the other party asks a question, raises an
  objection (pricing, competitors, "think about it"…), or the conversation
  needs a next move, it posts a ready-to-speak suggestion straight into the
  dialogue: *"Say this — …"*.
- **Ask anything** — type into the box at the bottom (or tap the quick chips:
  *Answer that* / *What should I say next?* / *See my screen*). The copilot
  answers using the transcript, your script outline and — with screen vision
  on — a **fresh screenshot taken at ask-time**.
- **Screen toggle** — the `screen on/off` chip flips the vision worker live
  (`{type:"screen"}` over the WebSocket), no restart.
- **One feed** — coaching cues (fillers, pacing) and RAG "Next up" cards
  flow through the same dialogue as compact system lines. Everything is
  persisted as `COPILOT_ASSIST` session events and shown in the session
  detail view.
- **Share-safe by design** — the dialogue lives in the protected overlay
  window (or the Electron shell), never in your screen share.

LLM chain: **Azure OpenAI (gpt-4o) → OpenRouter → keyless heuristic
fallback** — the same escalation used by the script auditor, so it works
with zero keys (demo mode), and answers still flow when a provider fails.

Settings (`backend/.env`):

```ini
COPILOT_ENABLED=true                    # master switch (default on)
COPILOT_SUGGEST_MIN_GAP_SECONDS=14      # anti-spam between proactive suggestions
COPILOT_SUGGEST_EVERY_WORDS=90          # periodic "what to say next" cadence
COPILOT_MIN_WORDS_TO_SUGGEST=8          # skip cold-start chatter
# Agent prompts — all configurable, none hardcoded:
# VISION_PROMPT / VISION_ASK_PROMPT / COPILOT_SUGGEST_PROMPT / COPILOT_ASK_PROMPT
```

### Flaky-DNS shield

This machine's resolver intermittently fails lookups for API hosts. The
backend wraps `getaddrinfo` in an in-process **cache with stale-on-error
fallback** (`DNS_CACHE_ENABLED=true`, installed app-wide from
`app.services.dns_cache`) — once a host resolves successfully, resolver
blips serve the last known-good address instead of dropping the STT segment
or LLM answer.

Smoke test (backend must be running):

```powershell
cd backend
$env:PYTHONPATH='.'; .\.venv\Scripts\python scripts\copilot_test.py
# → offline trigger heuristics + live WS ask/answer, registers a throwaway user
```

## WebSocket protocol — `GET /api/ws/stream/{session_id}?ticket=<ticket>`

Mint the ticket first with `POST /api/auth/ws-ticket` (authenticated). Tickets
are short-lived and bound to the user. Long-lived JWTs in the query string are
rejected, so bearer tokens cannot leak into URLs or proxy access logs.

Client → Server:

| Frame      | Payload                                        |
| ---------- | ---------------------------------------------- |
| binary     | 16 kHz 16-bit PCM frame (~160 ms)              |
| JSON `ping`| `{"type":"ping","timestamp":...}` heartbeat    |
| JSON `stop` | `{"type":"stop"}` — end session gracefully     |

Server → Client (typed in `src/types/index.ts` ↔ `app/schemas/stream.py`):

| `type`            | Meaning                                              |
| ----------------- | ---------------------------------------------------- |
| `session_started` | Handshake complete, clock running                    |
| `transcript`      | Interim/final transcript fragment                    |
| `metrics`         | `wpm`, `filler_word_count`, `elapsed_seconds`       |
| `cue`             | `FILLER_WORD` / `PACING_ALERT` / `SCRIPT_MISS` cue   |
| `copilot`         | Copilot dialogue message (suggestion / answer / status) |
| `session_ended`   | Final summary (fillers, pacing, misses, duration)    |
| `pong` / `error`  | Heartbeat reply / protocol errors                    |

Client → Server (JSON):

| `type`     | Meaning                                              |
| ---------- | ---------------------------------------------------- |
| `ping`     | `{"type":"ping","timestamp":...}` heartbeat          |
| `stop`     | `{"type":"stop"}` — end session gracefully           |
| `capture`  | `{"type":"capture","action":"start"\|"stop"}` native dual-audio |
| `ask`      | `{"type":"ask","id":"...","text":"..."}` copilot question |

**Resilience** — the client reconnects with exponential backoff (1 s → 15 s,
±20 % jitter) while buffering audio frames; the server keeps the session's
coach state alive for `WS_RECONNECT_GRACE_SECONDS` (default 30 s), so a
dropped mid-call connection resumes the *same* session.

## REST API

| Method   | Route                        | Purpose                          |
| -------- | ---------------------------- | -------------------------------- |
| `POST`   | `/api/auth/register`         | Create account → JWT             |
| `POST`   | `/api/auth/login`            | Sign in → JWT                    |
| `GET`    | `/api/auth/me`               | Current user (+ plan, consent)   |
| `POST`   | `/api/auth/forgot-password`  | Request a reset link (generic)   |
| `POST`   | `/api/auth/reset-password`   | Set a new password (single-use)  |
| `POST`   | `/api/auth/consent`          | Record recording consent         |
| `GET`    | `/api/auth/export`           | Download account data (JSON)     |
| `DELETE` | `/api/auth/account`          | Delete account + cancel billing  |
| `GET`    | `/api/scripts`               | List scripts                     |
| `POST`   | `/api/scripts`               | Create script *(free: 3/month)*  |
| `PUT`    | `/api/scripts/{id}`          | Update script                    |
| `DELETE` | `/api/scripts/{id}`          | Delete script                     |
| `POST`   | `/api/sessions`              | Create coaching session *(free: 3/month)* |
| `GET`    | `/api/sessions`              | List sessions                    |
| `GET`    | `/api/sessions/{id}`         | Detail + events + summary         |
| `POST`   | `/api/sessions/{id}/end`     | Force-close a session            |
| `GET`    | `/api/billing/usage`         | Plan + monthly usage meters      |
| `POST`   | `/api/billing/checkout`      | Start Plus upgrade (Paystack URL or demo) |
| `POST`   | `/api/billing/verify`        | Confirm a payment by reference (webhook fallback) |
| `POST`   | `/api/billing/cancel`        | Cancel Plus (Paystack: at period end) |
| `GET`    | `/api/billing/events`        | Payment & plan-change history   |
| `POST`   | `/api/billing/webhook`       | Paystack webhook receiver (HMAC-SHA512) |
| `GET`    | `/api/health`                | Liveness                         |
| `GET`    | `/api/ready`                 | Readiness (database round-trip)  |

Interactive docs: <http://localhost:8000/docs>

## Billing — PitchPilot Plus 💳

| Plan | Price | Limits                                                     |
| ---- | ----- | ---------------------------------------------------------- |
| Free | ₦0    | **3 coaching sessions + 3 scripts per calendar month**     |
| Plus | ₦22,500/month | **Unlimited** sessions, scripts and every copilot feature |

- Free users hitting a quota get `HTTP 402` from `POST /api/sessions` or
  `POST /api/scripts`; the dashboard catches it and opens the upgrade dialog.
- Usage meters reset on the 1st of each month (counted by `started_at` /
  `created_at` within the current calendar month).

### How it works

1. `POST /api/billing/checkout` creates (or reuses) a Paystack **customer**,
   resolves the monthly **plan** (`PitchPilot Plus (Monthly)`, idempotent by
   name + amount + currency), then initializes a transaction and returns the
   hosted **`authorization_url`** — the frontend redirects the user there.
2. Paystack redirects back to `/?view=dashboard&billing=success&reference=…`;
   the dashboard confirms the payment via `POST /api/billing/verify` (direct
   Paystack verification — the idempotent fallback that makes checkout work
   even where webhooks can't reach the host, e.g. localhost dev) and then
   polls `GET /api/billing/usage` briefly while the webhook lands.
3. `POST /api/billing/webhook` verifies the `x-paystack-signature`
   (HMAC-SHA512 over the raw body) and processes `charge.success`,
   `subscription.create`, `subscription.enable` and `subscription.disable` —
   flipping `users.plan`, storing subscription codes and renewal dates, and
   logging every event to `billing_events` (shown in the **Plus** tab).
   Duplicate events are ignored (reference-idempotent).
4. Cancelling (`POST /api/billing/cancel`) calls Paystack's
   `/subscription/disable` — Plus stays active until the period ends, then a
   lazy check downgrades the account on the next API call.

### Configuration (`backend/.env`)

```ini
PAYMENTS_PROVIDER=auto        # auto | paystack | demo
PAYSTACK_CURRENCY=NGN         # this merchant has USD disabled on Paystack
PLUS_PRICE_AMOUNT=2250000     # kobo: ₦22,500
PLUS_PRICE_DISPLAY=₦22,500
FREE_MONTHLY_SESSIONS=3
FREE_MONTHLY_SCRIPTS=3
FRONTEND_BASE_URL=http://localhost:5173   # Paystack redirects back here
# PAYSTACK_SECRET_KEY lives in the workspace-root .env (auto-detected)
```

**Demo mode** — with no `PAYSTACK_SECRET_KEY` configured, `auto` resolves to
the demo provider: the checkout endpoint upgrades the account instantly (no
real charge), mirroring the STT/LLM zero-key demo conventions.

**Webhook URL for Paystack** (Dashboard → Settings → API Keys & Webhooks):
`https://<your-host>/api/billing/webhook`

## Project structure

```
backend/
  app/
    api/          auth.py · billing.py (Paystack + demo) · scripts.py · sessions.py · stream.py (WS)
    services/     billing.py · ai_coach.py · copilot_chat.py (Cluely dialogue) · audio_processor.py · llm_client.py
                  session_registry.py · stt/ (base · deepgram · openai_realtime · mock)
    models/       SQLAlchemy 2.0 declarative (users, billing_events, scripts, coaching_sessions, session_events)
    schemas/      Pydantic v2 request/response + WS protocol models
    config/       Pydantic Settings (.env)
    db/            async engine + session factory
  alembic/         async migrations (0001_initial_tables · 0002_knowledge_cards · 0003_plus_billing · 0004_email_verification · 0005_account_lifecycle)
frontend/
  src/
    components/   Dashboard · UpgradeDialog (paywall) · OverlayWidget (copilot dialogue box) · AnalyticsCard · SettingsForm · AuthForm · WpmGauge · CueToast · Teleprompter
    hooks/         useAudioRecorder (AudioWorklet PCM) · useWebSocket (backoff reconnect)
    context/       AuthContext · AppContext
    types/         strict TS mirrors of every Pydantic model
    lib/           typed REST client · utils
  public/worklets/ pcm-recorder.js (48 k → 16 k downsample + Int16)
```

## Copilot upgrades (advanced, ultra-low-latency)

The five upgrade features integrate into the existing loops — no separate app:

### F1 — Native dual audio capture (mic + system loopback)
`app/services/capture.py` — a background `soundcard` (WASAPI) service that
captures the microphone **and** the system-audio loopback simultaneously,
mixes both channels, resamples to 16 kHz mono PCM and feeds ~50 ms frames
straight into the live STT provider (sub-200 ms chunking). Opt in with
`AUDIO_CAPTURE_ENABLED=true`; in the overlay click **Native capture** — no
browser mic needed, and both sides of the call get transcribed.

### F2 — Screen-share-invisible overlay (web + Electron)
Two flavours, same goal — you see the copilot, your call never does:

**Web (Chrome/Edge) — always-on-top share-safe PiP.** The dashboard's
**Floating overlay (share-safe)** button opens the overlay as a **Document
Picture-in-Picture** window (`src/lib/pipOverlay.ts`): it floats above all
other windows Cluely-style and, because conferencing software captures the
*shared surface*, it is **never included when you share a tab or an app
window** — share the call tab/window, not your entire screen, and the
overlay is invisible to the call. The overlay shows a **share-safe** badge
and a reminder about share mode. Auth re-hydrates from localStorage, so the
PiP root behaves exactly like the detached window (own session, own feed).
Browsers without Document PiP (Firefox/Safari) automatically fall back to a
regular popup window.

**Desktop (Electron) — OS-level capture exclusion.** `desktop/` wraps the
same web overlay: `win.setContentProtection(true)`
(WDA_EXCLUDEFROMCAPTURE on Windows) makes the window invisible to screen
recording/conferencing software *even in full-screen shares*, while staying
fully visible on the monitor. Use this when you must share your whole screen.

```powershell
cd desktop ; npm install ; npm start   # loads http://localhost:5173/?view=live
```

### F3 — Zero-prompt next-topic anticipation (RAG)
`app/services/anticipation.py` + `knowledge_cards` (pgvector on your Supabase
project). Script outlines and a seeded Q&A deck are embedded with
`gemini-embedding-001`; as you speak, semantic triggers (questions, "the
ask", "pricing"…) fire an async vector search and pre-fetch the best answer
card **before you finish the thought** — surfaced in the overlay as
"Next up" cards and persisted as `CONTEXT_CARD` session events.

### F4 — Voice-responsive teleprompter
`Teleprompter.tsx` + `useTeleprompter.ts` — the script outline renders as a
live prompt: spoken words dim green, the next ~10 words highlight, auto-scroll
follows actual transcript coverage (it waits when you wait), mic amplitude
pulses the current word, WPM drives the progress-bar colour, and a stalled
line gently breathes as a keep-going nudge. Toggle it in the overlay when a
script with an outline is active.

### F5 — Multi-modal screen vision (restored, prompts configurable)
`app/services/vision.py` — a silent background worker grabs the primary
display every 2 s, perceptual-hash gates unchanged frames, rate-limits the
API calls, and feeds the description into the copilot, script-auditor and
RAG prompts. **No agent prompt is hardcoded** — every prompt lives in
settings and can be overridden from `backend/.env`:

```ini
VISION_PROMPT=…            # generic screen-description prompt
VISION_ASK_PROMPT=…        # ask-time prompt; may contain {question}
COPILOT_SUGGEST_PROMPT=…   # proactive suggestion system prompt (keep JSON reply shape)
COPILOT_ASK_PROMPT=…       # typed-question system prompt (keep JSON reply shape)
```

Vision provider chain (first usable answer wins): `gemini-3.5-flash` →
`gemini-3.5-flash-lite` (higher free RPM) → **Azure gpt-4o** → NVIDIA NIM.
A provider that 429s cools down 5 min while the next one answers; any other
failure cools down 60 s. The worker is strictly opt-in: set
`VISION_ENABLED=true` to start it with the API (off by default, and off in the
container deployment).

Everything runs asynchronously: capture on daemon threads with asyncio
hand-off, RAG/vision as fire-and-forget tasks, and every external call is
cold-DNS-warmed (DB pool, STT connection) or retry-protected so transient
resolver blips never drop the first seconds of a pitch.

## Verify your setup

Runnable checks live in `backend/scripts/` (run from `backend/`, with
`$env:PYTHONPATH='.'` and the API running for the e2e test):

| Script | What it proves |
| ------ | -------------- |
| `check_copilot.py` | Copilot imports, config, audio device enumeration |
| `test_gemini.py` | Gemini embedding key works (768-dim vectors) |
| `test_anticipation.py` | RAG loop: seed → trigger → pgvector search → card + cooldown |
| `test_vision.py` | Screenshot → live vision chain → real screen description |
| `copilot_test.py` | Copilot dialogue: offline triggers + live WS ask → answer |
| `azure_stt_test.py [wav]` | Azure OpenAI audio deployment: real speech WAV → transcript segments |
| `groq_stt_test.py [wav]` | Groq STT: real speech WAV → transcript segments |
| `account_lifecycle_test.py` | Register → consent gate → export → single-use reset → JWT revocation → delete |
| `ws_ticket_test.py` | JWT query rejected → short-lived ticket handshake → graceful stop |
| `e2e_test.py --audio <wav>` | Full stack over the live WS: real audio → configured STT provider → cues → RAG cards → persistence |
| `billing_test.py` | Monetization loop: 3+3 free quota → 402 paywall → Paystack checkout → signed webhook → Plus (unlimited) |

## Deploy with Docker

The repo ships a production-shaped stack: an **nginx-served SPA** (`web`) and a
**single-worker FastAPI** service (`api`). Postgres stays on Supabase (it needs
the `vector` extension).

```powershell
copy .env.production.example .env.production   # fill in every <placeholder>
docker compose up -d --build
```

- `web` listens on `:80` (`HTTP_PORT` overrides) and proxies `/api` — including
  the WebSocket upgrade — to `api:8000`. The SPA is same-origin, so there is no
  cross-origin/CORS setup.
- `api` applies migrations (`alembic upgrade head`) before booting uvicorn with
  **exactly one worker** and `--ws wsproto`.
- Put a TLS-terminating proxy or load balancer in front (or mount certificates
  in `frontend/nginx.conf`) and serve over HTTPS so the live socket uses WSS.
- `ENVIRONMENT=production` makes the API **refuse to start** with insecure
  configuration: a default/placeholder JWT secret or provider credential,
  `AUTO_CREATE_TABLES=true`, wildcard/localhost/placeholder CORS or public
  URLs, demo billing without `ALLOW_DEMO_BILLING=true`, a missing/simulated STT
  provider, or email verification without a configured email provider.
- Health checks: `GET /api/health` (liveness), `GET /api/ready` (database
  readiness), and `GET /healthz` (web). Both images define Docker
  `HEALTHCHECK`s.
- Leave `AUDIO_CAPTURE_ENABLED` and `VISION_ENABLED` **off** in containers:
  native audio capture and screen capture are desktop features (the Electron
  shell), and there is no audio device or display on a server.

### Operational notes

- **One API worker, always.** Live sessions keep their registry in process; a
  second worker would accept sockets it cannot service. The image pins
  `--workers 1` and warns at boot if `WEB_CONCURRENCY>1`.
- **Auth hardening is on by default**: per-IP rate limits on register/login/
  resend, and WebSocket handshakes use short-lived tickets
  (`POST /api/auth/ws-ticket`) so long-lived JWTs never appear in URLs or logs.
- **Email verification is opt-in**: set `REQUIRE_EMAIL_VERIFICATION=true` and
  either `EMAIL_PROVIDER=smtp` with `SMTP_*` (VPS hosts) or
  `EMAIL_PROVIDER=resend` with `RESEND_API_KEY` (HTTPS; works on Render Free,
  which blocks outbound SMTP). Unverified accounts can sign in and browse but
  cannot start a coaching session until they confirm.
- **Recording consent is on by default** (`REQUIRE_RECORDING_CONSENT=true`).
  The first signed-in screen asks the presenter to confirm they have permission
  to record everyone on the call; the acceptance timestamp and
  `CONSENT_VERSION` are stored on the user row. `POST /api/sessions` and the
  WebSocket handshake both enforce it server-side. Bump `CONSENT_VERSION` when
  the consent text changes so existing users re-confirm.
- **Signups can be private**: set `SIGNUPS_ENABLED=false` to close public
  registration (existing accounts can still sign in), or keep it on and list
  invited addresses in `SIGNUP_ALLOWLIST` (JSON array). Registration is checked
  server-side.
- **Password reset + session revocation**: reset links are single-use and
  expire after `PASSWORD_RESET_TOKEN_MINUTES` (default 30). A successful reset
  increments `users.token_version`, immediately invalidating every old access
  token and WebSocket ticket. The endpoint returns the same generic response
  for known and unknown email addresses.
- **Account lifecycle**: `GET /api/auth/export` returns the user's scripts,
  sessions/transcripts, knowledge cards, and billing history as JSON.
  `DELETE /api/auth/account` requires the current password, cancels any active
  Paystack subscription first, finalizes live sessions, and then deletes the
  account and its rows.
- **Secrets**: every `.env` file is git-ignored and only `*.example` templates
  are committed. Rotate anything that was ever shared or committed —
  `JWT_SECRET_KEY`, STT/LLM provider keys, `PAYSTACK_SECRET_KEY`, and the
  database password — before going live.
- **Billing webhook**: point Paystack at
  `https://<your-domain>/api/billing/webhook`; request signatures are verified
  with `PAYSTACK_SECRET_KEY`.
- **Free demo billing**: `PAYMENTS_PROVIDER=demo` plus
  `ALLOW_DEMO_BILLING=true` allows upgrades without a payment account. This is
  an explicit production opt-in; never use it for paid customers.
- **Proxy IP trust**: leave `TRUST_PROXY_HEADERS=false` unless the API is
  reachable **only** through a trusted proxy (Render, nginx). The rate limiter
  then reads the rightmost `X-Forwarded-For` entry, never the client-supplied
  leftmost one.
- **Split hosting** (CDN frontend + separate API): build the image with
  `--build-arg VITE_API_BASE_URL=https://api.example.com/api`, add that origin
  to `CORS_ORIGINS`, and expose the API's WebSocket path through your proxy.
  For Cloudflare Pages, update `frontend/public/_headers` `connect-src` to the
  API origin (`https://*.onrender.com` / `wss://*.onrender.com` by default).
- **Reproducible builds**: backend dependencies are pinned in
  `backend/requirements.lock.txt`; the frontend installs from `package-lock.json`
  via `npm ci`.
- **Scaling beyond one process**: move the live-session registry and rate-limit
  counters to Redis, then you can raise the worker count.

### Free hosting path (no credit card)

This split works without a card and is suitable for a demo or a small pilot:

| Piece | Free service | Notes |
| --- | --- | --- |
| API + WebSocket | **Render Free Web Service** | Docker, context `backend`, Dockerfile `backend/Dockerfile`, health check `/api/health`. |
| Frontend | **Cloudflare Pages** | Root `frontend`; build `npm ci && npm run build`; output `dist`; `VITE_API_BASE_URL=https://<api>.onrender.com/api`. |
| Postgres + pgvector | **Neon Free** or **Supabase Free** | Neon auto-resumes; Supabase pauses after 7 days idle. Render's free Postgres expires after 30 days — don't use it. |

Render environment (minimum):

```ini
ENVIRONMENT=production
AUTO_CREATE_TABLES=false
DATABASE_URL=postgresql+asyncpg://...        # Neon/Supabase pooler
JWT_SECRET_KEY=<64-hex-random>
FRONTEND_BASE_URL=https://<project>.pages.dev
CORS_ORIGINS=["https://<project>.pages.dev"]
STT_PROVIDER=groq
GROQ_API_KEY=<groq-key>
LLM_PROVIDER=openrouter                      # or heuristic with no key
OPENROUTER_API_KEY=<openrouter-key>
PAYMENTS_PROVIDER=demo                       # free demo, no card
ALLOW_DEMO_BILLING=true
REQUIRE_EMAIL_VERIFICATION=true              # use Resend on Render Free
EMAIL_PROVIDER=resend
RESEND_API_KEY=<resend-key>
EMAIL_FROM=PitchPilot <no-reply@your-domain.com>
PASSWORD_RESET_TOKEN_MINUTES=30
REQUIRE_RECORDING_CONSENT=true
CONSENT_VERSION=2026-10-24
SIGNUPS_ENABLED=true                         # false = private pilot
SIGNUP_ALLOWLIST=[]                          # or ["you@example.com"]
TRUST_PROXY_HEADERS=true                     # API is behind Render's proxy
AUDIO_CAPTURE_ENABLED=false
VISION_ENABLED=false
```

For a smaller pilot, set `REQUIRE_EMAIL_VERIFICATION=false` and omit
`EMAIL_PROVIDER`/`RESEND_API_KEY`; password-reset emails then need another
delivery path. If you keep verification on, Resend requires a verified sending
domain before it will deliver.

Cloudflare Pages copies `frontend/public/_headers` into the build. The default
CSP allows `https://*.onrender.com` and `wss://*.onrender.com`; if you use a
custom API domain, update `connect-src` there.

The consent dialog links to the legal pages only when
`VITE_PRIVACY_URL` / `VITE_TERMS_URL` are set at build time. Until those real
pages exist, it still records the presenter's permission confirmation.

**Free-tier reality check:** Render Free sleeps after 15 minutes idle (≈1 min
cold start), has 0.1 CPU / 512 MB RAM, 750 instance hours/month, 5 GB outbound,
and can restart at any time. It also blocks outbound SMTP (use Resend or leave
verification off). The app runs **one worker** because live sessions are
in-process. This is a demo/pilot setup, not a production SLA: for paying
customers, use a paid API host, Redis-backed state, database backups, and a
paid email provider. The Electron overlay and screen vision are desktop-only
and are not part of the hosted deployment.
