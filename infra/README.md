# infra

Infrastructure as code for ShipCube's own GCP projects, plus the scripts users run to link their own project. Design and reasoning: [docs/research/security-tenancy.md](../docs/research/security-tenancy.md).

**Status (Oct 28): applied.** Kunal approved the plan and cost and ran `terraform apply`; a follow-up `terraform plan` shows no changes. State is in `gs://shipcube-control-ks-tfstate` (versioned, prefix `shipcube`). Cloud SQL connection name: `shipcube-control-ks:asia-south1:shipcube-pg`. The budget kill switch is live (see `budget-killswitch/`).

Database (Oct 28): roles bootstrapped; migrations `0001`–`0004` applied and verified with `db/checks/check.sh`; see `db/` below.

| Path | What it is |
|---|---|
| `terraform/` | ShipCube's two projects: `shipcube-control` (API, MCP, gateway, Cloud SQL, secrets, audit logs) and `shipcube-apps` (hosted apps, profiler sandbox) |
| `budget-killswitch/` | Hard spending cap: function + `deploy.sh`; Terraform owns its budget, topic and service account |
| `db/` | `bootstrap.sql`/`.sh` (once, as `postgres`), `owner_defaults.sql` and `migrate.sh` (every migration, as `sc-migrator`), `proposed/` migrations for `services/api/migrations` |
| `scripts/link-project.sh` | Run by a user, as Owner of their project, to let their workspace's ShipCube deployer deploy there |
| `scripts/unlink-project.sh` | Removes every grant `link-project.sh` made |

## What the plan creates and costs

| Area | Resources | Monthly cost (asia-south1, list price) |
|---|---|---|
| Cloud SQL | Postgres 16 `db-f1-micro`, 10 GB SSD (autoresize to 50), daily backups kept 14, point-in-time recovery 7 days, pgaudit (DDL and role changes), query insights, deletion protection | ~$8–12 instance + ~$2 storage + <$1 backups/logs ≈ **$11–15** |
| Audit logging | Data Access logs for Secret Manager, Cloud SQL, IAM and IAM Credentials (control) and IAP (apps); a `shipcube-audit` log bucket kept 365 days with sinks from both projects | Within the 50 GiB/month free ingestion at current volume; retention past 30 days $0.01/GiB-month ≈ **<$1** |
| Identity | 6 service accounts, least-privilege IAM bindings, a 2-permission custom role | free |
| Registry | `shipcube` (apps, adopted) and `platform` repos; keep 5 newest versions, delete others after 30 days | $0.10/GB-month over 0.5 GB ≈ **<$1** |
| Network | Profiler sandbox VPC, one /26 subnet, deny-all-egress firewall | free |
| Billing | Hard-cap budget, topic and kill-switch account (adopted, unchanged) | free |
| Monitoring | `modules/monitoring`: 4 uptime checks, 8 alert policies (one condition each), 1 email channel | uptime checks ~104k executions/month of 1M free; alerting is billed per condition (~$0.10/condition-month) ≈ **<$1** |
| APIs | Enables the APIs both projects need (most already on) | free |

**Total ≈ $12–17/month (~₹1,000–1,450), about $40–50 over the rest of the trial**, nearly all Cloud SQL. That is within the ₹24,000 cap.

The one change to an existing resource: the `shipcube` registry's cleanup policy changes from "delete images older than 1 day" to "keep the 5 newest per app, delete others after 30 days", so images outlive the 7-day restore window for deleted apps.

## Terraform

The projects themselves are created by hand (a free-trial account has no organization to create them under). State lives in a GCS bucket with versioning, created once:

```bash
gcloud storage buckets create gs://<control-project>-tfstate --location=asia-south1 --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://<control-project>-tfstate --versioning
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in project IDs and billing account; gitignored
terraform init -backend-config="bucket=<control-project>-tfstate" -backend-config="prefix=shipcube"
terraform plan -out=tfplan
terraform apply tfplan   # only after the plan and its cost are approved
```

`imports.tf` adopts resources that were created by hand or by other sessions before Terraform owned them (budget, topic, kill-switch account, two secrets, `sc-api`, the `shipcube` registry). The import blocks are no-ops once applied.

What it creates:

