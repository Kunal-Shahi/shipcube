#!/usr/bin/env bash
# One-time setup for the real deploy path (SHIPCUBE_BACKEND=gcp). Idempotent: safe to re-run.
#
# Names match security's Terraform (infra/terraform on PR #2): repo `shipcube`, SAs `sc-api` and
# `sc-builder`. If that Terraform is applied later, import these resources instead of recreating.
#
# Everything here is free or within free tiers except Artifact Registry storage beyond 0.5 GB
# (~$0.10/GB-month, capped by the keep-5 cleanup policy) and Cloud Build beyond 2,500 min/month.
set -euo pipefail

APPS=${SHIPCUBE_APPS_PROJECT:-shipcube-apps-ks}
CONTROL=${SHIPCUBE_CONTROL_PROJECT:-shipcube-control-ks}
REGION=${SHIPCUBE_REGION:-asia-south1}
BUCKET=${SHIPCUBE_UPLOAD_BUCKET:-shipcube-uploads-ks}
ME=$(gcloud config get-value account 2>/dev/null)
BUILDER="sc-builder@${APPS}.iam.gserviceaccount.com"
SIGNER="sc-api@${CONTROL}.iam.gserviceaccount.com"

echo "== APIs (free)"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  iap.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com --project "$APPS"
gcloud services enable iamcredentials.googleapis.com --project "$CONTROL"

echo "== Artifact Registry repo (keeps the 5 newest versions per image)"
gcloud artifacts repositories describe shipcube --location "$REGION" --project "$APPS" >/dev/null 2>&1 ||
  gcloud artifacts repositories create shipcube --repository-format docker --location "$REGION" \
    --project "$APPS" --description "Images for hosted user apps"
cat > /tmp/shipcube-ar-cleanup.json <<'EOF'
[{"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
 {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "1d"}}]
EOF
gcloud artifacts repositories set-cleanup-policies shipcube --location "$REGION" --project "$APPS" \
  --policy /tmp/shipcube-ar-cleanup.json --no-dry-run

echo "== Upload bucket (private, objects deleted after 1 day)"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 ||
  gcloud storage buckets create "gs://$BUCKET" --project "$APPS" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
cat > /tmp/shipcube-lifecycle.json <<'EOF'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 1}}]}
EOF
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file /tmp/shipcube-lifecycle.json

echo "== Service accounts (no project roles beyond what is listed)"
gcloud iam service-accounts describe "$BUILDER" --project "$APPS" >/dev/null 2>&1 ||
  gcloud iam service-accounts create sc-builder --project "$APPS" --display-name "Cloud Build for hosted apps"
gcloud iam service-accounts describe "$SIGNER" --project "$CONTROL" >/dev/null 2>&1 ||
  gcloud iam service-accounts create sc-api --project "$CONTROL" --display-name "ShipCube control-plane API and MCP server"

echo "== Grants"
# Builder: push images, read uploaded sources, write build logs.
gcloud artifacts repositories add-iam-policy-binding shipcube --location "$REGION" --project "$APPS" \
  --member "serviceAccount:$BUILDER" --role roles/artifactregistry.writer --condition None >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$BUILDER" \
  --role roles/storage.objectViewer >/dev/null
gcloud projects add-iam-policy-binding "$APPS" --member "serviceAccount:$BUILDER" \
  --role roles/logging.logWriter --condition None >/dev/null
# Signer: upload URLs are signed as sc-api, so it needs to create objects in the bucket, and the
# local operator needs to sign as it (IAM signBlob).
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SIGNER" \
  --role roles/storage.objectCreator >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$SIGNER" --project "$CONTROL" \
  --member "user:$ME" --role roles/iam.serviceAccountTokenCreator >/dev/null

echo "== IAP service agent (gets run.invoker on each app service at deploy time)"
gcloud beta services identity create --service iap.googleapis.com --project "$APPS"

echo "Done. Remaining manual step: the IAP OAuth client (see services/api/README.md, 'IAP on a project without an organization')."
