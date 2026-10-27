# shellcheck shell=bash disable=SC2034  # sourced by the other scripts, which use these variables
# Shared settings for deploy/*.sh. Override any of them in the environment.
: "${PROJECT:=shipcube-control-ks}"
: "${REGION:=asia-south1}"
: "${REPO:=platform}"                     # Artifact Registry repo (infra/terraform/registry.tf)
: "${TAG:=$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
IMAGES="$REGION-docker.pkg.dev/$PROJECT/$REPO"
# Cloud Run URLs are deterministic: https://<service>-<project number>.<region>.run.app
run_url() { echo "https://$1-$PROJECT_NUMBER.$REGION.run.app"; }
# Service accounts, named as in infra/terraform/iam.tf so Terraform can import them later.
SA_API="sc-api@$PROJECT.iam.gserviceaccount.com"
SA_GATEWAY="sc-gateway@$PROJECT.iam.gserviceaccount.com"
SA_WEB="sc-web@$PROJECT.iam.gserviceaccount.com"
SA_BUILD="sc-platform-build@$PROJECT.iam.gserviceaccount.com"
SA_MIGRATOR="sc-migrator@$PROJECT.iam.gserviceaccount.com"