- APIs in both projects.
- Service accounts: `sc-api`, `sc-gateway`, `sc-worker`, `sc-hosted-deployer` (control), `sc-profiler` (apps). Hosted builds run as per-workspace `sc-build-<id>` accounts that the worker provisions (C-1); the shared `sc-builder` was deleted on 2026-10-30. Per-workspace deployers (`ws-<id>-deployer`) and per-app runtime accounts are created by the API at runtime, not here.
- Cloud SQL Postgres 16, `db-f1-micro`, IAM database auth only, backups and PITR, pgaudit.
- Audit logging (Data Access logs and a 365-day audit bucket).
- Secret Manager **containers** for platform secrets, with accessor bindings. Values are added with `gcloud secrets versions add`, never through Terraform (state would hold them in plain text).
- Artifact Registry repos, with a cleanup policy for app images.
- The profiler sandbox VPC with a deny-all egress firewall.
- A billing budget on the credit pool that publishes to Pub/Sub for the kill switch.

Manual steps Terraform can't do:

1. Identity Platform with the Google provider on `shipcube-control` (done Oct 28).
2. The external OAuth client for IAP in `shipcube-apps`, applied with `gcloud iap settings set` (the IAP OAuth Admin API is shut down for new projects).
3. Secret values: `meta-api-key` and `token-hmac-pepper` were added at version 1 on Oct 28 (adopted by `imports.tf`). Add or rotate with `printf '%s' "$VALUE" | gcloud secrets versions add NAME --data-file=-`.

## Local stack (database and secrets)

The local database is built the same way as Cloud SQL, so RLS and grants behave identically. Platform's `deploy/local/compose.yaml` includes `local/compose.yaml`; it also runs on its own:

```bash
python3 infra/local/localsecrets.py init      # .local/secrets.{json,env}, gitignored, 0600; idempotent
docker compose --env-file .local/secrets.env -f infra/local/compose.yaml up -d --wait db secretmanager
docker compose --env-file .local/secrets.env -f infra/local/compose.yaml run --rm db-init
docker compose --env-file .local/secrets.env -f infra/local/compose.yaml run --rm migrate
infra/local/test.sh
```

| Piece | Local | Cloud SQL / production | Shared |
|---|---|---|---|
| Logins | password logins named `sc-migrator@…iam`, `sc-api@…iam`, `sc-worker@…iam` (`db/local-init.sh`) | IAM database users (Terraform) | the names |
| Roles, grants, default session roles | `db/bootstrap.sql` | `db/bootstrap.sql` (`bootstrap.sh`) | the file, unchanged |
| Default privileges | `db/owner_defaults.sql` as sc-migrator | same, before every migration (`migrate.sh`) | the file, unchanged |
| Migrations | `python -m shipcube_api.admin migrate` as sc-migrator (compose `migrate`) | same, through the proxy (`migrate.sh`) | the runner and `services/api/migrations` |
| Secrets | `local/localsecrets.py` generates values (prefix `sclocal_`) into `.local/`; `local/secretmanager.py` serves them at Secret Manager's REST path; compose passes them as env | Secret Manager; Cloud Run injects env | the secret names and env names |
| Isolation checks | `local/test.sh` | `db/checks/check.sh` | `db/checks/rls_and_token.sql` via `db/checks/run.sh` |

`db/checks/rls_and_token.sql` also checks that the migration ledgers are read-only to the service roles (a WARN until `db/proposed/0005_lock_migration_ledger.sql` is adopted). When shipcube-data's `catalog` schema exists, it checks that every catalog table has forced RLS, is owned by `shipcube_owner` and has only the `workspace_id = app_workspace()` policy, and that A can't read or write B there. `bootstrap.sql` creates the `catalog` schema and lets `shipcube_owner` create tables in it (no CREATE on the database); **re-run `db/bootstrap.sh` on Cloud SQL before the catalog migration goes live**.

Both secret tools refuse to run on Cloud Run (`K_SERVICE`, `CLOUD_RUN_JOB`) or with `SHIPCUBE_ENV` other than `local`. Values are never rotated once created, so restarting keeps the database usable. If `.env` has `MUSE_API_KEY` (or `META_API_KEY`), it becomes the local `meta-api-key`. `local/test.sh` runs the shared RLS/PAT SQL check as sc-api, a PAT login through `auth.verify_pat`, and the deployer's build-isolation checks (workspace A can't use B's upload or image). Real IAM on build service accounts needs GCP: `tests/build-isolation.sh`. CI's `local-db` job runs the same commands.

## Build isolation (per-workspace builders)

