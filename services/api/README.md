# services/api

The ShipCube control-plane API and its durable operations worker. The API owns all state in Postgres, makes every authorization decision and deploys to GCP. The MCP server (`services/mcp`) and the web console are its clients.

- **Contract:** [`openapi.yaml`](openapi.yaml). `tests/test_api.py` fails if the FastAPI routes drift from it.
- **Design:** `docs/research/networking.md`: §2.5 approvals, §3 API and job model, §4 request flow, §4.3 the deploy path as built.

## How it works

```
agent ─MCP─> /mcp ─in-process─> /v1 (FastAPI) ──writes desired state + operation──> Postgres
                                                                                      │ claim (FOR UPDATE SKIP LOCKED, lease)
                                              worker (embedded thread or standalone) ◄┘
                                                │ idempotent steps, state saved after each
                                                ▼
             GCS (upload) → Cloud Build (buildpacks) → Artifact Registry → Cloud Run (gen2, IAP) → IAP bindings
```

| Module | Role |
|---|---|
| `app.py` | Routes, auth dependencies, typed error mapping, request ids and trace propagation |
| `store.py` | Request-side logic on security's schema: apps, deployments, caps, access grants, approvals, idempotent operation enqueueing, workspace limits (`max_apps`, `deploys_per_day`) |
| `worker.py` | Durable queue: claim with a lease, a 30 s heartbeat, step plans per operation kind, retries with backoff, housekeeping |
| `deploy/gcp.py` | GCP over REST with `google-auth`: signed uploads, Cloud Build, Cloud Run v2, IAM, IAP, traffic, purge |
| `deploy/mock.py` | DEV_MODE only |
| `estimator.py` | `estimate_load` engine: load-estimator's `shipcube_placement` if installed, otherwise a typed `estimator_unavailable` error |
| `telemetry.py` | Logs and metrics interface. The platform workstream installs the real implementation; without it the API answers 501. |
| `auth.py` | PATs in `api_tokens` (HMAC with a pepper), Identity Platform ID tokens for console users |
| `db.py`, `migrations/` | Pool, versioned migrations (checksummed, advisory-locked), the startup schema check, and per-transaction role and caller context for row-level security |
| `obs.py` | JSON logs: `severity`, `message`, `request_id`, `operation_id`, `logging.googleapis.com/trace` |
| `admin.py` | Operator CLI |

## Run locally

```bash
brew install postgresql@16 uv
initdb -D ~/.shipcube-pg -U postgres -A trust && pg_ctl -D ~/.shipcube-pg -o "-p 5433" -l ~/.shipcube-pg/log start
createdb -h localhost -p 5433 -U postgres shipcube
export SHIPCUBE_DATABASE_URL=postgresql://postgres@localhost:5433/shipcube SHIPCUBE_TOKEN_PEPPER=$(openssl rand -hex 32)

# Production mode against real GCP (ADC: gcloud auth application-default login):
uv run python -m shipcube_api.admin migrate                # services never migrate on startup outside DEV_MODE
uv run python -m shipcube_api.admin bootstrap --email you@gmail.com --workspace demo --name "Demo"   # prints a PAT once
uv run python -m shipcube_api.worker &                     # the durable worker
cd ../mcp && uv run python -m shipcube_mcp.server          # /mcp + /v1 on :8080

# Or DEV_MODE (mock deployer, mock estimator, mock telemetry, dev console sessions): SHIPCUBE_DEV_MODE=1
```

## Configure

| Variable | Default | Meaning |
|---|---|---|
| `SHIPCUBE_DATABASE_URL` | required | Postgres. On Cloud SQL, connect as the IAM database user for `shipcube_app` (API) or `shipcube_worker` (standalone worker). |
| `SHIPCUBE_DB_SET_ROLE` | `1` | `SET LOCAL ROLE shipcube_app` (API) or `shipcube_worker` (worker, CLI) per transaction, for local and test logins that are superusers. **Set to `0` on Cloud SQL**, where each IAM login already starts in its role (`infra/db/bootstrap.sql`). |
| `SHIPCUBE_TOKEN_PEPPER` | required outside DEV_MODE | HMAC pepper for token digests, from Secret Manager |
| `SHIPCUBE_IDP_PROJECT` | unset | Identity Platform project for console ID tokens (approvals, restore) |
| `SHIPCUBE_BACKEND` | `gcp` (`mock` in DEV_MODE) | `mock` is refused outside DEV_MODE |
| `SHIPCUBE_DEV_MODE` | unset | `1` enables every mock; never set in production |
| `SHIPCUBE_APPS_PROJECT`, `SHIPCUBE_CONTROL_PROJECT`, `SHIPCUBE_REGION` | `shipcube-apps-ks`, `shipcube-control-ks`, `asia-south1` | Where apps run |
| `SHIPCUBE_UPLOAD_BUCKET`, `SHIPCUBE_SIGNER_SA`, `SHIPCUBE_REPO`, `SHIPCUBE_BUILDER` | see `settings.py` | Upload bucket, URL signer, Artifact Registry repo in linked projects, buildpacks builder |
| `SHIPCUBE_BUILDER_SA_TEMPLATE`, `SHIPCUBE_AR_REPO_TEMPLATE`, `SHIPCUBE_UPLOAD_PREFIX_TEMPLATE` | `sc-build-{ws_short}@{apps_project}.iam.gserviceaccount.com`, `ws-{ws_short}`, `ws/{ws}/` | Per-workspace build isolation: each workspace builds as its own SA, reads only its upload prefix and pushes only to its own repo; deploys pin the digest (`deploy/isolation.py`) |
| `SHIPCUBE_HOSTED_DEPLOYER_SA` | unset | Impersonate this SA for hosted deploys (production: `sc-hosted-deployer`) |
| `SHIPCUBE_PROFILER_JOB` | unset | Profiler sandbox job for `estimate_load`; unset means static analysis only |
| `SHIPCUBE_EMBEDDED_WORKER` | `1` in DEV_MODE, else `0` | Run a worker thread in the API process. Not possible in production: the API's login (`shipcube_app`) is confined to the caller's workspace by RLS, and the worker needs `shipcube_worker`. |
| `SHIPCUBE_LOG_LEVEL` | `INFO` | |

