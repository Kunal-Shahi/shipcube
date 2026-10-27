#!/usr/bin/env bash
# Deploys (or redeploys) the budget kill switch. Terraform owns the topic, budget,
# service account and its project roles (infra/terraform/budget.tf); this script owns
# the function code and the invoker binding the Pub/Sub trigger needs.
#
#   CONTROL_PROJECT=shipcube-control-ks CAP=24000 \
#   PROJECTS=shipcube-apps-ks,other-project,shipcube-control-ks ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

: "${CONTROL_PROJECT:?}" "${CAP:?}" "${PROJECTS:?}"
REGION="${REGION:-asia-south1}"
SA="budget-killswitch@${CONTROL_PROJECT}.iam.gserviceaccount.com"

case "$PROJECTS" in *"${CONTROL_PROJECT}") ;; *) echo "PROJECTS must end with ${CONTROL_PROJECT}" >&2; exit 1 ;; esac

gcloud functions deploy budget-killswitch --gen2 \
  --project "$CONTROL_PROJECT" --region "$REGION" \
  --runtime python312 --source . --entry-point on_budget \
  --trigger-topic budget-alerts --retry \
  --service-account "$SA" --trigger-service-account "$SA" \
  --set-env-vars "^;^CAP=${CAP};PROJECTS=${PROJECTS}" \
  --max-instances 1 --memory 256Mi --ingress-settings internal-only --quiet

gcloud run services add-iam-policy-binding budget-killswitch \
  --project "$CONTROL_PROJECT" --region "$REGION" \
  --member "serviceAccount:${SA}" --role roles/run.invoker --format=none

echo "Deployed. Verify without changing anything:"
echo "  gcloud pubsub topics publish budget-alerts --project $CONTROL_PROJECT --message '{\"costAmount\": 1}' --attribute dryRun=true"
