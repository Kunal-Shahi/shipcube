#!/usr/bin/env bash
# One-time setup for hosting ShipCube itself on Cloud Run. Idempotent: re-running skips what exists.
# Names match infra/terraform (security workstream) so `terraform import` can adopt them later.
#
# Creates (all in $PROJECT, $REGION):
#   - Artifact Registry repo "platform" (Docker), keeping the 5 most recent images per package
#   - Service accounts sc-api, sc-gateway, sc-web (runtime) and sc-platform-build (Cloud Build)
#   - Secret containers shipcube-dev-token and gateway-registry (values added below / by hand)
#   - IAM: secret accessors, monitoring/logging viewer for sc-api (inspect_app), build SA roles
# Cloud SQL is NOT created here; see deploy/cloudsql.sh and deploy/README.md.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/env.sh

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com monitoring.googleapis.com logging.googleapis.com

if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" --location "$REGION" --repository-format docker \
    --description "Images for ShipCube's own services"
fi
cat > /tmp/sc-cleanup.json <<'JSON'
[{"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
 {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "604800s"}}]
JSON
gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" --policy /tmp/sc-cleanup.json --no-dry-run >/dev/null

sa() {  # sa <id> <display name>
  gcloud iam service-accounts describe "$1@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$1" --display-name "$2"
}
sa sc-api "ShipCube control-plane API and MCP server"
sa sc-gateway "ShipCube AI gateway and access proxy"
sa sc-web "ShipCube web console"
sa sc-platform-build "Cloud Build for ShipCube's own images"

secret() {  # secret <id>
  gcloud secrets describe "$1" >/dev/null 2>&1 || gcloud secrets create "$1" --replication-policy automatic
}
secret shipcube-dev-token   # the demo PAT until the API's token store is live (SHIPCUBE_DEV_TOKEN)
secret gateway-registry     # the gateway's app registry JSON until GET /internal/gateway/resolve exists

bind_secret() {  # bind_secret <secret> <sa email>
  gcloud secrets add-iam-policy-binding "$1" --member "serviceAccount:$2" \
    --role roles/secretmanager.secretAccessor >/dev/null
}
bind_secret meta-api-key "$SA_GATEWAY"
bind_secret gateway-registry "$SA_GATEWAY"
bind_secret shipcube-dev-token "$SA_API"

project_role() {  # project_role <sa email> <role>
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$1" --role "$2" \
    --condition None >/dev/null
}
# inspect_app reads metrics and logs (packages/monitor).
project_role "$SA_API" roles/monitoring.viewer
project_role "$SA_API" roles/logging.viewer
# Cloud Build: push images, write build logs.
project_role "$SA_BUILD" roles/artifactregistry.writer
project_role "$SA_BUILD" roles/logging.logWriter
project_role "$SA_BUILD" roles/storage.objectViewer

# Seed the demo PAT if empty. The value never leaves Secret Manager and this shell.
if ! gcloud secrets versions list shipcube-dev-token --filter state=enabled --format 'value(name)' | grep -q .; then
  printf 'sc_pat_%s_%s' "$(openssl rand -hex 4)" "$(openssl rand -base64 36 | tr -dc 'A-Za-z0-9' | head -c 40)" |
    gcloud secrets versions add shipcube-dev-token --data-file=- >/dev/null
  echo "Seeded shipcube-dev-token. Read it with: gcloud secrets versions access latest --secret shipcube-dev-token"
fi
if ! gcloud secrets versions list gateway-registry --filter state=enabled --format 'value(name)' | grep -q .; then
  echo '{"apps": []}' | gcloud secrets versions add gateway-registry --data-file=- >/dev/null
fi
echo "Bootstrap done."
