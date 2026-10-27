#!/usr/bin/env bash
# Applies pending schema migrations (services/api/migrations) as sc-migrator, using the
# API's own runner (`python -m shipcube_api.admin migrate`: checksummed, advisory-locked).
# The proxy signs in with an IAM token for sc-migrator; the caller must be listed in
# `migration_operators` (Terraform) to impersonate it. No password is involved.
#   API_DIR=path/to/services/api ./migrate.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

PROJECT="${PROJECT:-shipcube-control-ks}"
INSTANCE="${INSTANCE:-${PROJECT}:asia-south1:shipcube-pg}"
PORT="${PORT:-5434}"
MIGRATOR="sc-migrator@${PROJECT}.iam.gserviceaccount.com"
: "${API_DIR:?set API_DIR to the services/api directory}"

cloud-sql-proxy "$INSTANCE" --port "$PORT" --auto-iam-authn --impersonate-service-account "$MIGRATOR" --quiet &
PROXY=$!
trap 'kill $PROXY 2>/dev/null' EXIT
for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PORT" 2>/dev/null && break; sleep 1; done

PSQL="${PSQL:-$(command -v psql || echo /opt/homebrew/opt/libpq/bin/psql)}"
DB="host=127.0.0.1 port=${PORT} dbname=shipcube user=sc-migrator@${PROJECT}.iam sslmode=disable"
"$PSQL" "$DB" -q -f "$HERE/owner_defaults.sql"

cd "$API_DIR"
SHIPCUBE_DATABASE_URL="postgresql://sc-migrator%40${PROJECT}.iam@127.0.0.1:${PORT}/shipcube?sslmode=disable" \
  "${PYTHON:-python3}" -m shipcube_api.admin migrate
