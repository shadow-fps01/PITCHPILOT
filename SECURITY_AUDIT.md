# PitchPilot — Security Assessment

**Date:** 2026-10-26
**Targets:**
- Web app: `https://pitchpilot-web.onrender.com`
- API: `https://pitchpilot-w7dj.onrender.com`
- Source: local repository (`backend/`, `frontend/`, `desktop/`)

**Method:** Black-box testing of the live deployment (non-destructive) plus full white-box review of the backend, frontend, Electron shell, Docker and deployment configuration. No data was modified, no accounts were created, and no brute-force or load testing was performed. Login attempts used non-existent addresses only.

**Overall risk: HIGH.** No remote-code-execution, SQL-injection, IDOR or authentication-bypass was found — the core object-level authorization is genuinely good. The main risks are (1) an ineffective brute-force control, (2) long-lived non-revocable bearer tokens, (3) unbounded paid-AI consumption by any authenticated user, and (4) token-type confusion between password-reset/verification JWTs and access JWTs.

> **Remediation progress (2026-10-29, branch `fix/readiness-quick-wins`):**
> - **H1 fixed:** shared Postgres fixed-window limiter (`RATE_LIMIT_BACKEND=db`) keyed on the rightmost trusted `X-Forwarded-For` entry, plus per-email exponential login lockout.
> - **H2 fixed:** `POST /auth/logout` and `/auth/logout-all` revoke tokens via `token_version`; access tokens are now 24 h in production; WS tickets are revoked on logout/password change.
> - **H3 fixed:** 500-char ask cap, 3 s ask interval, 20 asks/session, 90-minute session cap, 2 concurrent sessions/user, 20 concurrent sessions/instance, 6 sessions/day and 100 asks/day per user, plus explicit uvicorn `--ws-max-size`.
> - **H4 fixed:** access tokens carry `purpose="access"`; the access decoder rejects reset/verification tokens; verification tokens now carry and check `ver`.
> - **H5 fixed:** session and script quota checks take a `SELECT ... FOR UPDATE` lock on the user row, serializing the check with the insert.
> - **M1 fixed:** `/docs`, `/redoc`, `/openapi.json` are disabled when `ENVIRONMENT=production`.
> - **M2 fixed:** API middleware sets HSTS, `X-Content-Type-Options`, `X-Frame-Options`, CSP, Referrer-Policy, Permissions-Policy and `Cache-Control: no-store`.
> - **M3 fixed:** registration always returns the same 202 message; login runs a dummy bcrypt comparison for unknown addresses.
> - **M4 fixed:** per-email failed-login lockout (5 failures → 60 s, exponential to 1 h).
> - **M5 fixed:** 12+ character policy, common-password and identity checks (`app/services/passwords.py`).
> - **M6 fixed in code:** webhook payloads are whitelisted before storage; `scripts/sanitize_billing_payloads.py` is a dry-run-by-default cleanup for historical rows. Running it on production still needs backup + approval.
> - **M7 fixed:** hashed, single-use, session-bound WS tickets with a 60 s TTL.
> - **M8 still open:** no verified sending domain / provider is configured. Password recovery and email verification remain off until Resend or SMTP credentials are supplied.
> - **M9 fixed:** Electron external URLs and navigation are restricted to `http(s)`; unsafe protocols are dropped.
> - **M10 fixed:** ask/keyword/body limits, `TrustedHostMiddleware`, `MAX_HTTP_BODY_BYTES`, nginx `client_max_body_size`.
> - **M11 partially fixed:** recipient addresses are masked in email logs; failed-login events are logged without the address. A full structured security-log pipeline is still recommended.
> - **M12 fixed:** `TrustedHostMiddleware` with an explicit host allowlist.
> - **L1 fixed:** reset/verify tokens are read from the URL fragment and stripped immediately.
> - **L2 fixed:** CSP pins the exact API origin and removes wildcard `ws:`/`wss:`; nginx is same-origin only.
> - **L5 fixed:** copilot source URLs are filtered to `http(s)`.
> - **L6 fixed:** `/auth/email-available` was removed; forgot-password always returns the same 202 response.
> - **L8 fixed:** `Cache-Control: no-store` on API responses.
> - **L9 fixed (tag pinning):** `python:3.14.8-slim-bookworm`, `node:22.21.1-alpine3.21`, `nginx:1.29.3-alpine`. Digest pinning + automated updates remain recommended.
> - **L10 fixed:** request IDs must match `[A-Za-z0-9._-]{1,64}`.
> - **L11 fixed:** default seed cards now ask the presenter for their own numbers instead of asserting fictional metrics.
> - **L14 fixed:** script writes require a verified email.
> - **Still open:** M8 (email credentials), L3 (Google Fonts SRI), L4 (full version-header removal), L7 (public readiness probe), L12/L13 (JWT audience/rotation policy), digest pinning, CI/dependency scanning, and the historical Paystack cleanup.
> - **Deployment:** changes are committed and deployed through Render (`main`); the production entrypoint runs `alembic upgrade head` before starting one uvicorn worker.

