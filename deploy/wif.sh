#!/usr/bin/env bash
# One-time: let the GitHub deploy workflow (.github/workflows/deploy.yml) act as a deployer service
# account through Workload Identity Federation. No service-account keys.
# Trust is limited to this repository's main branch.
set -euo pipefail
source "$(dirname "$0")/env.sh"
GITHUB_REPO="${GITHUB_REPO:-Kunal-Shahi/shipcube}"
POOL=github
DEPLOYER="sc-github-deployer@$PROJECT.iam.gserviceaccount.com"

gcloud iam workload-identity-pools describe "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$POOL" --location global --project "$PROJECT" --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers describe github --workload-identity-pool "$POOL" --location global \
  --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc github --workload-identity-pool "$POOL" \
    --location global --project "$PROJECT" --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '$GITHUB_REPO' && assertion.ref == 'refs/heads/main'"

gcloud iam service-accounts describe "$DEPLOYER" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam service-accounts create sc-github-deployer --project "$PROJECT" --display-name "GitHub deploy workflow"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --project "$PROJECT" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$GITHUB_REPO" >/dev/null
for role in roles/run.admin roles/cloudbuild.builds.editor roles/serviceusage.serviceUsageConsumer roles/storage.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$DEPLOYER" --role "$role" --condition None >/dev/null
done
for sa in "$SA_API" "$SA_WORKER" "$SA_GATEWAY" "$SA_WEB" "$SA_BUILD"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$PROJECT" \
    --member "serviceAccount:$DEPLOYER" --role roles/iam.serviceAccountUser >/dev/null
done
echo "Set these GitHub repository variables (not secrets; they aren't sensitive):"
echo "  GCP_WIF_PROVIDER=projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/providers/github"
echo "  GCP_DEPLOYER_SA=$DEPLOYER"
