# PitchPilot production checklist

This is the deployment runbook for the hosted web app. Desktop/Electron
packaging, native audio capture, and screen vision are intentionally out of
scope for the server deployment.

## 1. Choose the launch mode first

| Mode | What it is | What it is not |
| --- | --- | --- |
| **Free pilot** | Render Free API + Cloudflare Pages + Neon/Supabase Free. Demo billing, one worker, invite-only or closed signups. | An SLA, a paid product, or a place for real payments. |
| **Paid production** | Paid API host, managed Postgres with backups, Redis for multi-worker state, Resend, live Paystack, custom domain, legal pages. | Free. There is no reliable no-card paid-production path. |

Do not mix them. A free pilot with `ALLOW_DEMO_BILLING=true` must not take real
money.

## 2. Code-side work already in place

- [x] Live transcription through Groq (`whisper-large-v3-turbo`).
- [x] Email verification via SMTP or Resend (Resend works on Render Free).
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
- [ ] **Email**: create Resend account, verify the sending domain (SPF/DKIM),
      set `EMAIL_FROM`, then enable `REQUIRE_EMAIL_VERIFICATION=true`.
- [ ] **Billing currency**: local config is **NGN 22,500/month**. The production
      template is now aligned to NGN because this merchant has USD disabled on
      Paystack. If USD is enabled later, change `PAYSTACK_CURRENCY`,
      `PLUS_PRICE_AMOUNT`, and `PLUS_PRICE_DISPLAY` together.
- [ ] **Paystack live**: business verification, live keys, webhook URL
      `https://<api-host>/api/billing/webhook`, callback URL, and an end-to-end
      test with a test/live transaction. For the free pilot use
      `PAYMENTS_PROVIDER=demo` + `ALLOW_DEMO_BILLING=true` instead.
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
   - `PAYMENTS_PROVIDER=demo` + `ALLOW_DEMO_BILLING=true` for the pilot
   - `REQUIRE_RECORDING_CONSENT=true`, `CONSENT_VERSION`
   - `SIGNUPS_ENABLED=true` or `false`, `SIGNUP_ALLOWLIST`
   - `TRUST_PROXY_HEADERS=true` (Render is the only ingress)
   - `AUDIO_CAPTURE_ENABLED=false`, `VISION_ENABLED=false`
5. Deploy and confirm `GET /api/health` and `GET /api/ready` both return 200.
6. Tail logs for the startup line showing the STT provider, signup mode, and
   payments provider.

## 5. Deploy the frontend (Cloudflare Pages)

1. Root: `frontend`.
2. Build: `npm ci && npm run build`.
3. Output: `dist`.
4. Environment: `VITE_API_BASE_URL=https://<api-host>/api`.
5. Optional: `VITE_PRIVACY_URL`, `VITE_TERMS_URL`.
6. Confirm `frontend/public/_headers` allows the API origin in
   `connect-src` (default `https://*.onrender.com` / `wss://*.onrender.com`).
7. Set the final Pages URL in the API's `FRONTEND_BASE_URL` and
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
- [ ] Reconnect the WebSocket mid-session.
- [ ] Hit the free quota and see the upgrade path.
- [ ] Demo upgrade works (pilot) or Paystack test payment works (paid).
- [ ] Cancel subscription; confirm period-end behavior.
- [ ] Export data downloads valid JSON.
- [ ] Delete account; old token cannot sign in; user rows are gone.
- [ ] `/api/ready` returns 503 when the database is unreachable.

## 7. Free-pilot guardrails

- Keep exactly **one API worker**; live session state and rate limits are
  in-process.
- Expect Render Free to sleep after 15 minutes idle (cold start) and restart
  without warning. Do not promise uptime.
- Use demo billing only; do not accept real payments.
- Prefer `SIGNUPS_ENABLED=false` or a small `SIGNUP_ALLOWLIST`.
- Keep `REQUIRE_EMAIL_VERIFICATION=true` with Resend to reduce abuse.
- Monitor DB size, 429s, and Render logs. Free Postgres tiers may pause/expire.
- Have a rollback plan: previous Render deploy + Alembic downgrade only after a
  backup.

## 8. Before charging money

- [ ] Move to a paid API host with an SLA and enough CPU/RAM.
- [ ] Move live-session registry, rate limits, and WS tickets to Redis before
      increasing workers.
- [ ] Managed Postgres with backups/PITR.
- [ ] Live Paystack keys, webhook delivery monitoring, refund/cancellation
      policy, and tax/business details.
- [ ] Verified Resend domain and a support inbox.
- [ ] Real privacy/terms/consent pages linked from the app.
- [ ] Error tracking and uptime alerts.