---

## 1. Summary

| # | Finding | Severity |
|---|---------|----------|
| H1 | Auth rate limiting is not enforced in production (28/28 attempts passed, 0 × 429) | **High** |
| H2 | No logout / no server-side revocation; 7-day JWT stored in `localStorage` | **High** |
| H3 | Denial-of-wallet: unlimited live sessions, copilot questions, screen frames → paid STT/LLM/vision calls | **High** |
| H4 | Token-type confusion: password-reset and email-verification JWTs are accepted as access tokens | **High** |
| H5 | Free-plan quota race condition (concurrent requests bypass the 3/month limit) | **High** |
| M1 | `/docs`, `/redoc`, `/openapi.json` publicly exposed on the API | Medium |
| M2 | API responses lack HSTS, `X-Content-Type-Options`, `X-Frame-Options`, CSP | Medium |
| M3 | Account enumeration via `POST /auth/register` (409) and login timing | Medium |
| M4 | No account lockout or progressive delay after failed logins | Medium |
| M5 | Weak password policy (8 chars, no complexity/breach check) | Medium |
| M6 | Full Paystack webhook payload (card BIN/last4/signature/authorization code) stored indefinitely | Medium |
| M7 | WebSocket ticket travels in the query string, is reusable for 15 min and is not session-bound | Medium |
| M8 | Email delivery disabled in production → password recovery impossible; email verification off → pre-account takeover of allowlisted addresses | Medium |
| M9 | Electron `shell.openExternal()` accepts arbitrary URI schemes | Medium |
| M10 | Unbounded input surfaces (copilot question text, HTTP bodies, keyword strings) | Medium |
| M11 | PII in logs; failed logins are not logged at all | Medium |
| M12 | No `TrustedHostMiddleware` (Host-header not validated) | Medium |
| L1 | Reset/verify tokens placed in URL query strings | Low |
| L2 | CSP too broad (`connect-src https://*.onrender.com`, `style-src 'unsafe-inline'`, nginx `ws: wss:`) | Low |
| L3 | No Subresource Integrity on Google Fonts | Low |
| L4 | Technology/version disclosure (`x-render-origin-server: uvicorn`, OpenAPI version) | Low |
| L5 | Copilot source URLs rendered as `href` without scheme validation | Low |
| L6 | Deployment-config disclosure via `/auth/email-available` and `503` on forgot-password | Low |
| L7 | `/api/ready` unauthenticated and reports DB reachability | Low |
| L8 | Authenticated JSON responses lack `Cache-Control: no-store` | Low |
| L9 | Docker base images use floating tags (`python:3.14-slim`, `node:22-alpine`, `nginx:1.29-alpine`) | Low |
| L10 | Client-controlled `X-Request-ID` is reflected (currently sanitized) | Low |
| L11 | Default RAG seed cards contain **fabricated business metrics** that the copilot may present as the user's own facts | Low (integrity) |
| L12 | JWT `iss` is issued but never validated; no `aud`/`nbf` | Low |
| L13 | JWT-secret strength check only enforces length/placeholder (32 × "a" passes) | Low |
| L14 | `POST /api/scripts` does not require a verified email (sessions do) | Low |