## Deploy (Cloud Run, in `shipcube-control-ks`)

**One-time GCP setup:** `scripts/gcp_setup.sh` (idempotent). It creates the APIs, the Artifact Registry repo, the upload bucket with a 1-day lifecycle, `sc-builder`, `sc-api` and the IAP service agent, plus the grants. **IAP on a project without an organization** also needs one console step:
1. Set Google Auth Platform → Branding to **External**. While in Testing, add every user as a test user.
2. Create a Web OAuth client with the redirect URI `https://iap.googleapis.com/v1/oauth/clientIds/CLIENT_ID:handleRedirect`.
3. Apply it:

```bash
printf 'access_settings:\n  oauth_settings:\n    client_id: CLIENT_ID\n    client_secret: CLIENT_SECRET\n' > /tmp/iap.yaml && gcloud iap settings set /tmp/iap.yaml --project=shipcube-apps-ks && rm /tmp/iap.yaml
```

**Schema:** `API_DIR=services/api infra/db/migrate.sh` (security's script) applies pending migrations as `sc-migrator`, using `python -m shipcube_api.admin migrate`, before new code rolls out. Services check the schema at startup and refuse to start if a migration is pending.

**Services:** one image, two Cloud Run services, both with `SHIPCUBE_DB_SET_ROLE=0`, `SHIPCUBE_TOKEN_PEPPER` from Secret Manager and Cloud SQL through the connector with IAM authentication.
- `shipcube-api`: `python -m shipcube_mcp.server` (MCP + API), running as **`sc-api`**, with `min-instances=1`.
- `shipcube-worker`: `python -m shipcube_api.worker`, running as **`sc-worker`**, with `min-instances=1` and CPU always allocated (it polls the queue). Add instances to deploy in parallel; workers coordinate through row locks.

Hosting ShipCube itself is the platform workstream's job; this section says what the API needs.

## Operate (runbook)

| Situation | What to do |
|---|---|
| See what's happening | `python -m shipcube_api.admin ops` (latest 20), or filter the logs by `jsonPayload.operation_id` |
| A deploy seems stuck | `admin ops --stuck` lists running operations with expired leases, operations retried more than twice, and queued ones overdue by 5 minutes. An expired lease is picked up again by any worker automatically, resuming at `step_index`. A running build is re-attached by its `build_id`, not restarted. |
| Retry a failed operation | `admin requeue <id>` resumes from the step that failed |
| Give up on one | `admin fail <id> --reason "..."` |
| Transient GCP errors | 429, 5xx, timeouts and network errors retry automatically with exponential backoff (5 s × 2ⁿ, capped at 300 s, with jitter) up to `max_attempts` (5). Other errors fail at once with a typed `error_code`: `build_failed`, `permission_denied`, `invalid_argument`, `not_found`, `conflict`, `estimator_unavailable`, `gcp_error`. |
| Build failed | The operation's error includes the Cloud Build log URL. Python apps need `requirements.txt` and a `Procfile`. |
| IAP answers 502 "Empty Google Account OAuth client ID(s)/secret(s)" | The OAuth client step above wasn't done for that project |
| Unanswered approvals | They expire after 72 h; housekeeping (every minute) cancels the operation with `approval_expired` |
| Deleted apps | A soft delete removes IAP access and makes ingress internal-only. `POST /v1/apps/{app}/restore` (console) undoes it within 7 days. After that, housekeeping enqueues a `purge`, which deletes the service, its runtime SA and its images. |
| Migrations | Only through `infra/db/migrate.sh` as `sc-migrator` (advisory-locked, checksummed). A service that finds a pending or changed migration refuses to start, naming it. Never edit an applied file; add `migrations/000N_*.sql` instead. `0001` and `0003` are security's (verbatim), `0002` and `0004` are networking's. |

## Tests

```bash
uv run pytest                     # unit + API + durability tests on a throwaway local Postgres (or SHIPCUBE_TEST_DATABASE_URL)
SHIPCUBE_IT_GCP=1 SHIPCUBE_BACKEND=gcp SHIPCUBE_IT_OWNER=you@gmail.com uv run pytest tests/integration -s   # real GCP, ~4 min
```

The GCP integration test covers build → deploy → IAP policy and interception → redeploy → rollback (it checks Cloud Run traffic) → grant → soft delete → restore → purge, and checks that the service and SA are gone afterwards.

## Known limits

- **Linked user projects:** the code path (impersonating the workspace's deployer SA) is unit-tested, but it hasn't run against a real linked project yet.
- **Approvals:** one deploy that needs two approvals stores one `approvals.action`, chosen by precedence; all actions are listed in `approval_actions`. This waits on security's decision.
- **Console identity:** Identity Platform token verification is implemented but untested end to end, because the console doesn't issue tokens yet.
- **Rollback:** it pins traffic to a revision. The next deploy moves traffic back to the latest revision.
- **T2:** Cloud Run jobs and Cloud Scheduler aren't built yet.
- **Requests per minute:** not enforced on app traffic while apps use IAP directly (see `networking.md` §6).
