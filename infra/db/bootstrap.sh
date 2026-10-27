#!/usr/bin/env bash
# Runs bootstrap.sql against Cloud SQL as `postgres`, through the Cloud SQL Auth Proxy.
# The password is read from Secret Manager into the environment and never printed.
# Needs: gcloud (signed in as a project owner), cloud-sql-proxy, psql.
#   brew install cloud-sql-proxy libpq
set -euo pipefail
cd "$(dirname "$0")"

PROJECT="${PROJECT:-shipcube-control-ks}"
INSTANCE="${INSTANCE:-${PROJECT}:asia-south1:shipcube-pg}"
PORT="${PORT:-5433}"
PSQL="${PSQL:-$(command -v psql || echo /opt/homebrew/opt/libpq/bin/psql)}"

cloud-sql-proxy "$INSTANCE" --port "$PORT" --quiet &
PROXY=$!
trap 'kill $PROXY 2>/dev/null' EXIT
for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PORT" 2>/dev/null && break; sleep 1; done

PGPASSWORD="$(gcloud secrets versions access latest --secret sql-postgres-password --project "$PROJECT")" \
  "$PSQL" "host=127.0.0.1 port=$PORT dbname=shipcube user=postgres sslmode=disable" -f bootstrap.sql
