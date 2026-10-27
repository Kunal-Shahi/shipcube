#!/usr/bin/env bash
# Build the three images on Cloud Build, then deploy them to Cloud Run (scale to zero).
#   deploy/deploy.sh            build + deploy all
#   deploy/deploy.sh --no-build deploy the current $TAG again (e.g. after changing env)
# Needs: deploy/bootstrap.sh run once; the code for apps/web and services/{mcp,api} in this checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/env.sh

for p in apps/web/package.json services/mcp/pyproject.toml services/api/pyproject.toml services/gateway/go.mod; do
  [ -f "$p" ] || { echo "missing $p: merge the console (PR #3) and MCP/API (PR #1) branches first" >&2; exit 1; }
done

if [ "${1:-}" != "--no-build" ]; then
  gcloud builds submit . --project "$PROJECT" --region "$REGION" --config deploy/cloudbuild.yaml \
    --ignore-file deploy/.gcloudignore --service-account "projects/$PROJECT/serviceAccounts/$SA_BUILD" \
    --default-buckets-behavior regional-user-owned-bucket \
    --substitutions "_IMAGES=$IMAGES,_TAG=$TAG"
fi

MCP_URL="$(run_url shipcube-mcp)"
GATEWAY_URL="$(run_url shipcube-gateway)"
WEB_URL="$(run_url shipcube-console)"

# Common flags: request-based billing (CPU only while serving), scale to zero, small instances.
common=(--project "$PROJECT" --region "$REGION" --min-instances 0 --cpu-throttling --cpu-boost --quiet)

# MCP server + API. Public at the Cloud Run level: MCP clients send PATs, which the API checks.
gcloud run deploy shipcube-mcp "${common[@]}" --image "$IMAGES/mcp:$TAG" \
  --service-account "$SA_API" --allow-unauthenticated --max-instances 2 --cpu 1 --memory 512Mi \
  --concurrency 40 --timeout 300 \
  --set-env-vars "ALLOWED_HOSTS=${MCP_URL#https://},SHIPCUBE_CONSOLE_URL=$WEB_URL,SHIPCUBE_MONITOR=gcp" \
  --set-secrets "SHIPCUBE_DEV_TOKEN=shipcube-dev-token:latest"

# AI gateway. Public at the Cloud Run level: it verifies the app's Google ID token itself
# (audience = its own URL), because user apps' per-app SAs can't all be granted run.invoker here.
gcloud run deploy shipcube-gateway "${common[@]}" --image "$IMAGES/gateway:$TAG" \
  --service-account "$SA_GATEWAY" --allow-unauthenticated --max-instances 2 --cpu 1 --memory 256Mi \
  --concurrency 80 --timeout 3600 \
  --set-env-vars "GATEWAY_AUDIENCE=$GATEWAY_URL,GATEWAY_REGISTRY_FILE=/etc/shipcube/registry/registry.json,META_SECRET_REF=projects/$PROJECT/secrets/meta-api-key/versions/1" \
  --set-secrets "/etc/shipcube/registry/registry.json=gateway-registry:latest"

# Console. Public: sign-in happens in the app (Identity Platform).
gcloud run deploy shipcube-console "${common[@]}" --image "$IMAGES/console:$TAG" \
  --service-account "$SA_WEB" --allow-unauthenticated --max-instances 2 --cpu 1 --memory 512Mi \
  --concurrency 80 --set-env-vars "NEXT_PUBLIC_MCP_URL=$MCP_URL/mcp,SHIPCUBE_API_URL=$MCP_URL"

cat <<URLS

Console:  $WEB_URL
MCP:      $MCP_URL/mcp
Gateway:  $GATEWAY_URL/v1
URLS
