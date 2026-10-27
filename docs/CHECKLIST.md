# Build checklist

What has to exist for the demo: a user builds a tracker in their coding agent, connects to ShipCube's MCP server, estimates load, deploys to GCP behind access control, shares it, and watches it in the console.

**Legend**
- ★ = in the 2-minute demo; cut ☆ items first if time runs out.
- Status (as of Oct 28): ✅ done · 🟡 designed or mocked · 🔴 not started.

## 1. Control plane

| | Component | Owner | Status |
|---|---|---|---|
| ★ | MCP server, 16 tools (Python, streamable HTTP, PAT auth, operations, idempotency) | networking | 🟡 contract done; tools return mocks |
| ★ | Control-plane API (FastAPI, OpenAPI) | networking | 🟡 skeleton |
| ★ | Operations runner (queue + worker: Cloud Tasks or in-process for the demo) | networking | 🔴 |
| ★ | App database on Cloud SQL Postgres: schema, migrations, row-level security | security | 🟡 schema designed, not provisioned |
| ★ | PAT minting, hashing, scopes, expiry | security + networking | 🟡 designed |
| ★ | Approvals: `awaiting_approval`, approve/reject endpoint | networking + security | 🟡 API stub; how a deploy needing two approvals is recorded is still open |
| ☆ | Invite-gated sign-up (`beforeCreate` blocking function) | security | 🟡 designed |
| ☆ | Audit log | security | 🟡 designed |

## 2. Deploy path (GCP)

| | Component | Owner | Status |
|---|---|---|---|
| ★ | `create_upload`: signed URL to a private GCS bucket | networking | 🔴 |
| ★ | Build: Cloud Build → Artifact Registry (buildpacks, no Dockerfile needed) | networking | 🔴 |
| ★ | Deploy T1: Cloud Run service (scale to zero), per-app service account | networking | 🔴 |
| ★ | Access: IAP on the Cloud Run service; reconcile `access_grants` → IAM | security | 🟡 designed |
| ★ | Caps on Cloud Run: max instances, concurrency, CPU, memory, timeout | networking | 🔴 |
| ☆ | Deploy T2: Cloud Run job + Cloud Scheduler | networking | 🔴 |
| ☆ | T3/T4 (min instances, GKE Autopilot, GPU): placement decision only, not deployed | load-estimator | 🔴 |
| ★ | Linked user GCP project: link/unlink script, per-workspace deployer SA | security | 🟡 scripts written, untested |
| ☆ | Spend caps: budgets and alerts, `monthly_spend_usd` | security | 🔴 |
| ☆ | Soft delete with 7-day restore | networking + security | 🟡 designed |

## 3. Intelligence (Meta models)

| | Component | Owner | Status |
|---|---|---|---|
| ★ | Meta Model API access (key in `.env`) | Kunal | 🔴 ask on Discord; Fireworks open-weights as backup |
| ★ | Workload classifier trained on public traces, with held-out metrics | load-estimator | 🟡 in progress |
| ★ | Profiler (local subprocess sampling; Cloud Run job sandbox stubbed) | load-estimator | 🟡 in progress |
| ★ | Placement: tier, resources, cost range, recommended caps | load-estimator | 🟡 in progress |
| ★ | Muse Spark explainer in `estimate_load` (`explained_by`) | load-estimator | 🟡 in progress, rule fallback |
| ☆ | Muse Spark drafting for `data_propose_migration` | data-layer | 🟡 mocked |
| ★ | Muse Code as the demo coding agent connected to our MCP | Kunal | 🔴 check how to connect Muse Code to an MCP server |

## 4. Data layer (shipcube-data)

| | Component | Owner | Status |
|---|---|---|---|
| ★ | Catalog DB, migration vocabulary and runner, L0 compiler, schema routing, partition pruning | data-layer | ✅ prototype, 109 tests |
| ★ | Five `data_` MCP tools as an in-process library, mounted on the MCP server | data-layer + networking | ✅ mounted |
| ★ | Demo warehouse: Olist in one database, two schemas, date partitions (DuckDB locally; BigQuery or Cloud SQL on GCP) | data-layer | 🟡 local only |
| ☆ | L1 operators and L2 stages compiled | data-layer | 🔴 |
| ★ | Console-registered datasource from a secret reference | data-layer + security | 🟡 function exists, no UI wiring |
| ☆ | Rename of the data layer's token scopes to match security's list | data-layer | 🔴 open |

## 5. AI gateway and secrets

| | Component | Owner | Status |
|---|---|---|---|
| ★ | AI gateway for user apps (Go): Muse default, BYOK OpenAI-compatible, OpenAI, Anthropic | security (design) | 🔴 unowned for build |
| ★ | Secret Manager for BYOK keys and warehouse credentials | security | 🟡 designed |
| ☆ | Access proxy fallback (Go), if IAP can't be used | security | 🟡 designed, build only if needed |

## 6. Monitoring

| | Component | Owner | Status |
|---|---|---|---|
| ★ | `inspect_app` reading Cloud Logging and Cloud Monitoring (requests, errors, p50/p95, instances, cold starts) | unowned | 🔴 |
| ☆ | Cost per app (billing export or estimate) | unowned | 🔴 |
| ☆ | Data freshness and output drift checks | unowned | 🔴 |
| ☆ | Re-placement suggestion (live telemetry → classifier) | load-estimator | 🔴 |
| ☆ | Alerts (email or webhook) | unowned | 🔴 |

## 7. Web console

| | Component | Owner | Status |
|---|---|---|---|
| ★ | Design system, all screens on mock data (apps, app detail, access, caps, approvals, data, AI, billing, MCP guide) | frontend | ✅ mock |
| ★ | Identity Platform enabled with the Google provider on `shipcube-control-ks` | Kunal | 🔴 the previous attempt was on the wrong project |
| ★ | Identity Platform auth adapter | frontend | 🔴 |
| ★ | Wire screens to the real API | frontend | 🔴 |
| ★ | Create PAT screen | frontend | 🟡 mock |

## 8. Platform, CI and hosting ShipCube itself

| | Component | Owner | Status |
|---|---|---|---|
| ★ | Terraform for `shipcube-control` / `shipcube-apps`: validate and apply | security | 🟡 skeleton, never validated |
| ★ | Deploy the console, MCP server and API to Cloud Run with a domain | unowned | 🔴 |
| ☆ | CI: lint, tests and build on every PR (GitHub Actions) | unowned | 🔴 |

## 9. Submission

| | Component | Owner | Status |
|---|---|---|---|
| ★ | Merge PRs: #4 → #2 → #1 → #3 → #5 | Kunal | 🔴 |
| ★ | Demo app (Olist tracker) built through the MCP | Kunal + all | 🔴 |
| ★ | README with real numbers (classifier metrics, deploy time, cost per deploy, compiler tests) | all | 🔴 |
| ★ | 2-minute demo video | Kunal | 🔴 |
| ★ | Submit before Nov 4, 9:00 PM ET | Kunal | 🔴 |
