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

### With the Firebase Auth emulator

The same popup, ID token and session cookie flow as production, with the emulator in place of Google. It needs Node only:

```bash
npx firebase-tools emulators:start --only auth --project demo-shipcube   # in another terminal
DEV_MODE=1 AUTH_PROVIDER=firebase-emulator SHIPCUBE_API_URL=http://127.0.0.1:8080 SHIPCUBE_MCP_URL=http://localhost:8080/mcp SHIPCUBE_DEV_API_SESSION=<the API's dev console token> npm run dev
```

The emulator's popup lets you type any email address. The API can't verify the emulator's unsigned tokens, so the console calls it with `SHIPCUBE_DEV_API_SESSION`, as the API's bootstrap user.

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
| `npm run test:e2e` | Playwright against a running local stack: sign in, create a PAT, the setup guide, the smoke app, access, caps, delete and restore. See [`e2e/README.md`](e2e/README.md) |
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
| `SHIPCUBE_API_URL` | Yes, unless `DEV_MODE=1` | Control-plane API base URL: the `shipcube-mcp` Cloud Run service URL |
| `SHIPCUBE_MCP_URL` | Yes, unless `DEV_MODE=1` | The MCP endpoint shown on the setup guide: `<your ShipCube MCP URL>`, the `shipcube-mcp` service URL plus `/mcp`. Must be `https` in production; the server exits at boot without it. `DEV_MODE` defaults to `http://localhost:8080/mcp` |
| `SHIPCUBE_API_TIMEOUT_MS` | No (10000) | Per-request timeout for API calls |
| `AUTH_PROVIDER` | No | `identity-platform` (default). `DEV_MODE` only: `firebase-emulator` (the real sign-in flow against the Firebase Auth emulator) or `mock` |
| `FIREBASE_AUTH_EMULATOR_HOST` | With `firebase-emulator` (`127.0.0.1:9099`) | The emulator as the console server reaches it. Refused outside `DEV_MODE` with any provider, because the Admin SDK would accept unsigned tokens |
| `FIREBASE_AUTH_EMULATOR_PUBLIC_URL` | No | The emulator as the browser reaches it, when that differs (Docker Compose) |
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

- **Every console page reads the real API.** That covers apps, approvals and the approvals inbox, API tokens, AI providers, billing, GCP linking, the data model and recently deleted apps. `AVAILABLE` in `src/lib/server/data/http.ts` lists them; a page whose feature is missing from it shows a "not available yet" state naming the endpoint.
- **API sessions aren't checked for revocation by the API itself.** The API verifies the console's Identity Platform session cookie (signature, issuer, project) but doesn't check whether it was revoked; the console does, on every request, before it forwards the cookie. Checking it in the API needs `roles/firebaseauth.viewer` for `sc-api`.
- **API data the console doesn't get yet:** app lists don't include cost, request counts or who deployed. Metrics have no time series, so the latency and daily-cost charts only appear in `DEV_MODE`. Approval pages show the API's reasons, not a before/after impact table or the original tool arguments.
- **Onboarding** is remembered per browser (a cookie), not per account.
- **Not tested here:** the Identity Platform adapter. No Firebase emulator or live project was available, so the sign-in flow was only exercised with the mock provider.
