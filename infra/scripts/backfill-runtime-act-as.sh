#!/usr/bin/env bash
# One-off for #16, before the hosted deployer's project-wide service-account roles are removed:
# give it actAs on each existing app runtime SA (app-*) in the apps project, which new deploys
# get from the provisioner (deploy/gcp.py _ensure_runtime_sa). Without this, updating an app
# deployed earlier (caps, access, redeploy) fails until it is redeployed. Idempotent.
# Run as a project Owner of the apps project.
#   infra/scripts/backfill-runtime-act-as.sh [--dry-run]
set -euo pipefail
APPS_PROJECT="${APPS_PROJECT:-shipcube-apps-ks}"
CONTROL_PROJECT="${CONTROL_PROJECT:-shipcube-control-ks}"
DEPLOYER="serviceAccount:sc-hosted-deployer@${CONTROL_PROJECT}.iam.gserviceaccount.com"
DRY="${1:-}"

sas="$(gcloud iam service-accounts list --project "$APPS_PROJECT" --format='value(email)' | grep -E '^app-[0-9a-f]{8}@' || true)"
[[ -n "$sas" ]] || {
  echo "no app runtime SAs in $APPS_PROJECT"
  exit 0
}
for sa in $sas; do
  if [[ "$DRY" == --dry-run ]]; then
    echo "would grant roles/iam.serviceAccountUser on $sa to $DEPLOYER"
    continue
  fi
  gcloud iam service-accounts add-iam-policy-binding "$sa" --project "$APPS_PROJECT" \
    --member "$DEPLOYER" --role roles/iam.serviceAccountUser --condition=None >/dev/null
  echo "granted roles/iam.serviceAccountUser on $sa to $DEPLOYER"
done
