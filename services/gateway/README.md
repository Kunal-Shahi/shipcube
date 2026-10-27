# AI gateway

The OpenAI-compatible endpoint that deployed ShipCube apps call for models. Design (security
workstream): `services/gateway/DESIGN.md` §1 and `docs/research/security-tenancy.md` on PR #2. This
is the build. Go standard library only.

```
app (per-app SA) --Google ID token--> gateway --key from Secret Manager--> Muse / OpenAI / Anthropic / BYOK base URL
```

## What it does, per request

1. **Authenticates the app.** A Google ID token for the app's own service account (audience = the
   gateway URL, fetched from the metadata server), verified against Google's JWKS. Fallback: a per-app
   `scg_…` gateway token, stored as SHA-256 only.
2. **Resolves the app** (`app_id`, `workspace_id`, provider config, caps) from the control-plane API
   (`GET /internal/gateway/resolve`, cached 30 s) or, before that endpoint exists, a JSON file. When
   apps share a service account (linked projects), `X-ShipCube-App` names the app.
3. **Checks caps:** `requests_per_minute` (token bucket), `ai_tokens_per_day` (also clamps
   `max_tokens` to what's left) and `spend_usd_per_month`. Over a cap → `429` with the cap, its limit,
   `resets_at` and `Retry-After`.
4. **Gets the key** from a pinned Secret Manager version, cached 5 minutes; a cached key keeps working
   for 30 minutes if Secret Manager is down. Keys never appear in logs, errors or responses (provider
   error bodies are redacted).
5. **Forwards:** `meta` (Muse, the default), `openai`, `openai_compatible` (BYOK base URL: https only;
   private, loopback, link-local and metadata addresses are refused at connect time, redirects aren't
   followed) and `anthropic` (chat completions ↔ Messages API, `anthropic-version: 2026-10-21`; text and
   images, no tool calls yet). Streaming works for every provider.
6. **Meters** input/output tokens and cost into usage events (structured log lines tagged
   `shipcube_usage`, and optionally batched to the API for `usage_events`).

If `META_SECRET_REF` is unset, Meta calls are answered by a built-in mock (`X-ShipCube-Provider: mock`)
so apps and the demo work before the Meta key arrives.

## Calling it from an app

```python
import google.auth.transport.requests, google.oauth2.id_token
from openai import OpenAI

GATEWAY = os.environ["SHIPCUBE_AI_GATEWAY"]          # injected by the deployer
token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), GATEWAY)
client = OpenAI(base_url=f"{GATEWAY}/v1", api_key=token)
client.chat.completions.create(model="default", messages=[{"role": "user", "content": "hi"}])
```

`model: "default"` uses the app's configured model.

## Configuration

| Variable | Meaning |
|---|---|
| `GATEWAY_AUDIENCE` | Expected ID-token audience (the gateway's URL). Enables ID-token auth. |
| `GATEWAY_ALLOW_APP_TOKENS=1` | Accept `scg_` per-app tokens. |
| `GATEWAY_API_URL` | Control-plane API base for `/internal/gateway/resolve`. |
| `GATEWAY_REGISTRY_FILE` | JSON registry instead of the API (see `testdata/registry.example.json`). |
| `GATEWAY_USAGE_URL` | Optional: POST usage batches here every 10 s. |
| `META_SECRET_REF` | Secret Manager version holding the Meta key. Unset → mock. |
| `META_BASE_URL`, `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL` | Provider endpoints. |
| `GATEWAY_PRICES` | JSON `{"provider/model": [in, out]}` USD per 1M tokens, added to the built-in Anthropic prices. |
| `GATEWAY_DEV=1` | Local only: allows `env:NAME` secret refs; with `GATEWAY_ALLOW_PRIVATE_UPSTREAM=1`, private BYOK URLs. |

## Run and test

```bash
go test -race ./...
GATEWAY_DEV=1 GATEWAY_ALLOW_APP_TOKENS=1 GATEWAY_REGISTRY_FILE=testdata/registry.example.json go run .
```

## What the gateway needs from the API (networking)

- `GET /internal/gateway/resolve?principal=<sa email | token:sha256>&app=<slug>` → the `AppConfig`
  JSON in `config.go` (404 when unknown). Auth: ID token of `sc-gateway`.
- `POST /internal/usage` with a JSON array of `UsageEvent` (`usage.go`) → rows in `usage_events`
  (`source = 'gateway'`).
