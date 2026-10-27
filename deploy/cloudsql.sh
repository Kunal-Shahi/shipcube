#!/usr/bin/env bash
# Cloud SQL Postgres for the app database: deferred until the API reads and writes Postgres
# (today services/api keeps mock state in memory). Matches infra/terraform/database.tf exactly
# (security workstream), so prefer `terraform apply` there; this is the no-Terraform path.
# Cost: db-f1-micro (shared core, 0.6 GB) + 10 GB SSD, zonal, ≈ $10–12/month in asia-south1.
set -euo pipefail
source "$(dirname "$0")/env.sh"
gcloud services enable sqladmin.googleapis.com --project "$PROJECT"
gcloud sql instances describe shipcube-pg --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud sql instances create shipcube-pg --project "$PROJECT" --region "$REGION" \
    --database-version POSTGRES_16 --edition ENTERPRISE --tier db-f1-micro \
    --storage-type SSD --storage-size 10 --no-storage-auto-increase --availability-type zonal \
    --backup --ssl-mode ENCRYPTED_ONLY --database-flags cloudsql.iam_authentication=on \
    --deletion-protection
gcloud sql databases describe shipcube --instance shipcube-pg --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud sql databases create shipcube --instance shipcube-pg --project "$PROJECT"
gcloud sql users create "${SA_API%.gserviceaccount.com}" --instance shipcube-pg --project "$PROJECT" \
  --type cloud_iam_service_account 2>/dev/null || true
for role in roles/cloudsql.client roles/cloudsql.instanceUser; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA_API" --role "$role" \
    --condition None >/dev/null
done
echo "Then add --add-cloudsql-instances $PROJECT:$REGION:shipcube-pg to the shipcube-mcp deploy."
