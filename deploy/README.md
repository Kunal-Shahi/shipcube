# Hosting ShipCube itself

Three Cloud Run services in `shipcube-control-ks` (asia-south1), built on Cloud Build (no local
Docker). One image per service is built per commit and promoted unchanged; everything
environment-specific comes from environment variables and Secret Manager.

| Service | Source | Image | Runs as | Ingress / auth | Health |
|---|---|---|---|---|---|
| `shipcube-console` | `apps/web` (Next.js) | `deploy/web.Dockerfile` | `sc-web` | public; sign-in in the app (Identity Platform) | `/sign-in` |
| `shipcube-mcp` | `services/mcp` + `services/api`, one process | `deploy/mcp.Dockerfile` | `sc-api` | public; MCP clients send `sc_pat_` tokens the API checks | `/healthz` |
| `shipcube-gateway` | `services/gateway` (Go) | `services/gateway/Dockerfile` | `sc-gateway` | public; verifies the calling app's Google ID token itself | `/healthz` |
| `shipcube-migrate` (job) | `deploy/migrate` + `db/migrations` | `deploy/migrate.Dockerfile` | `sc-migrator` | none; runs on demand | exit code |

All services: `--min-instances 0`, request-based billing, at most 2 instances, 1 vCPU, 256–512 MiB.
`DEV_MODE` is never set in production, so no service can return mock data there.

URLs are Cloud Run's deterministic ones, `https://<service>-340481939948.asia-south1.run.app`.
**Custom domain:** Cloud Run domain mapping isn't offered in asia-south1, and a global load balancer
costs ≈ $18/month. Firebase Hosting rewrites to these services are the free option if we register
a domain later. Until then no ShipCube domain exists, and nothing may refer to one: the console
shows users `SHIPCUBE_MCP_URL`, which `deploy.sh` sets to the `shipcube-mcp` URL plus `/mcp`.

## Deploy

```bash
deploy/bootstrap.sh                          # once: Artifact Registry repo, service accounts, secrets, IAM
deploy/deploy.sh                             # each release: Cloud Build → three Cloud Run services; prints URLs
python3 deploy/monitoring.py --email <you>   # once, and after adding services: uptime checks + alerts
deploy/cloudsql.sh                           # when the API moves to Postgres: instance, backups, IAM users
deploy/migrate.sh                            # each schema change: apply db/migrations as a Cloud Run job
deploy/wif.sh                                # once: GitHub → GCP Workload Identity Federation for deploy.yml
```

`deploy.sh --no-build` redeploys the current tag (for an env change). Rollback: route traffic to the
previous revision, with `gcloud run services update-traffic <svc> --to-revisions <rev>=100 --region asia-south1`.
`gcloud run revisions list --service <svc>` lists the revisions.

## CI and merges

`.github/workflows/ci.yml` runs on every PR. Its jobs:

- Python: `uv sync --locked`, ruff, then pytest against a real Postgres 16 service.
- Gateway: gofmt, `go vet`, `go test -race`.
- Console: lint and `next build`.
- Deploy scripts: shellcheck, syntax and YAML checks.

Jobs skip when their paths didn't change. **`ci-ok`** always runs and fails if any job failed; it is
the one check branch protection requires on `main`. Deploys (`deploy.yml`) are manual, run only from
`main`, and authenticate with Workload Identity Federation (no keys).

## Database: Cloud SQL Postgres

`deploy/cloudsql.sh` mirrors security's `infra/terraform/database.tf`: `shipcube-pg`, Postgres 16,
`db-f1-micro`, 10 GB SSD (auto-grow to 50 GB), zonal, IAM database auth only, TLS required, deletion
protection. Add to that:

- **Backups:** daily at 18:00 UTC, 14 kept, point-in-time recovery with 7 days of WAL.
- **Maintenance:** Sundays 21:00 UTC.

It's deferred until the API stops using in-memory state, because it has no scale-to-zero.

