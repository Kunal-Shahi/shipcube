# Gateway design

`services/gateway` holds two small services, written in Go (the one exception to Python, per the language decision in PR #4). Both run in `shipcube-control` as `sc-gateway`. Security reasoning: [docs/research/security-tenancy.md](../../docs/research/security-tenancy.md).

## 1. AI gateway

Apps call one OpenAI-compatible endpoint; the gateway picks the provider, injects the key, and enforces caps.

```
app (per-app SA) --ID token--> AI gateway --key from Secret Manager--> Muse / OpenAI / Anthropic / OpenAI-compatible
```

**Request path**

1. **Authenticate the app.** The app sends a Google ID token for its own service account (audience = gateway URL), fetched from the metadata server. The gateway verifies it and maps the service account email to `apps.runtime_sa`, which gives it `app_id` and `workspace_id`. Apps never hold a ShipCube secret. In a linked project all apps share `shipcube-apps@<project>`, so the service account identifies the workspace and the app is named in an `X-ShipCube-App` header; apps in one workspace can then spend each other's budget, but never another workspace's.
2. **Resolve config.** `provider_configs` for the app, else the workspace default, else Meta (Muse).
3. **Check caps** (`caps`: `requests_per_minute`, `ai_tokens_per_day`, `spend_usd_per_month`). Counters are shared across gateway instances in Postgres: one atomic `insert … on conflict do update set count = count + $n returning count` per request on the `rate_counters` table (database-design §3), keyed by app, cap kind and time window. A request over the limit is rejected before it reaches the provider. **Limit:** this adds one small write per request; Cloud SQL `db-f1-micro` handles it comfortably up to roughly 100 requests per second across all apps. When gateway traffic stays above 50 rps, move counters to Memorystore for Redis (same keys, `INCRBY` + `EXPIRE`). Token counts are only known after the response, so the token cap can be overshot by at most one in-flight request per instance.
   **Today (#20):** counters are still in each instance's memory, with reserve-then-settle admission (a request reserves its prompt estimate plus its clamped output before the call), platform-key defaults per app (200k tokens/day, 60 rpm), and per-workspace caps on the platform key (1M tokens/day, 300 rpm). The `rate_counters` path above needs a Cloud SQL login for the gateway (an IAM database user and grant, infra), and is the follow-up.
4. **Get the key.** Read the pinned version from Secret Manager (`secret_refs.resource_name` + `version`). Cache in memory for 5 minutes. Keys never appear in logs, errors or responses.
5. **Forward.** For `openai_compatible`, `base_url` must be `https`, and the resolved IP must not be private, loopback, link-local or the metadata server (SSRF guard, checked at request time, not only when saved).
6. **Meter.** Record input and output tokens and estimated cost in `usage_events` (`source = 'gateway'`).

**Failure modes:** over cap returns `429` with which cap and when it resets. Provider errors pass through with the key redacted. If Secret Manager is down, cached keys keep working for their TTL.

## 2. Access proxy (fallback only)

Built when a linked project can't use IAP directly on Cloud Run (security-tenancy §2 and Known limits). Until then IAP covers every app.

```
browser --> access proxy (Google sign-in, signed cookie) --ID token for sc-gateway--> app (run.invoker = sc-gateway only)
```

- Sign-in with Identity Platform. The proxy sets a cookie per app host: `HMAC-SHA-256` signed, `HttpOnly`, `Secure`, `SameSite=Lax`, 1-hour lifetime, holding user ID, app ID and expiry.
- Each request checks the cookie, then `access_grants` for that app (`user` and `domain` principals; `group` only if the owner connects Cloud Identity). The grant lookup is cached for 30 seconds, so revocation takes effect within 30 seconds, faster than IAP.
- The proxy forwards with an ID token for `sc-gateway`, the only principal with `roles/run.invoker` on the app.
- Strips incoming `X-Serverless-Authorization`, `Authorization` and cookie headers meant for the proxy before forwarding; adds `X-ShipCube-User-Email`.

Trade-off: all fallback traffic flows through ShipCube's project and credits, which is why IAP is the default.
