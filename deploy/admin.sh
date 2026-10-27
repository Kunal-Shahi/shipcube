#!/usr/bin/env bash
# Run the API's operator CLI (python -m shipcube_api.admin) as a one-off Cloud Run job, signed in
# as sc-worker (DB role shipcube_worker), with the same image and DB settings as shipcube-worker.
# The job costs only while it runs.
#
#   deploy/admin.sh bootstrap --email you@gmail.com --workspace demo --name "Demo" --no-token
#   deploy/admin.sh ops --stuck
#   deploy/admin.sh requeue <operation id>
#
# Output goes to Cloud Logging, so commands that print a secret are refused: mint PATs in the
# console, and always pass --no-token to bootstrap.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/env.sh
[ $# -gt 0 ] || { sed -n 2,12p "$0"; exit 2; }
case "$1" in
  mint-pat) echo "refused: mint PATs in the console (job output is logged)" >&2; exit 2 ;;
  bootstrap) [[ " $* " == *" --no-token "* ]] || { echo "refused: bootstrap needs --no-token (job output is logged)" >&2; exit 2; } ;;
esac

IMAGE="$(gcloud run services describe shipcube-worker --project "$PROJECT" --region "$REGION" \
  --format 'value(spec.template.spec.containers[0].image)')"
ARGS="-m,shipcube_api.admin$(printf ',%s' "$@")"

gcloud run jobs deploy shipcube-admin --project "$PROJECT" --region "$REGION" --quiet \
  --image "$IMAGE" --service-account "$SA_WORKER" --set-cloudsql-instances "$SQL_INSTANCE" \
  --command python --args "$ARGS" --tasks 1 --max-retries 0 --task-timeout 300s --cpu 1 --memory 1Gi \
  --set-env-vars "^|^SHIPCUBE_DATABASE_URL=postgresql://sc-worker%40$PROJECT.iam@/shipcube?host=/cloudsql/$SQL_INSTANCE|SHIPCUBE_DB_IAM_AUTH=1|SHIPCUBE_DB_SET_ROLE=0" \
  --labels managed-by=shipcube-platform >/dev/null

EXEC="$(gcloud run jobs execute shipcube-admin --project "$PROJECT" --region "$REGION" --wait --format 'value(metadata.name)')"
gcloud logging read --project "$PROJECT" --freshness 10m --order asc --format 'value(textPayload,jsonPayload)' \
  "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"shipcube-admin\" AND labels.\"run.googleapis.com/execution_name\"=\"$EXEC\""
