# ShipCube console (`apps/web`)

The web console: Google sign-in (Identity Platform), onboarding and GCP project linking, the MCP setup guide, apps and app detail (load estimate, caps, access, monitoring, logs, soft delete and restore), approvals for agent actions that need a person, the data model (warehouse, cubes, migration review), AI providers, API tokens and billing.

Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS 4. The design system is in [`docs/design/DESIGN.md`](../../docs/design/DESIGN.md).

- **Production** reads everything from the control-plane API ([`services/api/openapi.yaml`](../../services/api/openapi.yaml)) and signs people in with Identity Platform. It never serves mock data.
- **`DEV_MODE=1`** is for local development only. It allows a mock sign-in and, if no API URL is set, typed mock data. The server refuses to start in `DEV_MODE` on Cloud Run.

## Run it

Requires Node 20.9 or later.

```bash
cd apps/web
npm install
DEV_MODE=1 npm run dev
```

Open http://localhost:3000 and choose **Continue with Google**. In `DEV_MODE` this signs you in as a demo user without contacting Google.

### Against a local control-plane API

Start `services/api` with a console session token (see its README), then point the console at it. The mock sign-in sends that token to the API, so approvals, restores and caps go through the real API.

```bash
DEV_MODE=1 SHIPCUBE_API_URL=http://127.0.0.1:8081 SHIPCUBE_DEV_API_SESSION=sc_ses_devcons1_devonlyConsoleNotASecret0123456789abc npm run dev
```

### Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server |
| `npm run build` | Production build. Needs no environment variables, so one artifact is promoted across environments |
| `npm start` | Serves the build on `$PORT` (default 3000) |
| `npm run lint` | ESLint (`next/core-web-vitals`, TypeScript) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Unit tests: the API client (timeouts, retries, errors), API-to-console mapping, config guards, and WCAG contrast of the design tokens |
| `npm run test:integration` | Runs the HTTP data source against a live API. Set `SHIPCUBE_IT_API_URL` and `SHIPCUBE_IT_SESSION`; skipped otherwise |

### Previewing states in `DEV_MODE`

With mock data, data pages accept `?state=empty` or `?state=error`. Production ignores the parameter. Example links:

- Approvals: `/approvals/apr_7k2m` (first deploy to your GCP project), `apr_c4q9` (cap raise), `apr_d8w1` (domain sharing), `apr_e2n5` (delete)
- Migrations: `/data/migrations/0006_late_delivery_rate` (additive), `0007_net_revenue` (changes results), `0008_rework_items` (breaking)
- A deleted app: `/apps/app_q3p07`

Mock data is held in memory. The dev server can reload modules between requests, so a change made in one page may not show up in another; `npm run build && DEV_MODE=1 npm start` keeps one copy.

## Configure

Everything is read from the environment at request time.

| Variable | Required | Meaning |
|---|---|---|
| `SHIPCUBE_API_URL` | Yes, unless `DEV_MODE=1` | Control-plane API base URL, e.g. `https://api.shipcube.dev` |
| `SHIPCUBE_API_TIMEOUT_MS` | No (10000) | Per-request timeout for API calls |
| `SHIPCUBE_MCP_URL` | No | The hosted MCP endpoint shown on the setup page, e.g. `https://shipcube-mcp-….run.app/mcp`. Defaults to `https://mcp.shipcube.dev/mcp` |
| `AUTH_PROVIDER` | No | `identity-platform` (default) or `mock` (`DEV_MODE` only) |
| `IDENTITY_PLATFORM_PROJECT` | Yes for Identity Platform | Project that hosts Identity Platform, e.g. `shipcube-control-ks` |
| `FIREBASE_WEB_API_KEY` | Yes for Identity Platform | The web API key from Identity Platform → Application setup details. It's public by design, not a secret |
| `FIREBASE_AUTH_DOMAIN` | No | Defaults to `<project>.firebaseapp.com`. Set it if you use a custom auth domain |
| `IDENTITY_PLATFORM_TENANT_ID` | No | Identity Platform tenant, if multi-tenancy is on |
| `SESSION_DAYS` | No (5) | Session cookie lifetime, 1–14 days |
| `DEV_MODE` | No | `1` for local development. Refused when `K_SERVICE` is set (Cloud Run) |
| `SHIPCUBE_DEV_API_SESSION` | No | `sc_ses_…` token the mock sign-in sends to a local API |
| `GOOGLE_CLOUD_PROJECT` | No | Adds the trace link to log entries |

No secrets are needed. The Admin SDK uses Application Default Credentials.

## Deploy

