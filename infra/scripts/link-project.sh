#!/usr/bin/env bash
# Links a GCP project to a ShipCube workspace ("bring your own credits").
# Run it as a project Owner, in Cloud Shell or anywhere gcloud is signed in.
# The console fills in both arguments. Nothing here sends credentials to ShipCube:
# it grants ShipCube's per-workspace deployer service account a fixed set of roles,
# and you can read every grant below before running it.
#
#   ./link-project.sh <your-project-id> <ws-xxxx-deployer@shipcube-links.iam.gserviceaccount.com> [region]
set -euo pipefail

PROJECT="${1:?project id}"
DEPLOYER="${2:?ShipCube deployer service account email}"
REGION="${3:-asia-south1}"
RUNTIME_SA="shipcube-apps@${PROJECT}.iam.gserviceaccount.com"
BUILDER_SA="shipcube-builder@${PROJECT}.iam.gserviceaccount.com"

echo "Linking ${PROJECT} to ShipCube deployer ${DEPLOYER}"

gcloud services enable --project "$PROJECT" \
  run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  iap.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com \
  logging.googleapis.com monitoring.googleapis.com

# Images for your apps stay in your project.
gcloud artifacts repositories describe shipcube --project "$PROJECT" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create shipcube --project "$PROJECT" --location "$REGION" \
    --repository-format docker --description "ShipCube app images"

# Identities your apps run as and build with. They get no project roles.
for sa in shipcube-apps shipcube-builder; do
  gcloud iam service-accounts describe "${sa}@${PROJECT}.iam.gserviceaccount.com" --project "$PROJECT" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$sa" --project "$PROJECT" --display-name "ShipCube ${sa#shipcube-}"
done

gcloud artifacts repositories add-iam-policy-binding shipcube --project "$PROJECT" --location "$REGION" \
  --member "serviceAccount:${BUILDER_SA}" --role roles/artifactregistry.writer --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:${BUILDER_SA}" --role roles/logging.logWriter --condition=None >/dev/null

# The only permission ShipCube needs that roles/run.developer lacks: letting IAP invoke the service.
gcloud iam roles describe shipcubeRunIamAdmin --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam roles create shipcubeRunIamAdmin --project "$PROJECT" \
    --title "ShipCube Cloud Run IAM admin" \
    --permissions run.services.setIamPolicy,run.jobs.setIamPolicy

# Project-level grants to the ShipCube deployer.
for role in \
  roles/run.developer \
  "projects/${PROJECT}/roles/shipcubeRunIamAdmin" \
  roles/iap.admin \
  roles/cloudbuild.builds.editor \
  roles/cloudscheduler.admin \
  roles/logging.viewer \
  roles/monitoring.viewer \
  roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:${DEPLOYER}" --role "$role" --condition=None >/dev/null
done

# Resource-level grants: push to the shipcube repo only, act as the two ShipCube accounts only.
gcloud artifacts repositories add-iam-policy-binding shipcube --project "$PROJECT" --location "$REGION" \
  --member "serviceAccount:${DEPLOYER}" --role roles/artifactregistry.writer --condition=None >/dev/null
for sa in "$RUNTIME_SA" "$BUILDER_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$PROJECT" \
    --member "serviceAccount:${DEPLOYER}" --role roles/iam.serviceAccountUser >/dev/null
done

echo "Done. Return to the ShipCube console and press Verify."