---

## 2. Detailed findings

### H1 — Auth rate limiting is not enforced in production

**Evidence (live):**

```
A) 14 × POST /api/auth/login, no X-Forwarded-For  → 401 × 14   (no 429)
B) 14 × POST /api/auth/login, unique XFF per req  → 401 × 14   (no 429)
```

The configured `AUTH_RATE_LIMIT_PER_MINUTE=12` was never triggered. Earlier in the same session, isolated `429`s did appear, which shows the limiter is *sometimes* active but keyed to an unstable value.

**Root cause:** `backend/app/services/rate_limit.py`

- The store is a plain in-process dict (`_FixedWindowLimiter`). Render can run more than one instance, and each has an independent counter. The file's own docstring admits this is only valid for a single process.
- `_client_key()` uses `request.client.host` unless `X-Forwarded-For` is present **and** the peer is private (which it is behind Render). Behind a load balancer, `request.client.host` is the proxy address and can vary per request, so requests land in different buckets. When XFF is honoured it is client-influenced.
- `docker-entrypoint.sh` sets `--forwarded-allow-ips=127.0.0.1`; the Render proxy is not `127.0.0.1`, so uvicorn does not rewrite `request.client.host` to the real client.

**Impact:** unlimited password guessing / credential stuffing against any known email, unlimited reset-email bombing once SMTP is enabled, and unthrottled abuse of every other rate-limited endpoint (`/billing/checkout`, `/billing/verify`, `/auth/export`, `/auth/ws-ticket`).

**Fix:**
1. Move counters to a shared store (Redis/Upstash) keyed by `(scope, user_id_or_real_ip)`.
2. Derive the client IP from a **trusted** header only (e.g. Render's `X-Forwarded-For` rightmost value *after* confirming the proxy appends it, or Cloudflare's `CF-Connecting-IP`).
3. Add per-account throttling and exponential backoff on failed logins (not just per IP).
4. If staying single-process, at minimum key on `email` + IP and add a global per-email failure counter.

---

### H2 — No logout and no server-side revocation; 7-day bearer token in `localStorage`

**Evidence:**
- `backend/app/config/settings.py:35` — `jwt_expires_minutes = 60 * 24 * 7`
- `backend/app/services/security.py:29-44` — access token has only `sub`, `ver`, `iat`, `exp`, `iss`
- There is **no** `POST /auth/logout` endpoint. The client only calls `setStoredToken(null)` (`frontend/src/lib/api.ts:52-62`).
- Token is persisted in `localStorage` (`frontend/src/lib/api.ts:32,44-62`).

**Impact:** a token stolen via XSS, a malicious browser extension, a shared machine, or a leaked log remains valid for **7 days**. The user cannot revoke it. `token_version` is only bumped on password change/reset, so "log out everywhere" does not exist.

**Fix:**
- Add `POST /auth/logout` and `POST /auth/logout-all` that bump `token_version` (or maintain a `jti` denylist in Redis).
- Shorten access-token lifetime to 15–60 min and add a refresh token (rotating, revocable, `HttpOnly; Secure; SameSite=Lax` cookie).
- Prefer `HttpOnly` cookies over `localStorage`; if `localStorage` is kept, document the XSS blast radius and add a strict CSP with nonces (already close).
- Add `aud` and `jti` claims.

---

### H3 — Denial-of-wallet: unbounded paid-AI consumption by any authenticated user