Hosted builds run tenant code, so each workspace builds as its own service account with access to only its own uploads folder and image repo (design: security-tenancy §4).

- `terraform/modules/build_isolation`: the static pieces. `sc-provisioner` (impersonable only by `sc-worker`), its two custom roles (SA + repo admin in the apps project; managed-folder admin on the uploads bucket), and `storage.buckets.get` (metadata only) on the uploads bucket for all SAs in the apps project, which Cloud Build requires before it accepts a logs folder. Builders hold no `logging.logWriter`: hosted builds log to `ws/<ws_uuid>/build-logs/` (#28).
- `scripts/provision-workspace-builder.sh <workspace-uuid> [--delete]`: the per-workspace resources (`sc-build-<ws_short>` SA, `ws-<ws_short>` repo, `ws/<ws_uuid>/` and `ws/<ws_uuid>/build-logs/` managed folders, and their bindings). The worker makes the same calls in Python. Idempotent.
- `tests/build-isolation.sh`: live test. It provisions two throwaway workspaces and, with real tokens for A's build SA, checks that A reads its own upload but not B's, can't list the bucket, and can push and pull only its own repo (not B's, `shipcube` or `profiler`). It cleans up on exit. Run it as an apps-project Owner.

Rollout: done on 2026-10-30. `build_isolation` is applied, `build-isolation.sh` passes, hosted builds run as `sc-build-<ws_short>` and deploy by digest, and the shared `sc-builder` (with its bucket-wide read, project-wide registry write and hand-made `logging.logWriter`) is deleted.

## Monitoring (uptime checks and alerts)

`terraform/modules/monitoring` (wired in `monitoring.tf`) watches the control project. Every alert emails one channel, `var.alert_email`, which lives only in the gitignored `terraform.tfvars`, and links to the runbook in `deploy/README.md` (Operate).

- **Uptime checks**, every 5 minutes from 3 regions: console `/api/healthz`, MCP `/health`, gateway `/health`, and worker `/health` (with an OIDC token from the Monitoring service agent, which `deploy/deploy.sh` grants `run.invoker`; the worker answers 503 when its queue loop died or stalled).
- **Alerts:** a service's uptime check failing from 2+ regions for 5 minutes (one per service); a 5xx ratio above 5% for 5 minutes on any of the four services; no worker instance for 15 minutes (the queue isn't draining); Cloud SQL down for 5 minutes; Cloud SQL CPU above 80% for 15 minutes.
- **Spend** isn't alerted here: the budget and its kill switch (`budget.tf`) email at 25/50/75/90% and unlink billing at the cap.

Cost: under $1/month (see the table above). Scaling the worker to zero to save money (deploy/README.md) trips the worker alert by design.

## Database

```bash
brew install cloud-sql-proxy libpq                      # once
infra/db/bootstrap.sh                                   # per instance, and again when bootstrap.sql changes (idempotent), as postgres
API_DIR=services/api PYTHON=python3.12 infra/db/migrate.sh   # every migration, as sc-migrator
```

`migrate.sh` impersonates `sc-migrator`, so the caller must be in `migration_operators` (Terraform). It applies `owner_defaults.sql` first, then the API's checksummed, advisory-locked runner. After any migration, verify as the API's role (everything runs in a rolled-back transaction):

```bash
LOGIN=sc-api infra/db/checks/check.sh
```

### Pending live steps (Cloud SQL), in order

The instance must be running. Take an on-demand backup first (`gcloud sql backups create --instance shipcube-pg --project shipcube-control-ks`), and run `check.sh` after each step. An API build refuses to start while its migrations are pending (`check_schema`), so each step must come **before** the deploy that needs it.

1. **Before deploying #38 (shipcube-data catalog):** re-run `infra/db/bootstrap.sh`. `bootstrap.sql` now creates schema `catalog` (owned by postgres) and grants `shipcube_owner` CREATE in it. Without this step, the catalog migration fails with "permission denied for database shipcube".
2. **Before deploying #35 (migration 0005, read-only migration ledger):** `migrate.sh` applies `0005_lock_migration_ledger.sql`. After that, `check.sh` reports `PASS 5` for `public.schema_migrations`.
3. **With #38:** the same `migrate.sh` run applies the catalog (`data catalog applied: ['0001_catalog.sql']`), and `check.sh` then runs `PASS 6` on the catalog tables.

The `postgres` password is only in Secret Manager (`sql-postgres-password`); rotate it by bumping `postgres_password_version`.
