# Hosting ShipCube itself

Three Cloud Run services in `shipcube-control-ks` (asia-south1), built on Cloud Build (no local Docker):

| Service | Source | Image | Runs as | Ingress / auth |
|---|---|---|---|---|
| `shipcube-console` | `apps/web` (Next.js) | `deploy/web.Dockerfile` | `sc-web` | public; sign-in in the app (Identity Platform) |
| `shipcube-mcp` | `services/mcp` + `services/api` in one process | `deploy/mcp.Dockerfile` | `sc-api` | public; MCP clients send `sc_pat_` tokens the API checks |
| `shipcube-gateway` | `services/gateway` (Go) | `services/gateway/Dockerfile` | `sc-gateway` | public; verifies the calling app's Google ID token itself |

All three: `--min-instances 0`, request-based billing (`--cpu-throttling`), at most 2 instances,
1 vCPU, 256–512 MiB.

URLs are Cloud Run's deterministic ones, `https://<service>-340481939948.asia-south1.run.app`.
**Custom domain:** Cloud Run domain mapping isn't offered in asia-south1, and a global load balancer
costs ≈ $18/month, so the hackathon uses the `run.app` URLs. If we want `shipcube.dev` later, the cheap
option is Firebase Hosting rewrites to these services (free tier).

## Steps

```bash
deploy/bootstrap.sh   # once: Artifact Registry repo, service accounts, secret containers, IAM
deploy/deploy.sh      # each release: Cloud Build → three Cloud Run services; prints the URLs
deploy/wif.sh         # optional, once: GitHub → GCP Workload Identity Federation for .github/workflows/deploy.yml
```

`deploy.sh` needs the console (PR #3) and MCP/API (PR #1) code in the checkout, so run it from `main`
after those merge (or from a branch that merges them).

## Database: Cloud SQL Postgres (deferred), not Firestore

`deploy/cloudsql.sh` (or security's `infra/terraform/database.tf`, which it mirrors) creates
`shipcube-pg`: Postgres 16, `db-f1-micro`, 10 GB SSD, zonal, IAM database auth, ≈ $10–12/month. It is
the cheapest Cloud SQL tier; there is no scale-to-zero. Firestore would be ≈ $0 at demo volume (free
tier: 1 GiB, 50k reads/day), but security's schema (database-design.md §1) relies on row-level security,
unique constraints and joins, so Firestore would mean redesigning tenancy. That saving (~$11/month,
about 4% of the credit over the hackathon) isn't worth it. **Create the instance only when the API stops
using in-memory state**; until then it would bill for nothing.

## Monthly cost estimate (demo traffic)

| Item | Estimate | Why |
|---|---|---|
| Cloud Run, 3 services | $0 | Scale to zero; the free tier (180k vCPU-s, 360k GiB-s, 2M requests/month) covers demo use |
| Cloud Build | $0–1 | First 2,500 build-minutes/month free (e2-standard); `E2_HIGHCPU_8` is billed per minute, ≈ 3–5 min per release |
| Artifact Registry | < $0.10 | ~0.6 GB of images, 0.5 GB free, $0.10/GB-month after; cleanup keeps 5 versions |
| Secret Manager | $0 | 6 active versions and 10k accesses/month free |
| Logging / Monitoring reads | $0 | Within free allotments |
| **Total without Cloud SQL** | **≈ $0–2** | |
| Cloud SQL (when created) | ≈ $10–12 | `db-f1-micro` + 10 GB SSD |

## Secrets

- `meta-api-key` (exists): read by `sc-gateway` only, pinned to version 1 in `META_SECRET_REF`.
- `shipcube-dev-token`: the demo PAT, seeded by `bootstrap.sh`, mounted as `SHIPCUBE_DEV_TOKEN` until the
  API's token store is live. Read it with `gcloud secrets versions access latest --secret shipcube-dev-token`.
- `gateway-registry`: the gateway's app registry JSON (`services/gateway/testdata/registry.example.json`
  shape), mounted as a file until the API serves `GET /internal/gateway/resolve`.

No secret values are in this repo, in Terraform state or in build logs.
