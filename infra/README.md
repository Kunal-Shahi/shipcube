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

`imports.tf` adopts resources that were created by hand or by other sessions before Terraform owned them (budget, topic, kill-switch account, two secrets, `sc-api`, `sc-builder`, the `shipcube` registry). The import blocks are no-ops once applied.

What it creates:

- APIs in both projects.
- Service accounts: `sc-api`, `sc-gateway`, `sc-worker`, `sc-hosted-deployer` (control), `sc-profiler`, `sc-builder` (apps). Per-workspace deployers (`ws-<id>-deployer`) and per-app runtime accounts are created by the API at runtime, not here.
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

## Build isolation (per-workspace builders)

Hosted builds run tenant code, so each workspace builds as its own service account with access to only its own uploads folder and image repo (design: security-tenancy §4).

- `terraform/modules/build_isolation`: the static pieces. `sc-provisioner` (impersonable only by `sc-worker`), its two custom roles (SA + repo admin in the apps project; managed-folder admin on the uploads bucket), and `logging.logWriter` for all SAs in the apps project, so provisioning never edits project IAM.
- `scripts/provision-workspace-builder.sh <workspace-uuid> [--delete]`: the per-workspace resources (`sc-build-<ws_short>` SA, `ws-<ws_short>` repo, `ws/<ws_uuid>/` managed folder, and their bindings). The worker makes the same calls in Python. Idempotent.
- `tests/build-isolation.sh`: live test. It provisions two throwaway workspaces and, with real tokens for A's build SA, checks that A reads its own upload but not B's, can't list the bucket, and can push and pull only its own repo (not B's, `shipcube` or `profiler`). It cleans up on exit. Run it as an apps-project Owner.

Rollout:

1. `terraform apply -target=module.build_isolation` (7 resources, all IAM, free). Run it from a tree that also contains platform's PR #13 Terraform, or a full plan would remove that PR's live resources. Use `-target` while the Cloud SQL instance is deliberately stopped, because a full apply turns it back on.
2. `infra/tests/build-isolation.sh` must print `ALL ISOLATION CHECKS PASSED`.
3. Networking switches hosted builds to the per-workspace SA, repo and `ws/<ws_uuid>/` paths, and deploys by digest.
4. Provision existing workspaces (`provision-workspace-builder.sh <uuid>` for each), then set `legacy_shared_builder = false` and apply. That removes `sc-builder`'s bucket-wide read and project-wide registry write. Also remove its hand-made `logging.logWriter` binding: `gcloud projects remove-iam-policy-binding shipcube-apps-ks --member serviceAccount:sc-builder@shipcube-apps-ks.iam.gserviceaccount.com --role roles/logging.logWriter`.

## Database

```bash
brew install cloud-sql-proxy libpq                      # once
infra/db/bootstrap.sh                                   # once per instance, as postgres
API_DIR=services/api PYTHON=python3.12 infra/db/migrate.sh   # every migration, as sc-migrator
```

`migrate.sh` impersonates `sc-migrator`, so the caller must be in `migration_operators` (Terraform). It applies `owner_defaults.sql` first, then the API's checksummed, advisory-locked runner. After any migration, verify as the API's role (everything runs in a rolled-back transaction):

```bash
LOGIN=sc-api infra/db/checks/check.sh
```

The `postgres` password is only in Secret Manager (`sql-postgres-password`); rotate it by bumping `postgres_password_version`.
