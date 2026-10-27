#!/usr/bin/env bash
# Cloud SQL Postgres for the app database. Mirrors infra/terraform/database.tf (security workstream),
# plus the backup settings below. Run it once the API stores data in Postgres; until then it would
# bill for nothing.
#
# Instance: shipcube-pg, Postgres 16, Enterprise edition, db-f1-micro (shared core), 10 GB SSD,
# zonal, IAM database auth only (no passwords), TLS required, deletion protection.
# Backups: daily automated backups at 18:00 UTC (23:30 IST), 14 kept; point-in-time recovery with
# 7 days of write-ahead logs. Maintenance: Sundays 21:00 UTC.
# Cost: ≈ $10–12/month for the instance and disk, plus ≈ $1–2 for backups and logs at this size.
set -euo pipefail
source "$(dirname "$0")/env.sh"
INSTANCE=shipcube-pg

gcloud services enable sqladmin.googleapis.com --project "$PROJECT"
if ! gcloud sql instances describe "$INSTANCE" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud sql instances create "$INSTANCE" --project "$PROJECT" --region "$REGION" \
    --database-version POSTGRES_16 --edition ENTERPRISE --tier db-f1-micro \
    --storage-type SSD --storage-size 10 --storage-auto-increase --storage-auto-increase-limit 50 \
    --availability-type zonal --ssl-mode ENCRYPTED_ONLY \
    --database-flags cloudsql.iam_authentication=on \
    --backup-start-time 18:00 --retained-backups-count 14 \
    --enable-point-in-time-recovery --retained-transaction-log-days 7 \
    --maintenance-window-day SUN --maintenance-window-hour 21 \
    --deletion-protection
else
  # Bring an existing instance (e.g. one created by Terraform) up to the backup policy.
  gcloud sql instances patch "$INSTANCE" --project "$PROJECT" --quiet \
    --backup-start-time 18:00 --retained-backups-count 14 \
    --enable-point-in-time-recovery --retained-transaction-log-days 7
fi
gcloud sql databases describe shipcube --instance "$INSTANCE" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud sql databases create shipcube --instance "$INSTANCE" --project "$PROJECT"

gcloud iam service-accounts describe "$SA_MIGRATOR" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam service-accounts create sc-migrator --project "$PROJECT" --display-name "Schema migrations job"
for sa in "$SA_API" "$SA_MIGRATOR"; do
  gcloud sql users create "${sa%.gserviceaccount.com}" --instance "$INSTANCE" --project "$PROJECT" \
    --type cloud_iam_service_account 2>/dev/null || true
  for role in roles/cloudsql.client roles/cloudsql.instanceUser; do
    gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$sa" --role "$role" \
      --condition None >/dev/null
  done
done
cat <<NEXT
Done. Next:
  - The first migration must grant sc-migrator ownership and the API its table privileges (run once
    as the postgres admin, or have security's 0001 migration do it).
  - deploy/migrate.sh to apply db/migrations.
  - Add --add-cloudsql-instances $PROJECT:$REGION:$INSTANCE to the shipcube-mcp deploy.
  - deploy/monitoring.sh again to add the Cloud SQL alerts.
Restore drill: gcloud sql backups list --instance $INSTANCE; restore a backup into a scratch instance
with gcloud sql backups restore, or clone to a point in time with
gcloud sql instances clone $INSTANCE ${INSTANCE}-restore --point-in-time <RFC3339>.
NEXT