Platform owns hosting: [`deploy/web.Dockerfile`](../../deploy/web.Dockerfile) and `deploy/cloudbuild.yaml` on the platform branch build the image on Cloud Build, and Cloud Run serves it as `shipcube-console`. The app also builds with Google Cloud buildpacks (`gcloud run deploy --source apps/web`): `npm run build`, then `npm start`, which listens on `$PORT`.

The console's Cloud Run service needs:

- **Service account** (`sc-web`): `roles/firebaseauth.admin` on the Identity Platform project, to verify ID tokens, mint and verify session cookies, and revoke on sign-out. Nothing else.
- **Environment:** the variables above. `DEV_MODE` must not be set.
- **Identity Platform:** the Google provider enabled, the console's domain in Authorized domains, and security's `beforeCreate` blocking function for invite-only sign-up.
- **Probes:** a startup and liveness probe on `GET /api/healthz`. It returns 500 with the reason when configuration is invalid, so a misconfigured revision never takes traffic.

## Operate

**Logs** are one JSON object per line, which Cloud Logging parses:

- `severity`, `message`, `component: "console"`, plus the trace link when Cloud Run passes `X-Cloud-Trace-Context`.
- Every API call logs `method`, `path`, `status`, `ms`, `attempt` and a `requestId`. The same `X-Request-Id` is sent to the API, so one id finds both sides.
- Unhandled server errors are logged by `src/instrumentation.ts` in Error Reporting's format, with the digest shown to the user as "Reference …".
- Values of keys that look like credentials (`authorization`, `cookie`, `token`, `secret`, `password`, `key`) are redacted.

**Errors and retries** (`src/lib/server/http.ts`):

- Every call has a timeout.
- `GET`, `PUT`, `DELETE`, and any request with an `Idempotency-Key`, are retried up to 3 times on network errors, timeouts, 429 and 5xx. Backoff is exponential with jitter, capped at 4 s, and honours `Retry-After` up to 10 s.
- Caps and access changes send a fresh `Idempotency-Key` per user action.
- Server actions return typed errors and never throw to the browser. Pages that fail render an error state with a retry button.

**Security:**

- Headers: a per-request nonce CSP (`src/middleware.ts`), plus HSTS, `nosniff`, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, a strict `Referrer-Policy` and `Permissions-Policy` (`next.config.ts`).
- The session is an httpOnly, `SameSite=Lax`, `Secure` Identity Platform session cookie, checked for revocation on every request. `/api/auth/session` refuses cross-origin posts and ID tokens older than 5 minutes.
- Server actions validate their input before calling the API.

**Runbook:**

| Symptom | Check |
|---|---|
| `/api/healthz` returns 500 | The `error` field names the missing or invalid variable |
| Every page shows "This page couldn't load" | Search logs for `api call failed`. `code: network` or `timeout` means the API is unreachable. `unauthenticated` means the API doesn't accept the console's session (see limits) |
| Sign-in says "invite-only" | Expected for accounts the blocking function rejects. Add the email to the invite list |
| Sign-in says "isn't set up for this address" | Add the console's domain to Identity Platform → Settings → Authorized domains |
| A page says "… isn't available yet" | The API doesn't serve that feature yet (see limits). Nothing is wrong with the console |

## Known limits

These are honest gaps, tracked in the PR.

- **API endpoints that don't exist yet.** Each of these pages shows a "not available yet" state in production, naming the endpoint it needs:
  - Approvals inbox: `GET /v1/operations?status=awaiting_approval`
  - API tokens: `/v1/tokens`
  - AI provider keys: `/v1/ai/providers`
  - Billing: `/v1/billing`
  - GCP project linking: `/v1/gcp-targets`
  - Data model: the data-layer endpoints
  - Recently deleted apps: `?include_deleted=true`

  Individual approval pages, apps, caps, access, logs, metrics and restore use the real API.
- **The API must accept the console's session.** The console sends the Identity Platform session cookie as `Authorization: Bearer`. The API needs to verify it with the Admin SDK's `verify_session_cookie`. Its skeleton accepts `sc_ses_…` tokens instead.
- **API data the console doesn't get yet:** app lists don't include cost, request counts or who deployed. Metrics have no time series, so the latency and daily-cost charts only appear in `DEV_MODE`. Approval pages show the API's reasons, not a before/after impact table or the original tool arguments.
- **Onboarding** is remembered per browser (a cookie), not per account.
- **Not tested here:** the Identity Platform adapter. No Firebase emulator or live project was available, so the sign-in flow was only exercised with the mock provider.
