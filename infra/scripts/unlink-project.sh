#!/usr/bin/env bash
# Removes every grant link-project.sh made. Apps already deployed keep running
# until you delete them (the console can do that before you run this).
#
#   ./unlink-project.sh <your-project-id> <ws-xxxx-deployer@shipcube-links.iam.gserviceaccount.com> [region]
set -uo pipefail

PROJECT="${1:?project id}"
DEPLOYER="${2:?ShipCube deployer service account email}"
REGION="${3:-asia-south1}"
MEMBER="serviceAccount:${DEPLOYER}"

for role in \
  roles/run.developer \
  "projects/${PROJECT}/roles/shipcubeRunIamAdmin" \
  roles/iap.admin \
  roles/cloudbuild.builds.editor \
  roles/cloudscheduler.admin \
  roles/logging.viewer \
  roles/monitoring.viewer \
  roles/serviceusage.serviceUsageConsumer; do
  gcloud projects remove-iam-policy-binding "$PROJECT" --member "$MEMBER" --role "$role" --condition=None >/dev/null
done

gcloud artifacts repositories remove-iam-policy-binding shipcube --project "$PROJECT" --location "$REGION" \
  --member "$MEMBER" --role roles/artifactregistry.writer >/dev/null
for sa in shipcube-apps shipcube-builder; do
  gcloud iam service-accounts remove-iam-policy-binding "${sa}@${PROJECT}.iam.gserviceaccount.com" --project "$PROJECT" \
    --member "$MEMBER" --role roles/iam.serviceAccountUser >/dev/null
done

echo "ShipCube can no longer deploy to ${PROJECT}."
echo "Optional cleanup: delete the shipcube repo, the shipcube-apps and shipcube-builder service accounts, and the shipcubeRunIamAdmin role."
