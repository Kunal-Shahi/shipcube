# AI gateway

The OpenAI-compatible endpoint that deployed ShipCube apps call for models. Design (security
workstream): `services/gateway/DESIGN.md` §1 and `docs/research/security-tenancy.md`. This is the
build. Go standard library only, so there are no third-party dependencies to patch.

```
app (per-app SA) --Google ID token--> gateway --key from Secret Manager--> Muse / OpenAI / Anthropic / BYOK base URL
```

## Request path

1. **Request id and trace.** The gateway uses the caller's `X-Request-Id` if it is well formed, else
   generates one, and echoes it in the response. Every log line carries `request_id` and, given
   `traceparent` or `X-Cloud-Trace-Context`, Cloud Logging's trace field, so gateway logs nest under
   the Cloud Run request. The id and `traceparent` are forwarded to ShipCube's API, never to model providers.
2. **Authenticate the app.** A Google ID token for the app's own service account (audience = gateway
   URL), verified against Google's JWKS (RS256, issuer, audience, expiry, verified email). Fallback: a
   per-app `scg_…` token, stored as SHA-256 only. `X-ShipCube-App` names the app when apps share a
   service account (linked projects).
3. **Resolve the app** from the API (`GET /internal/gateway/resolve`, cached 30 s, retried) or, until
   that exists, a registry JSON mounted from Secret Manager.
4. **Caps:** `requests_per_minute` (token bucket), `ai_tokens_per_day` (also clamps `max_tokens`) and
   `spend_usd_per_month`. Over a cap → `429` with `cap`, `limit`, `resets_at` and `Retry-After`.
5. **Key:** a pinned Secret Manager version, cached 5 min, retried, served stale for up to 30 min if
   Secret Manager is down. Keys never appear in logs, errors or responses (provider error bodies are
   redacted).
6. **Forward** with retries (below) to `meta` (Muse, the default), `openai`, `openai_compatible` (BYOK:
   https only; private, loopback, link-local and metadata IPs refused at connect time; redirects not
   followed) or `anthropic` (chat completions ↔ Messages API, `anthropic-version: 2026-10-21`; text and
   images, no tool calls). Streaming works for all of them.
7. **Meter:** a usage event per request (request id, app, provider, model, tokens, cost, status,
   attempts, latency, cap hit). It is written as a structured log line (`shipcube_usage=true`) and,
   with `GATEWAY_USAGE_URL`, batched to the API for `usage_events`. Failed batches are requeued, up to
   10,000 events.

## Timeouts and retries