**Evidence:**
- `POST /api/sessions` only checks the monthly quota (`backend/app/api/sessions.py:53-85`). Once a session row exists it can be opened over WebSocket indefinitely; there is no maximum session duration, no maximum audio minutes and no per-user concurrent-socket limit (`backend/app/api/stream.py:175`).
- Every WS connection starts a paid STT stream (`build_stt_provider`), a second stream for shared call audio (`ensure_system_provider`), a copilot LLM (`CopilotChat`), RAG embeddings and optionally Tavily search.
- `handle_ask` spawns a task per message with no rate limit and no concurrency cap (`stream.py:313-331`), and `ClientAsk.text` has **no `max_length`** (`schemas/stream.py:198-204`).
- `describe_now` triggers a paid vision call at most every 1.5 s, but is invoked by every `ask`.
- Free plan = 3 sessions/month; an attacker can create 3 sessions and keep them open 24/7, or reconnect forever.

**Impact:** direct financial loss (Groq/Azure/Gemini/Tavily bills) and resource exhaustion of the single worker. A single account can pin CPU/memory and burn API quota.

**Fix:**
- Enforce a hard per-session wall-clock limit (e.g. 90 min) and a per-user concurrent-session limit.
- Add a token-bucket for `ask` (e.g. 1 per 3 s, 20 per session) and `screen_frame` (already 0.4 s) and a max length on `ClientAsk.text` (e.g. 500 chars).
- Add a per-user daily budget for STT seconds, LLM calls and vision calls; cut off with a clear message.
- Cap total concurrent live sessions per instance and reject new ones with a 503/4403.
- Add `max_size` to uvicorn WebSocket config and an HTTP body-size limit at the proxy.

---

### H4 — Token-type confusion: reset/verification JWTs accepted as access tokens

**Evidence:** `backend/app/services/security.py:47-61`

```python
def decode_access_token_claims(token: str) -> tuple[uuid.UUID, int]:
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    subject = payload.get("sub")
    ...
    version = payload.get("ver", 0)
```

There is **no `purpose` check**. Consequences:

- A **password-reset** token (`purpose="reset_password"`, includes `ver`) is a valid bearer token for the full API for its 30-minute lifetime — even before the password is changed.
- An **email-verification** token (`purpose="verify_email"`, **no `ver`**) defaults to `ver=0` and is a valid bearer token for 24 hours for any user whose `token_version` is 0 (the default for every new account).
- Conversely, `decode_email_verification_token` never checks `ver`, so an old verification link still works after a password reset.

**Impact:** a reset/verification link forwarded, screenshotted or leaked from an inbox grants full API access, not just the single action it was meant for. It also weakens the "single-use" property of reset links.

**Fix:** add a `purpose` (and `typ`) claim check in every decoder; issue distinct token types; reject `purpose != "access"` in `decode_access_token_claims`; include `ver` in verification tokens and check it.

---

### H5 — Free-plan quota race condition

**Evidence:** `backend/app/services/billing.py:181-225` + `backend/app/api/sessions.py:53-85` / `scripts.py:45-71`

`assert_session_quota()` performs `SELECT COUNT(...)` and the handler then `INSERT`s in a separate statement with no row lock, no advisory lock and no unique constraint. Concurrent requests all observe `used < limit` and all insert.

**Impact:** a free user can create unlimited sessions/scripts by firing N parallel requests at the quota boundary — direct revenue bypass.

**Fix:** wrap the check + insert in a single transaction with `SELECT ... FOR UPDATE` on the user row (or a Postgres advisory lock keyed by `user_id`), or enforce the quota with a DB-level counter/constraint.

---

### M1 — API documentation exposed in production

**Evidence:** live `GET /docs` → 200, `/redoc` → 200, `/openapi.json` → 200 (28 KB, full schema). `backend/app/main.py:234-239` creates `FastAPI(...)` without `docs_url=None`.

**Impact:** hands an attacker the complete API surface, parameter names, auth scheme, error models and the `DELETE /auth/account` / `POST /billing/webhook` routes. Speeds up targeted attacks.

**Fix:** `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` when `environment == "production"`, or put docs behind auth.

---

### M2 — Missing security headers on the API

**Evidence:** live API responses contain no `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`, `Referrer-Policy` or `Permissions-Policy`. (The static frontend does send them.)

