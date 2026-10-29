#!/usr/bin/env bash
# Runs rls_and_token.sql against an already-reachable database, inside a transaction that is
# always rolled back. Shared by check.sh (Cloud SQL, through the proxy) and infra/local/test.sh.
# Optional PRE_SQL files run first in the same transaction (dry runs of pending migrations).
#   ./run.sh "host=… port=… dbname=shipcube user=sc-api@shipcube-control-ks.iam …"
set -euo pipefail
cd "$(dirname "$0")"
DSN="${1:?libpq connection string}"
PSQL="${PSQL:-$(command -v psql || echo /opt/homebrew/opt/libpq/bin/psql)}"

{
  echo 'begin;'
  for f in ${PRE_SQL:-}; do echo "\\i $f"; done
  echo 'select current_user as running_as;'
  echo '\i rls_and_token.sql'
  echo 'rollback;'
} | "$PSQL" "$DSN" -X -v ON_ERROR_STOP=1 -q