**Migrations** are versioned and forward-only: `db/migrations/NNNN_name.sql`, applied by
`deploy/migrate` (tested against real Postgres in CI). Each file runs once, in its own transaction,
recorded with a checksum in `schema_migrations`. An edited applied file stops the run, so fix forward.
A Postgres advisory lock serialises concurrent runs. `-- migrate:no-transaction` on the first line is
for `CREATE INDEX CONCURRENTLY`. Never change the production schema by hand.

**Firestore instead?** It would be ≈ $0 at demo volume, but security's schema relies on row-level
security, unique constraints and joins (database-design.md §1). The saving (~$11/month) isn't worth
redesigning tenancy.

## Monthly cost estimate (demo traffic)

| Item | Estimate | Why |
|---|---|---|
| Cloud Run, 3 services | $0 | Scale to zero; the free tier (180k vCPU-s, 360k GiB-s, 2M requests/month) covers demo use and the ~39k uptime-check requests |
| Cloud Build | $0–1 | First 2,500 build-minutes/month free; ≈ 3–5 min per release |
| Artifact Registry | < $0.10 | ~0.7 GB of images; cleanup keeps 5 versions |
| Secret Manager, Logging | $0 | Within free allotments |
| Uptime checks, alert policies | $0 | ~39k executions/month, 1M free (SKU CA14-D3DE-E67F); alert policies have no billing SKU |
| **Total without Cloud SQL** | **≈ $0–2** | |
| Cloud SQL (when created) | ≈ $11–14 | `db-f1-micro` + 10 GB SSD ≈ $10–12, backups + PITR logs ≈ $1–2 |

## Operate (runbook)

Alerts arrive by email from `deploy/monitoring.py`. Each one links back here.

**A service is down (uptime alert)**
1. `gcloud run services describe <svc> --region asia-south1`: is the latest revision ready?
2. Logs: Logs Explorer, `resource.labels.service_name="<svc>" severity>=ERROR`. Startup failures show
   as `Container failed to start` with the process's own error just above.
3. A bad release: roll back to the previous revision (Deploy, above). Missing secrets or IAM:
   `deploy/bootstrap.sh` is idempotent, so re-run it.
4. The gateway refuses to start without `META_SECRET_REF`. That's deliberate, because production must
   not serve mock output.

**5xx ratio above 5%**
- Gateway: `jsonPayload.message="chat"` lines show `provider`, `status`, `attempts` and
  `upstream_request_id`. Provider outages show as 502/504 after retries. Runbook in
  `services/gateway/README.md`.
- MCP/API: check `severity>=ERROR`. `MonitorPermissionDenied` means `sc-api` lost monitoring/logging viewer.

**p95 latency above 3 s:** check whether instances are pinned at max (2); raise `--max-instances`, or
find the slow downstream call in the logs (the request id ties lines together).

**Database**
- *Down / CPU / disk alerts:* `gcloud sql operations list --instance shipcube-pg` shows maintenance or a
  failed operation. If the disk fills, auto-grow (to 50 GB) should cover it; if it's at the limit, raise
  `--storage-auto-increase-limit`.
- *Restore:* `gcloud sql backups list --instance shipcube-pg`, then restore into a scratch instance, or
  clone to a point in time with `gcloud sql instances clone shipcube-pg shipcube-pg-restore --point-in-time <RFC3339>`.
  Check the data, then switch the API's connection.
- *Migration failed:* the failed file was rolled back (unless it was `no-transaction`).
  `deploy/migrate.sh status` shows what's applied. Fix with a new migration; never edit an applied one.

**Secrets:** rotate by adding a version and redeploying with the new pinned version. Disable the old
version only after the deploy is healthy.

## Known limits

- The console and MCP/API code come from PRs #1 and #3. `deploy.sh` stops if they aren't in the checkout.
- The gateway reads its app registry from a Secret Manager file until the API serves
  `/internal/gateway/resolve`.
- There are no `db/migrations` yet: the schema (database-design.md) needs to be written as `0001_…sql`
  (security).
