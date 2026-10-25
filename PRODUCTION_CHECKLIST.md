# PitchPilot production checklist

This is the deployment runbook for the hosted web app. Desktop/Electron
packaging and native audio capture are intentionally out of scope for the
server deployment. Browser screen sharing is in scope: the frontend captures a
screen/tab plus system audio and sends downscaled frames over the live
WebSocket. The API never captures the host screen.

## 1. Choose the launch mode first

| Mode | What it is | What it is not |
| --- | --- | --- |
| **Free pilot** | Render Free API + Render Static Site (or Cloudflare Pages) + Neon/Supabase Free. Demo or Paystack **test** billing, one worker, invite-only or closed signups. | An SLA, a paid product, or a place for real payments. |
| **Paid production** | Paid API host, managed Postgres with backups, Redis for multi-worker state, Resend, live Paystack, custom domain, legal pages. | Free. There is no reliable no-card paid-production path. |

Do not mix them. A free pilot with demo or Paystack test billing must not take
real money.

## 2. Code-side work already in place

- [x] Live transcription through Groq (`whisper-large-v3-turbo`).
- [x] Browser screen sharing: screen/tab + system audio via `getDisplayMedia`,
      mixed with the mic, with downscaled JPEG frames analyzed in memory. No
      server-side host-screen capture in the hosted deployment.
- [x] Paystack checkout + webhook handling. Test and live keys use the same
      code path; the plan cache is keyed by key fingerprint so swapping modes
      cannot reuse the wrong plan.
- [x] Optional account email via SMTP or Resend. The current pilot leaves it
      off until a verified sending domain exists; password recovery is hidden
      while no provider is configured.
- [x] Password reset: single-use, expiring links; reset revokes old JWTs and
      WebSocket tickets.
- [x] Recording consent: blocking first-run dialog, server-side gate on
      `POST /api/sessions` and the WebSocket handshake, timestamp + version
      stored on the user.
- [x] Account data export (`GET /api/auth/export`) and account deletion
      (`DELETE /api/auth/account`) with subscription cancellation first.
- [x] Signup controls: `SIGNUPS_ENABLED` and `SIGNUP_ALLOWLIST`.
- [x] `/api/health` (liveness) and `/api/ready` (database readiness).
- [x] Production guard rejects unfilled `<placeholders>`, `example.com`
      values, default JWT secrets, localhost URLs, wildcard CORS, demo billing
      without the explicit opt-in, and missing/simulated STT.
- [x] Non-root Docker image, `.dockerignore` excludes secrets, backend and
      production frontend dependency audits run.
- [x] `backend/scripts/account_lifecycle_test.py` covers the new flows.

## 3. External accounts and decisions

These cannot be completed from the codebase alone.

- [ ] **Secrets rotation**: JWT, Groq, Azure/OpenRouter/Gemini, Paystack, DB
      password. Assume anything shared in a local `.env` is exposed.
- [ ] **Database**: create Neon or Supabase Postgres, enable `pgvector`, use
      the pooled connection URL, decide backup/PITR needs.
- [ ] **Email (deferred)**: Resend is intentionally disabled until a sending
      domain is verified (SPF/DKIM). Until then keep
      `REQUIRE_EMAIL_VERIFICATION=false`; the sign-in screen hides password
      recovery. When the domain is ready, set `EMAIL_PROVIDER=resend`,
      `RESEND_API_KEY`, and `EMAIL_FROM`, then enable verification.
- [ ] **Billing currency**: local config is **NGN 22,500/month**. The production
      template is now aligned to NGN because this merchant has USD disabled on
      Paystack. If USD is enabled later, change `PAYSTACK_CURRENCY`,
      `PLUS_PRICE_AMOUNT`, and `PLUS_PRICE_DISPLAY` together.
- [ ] **Paystack**: use webhook
      `https://pitchpilot-w7dj.onrender.com/api/billing/webhook` and callback
      `https://pitchpilot-web.onrender.com/?view=dashboard&billing=success` for
      the current hosts. Test with `sk_test_...`, then replace only
      `PAYSTACK_SECRET_KEY` with `sk_live_...` and update the dashboard URLs.
      For a no-payment pilot use `PAYMENTS_PROVIDER=demo` +
      `ALLOW_DEMO_BILLING=true` instead.
- [ ] **Browser capture**: Chrome/Edge desktop is recommended for screen/tab
      audio. Safari/Firefox may not expose system audio. Tell users to share
      only the call tab/window and tick “Share system audio”; sharing the whole
      screen can show the PitchPilot overlay.
- [ ] **Legal**: privacy policy, terms, recording consent language, retention
      and deletion policy, subprocessors (Groq, Azure/OpenRouter, Gemini,
      Paystack, Resend, Neon/Supabase), support contact.
- [ ] **Legal URLs in the UI**: set `VITE_PRIVACY_URL` and `VITE_TERMS_URL` at
      frontend build time. Until then the consent dialog still records the
      permission confirmation but does not link to pages.
- [ ] **Observability**: optional Sentry/UptimeRobot account; `/api/ready` is
      the endpoint to monitor for database health.
- [ ] **Desktop release** (only if shipping Electron): Windows code signing
      and macOS notarization.

## 4. Deploy the API (Render Free pilot)

