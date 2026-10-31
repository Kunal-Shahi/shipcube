# shellcheck shell=bash disable=SC2034  # sourced by the other scripts, which use these variables
# Shared settings for deploy/*.sh. Override any of them in the environment.
: "${PROJECT:=shipcube-control-ks}"
: "${APPS_PROJECT:=shipcube-apps-ks}"
: "${REGION:=asia-south1}"
: "${REPO:=platform}"                     # Artifact Registry repo (infra/terraform/registry.tf)
: "${TAG:=$(git rev-parse --short=12 HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
: "${META_KEY_VERSION:=1}"               # pinned Secret Manager versions
: "${PEPPER_VERSION:=1}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
IMAGES="$REGION-docker.pkg.dev/$PROJECT/$REPO"
SQL_INSTANCE="$PROJECT:$REGION:shipcube-pg"
# Extra Cloud SQL instances the data layer may connect to as warehouses (comma-separated
# connection names), e.g. the demo warehouse. Each is mounted on shipcube-mcp and allow-listed
# as /cloudsql/<name>; empty means only public warehouse hosts.
: "${DATA_SQL_INSTANCES:=}"
# Workspaces in total; waitlist approvals stop here (SHIPCUBE_MAX_WORKSPACES).
: "${MAX_WORKSPACES:=50}"
BUILD_BUCKET="$PROJECT-platform-builds"
# Cloud Run URLs are deterministic: https://<service>-<project number>.<region>.run.app
run_url() { echo "https://$1-$PROJECT_NUMBER.$REGION.run.app"; }
CONSOLE_RUN_URL="$(run_url shipcube-console)"
# The console's public address (Firebase Hosting in front of the Cloud Run service): used in approval
# links, the setup guide and sign-in. Override with CONSOLE_URL=... (e.g. the run.app URL above).
: "${CONSOLE_URL:=https://shipcube.web.app}"
# Where the landing page is served (CORS for its waitlist form): the public address and the run.app URL.
: "${CONSOLE_ORIGINS:=$CONSOLE_URL,$CONSOLE_RUN_URL}"
# Where Firebase finishes Google sign-in: the console's Firebase Hosting domain, which serves /__/auth/handler
# (and is in the OAuth client's redirect URIs). On the default firebaseapp.com domain the redirect fallback
# never completes when the popup is blocked: the browser keeps the two sites' storage apart.
: "${FIREBASE_AUTH_DOMAIN:=shipcube.web.app}"
MCP_URL="$(run_url shipcube-mcp)"
GATEWAY_URL="$(run_url shipcube-gateway)"
WORKER_URL="$(run_url shipcube-worker)"
# Service accounts from infra/terraform (iam.tf, platform.tf).
SA_API="sc-api@$PROJECT.iam.gserviceaccount.com"
SA_WORKER="sc-worker@$PROJECT.iam.gserviceaccount.com"
SA_GATEWAY="sc-gateway@$PROJECT.iam.gserviceaccount.com"
SA_WEB="sc-web@$PROJECT.iam.gserviceaccount.com"
SA_BUILD="sc-platform-build@$PROJECT.iam.gserviceaccount.com"
SA_HOSTED_DEPLOYER="sc-hosted-deployer@$PROJECT.iam.gserviceaccount.com"