**Impact:** lower than on an HTML origin, but JSON endpoints can still be MIME-sniffed by old browsers, and HSTS is absent on the API subdomain.

**Fix:** add a middleware (or nginx/Render header rule) setting at least `Strict-Transport-Security: max-age=31536000; includeSubDomains`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` for `/api/*`.

---

### M3 — Account enumeration

**Evidence:**
- `POST /auth/register` returns `409 {"detail":"An account with this email already exists"}` (`auth.py:115-120`). Currently masked by invite-only signups (`403`), but it becomes live the moment signups open.
- `POST /auth/login` uses `if user is None or not verify_password(...)` (`auth.py:147`). A non-existent user skips bcrypt (cost 12) entirely, so response time differs measurably (~ms vs ~250 ms) — a timing oracle even though the message is generic.
- `GET /auth/email-available` reveals whether email delivery is configured.

**Fix:** always run a dummy bcrypt comparison when the user is missing; return a generic 200/202 on registration and send a "you already have an account" email instead of a 409.

---

### M4 — No lockout or progressive delay

`login` has no failed-attempt counter per account. Combined with H1, online guessing is effectively unthrottled.

**Fix:** per-account failure counter with exponential backoff + CAPTCHA after N failures; alert on distributed attempts.

---

### M5 — Weak password policy

`RegisterRequest.password` / `ResetPasswordRequest.password` only enforce `min_length=8, max_length=128` (`schemas/auth.py:11,63`). No complexity, no breached-password check, no common-password denylist.

**Fix:** minimum 12 chars, check against HaveIBeenPwned k-anonymity API (or a local top-100k list), reject the user's email/local-part as password.

---

### M6 — Sensitive payment data stored indefinitely

**Evidence:** `backend/app/services/billing.py:548-559` stores the entire Paystack webhook/verify payload in `BillingEvent.raw_payload` (JSONB, `models/billing_event.py:41`). Paystack `charge.success` payloads include `authorization.authorization_code`, `signature`, `bin`, `last4`, `exp_month`, `exp_year`, `bank`, `account_name`.

**Impact:** card metadata and a reusable authorization code sit in the application DB (and any backup) with no retention policy. This expands PCI-DSS scope and turns a DB leak into payment-fraud risk.

**Fix:** whitelist only the fields you need (`reference`, `amount`, `currency`, `status`, `customer_code`, `subscription_code`, `paid_at`); never persist `authorization`; add a retention/purge job.

---

### M7 — WebSocket ticket weaknesses

**Evidence:** `backend/app/services/ws_tickets.py:22-39`, `frontend/src/components/OverlayWidget.tsx:80`

- Ticket is passed as `?ticket=` in the URL → recorded by Cloudflare/Render access logs, browser history and any intermediary.
- TTL is 900 s and the ticket is **reusable** for its whole life (by design, for reconnects).
- It is bound to the **user**, not to a session, so one ticket grants access to *any* of that user's live sessions.
- `revoke(ticket)` exists but is never called on disconnect or logout (there is no logout).

**Fix:** make tickets single-use (or bind to `session_id` + `jti` and rotate on each reconnect), shorten TTL to 60 s, never log query strings for `/api/ws/*`, and revoke on disconnect.

---

### M8 — Account recovery disabled; email verification off

**Evidence:** live `GET /api/auth/email-available` → `{"available":false}`. `forgot-password` therefore returns `503` (`auth.py:179-184`). `require_email_verification` defaults to `false`.

**Impact:**
1. **Users who forget their password are permanently locked out** — a self-inflicted denial of account access, and a support burden.
2. With verification off, anyone who knows an allowlisted address can register it and use the account without proving ownership (pre-account takeover). If the allowlist ever contains a real customer/partner address, this is exploitable.

**Fix:** configure Resend/SMTP before opening signups; set `REQUIRE_EMAIL_VERIFICATION=true`; block login (or at least sessions) until verified.

---

### M9 — Electron `shell.openExternal()` accepts arbitrary schemes

**Evidence:** `desktop/main.js:151-170`

```js
win.webContents.setWindowOpenHandler(({ url: target }) => {
  ...
  void shell.openExternal(target);
  return { action: "deny" };
});
```

Only `checkout.paystack.com` and localhost are special-cased; everything else — including `file:`, `ms-msdt:`, `search-ms:`, `smb:`, `javascript:` — is handed to the OS. On Windows this is a known RCE/credential-theft vector (e.g. `search-ms:` / `ms-msdt:` chains).

**Fix:** parse with `new URL()`, allow only `https:` (and `http:` in dev), and fall back to doing nothing for anything else. Also validate `will-navigate` targets the same way.

---

### M10 — Unbounded input surfaces

- `ClientAsk.text` has no `max_length` (`schemas/stream.py:202`) → arbitrarily large prompt sent to the LLM.
- `ScriptCreate.keywords` caps the list at 50 but each string is unbounded (`schemas/script.py:12,19`).
- Uvicorn/Starlette impose no HTTP body-size limit; `/auth/register` etc. read the whole JSON body into memory.
- WS JSON frames default to uvicorn's `ws_max_size` (16 MB).

**Fix:** add `Field(max_length=500)` to `ClientAsk.text`, `max_length=64` per keyword, and enforce a request-body limit (nginx `client_max_body_size`, or a middleware checking `Content-Length`). Set `--ws-max-size` explicitly.

---

### M11 — PII in logs; no failed-login logging

Every successful auth/billing event logs the user's **email address** (`auth.py:130,151,219,255,285,318,480`, `billing.py` passim, `email.py:80,94`). Failed logins are not logged at all, so brute-force attempts are invisible.

**Fix:** log a user ID (UUID) instead of email, or hash the email; add structured `auth.login_failed` events with IP + user-agent + reason for detection.

---

### M12 — Host header not validated

No `TrustedHostMiddleware` in `main.py`. Password-reset links use `FRONTEND_BASE_URL` (good), so classic reset poisoning is not possible, but Host-header-based cache poisoning and absolute-URL generation remain a latent risk.

**Fix:** add `TrustedHostMiddleware(allowed_hosts=["pitchpilot-w7dj.onrender.com", "pitchpilot-web.onrender.com"])`.

---

### L1 — Tokens in URL query strings

`/?reset=<jwt>` and `/?verify=<jwt>` (`auth.py:79,90`). The SPA strips them immediately (`AuthContext.tsx:45-62,88-94`), but they are still written to browser history and any proxy/CDN log on the first request.

**Fix:** exchange the URL token for a short-lived, single-use code server-side, or use a POST form / fragment.

---

### L2 — CSP breadth

`frontend/public/_headers` and `frontend/nginx.conf` allow `connect-src ... https://*.onrender.com wss://*.onrender.com` (any Render tenant), `style-src 'unsafe-inline'`, and in nginx `ws: wss:` (any host). If an XSS ever exists, exfiltration to an attacker-controlled Render subdomain is trivial.

**Fix:** pin the exact API origin (`https://pitchpilot-w7dj.onrender.com` / `wss://pitchpilot-w7dj.onrender.com`), replace `'unsafe-inline'` styles with a nonce/hash, and remove `ws:`/`wss:` wildcards from nginx.

---

### L3 — No SRI on Google Fonts

`frontend/index.html` loads `https://fonts.googleapis.com/...` without `integrity`. Low risk (fonts are not script), but a compromised CDN could alter rendering.

---

### L4 — Technology/version disclosure

`x-render-origin-server: uvicorn`, `Server: cloudflare`, `/openapi.json` `"version":"1.0.0"`, FastAPI validation error shapes. Minor, but reduces attacker work.

---

### L5 — Copilot source URLs not scheme-validated

`frontend/src/components/OverlayWidget.tsx:878-889` renders `href={source.url}` from Tavily/LLM output. React escapes the attribute, and the CSP (`script-src 'self'`) blocks `javascript:` execution, so this is not currently exploitable — but it is one CSP relaxation away from stored XSS.

**Fix:** allow only `https://` in the `href`; render non-http(s) sources as plain text.

---

### L6 — Deployment-config disclosure

`/auth/email-available` returns `{"available": false}` and `forgot-password` returns `503` when email is unconfigured. This tells an attacker account recovery is off and email is not set up.

**Fix:** return a generic 200 and handle delivery failures asynchronously.

---

### L7 — `/api/ready` unauthenticated

Returns `{"status":"ready"}` after a DB round-trip (`main.py:277-293`). Useful for monitoring, but also for mapping infrastructure and detecting DB outages. Restrict to the platform health-check IP or require a token.

---

### L8 — No `Cache-Control: no-store` on authenticated JSON

`/api/auth/me`, `/api/billing/usage`, `/api/sessions` etc. do not set caching headers. Cloudflare currently marks them `DYNAMIC`, but browsers and corporate proxies may heuristically cache. `/auth/export` correctly sets `no-store`.

**Fix:** add `Cache-Control: no-store` to all `/api/*` responses.

---

### L9 — Floating Docker base images

`python:3.14-slim`, `node:22-alpine`, `nginx:1.29-alpine` are not pinned by digest, so a compromised or broken upstream tag flows straight into the next build.

**Fix:** pin by digest (`python:3.14-slim@sha256:...`) and add Dependabot/Renovate.

---

### L10 — `X-Request-ID` reflection

`main.py:245` echoes the client-supplied header. Starlette currently rejects CR/LF, so header injection did not work in testing, but the value is unvalidated and could pollute logs.

**Fix:** accept only `[A-Za-z0-9-]{1,64}`, otherwise generate a UUID.

---

### L11 — Fabricated metrics in the default RAG deck (integrity)

`backend/app/services/knowledge.py:23-34` seeds every new account with cards claiming:

- "$49 per seat per month", "20% annual discount", "14-day free trial"
- "We are raising $500,000", "40 pilots", "68% converting", "NPS 61", "$6B market"

The copilot treats these as ground truth and will confidently suggest the user say them **in a live pitch**. This is not a classic vulnerability, but it is a serious correctness/liability issue: the product can make a founder state fabricated traction to investors.

**Fix:** ship an empty deck or clearly-labelled example cards; never inject fictional numbers into a live-call prompt.

---

### L12 — JWT claims not fully validated

`iss` is set to `settings.app_name` but never checked; there is no `aud`, `nbf` or clock-skew leeway. If the same secret is ever shared between staging and production, tokens cross-accept.

**Fix:** validate `iss` and `aud`; use distinct secrets per environment.

---

### L13 — Weak JWT-secret validation

`production_config_issues` only rejects placeholders and secrets shorter than 32 chars. `"a" * 32` passes. It also cannot detect a secret reused from another app.

**Fix:** require ≥ 64 hex chars (or base64 of 32 random bytes) and document rotation.

---

### L14 — Inconsistent verification gate

`POST /api/sessions` uses `require_verified_email`; `POST /api/scripts` uses only `get_current_user` (`scripts.py:48`). An unverified account can create scripts and consume embedding calls.

**Fix:** apply `require_verified_email` consistently to all write endpoints.

---

## 3. Controls verified as working

These are genuinely well implemented — do not regress them:

- **Object-level authorization:** every script/session read, update, delete and WS handshake is scoped to `user_id` (`deps.py:71-82`, `sessions.py:104-158`, `scripts.py:21-29`, `stream.py:224-251`). No IDOR found.
- **SQL injection:** all queries use SQLAlchemy expressions; no raw/f-string SQL with user input. The only `text()` calls are static (`SELECT 1`, `CREATE EXTENSION`).
- **Paystack webhook:** HMAC-SHA512 over the raw body with `hmac.compare_digest`, idempotency via `_event_seen` (`billing.py:509-515, 732-740`).
- **JWT algorithm pinning:** `algorithms=[HS256]`; a forged `alg:none` token returns 401.
- **Default JWT secret not in use:** a token signed with `change-me-in-production` is rejected (401), and a control token with a random secret gives the same error.
- **CORS:** explicit origin allowlist; `https://evil.example` and `null` receive **no** `Access-Control-Allow-Origin`. Preflight from a foreign origin is rejected with 400.
- **Secrets hygiene:** `.env` files are git-ignored and were never committed (`git log --all -- .env` is empty). No live-key patterns in any tracked file. `.dockerignore` excludes `.env*`. Backend image runs as UID 10001 (non-root).
- **Password hashing:** bcrypt cost 12, constant-time compare.
- **Token versioning:** password change/reset bumps `token_version` and revokes WS tickets.
- **Recording consent:** enforced server-side on both session creation and the WS handshake.
- **RAG isolation:** knowledge cards are always filtered by `user_id` (`anticipation.py:139,151`).
- **Frontend:** no `dangerouslySetInnerHTML`, no `eval`, no `new Function`; React escapes all LLM/transcript output; external links use `rel="noreferrer noopener"`.
- **Electron:** `contextIsolation: true`, `nodeIntegration: false`, minimal preload (a single boolean), `setContentProtection` on checkout windows.
- **Clickjacking:** `frame-ancestors 'self'` + `X-Frame-Options: SAMEORIGIN` on the web app.
- **HTTPS:** both hosts 301-redirect HTTP → HTTPS; frontend sends HSTS with `preload`.
- **DB exposure:** docker-compose publishes only the web port; Postgres is not exposed to the host.
- **Source maps:** disabled in the production build; `.map` requests return 404.
- **No secrets in the JS bundle:** grepped for `sk_live`, `AIza`, JWTs, `gsk_`, `nvapi-`, Supabase keys — zero hits.

---

## 4. Prioritized remediation plan

**This week (high impact, low effort)**

1. Disable `/docs`, `/redoc`, `/openapi.json` in production.
2. Add a shared-store rate limiter keyed on the real client IP + account; add per-account login backoff.
3. Add `POST /auth/logout` and shorten the access-token TTL; plan the refresh-token migration.
4. Add a `purpose` check to every JWT decoder (H4).
5. Add per-session limits: max duration, max `ask` rate, `ClientAsk.text` max length.
6. Fix the quota race with `SELECT ... FOR UPDATE` (H5).
7. Stop storing Paystack `authorization` data (M6).
8. Configure Resend/SMTP and turn on email verification (M8).
9. Add the missing API security headers.

**Next sprint**

10. Move tokens to `HttpOnly` cookies (or accept the localStorage risk with a nonce-based CSP).
11. Single-use, session-bound WS tickets with a 60 s TTL; redact query strings from logs.
12. Add `TrustedHostMiddleware`, body-size limits, `max_length` on all string fields.
13. Restrict Electron `shell.openExternal` to `https:`.
14. Replace fabricated seed cards with real/empty content.
15. Pin Docker images by digest; add dependency scanning in CI.

**Ongoing**

16. Add structured security logging + alerting (failed logins, 402/429 spikes, unusual STT minutes).
17. Add a per-user daily AI budget and a billing alarm on provider spend.
18. Run `pip-audit` / `npm audit` in CI and a yearly external penetration test.

---

## 5. Notes and limitations

- Testing was **non-destructive**: no accounts were created, no data was modified, no load/brute-force testing was performed. The 28 login attempts used non-existent `@gmail.com` addresses.
- The live deployment is on Render's free tier; instance restarts can reset in-process counters, which is itself part of finding H1.
- Findings marked "live" were reproduced against the production hosts on 2026-10-26. Findings marked with file/line references were confirmed by source review.
- The local `.env` contains many live-looking provider keys (Paystack, Supabase service key, Render API key, GitHub token, etc.). They are not committed, but if this folder has ever been zipped, screen-shared or uploaded anywhere, **rotate them**. Treat the workspace `.env` as a production secret store.