1. Create a Render Web Service from the repo.
2. Docker context: `backend`; Dockerfile: `backend/Dockerfile`.
3. Health check: `/api/health`.
4. Set the environment from `.env.production.example` / README, including:
   - `ENVIRONMENT=production`
   - `AUTO_CREATE_TABLES=false` (the container runs `alembic upgrade head`)
   - `DATABASE_URL` (pooled Neon/Supabase)
   - `JWT_SECRET_KEY` (64+ random hex)
   - `FRONTEND_BASE_URL` and exact `CORS_ORIGINS`
   - `STT_PROVIDER=groq` + `GROQ_API_KEY`
   - `PAYMENTS_PROVIDER=paystack`, `PAYSTACK_SECRET_KEY=sk_test_...`,
     `ALLOW_DEMO_BILLING=false` for checkout testing. Use
     `PAYMENTS_PROVIDER=demo` + `ALLOW_DEMO_BILLING=true` only for a
     no-payment pilot.
   - `PAYSTACK_CURRENCY=NGN`, `PLUS_PRICE_AMOUNT=2250000`,
     `PLUS_PRICE_DISPLAY=₦22,500`
   - leave email unset for now. After a sending domain is verified, set
     `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, and `EMAIL_FROM`, then enable
     verification. Keep `REQUIRE_EMAIL_VERIFICATION=false` until then.
   - `REQUIRE_RECORDING_CONSENT=true`, `CONSENT_VERSION`
   - `SIGNUPS_ENABLED=true` or `false`, `SIGNUP_ALLOWLIST`
   - `TRUST_PROXY_HEADERS=true` (Render is the only ingress)
   - `AUDIO_CAPTURE_ENABLED=false`, `VISION_ENABLED=false`
   - `VISION_CLIENT_ENABLED=true`, `VISION_SERVER_CAPTURE_ENABLED=false`
5. Deploy and confirm `GET /api/health` and `GET /api/ready` both return 200.
6. Tail logs for the startup line showing the STT provider, signup mode, and
   payments provider. The payments line must say `paystack (test key)` or
   `paystack (live key)`, never demo when checkout is shown.

## 5. Deploy the frontend (Render Static Site or Cloudflare Pages)

1. Root: `frontend`.
2. Build: `npm ci && npm run build`.
3. Output: `dist`.
4. Environment: `VITE_API_BASE_URL=https://<api-host>/api`.
5. Optional: `VITE_PRIVACY_URL`, `VITE_TERMS_URL`.
6. Confirm `frontend/public/_headers` allows the API origin in
   `connect-src` (default `https://*.onrender.com` / `wss://*.onrender.com`)
   and keeps `Permissions-Policy: display-capture=(self)` so the screen picker
   can open.
7. Set the final static-site URL in the API's `FRONTEND_BASE_URL` and
   `CORS_ORIGINS`, then redeploy the API if needed.

## 6. Verify before inviting users

Run from `backend/` with `PYTHONPATH=.` and the API running:

```powershell
.\.venv\Scripts\python.exe scripts\account_lifecycle_test.py
```

Also verify manually:

- [ ] Register → verification email arrives (if enabled) → verify.
- [ ] Consent dialog appears; accepting it allows a session.
- [ ] Start a session, speak, and see a real Groq transcript.
- [ ] Start a live session in Chrome/Edge, click “Share screen + call audio”,
      share only the call tab/window with system audio, and confirm the screen
      chip plus a screen-aware answer. Stop sharing and confirm mic-only
      coaching continues.
- [ ] Reconnect the WebSocket mid-session, including while screen sharing; the
      share must re-arm automatically.
- [ ] Hit the free quota and see the upgrade path.
- [ ] Demo upgrade works (pilot) or Paystack test checkout opens and the
      callback verifies `reference`/`trxref` (paid).
- [ ] Paystack webhook delivers a successful charge event.
- [ ] If email is enabled: a password-reset email arrives at a real inbox.
      While email is off, the sign-in screen hides password recovery.
- [ ] Cancel subscription; confirm period-end behavior.
- [ ] Export data downloads valid JSON.
- [ ] Delete account; old token cannot sign in; user rows are gone.
- [ ] `/api/ready` returns 503 when the database is unreachable.

## 7. Free-pilot guardrails

- Keep exactly **one API worker**; live session state and rate limits are
  in-process.
- Expect Render Free to sleep after 15 minutes idle (cold start) and restart
  without warning. Do not promise uptime.
- Use Paystack **test** keys or demo billing only; never live keys on a free
  pilot, and never accept real payments there.
- Prefer `SIGNUPS_ENABLED=false` or a small `SIGNUP_ALLOWLIST`.
- Once a Resend domain is verified, keep `REQUIRE_EMAIL_VERIFICATION=true` to
  reduce abuse.
- Monitor DB size, 429s, and Render logs. Free Postgres tiers may pause/expire.
- Have a rollback plan: previous Render deploy + Alembic downgrade only after a
  backup.

## 8. Before charging money

- [ ] Move to a paid API host with an SLA and enough CPU/RAM.
- [ ] Move live-session registry, rate limits, and WS tickets to Redis before
      increasing workers.
- [ ] Managed Postgres with backups/PITR.
- [ ] Replace `PAYSTACK_SECRET_KEY=sk_test_...` with `sk_live_...`, update the
      dashboard webhook/callback URLs, and run a live transaction test. No code
      change should be needed.
- [ ] Paystack webhook delivery monitoring, refund/cancellation policy, and
      tax/business details.
- [ ] Verified Resend domain and a support inbox.
- [ ] Privacy/terms/consent pages that cover screen frames, system audio,
      subprocessors, retention, and deletion.
- [ ] Rotate any provider key that may have appeared in old logs; the API now
      sends Gemini keys in headers, never query strings.
- [ ] Error tracking and uptime alerts.
