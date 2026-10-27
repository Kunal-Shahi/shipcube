#!/usr/bin/env bash
# Provision the profiler sandbox and the model bucket. Idempotent: safe to re-run.
#
#   PROJECT=shipcube-control-ks REGION=asia-south1 CALLER_SA=<sa that runs estimate()> \
#     packages/profiler/deploy/provision.sh
#
# Creates (all billable only when used; storage and images expire by policy):
#   - bucket gs://$BUCKET            model artefacts (models/**, kept) and profiler source
#                                    tarballs (profiler-src/**, deleted after 1 day)
#   - Artifact Registry repo $REPO   profiler images, deleted after 7 days
#   - VPC $NETWORK + subnet $SUBNET  no Private Google Access; a deny-all egress rule
#   - SA $SANDBOX_SA                 job runtime identity with NO roles and no secrets
#   - IAM for $CALLER_SA             run jobs, submit builds, read job logs, use the bucket
# Production (security-tenancy.md §4) puts the sandbox in a separate shipcube-apps
# project; pass PROJECT accordingly once it exists.
set -euo pipefail

: "${PROJECT:?set PROJECT}"
: "${REGION:=asia-south1}"
: "${BUCKET:=${PROJECT}-shipcube-ml}"
: "${REPO:=profiler}"
: "${NETWORK:=profiler-no-egress}"
: "${SUBNET:=profiler}"
: "${SUBNET_RANGE:=10.80.0.0/24}"
: "${SANDBOX_SA:=profiler-sandbox}"
: "${CALLER_SA:?set CALLER_SA (email of the service account that calls estimate())}"
SANDBOX_EMAIL="${SANDBOX_SA}@${PROJECT}.iam.gserviceaccount.com"
g() { gcloud --project "$PROJECT" --quiet "$@"; }
exists() { "$@" >/dev/null 2>&1; }

g services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  logging.googleapis.com storage.googleapis.com compute.googleapis.com iam.googleapis.com

# Bucket: uniform access, no public access, lifecycle on the source tarballs.
exists g storage buckets describe "gs://$BUCKET" || \
  g storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access \
    --public-access-prevention
cat > /tmp/shipcube-lifecycle.json <<'JSON'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 1, "matchesPrefix": ["profiler-src/"]}}]}
JSON
g storage buckets update "gs://$BUCKET" --lifecycle-file /tmp/shipcube-lifecycle.json

# Artifact Registry with a 7-day cleanup policy.
exists g artifacts repositories describe "$REPO" --location "$REGION" || \
  g artifacts repositories create "$REPO" --location "$REGION" --repository-format docker \
    --description "ShipCube profiler images (short-lived)"
cat > /tmp/shipcube-ar-cleanup.json <<'JSON'
[{"name": "delete-after-7d", "action": {"type": "Delete"}, "condition": {"olderThan": "604800s"}}]
JSON
g artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
  --policy /tmp/shipcube-ar-cleanup.json --no-dry-run

# Network: custom VPC, one subnet without Private Google Access, deny all egress.
exists g compute networks describe "$NETWORK" || \
  g compute networks create "$NETWORK" --subnet-mode custom
exists g compute networks subnets describe "$SUBNET" --region "$REGION" || \
  g compute networks subnets create "$SUBNET" --network "$NETWORK" --region "$REGION" \
    --range "$SUBNET_RANGE" --no-enable-private-ip-google-access
exists g compute firewall-rules describe "${NETWORK}-deny-egress" || \
  g compute firewall-rules create "${NETWORK}-deny-egress" --network "$NETWORK" \
    --direction EGRESS --action DENY --rules all --destination-ranges 0.0.0.0/0 --priority 100

# Sandbox identity: exists, has no roles.
exists g iam service-accounts describe "$SANDBOX_EMAIL" || \
  g iam service-accounts create "$SANDBOX_SA" --display-name "ShipCube profiler sandbox (no roles)"

# Caller permissions (least privilege for CloudRunJobRunner + the model registry).
for role in roles/run.developer roles/cloudbuild.builds.editor roles/logging.viewer; do
  g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$CALLER_SA" --role "$role" \
    --condition None >/dev/null
done
g iam service-accounts add-iam-policy-binding "$SANDBOX_EMAIL" \
  --member "serviceAccount:$CALLER_SA" --role roles/iam.serviceAccountUser >/dev/null
g storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$CALLER_SA" \
  --role roles/storage.objectAdmin >/dev/null

# Cloud Build's identity pushes images and reads the source tarball.
NUM=$(g projects describe "$PROJECT" --format 'value(projectNumber)')
BUILD_SA="${NUM}-compute@developer.gserviceaccount.com"
g artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" \
  --member "serviceAccount:$BUILD_SA" --role roles/artifactregistry.writer >/dev/null
g storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$BUILD_SA" \
  --role roles/storage.objectViewer >/dev/null
g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$BUILD_SA" \
  --role roles/logging.logWriter --condition None >/dev/null
# The caller submits builds that run as BUILD_SA.
g iam service-accounts add-iam-policy-binding "$BUILD_SA" \
  --member "serviceAccount:$CALLER_SA" --role roles/iam.serviceAccountUser >/dev/null

cat <<ENV
# Environment for the process that calls estimate():
SHIPCUBE_PROFILER_PROJECT=$PROJECT
SHIPCUBE_PROFILER_REGION=$REGION
SHIPCUBE_PROFILER_BUCKET=$BUCKET
SHIPCUBE_PROFILER_REPOSITORY=$REPO
SHIPCUBE_PROFILER_NETWORK=$NETWORK
SHIPCUBE_PROFILER_SUBNET=$SUBNET
SHIPCUBE_PROFILER_SERVICE_ACCOUNT=$SANDBOX_EMAIL
SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT=$BUILD_SA
SHIPCUBE_MODEL_URI=gs://$BUCKET/models/workload-classifier/latest
ENV
