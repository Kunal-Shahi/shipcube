#!/usr/bin/env bash
# Apply versioned schema migrations (db/migrations/NNNN_name.sql) to Cloud SQL, as a Cloud Run job.
#   deploy/migrate.sh          build the job image, apply pending migrations, wait for the result
#   deploy/migrate.sh status   list applied and pending migrations without changing anything
# The job connects over the Cloud SQL unix socket with IAM database auth as sc-migrator (no password).
# Production schema changes go only through this; never edit the schema by hand.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/env.sh
CMD="${1:-up}"
[ -d db/migrations ] || { echo "no db/migrations directory in this checkout" >&2; exit 1; }

gcloud builds submit . --project "$PROJECT" --region "$REGION" --config deploy/cloudbuild-migrate.yaml \
  --ignore-file deploy/.gcloudignore --service-account "projects/$PROJECT/serviceAccounts/$SA_BUILD" \
  --default-buckets-behavior regional-user-owned-bucket --substitutions "_IMAGES=$IMAGES,_TAG=$TAG"

gcloud run jobs deploy shipcube-migrate --project "$PROJECT" --region "$REGION" \
  --image "$IMAGES/migrate:$TAG" --service-account "$SA_MIGRATOR" \
  --set-cloudsql-instances "$PROJECT:$REGION:shipcube-pg" \
  --set-env-vars "PGHOST=/cloudsql/$PROJECT:$REGION:shipcube-pg,PGDATABASE=shipcube,PGUSER=${SA_MIGRATOR%.gserviceaccount.com},PG_IAM_AUTH=1" \
  --args "$CMD" --tasks 1 --max-retries 0 --task-timeout 900s --cpu 1 --memory 512Mi --quiet

gcloud run jobs execute shipcube-migrate --project "$PROJECT" --region "$REGION" --wait
