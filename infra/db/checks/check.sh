#!/usr/bin/env bash
# Runs rls_and_token.sql on Cloud SQL as a given service login (through run.sh), inside a
# transaction that is always rolled back. Optional PRE_SQL files run first in the same
# transaction (for dry-running migrations before they are applied).
#   LOGIN=sc-api ./check.sh                   # after migrations, as the API's role
#   LOGIN=sc-migrator PRE_SQL="a.sql b.sql" ./check.sh   # dry run of pending migrations
set -euo pipefail
cd "$(dirname "$0")"
PROJECT="${PROJECT:-shipcube-control-ks}"
LOGIN="${LOGIN:?sc-api or sc-migrator}"
PORT="${PORT:-5437}"
PSQL="${PSQL:-$(command -v psql || echo /opt/homebrew/opt/libpq/bin/psql)}"

cloud-sql-proxy "${PROJECT}:asia-south1:shipcube-pg" --port "$PORT" --auto-iam-authn \
  --impersonate-service-account "${LOGIN}@${PROJECT}.iam.gserviceaccount.com" --quiet &
PROXY=$!
trap 'kill $PROXY 2>/dev/null' EXIT
for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PORT" 2>/dev/null && break; sleep 1; done

"$PWD/run.sh" "host=127.0.0.1 port=$PORT dbname=shipcube user=${LOGIN}@${PROJECT}.iam sslmode=disable"