| What | Limit |
|---|---|
| Connect / TLS handshake to a provider | 10 s / 10 s |
| Wait for provider response headers | `GATEWAY_UPSTREAM_HEADER_TIMEOUT`, default 300 s (Muse reasons before its first byte) |
| Whole chat request, streaming included | `GATEWAY_REQUEST_TIMEOUT`, default 15 min (Cloud Run's request timeout is set to 3600 s) |
| Provider retries | `GATEWAY_UPSTREAM_RETRIES`, default 2, on connection errors, 429, 500, 502, 503, 504 and 529; backoff 0.5 s × 2ⁿ with jitter; a `Retry-After` up to 10 s is honoured, a longer one is passed back to the app as a 429 |
| API, Secret Manager, metadata calls | 15 s timeout, 2 retries (0.2 s, 0.4 s) |

Retries happen only before any byte reaches the app, so the app never sees duplicated output. A
retried 5xx may still have been billed by the provider, the same as with the providers' own SDKs.

## Run

```bash
go test -race ./...                               # unit tests (fake providers)
GATEWAY_IT_META_SECRET_REF=projects/shipcube-control-ks/secrets/meta-api-key/versions/1 \
GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) go test -run Integration -v ./...   # real Secret Manager + Muse

# Local server against the mock provider (no key needed):
DEV_MODE=1 GATEWAY_ALLOW_APP_TOKENS=1 GATEWAY_REGISTRY_FILE=testdata/registry.example.json go run .
```

Calling it from an app:

```python
import google.auth.transport.requests, google.oauth2.id_token
from openai import OpenAI

GATEWAY = os.environ["SHIPCUBE_AI_GATEWAY"]          # injected by the deployer
token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), GATEWAY)
client = OpenAI(base_url=f"{GATEWAY}/v1", api_key=token)
client.chat.completions.create(model="default", max_tokens=2000,
                               messages=[{"role": "user", "content": "hi"}])
```

`model: "default"` uses the app's configured model. For Muse, keep `max_tokens` ≥ 1000: Muse Spark
spends reasoning tokens inside it (173 of 183 for a one-word reply).

## Configure

| Variable | Meaning |
|---|---|
| `GATEWAY_AUDIENCE` | Expected ID-token audience (the gateway's URL). Enables ID-token auth. |
| `GATEWAY_ALLOW_APP_TOKENS=1` | Accept `scg_` per-app tokens. |
| `GATEWAY_API_URL` | Control-plane API base for `/internal/gateway/resolve`. |
| `GATEWAY_REGISTRY_FILE` | Registry JSON instead of the API (shape: `testdata/registry.example.json`). |
| `GATEWAY_USAGE_URL` | Optional: POST usage batches here every 10 s. |
| `META_SECRET_REF` | **Required** (except in `DEV_MODE`): the Secret Manager version of the Meta key. |
| `META_BASE_URL`, `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` | Provider endpoints (defaults: `https://api.meta.ai/v1`, `https://api.openai.com/v1`, `https://api.anthropic.com`). |
| `GATEWAY_PRICES` | JSON `{"provider/model": [in, out]}` USD per 1M tokens, merged with the built-in Anthropic prices. |
| `GATEWAY_UPSTREAM_HEADER_TIMEOUT`, `GATEWAY_REQUEST_TIMEOUT`, `GATEWAY_UPSTREAM_RETRIES` | See "Timeouts and retries". |
| `GOOGLE_CLOUD_PROJECT` | Project id, for the Cloud Logging trace field. |
| `DEV_MODE=1` | **Local only.** Enables `env:NAME` secret refs and the mock provider; with `GATEWAY_ALLOW_PRIVATE_UPSTREAM=1`, private BYOK URLs. The process refuses to start without `META_SECRET_REF` unless this is set. |

## Deploy

Until the API serves `/internal/gateway/resolve`, the registry is the `gateway-registry` secret, mounted
as a file. `deploy/gateway_registry.py` writes it (one entry per hosted app's runtime service account,
Muse `muse-spark-1.3`, default caps) and `deploy/deploy.sh --only gateway` refreshes it.

`deploy/deploy.sh` builds this directory's `Dockerfile` on Cloud Build (distroless, non-root) and
deploys Cloud Run service `shipcube-gateway`: service account `sc-gateway` (Secret Manager accessor on
`meta-api-key` and `gateway-registry` only), scale to zero, at most 2 instances, 256 MiB. It is public
at the Cloud Run level because it verifies app identity itself.

## Operate (runbook)

**Alerts.** `infra/terraform/modules/monitoring` creates an uptime check on `/health` (every 5 min, 3 regions) and
alerts on uptime failure and a 5xx ratio above 5% for 5 min. There is no latency alert, because streamed
completions are long by design. Usage per app: Logs Explorer with `jsonPayload.shipcube_usage=true`.

| Symptom | Likely cause | What to do |
|---|---|---|
| Apps get `401 unauthenticated` | ID token audience ≠ `GATEWAY_AUDIENCE`, or the app sends no token | Logs: `"auth failed"` with the reason. The app must fetch the token with audience = gateway URL. |
| `403 unknown_app` | The SA isn't in the registry / API, or apps share an SA without `X-ShipCube-App` | Add the app to `gateway-registry` (new secret version; instances pick it up on restart) or fix the API's resolve data. |
| `424 no_key` | The app's provider has no `secret_ref`, or `META_SECRET_REF` is missing | Set the provider key in the console, or redeploy with `META_SECRET_REF`. |
| `503 secret_unavailable` | Secret Manager is down past the stale window, or `sc-gateway` lost `secretAccessor` | `gcloud secrets get-iam-policy <secret>`; check the Secret Manager status page. |
| `503 registry_unavailable` | The API is down or slow | Check `shipcube-mcp` health and logs; cached app configs keep working for 30 s. |
| `502`/`504 upstream_*` | The provider is down or slow after retries | Logs: `"chat"` with `attempts` and `upstream_request_id` (quote it to the provider). Raise `GATEWAY_UPSTREAM_HEADER_TIMEOUT` if Muse is slow on long prompts. |
| `429 cap_exceeded` | An app hit its cap | Expected. The body says which cap and when it resets. Raising a cap is an approval in the console. |
| Usage gaps in `usage_events` | The API rejected batches | Logs: `"usage flush failed; will retry"` / `"buffer full"`. Usage log lines are the backup record. |

**Rotate the Meta key:** add a new version of `meta-api-key`, redeploy with
`META_SECRET_REF=…/versions/<new>` (versions are pinned), then disable the old version.

## Known limits

- **Caps are per instance** (in memory). With `max-instances 2` an app can briefly use up to twice its
  rate cap. Strict limits need shared counters (Redis or Postgres), which aren't built.
- **Muse and BYOK prices aren't known to the gateway.** Their `cost_usd` is 0 with `priced=false` until
  set in `GATEWAY_PRICES`, so spend caps don't bind for them; token caps do.
- **Streams without a usage chunk** (Meta sometimes omits it) are metered by a bytes/4 estimate,
  flagged `estimated=true`.
- **Anthropic:** tool calls aren't translated yet. Requests with `tools` get a 400 instead of being
  silently changed.
- **The registry file is interim** until the API serves `/internal/gateway/resolve`: changing it
  needs a new secret version and an instance restart.
