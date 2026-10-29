#!/usr/bin/env bash
# Live isolation test for per-workspace builders (red-team C-1). Creates two throwaway
# workspaces A and B, then checks, using real tokens for A's build SA, that:
#   1. A can read its own upload, but not B's, and can't list the bucket.
#   2. A can push to and pull from its own Artifact Registry repo, but not B's or the shared repo.
# Everything it creates is deleted on exit: two SAs, two empty repos, four managed folders and
# three small objects. No images are pushed; repo access is checked with testIamPermissions and
# with a registry request that answers 403 (no access) or 404 (access, no such image).
#
# Run as a project Owner of the apps project (it gives the caller tokenCreator on the two test
# SAs only):
#   infra/tests/build-isolation.sh
# Set AS_PROVISIONER=1 to provision as sc-provisioner too (from an identity allowed to impersonate it).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROVISION="${HERE}/../scripts/provision-workspace-builder.sh"
APPS_PROJECT="${APPS_PROJECT:-shipcube-apps-ks}"
REGION="${REGION:-asia-south1}"
UPLOADS_BUCKET="${UPLOADS_BUCKET:-shipcube-uploads-ks}"
export APPS_PROJECT REGION UPLOADS_BUCKET

ME="$(gcloud config get-value account 2>/dev/null)"
case "$ME" in
  *gserviceaccount.com) export EXTRA_IMPERSONATOR="serviceAccount:${ME}" ;;
  *) export EXTRA_IMPERSONATOR="user:${ME}" ;;
esac

uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }
short() { echo "$1" | tr -d '-' | cut -c1-20; }
A="$(uuid)"
B="$(uuid)"
SA_A="sc-build-$(short "$A")@${APPS_PROJECT}.iam.gserviceaccount.com"
REPO_A="ws-$(short "$A")"
REPO_B="ws-$(short "$B")"
OBJ_A="ws/${A}/uploads/upl_isolation.tgz"
OBJ_B="ws/${B}/uploads/upl_isolation.tgz"

FAILED=0
# expect <description> <actual> <wanted>
expect() {
  if [[ "$2" == "$3" ]]; then
    echo "PASS $1"
  else
    echo "FAIL $1: got '$2', want '$3'"
    FAILED=1
  fi
}

# shellcheck disable=SC2317,SC2329 # invoked by the EXIT trap
cleanup() {
  set +e
  gcloud storage rm --quiet "gs://${UPLOADS_BUCKET}/${OBJ_A}" "gs://${UPLOADS_BUCKET}/${OBJ_B}" \
    "gs://${UPLOADS_BUCKET}/ws/${A}/build-logs/log-isolation.txt" >/dev/null 2>&1
  "$PROVISION" "$A" --delete
  "$PROVISION" "$B" --delete
}
trap cleanup EXIT

"$PROVISION" "$A"
"$PROVISION" "$B"

TMP="$(mktemp)"
head -c 16 /dev/urandom >"$TMP"
gcloud storage cp --quiet "$TMP" "gs://${UPLOADS_BUCKET}/${OBJ_A}" >/dev/null
gcloud storage cp --quiet "$TMP" "gs://${UPLOADS_BUCKET}/${OBJ_B}" >/dev/null
rm -f "$TMP"

token() { gcloud auth print-access-token --impersonate-service-account "$SA_A" 2>/dev/null; }
# HTTP status of a request made as A's build SA.
status() { curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${T}" "$@"; }
enc() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
GCS="https://storage.googleapis.com/storage/v1/b/${UPLOADS_BUCKET}"

# New SAs and bindings take up to a few minutes to apply: wait until A can read its own upload.
echo "Waiting for IAM propagation..."
T=""
for _ in $(seq 1 36); do
  T="$(token || true)"
  if [[ -n "$T" && "$(status "${GCS}/o/$(enc "$OBJ_A")?alt=media")" == 200 ]]; then break; fi
  sleep 10
done
[[ -n "$T" ]] || {
  echo "could not impersonate ${SA_A}"
  exit 1
}

# 1. Storage.
expect "A reads its own upload" "$(status "${GCS}/o/$(enc "$OBJ_A")?alt=media")" 200
expect "A cannot read B's upload" "$(status "${GCS}/o/$(enc "$OBJ_B")?alt=media")" 403
expect "A cannot list the bucket" "$(status "${GCS}/o?prefix=ws/")" 403
expect "A cannot list B's folder" "$(status "${GCS}/o?prefix=$(enc "ws/${B}/")")" 403
# Build logs (GCS_ONLY, #28): A writes only its own build-logs folder, never an upload, never B's logs.
put() {
  curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${T}" -H "Content-Type: text/plain" \
    -X POST "https://storage.googleapis.com/upload/storage/v1/b/${UPLOADS_BUCKET}/o?uploadType=media&name=$(enc "$1")" -d x
}
expect "A writes its own build log" "$(put "ws/${A}/build-logs/log-isolation.txt")" 200
expect "A cannot write B's build logs" "$(put "ws/${B}/build-logs/log-isolation.txt")" 403
expect "A cannot write its own uploads" "$(put "ws/${A}/uploads/upl_forged.tgz")" 403

# 2. Artifact Registry: permissions A holds on each repo.
perms() {
  curl -s -H "Authorization: Bearer ${T}" -H "Content-Type: application/json" -X POST \
    "https://artifactregistry.googleapis.com/v1/projects/${APPS_PROJECT}/locations/${REGION}/repositories/$1:testIamPermissions" \
    -d '{"permissions":["artifactregistry.repositories.downloadArtifacts","artifactregistry.repositories.uploadArtifacts"]}' |
    python3 -c 'import json, sys; print(" ".join(sorted(json.load(sys.stdin).get("permissions", []))))'
}
want="artifactregistry.repositories.downloadArtifacts artifactregistry.repositories.uploadArtifacts"
expect "A can push and pull its own repo" "$(perms "$REPO_A")" "$want"
for repo in "$REPO_B" shipcube profiler; do
  expect "A has no access to repo ${repo}" "$(perms "$repo")" ""
done

# A pull request against the registry itself: 403 = denied, 404 = allowed but no such image.
HOST="${REGION}-docker.pkg.dev"
pull() {
  local rt
  rt="$(curl -s -u "oauth2accesstoken:${T}" \
    "https://${HOST}/v2/token?service=${HOST}&scope=repository:${APPS_PROJECT}/$1/app:pull" |
    python3 -c 'import json, sys; print(json.load(sys.stdin).get("token", ""))' 2>/dev/null || true)"
  curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${rt}" \
    -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    "https://${HOST}/v2/${APPS_PROJECT}/$1/app/manifests/latest"
}
expect "A's pull from its own repo is authorized (404 = no such image)" "$(pull "$REPO_A")" 404
expect "A cannot pull from B's repo" "$(pull "$REPO_B")" 403

if [[ "$FAILED" == 0 ]]; then echo "ALL ISOLATION CHECKS PASSED"; else echo "ISOLATION CHECKS FAILED"; fi
exit "$FAILED"
