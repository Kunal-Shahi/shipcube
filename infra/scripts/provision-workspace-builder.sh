#!/usr/bin/env bash
# Creates (or deletes) one workspace's isolated build identity in the apps project (red-team C-1).
# This is the reference for the worker's provisioning step: the worker makes the same calls in
# Python, impersonating sc-provisioner. Every step is idempotent, so re-running is safe.
#
#   SA      sc-build-<ws_short>@<apps>.iam.gserviceaccount.com
#   uploads gs://<uploads>/ws/<ws_uuid>/                 managed folder, SA has objectViewer
#   images  <region>-docker.pkg.dev/<apps>/ws-<ws_short>/  repo, SA has artifactregistry.writer
#
# ws_uuid is the canonical workspace uuid; ws_short is its first 20 hex digits (no dashes).
#
#   ./provision-workspace-builder.sh <workspace-uuid>            create
#   ./provision-workspace-builder.sh <workspace-uuid> --delete   remove
#
# Env: APPS_PROJECT, CONTROL_PROJECT, REGION, UPLOADS_BUCKET (defaults below).
#   AS_PROVISIONER=1     run every call as sc-provisioner (the caller must be allowed to impersonate it)
#   EXTRA_IMPERSONATOR   also give this member tokenCreator on the build SA (isolation test only)
set -euo pipefail

WS_UUID="$(echo "${1:?workspace uuid}" | tr 'A-F' 'a-f')"
ACTION="${2:-create}"
[[ "$WS_UUID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || {
  echo "not a canonical uuid: $WS_UUID" >&2
  exit 2
}
APPS_PROJECT="${APPS_PROJECT:-shipcube-apps-ks}"
CONTROL_PROJECT="${CONTROL_PROJECT:-shipcube-control-ks}"
REGION="${REGION:-asia-south1}"
UPLOADS_BUCKET="${UPLOADS_BUCKET:-shipcube-uploads-ks}"

WS_SHORT="$(echo "$WS_UUID" | tr -d '-' | cut -c1-20)"
SA_ID="sc-build-${WS_SHORT}"
SA="${SA_ID}@${APPS_PROJECT}.iam.gserviceaccount.com"
REPO="ws-${WS_SHORT}"
FOLDER="gs://${UPLOADS_BUCKET}/ws/${WS_UUID}/"
LOGS="${FOLDER}build-logs/"   # hosted build logs (GCS_ONLY); the builder never gets logging.logWriter
WORKER="serviceAccount:sc-worker@${CONTROL_PROJECT}.iam.gserviceaccount.com"
DEPLOYER="serviceAccount:sc-hosted-deployer@${CONTROL_PROJECT}.iam.gserviceaccount.com"

G=(gcloud --quiet)
if [[ "${AS_PROVISIONER:-}" == 1 ]]; then
  G+=(--impersonate-service-account "sc-provisioner@${CONTROL_PROJECT}.iam.gserviceaccount.com")
fi

# A new SA takes a few seconds to become visible to IAM policy calls.
retry() {
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    "$@" >/dev/null 2>&1 && return 0
    sleep $((i * 2))
  done
  "$@" >/dev/null
}

if [[ "$ACTION" == --delete ]]; then
  echo "Deleting build identity for workspace ${WS_UUID}"
  "${G[@]}" artifacts repositories delete "$REPO" --project "$APPS_PROJECT" --location "$REGION" 2>/dev/null ||
    echo "  repo ${REPO}: absent"
  # Objects under the folder expire with the bucket's 1-day lifecycle rule.
  "${G[@]}" storage managed-folders delete "$LOGS" 2>/dev/null || echo "  folder ${LOGS}: absent or not yet empty"
  "${G[@]}" storage managed-folders delete "$FOLDER" 2>/dev/null ||
    echo "  folder ${FOLDER}: absent or not yet empty"
  "${G[@]}" iam service-accounts delete "$SA" --project "$APPS_PROJECT" 2>/dev/null ||
    echo "  SA ${SA}: absent"
  exit 0
fi

echo "Provisioning build identity for workspace ${WS_UUID}"

"${G[@]}" iam service-accounts describe "$SA" --project "$APPS_PROJECT" >/dev/null 2>&1 ||
  "${G[@]}" iam service-accounts create "$SA_ID" --project "$APPS_PROJECT" \
    --display-name "Builds for workspace ${WS_UUID}"

# Same cleanup as the shared repo: keep the 5 newest versions, delete the rest after 30 days
# (images must outlive the 7-day soft-delete window).
POLICY="$(mktemp)"
trap 'rm -f "$POLICY"' EXIT
cat >"$POLICY" <<'EOF'
[
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "30d"}}
]
EOF
"${G[@]}" artifacts repositories describe "$REPO" --project "$APPS_PROJECT" --location "$REGION" >/dev/null 2>&1 ||
  "${G[@]}" artifacts repositories create "$REPO" --project "$APPS_PROJECT" --location "$REGION" \
    --repository-format docker --description "Images for workspace ${WS_UUID}"
"${G[@]}" artifacts repositories set-cleanup-policies "$REPO" --project "$APPS_PROJECT" \
  --location "$REGION" --policy "$POLICY" >/dev/null

"${G[@]}" storage managed-folders describe "$FOLDER" >/dev/null 2>&1 ||
  "${G[@]}" storage managed-folders create "$FOLDER"
"${G[@]}" storage managed-folders describe "$LOGS" >/dev/null 2>&1 ||
  "${G[@]}" storage managed-folders create "$LOGS"

retry "${G[@]}" artifacts repositories add-iam-policy-binding "$REPO" --project "$APPS_PROJECT" \
  --location "$REGION" --member "serviceAccount:${SA}" --role roles/artifactregistry.writer
retry "${G[@]}" storage managed-folders add-iam-policy-binding "$FOLDER" \
  --member "serviceAccount:${SA}" --role roles/storage.objectViewer
retry "${G[@]}" storage managed-folders add-iam-policy-binding "$LOGS" \
  --member "serviceAccount:${SA}" --role roles/storage.objectUser

# Only the worker may mint tokens for the build SA. The worker and the hosted deployer (whichever
# submits the build) may run builds as it. Never sc-api.
retry "${G[@]}" iam service-accounts add-iam-policy-binding "$SA" --project "$APPS_PROJECT" \
  --member "$WORKER" --role roles/iam.serviceAccountTokenCreator
for m in "$WORKER" "$DEPLOYER"; do
  retry "${G[@]}" iam service-accounts add-iam-policy-binding "$SA" --project "$APPS_PROJECT" \
    --member "$m" --role roles/iam.serviceAccountUser
done
if [[ -n "${EXTRA_IMPERSONATOR:-}" ]]; then
  retry "${G[@]}" iam service-accounts add-iam-policy-binding "$SA" --project "$APPS_PROJECT" \
    --member "$EXTRA_IMPERSONATOR" --role roles/iam.serviceAccountTokenCreator
fi

echo "  SA      ${SA}"
echo "  uploads ${FOLDER}"
echo "  images  ${REGION}-docker.pkg.dev/${APPS_PROJECT}/${REPO}/"
