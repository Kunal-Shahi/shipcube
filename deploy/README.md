# Hosting ShipCube itself

Four Cloud Run services in `shipcube-control-ks` (asia-south1), built on Cloud Build (no local Docker)
and pushed to the `platform` repo. One set of images per commit (tag = the commit) is promoted
unchanged; everything environment-specific comes from env vars and Secret Manager. Identities,
grants, secrets and the build bucket are Terraform (`infra/terraform/platform.tf` and security's files).

| Service | Code | Image | Runs as | Ingress / auth | Scaling | Health |
|---|---|---|---|---|---|---|
| `shipcube-console` | `apps/web` | `console` (`web.Dockerfile`) | `sc-web` | public; Google sign-in (Identity Platform) in the app | 0–2, request-based | `/api/healthz` |
| `shipcube-mcp` | `services/mcp` + `services/api`, with shipcube-data, the load estimator and `packages/monitor` in-process | `api` (`api.Dockerfile`) | `sc-api` → DB role `shipcube_app` (RLS) | public; PATs / console sessions checked by the API | 0–2, request-based | `/healthz` |
| `shipcube-worker` | `shipcube_api.worker` via `runtime/worker_service.py` | `api` | `sc-worker` → DB role `shipcube_worker` (BYPASSRLS) | private (IAM); only health checks call it | **1**, CPU always allocated | `/healthz` (loop alive, not stalled) |
| `shipcube-gateway` | `services/gateway` (Go) | `gateway` | `sc-gateway` | public; verifies each app's Google ID token | 0–2, request-based | `/healthz` |

URLs are Cloud Run's deterministic ones: `https://<service>-340481939948.asia-south1.run.app`.

**Wiring** (all set by `deploy.sh`):
- console → API: `SHIPCUBE_API_URL` = the `shipcube-mcp` URL. The setup guide shows `SHIPCUBE_MCP_URL`.
- MCP → API: in-process (same container). Nothing calls the worker directly: the API writes operations to Postgres and the worker claims them.
- deployed apps → gateway: the worker's deployer injects `SHIPCUBE_AI_GATEWAY=<gateway URL>` into every app it deploys.
- gateway → apps: the gateway's registry (`gateway-registry` secret) maps each app's runtime service account to the app. `gateway_registry.py` writes it from the hosted Cloud Run services until the API serves `/internal/gateway/resolve`.

**Database:** both the API and the worker reach Cloud SQL over Cloud Run's built-in `/cloudsql/<instance>`
socket, using IAM database auth: each new connection sends a fresh token for the service account
(`SHIPCUBE_DB_IAM_AUTH=1`). There's no password and no proxy sidecar. **Migrations** run only through
`infra/db/migrate.sh` (as `sc-migrator`). Services refuse to start while one is pending and never
migrate themselves.

## Deploy

```bash
# 0. Once, and whenever infra changes (shows the plan first; apply only after review):
terraform -chdir=infra/terraform plan -out=tfplan && terraform -chdir=infra/terraform apply tfplan
# 1. Any pending schema migrations (normally none):
API_DIR=services/api infra/db/migrate.sh
# 2. Build and deploy all four services (prints health and URLs):
DATA_WORKSPACE=<workspace uuid> deploy/deploy.sh
# Uptime checks and alerts are Terraform (infra/terraform/modules/monitoring), applied in step 0.
```

Partial deploys: `deploy/deploy.sh --only gateway`, `--no-build` (redeploy the images for the current
commit, for example after an env change). `DATA_WORKSPACE` is the workspace the `data_*` tools serve
(shipcube-data serves one catalog per process today).

`deploy.sh` also:

- grants `allUsers` invoker on the three public services, since they authenticate callers themselves;
- grants the Monitoring service agent invoker on the worker, for its uptime check;
- adds the console's host to Identity Platform's authorized domains.

Rollback: `gcloud run services update-traffic <svc> --to-revisions <rev>=100 --region asia-south1`. Revisions
carry a `release` label with the commit. CI (`.github/workflows/ci.yml`, required check `ci-ok`) gates
merges; `deploy.yml` deploys `main` through Workload Identity Federation (`wif.sh`) with repository
variables `SHIPCUBE_DATA_WORKSPACE`, `SHIPCUBE_PROFILER_ENV_JSON` and `SHIPCUBE_PLATFORM_ADMINS` (comma-separated
emails that review the waitlist; a local `deploy.sh` defaults it to your gcloud account). `MAX_WORKSPACES`
(default 50) caps waitlist approvals.

## Monthly cost (asia-south1 list prices, demo traffic)

| Item | Estimate | Basis |
|---|---|---|
| `shipcube-worker`, 1 vCPU + 1 GiB, always on | **≈ $52** | instance-based billing: 2.59M vCPU-s × $0.000018 + 2.59M GiB-s × $0.000002 (Cloud Billing Catalog, "Services CPU/Memory (Instance-based billing) in asia-south1") |
| console, MCP+API, gateway (scale to zero) | $0–2 | request-based billing; demo traffic fits the free tier |
| Cloud Build (`E2_HIGHCPU_8`, ~6–8 min per release) | ≈ $0.10–0.15 per release | build minutes on that machine type aren't in the free tier |
| Artifact Registry, build bucket | < $1 | ~1.5 GB of images, 5 kept per service; sources deleted after 7 days |
| Uptime checks and alerts | < $1 | ~104k uptime executions/month of 1M free; 8 alert conditions (see infra/README.md) |
| Cloud SQL (already running, security's) | ≈ $11–15 | unchanged |

**Why the worker is always on.** The durable queue is a Postgres table that the worker polls. With
request-based billing, Cloud Run throttles CPU between requests, so a scaled-down worker would never
claim work. Fractional CPU isn't allowed with CPU always allocated, so 1 vCPU is the floor. To stop paying
for it outside demos, run `gcloud run services update shipcube-worker --min-instances 0 --region asia-south1`;
queued operations wait until it's scaled back up.

## Operate (runbook)

Alerts (from `infra/terraform/modules/monitoring`) arrive by email and point here.

**A service is down**
1. `gcloud run services describe <svc> --region asia-south1`: is the latest revision ready? Check the
   `Ready` condition's message.
2. Logs: `resource.labels.service_name="<svc>" severity>=ERROR`. Startup refusals are deliberate and name
   their cause:
   - **API/worker:** `database schema is not current; pending: …` means run `infra/db/migrate.sh`.
     A missing `SHIPCUBE_TOKEN_PEPPER` means the secret version is wrong.
   - **Gateway:** a missing `META_SECRET_REF`.
   - **Console:** `/api/healthz` returns 500 and names the bad setting.
3. A bad release: roll back to the previous revision (see Deploy).

**Worker unhealthy or the queue isn't draining**
- `/healthz` returns 503 when the loop thread died or hasn't iterated for 30 minutes. The liveness probe
  then restarts the instance; an interrupted step resumes on the next worker after its 90 s lease expires.
- `python -m shipcube_api.admin ops --stuck` (from `services/api`, with `SHIPCUBE_DATABASE_URL` through
  `infra/db/migrate.sh`'s proxy) lists stuck operations; `admin requeue <id>` / `admin fail <id>`.
- Check `min-instances` is 1 (see the cost note).

**Database connection errors** (`password authentication failed for user "sc-api@…iam"`): the IAM
database user or `cloudsql.instanceUser` is missing. Check `gcloud sql users list -i shipcube-pg` and
Terraform. `SHIPCUBE_DB_IAM_AUTH` must be `1`, and the service must have the `run.googleapis.com/cloudsql-instances`
annotation.

**Sign-in fails on the console:** the console host must be in Identity Platform → Settings → Authorized
domains (`deploy/identity_platform.py` adds it). The browser key must allow `identitytoolkit` and
`securetoken`. `sc-web` needs `roles/firebaseauth.admin`.

**MCP clients get 421/403 "Invalid Host":** `ALLOWED_HOSTS` must equal the MCP service's host (set by
`deploy.sh`). A custom domain needs adding there too.

**Gateway answers `403 unknown_app` for a new app:** re-run `deploy/deploy.sh --only gateway`. It
regenerates the registry from the hosted Cloud Run services and restarts the gateway. More gateway
failures are in `services/gateway/README.md`.

**`inspect_app` metrics or logs fail:** `MonitorPermissionDenied` means `sc-api` lacks
`monitoring.viewer`/`logging.viewer` on the app's project (Terraform). See `packages/monitor/README.md`.

**Secrets:** versions are pinned (`META_KEY_VERSION`, `PEPPER_VERSION` in `env.sh`). Rotate by adding a
version, redeploying with the new number, and disabling the old one only after the deploy is healthy.
Rotating the pepper invalidates every PAT.

## Known limits

- **No custom domain.** Cloud Run domain mapping isn't offered in asia-south1, and a load balancer costs about $18/month. Until we register one, nothing may name a ShipCube domain; CI's `guards` job fails if the old placeholder domain comes back.
- **Data tools serve one workspace per process** (shipcube-data PR #1): a DuckDB demo warehouse built in each instance, so it is rebuilt on cold start.
- **The gateway registry is interim** until `/internal/gateway/resolve` exists: one default Muse config and caps for every app.
- **Invite-only sign-up isn't enforced yet:** the Identity Platform `beforeCreate` blocking function isn't deployed.
