#!/usr/bin/env bash
# Local tenant-isolation tests: the same checks as on Cloud SQL, against the local stack's
# database (or CI's), signed in as the real service logins. Needs psql and uv.
#
#   infra/local/test.sh                 # database at 127.0.0.1:54329 (compose)
#   LOCAL_DB=localhost:5432 infra/local/test.sh
#
# 1. infra/db/checks/rls_and_token.sql as sc-api: forced RLS on every tenant table, cross-workspace
#    reads and writes blocked, PAT lookup under app.token_hmac. The SQL is shared with check.sh.
# 2. tests/test_local_db.py: PAT login through auth.verify_pat as sc-api, RLS as a real caller.
# 3. tests/test_local_build.py: workspace A can't use B's upload or image through the deployer.
# The GCP-only half (real IAM on build SAs) is infra/tests/build-isolation.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOCAL_DIR="${SHIPCUBE_LOCAL_DIR:-$ROOT/.local}"
export LOCAL_DB="${LOCAL_DB:-127.0.0.1:54329}"
[[ -f "$LOCAL_DIR/secrets.env" ]] || {
  echo "no $LOCAL_DIR/secrets.env: run python3 infra/local/localsecrets.py init" >&2
  exit 1
}
SC_API_DB_PASSWORD="$(sed -n 's/^SC_API_DB_PASSWORD=//p' "$LOCAL_DIR/secrets.env")"

echo "== RLS and PAT lookup (SQL, as sc-api)"
PGPASSWORD="$SC_API_DB_PASSWORD" "$ROOT/infra/db/checks/run.sh" \
  "host=${LOCAL_DB%:*} port=${LOCAL_DB#*:} dbname=shipcube user=sc-api@shipcube-control-ks.iam" 2>&1 |
  tee /dev/stderr | grep -q "ALL CHECKS PASSED"

echo "== PAT login, RLS and build isolation (Python, as the service logins)"
cd "$ROOT/services/api"
uv run --quiet pytest -q -p no:cacheprovider --rootdir "$HERE/tests" "$HERE/tests"
