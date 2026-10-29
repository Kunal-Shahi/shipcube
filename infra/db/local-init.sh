#!/usr/bin/env bash
# Bootstraps a local or CI Postgres exactly like Cloud SQL, then hands over to the migration runner.
# Idempotent. Used by the compose `db-init` service and by CI; there is no other local path.
#
# 1. Creates database `shipcube` and password logins with the same names as the Cloud SQL IAM
#    database users (sc-migrator@…iam, sc-api@…iam, sc-worker@…iam). On Cloud SQL, Terraform
#    creates those users (database.tf); this is the only step that differs.
# 2. Runs bootstrap.sql unchanged (extensions, roles, grants, default session roles).
# 3. Runs owner_defaults.sql unchanged, as sc-migrator (acting as shipcube_owner).
# Then apply migrations as sc-migrator: `python -m shipcube_api.admin migrate` (compose `migrate`).
#
# Env: PGHOST, PGPORT (5432), PGUSER (postgres), PGPASSWORD (POSTGRES_PASSWORD),
#      SC_MIGRATOR_DB_PASSWORD, SC_API_DB_PASSWORD, SC_WORKER_DB_PASSWORD.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT="${PROJECT:-shipcube-control-ks}"
export PGHOST="${PGHOST:-localhost}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-postgres}"
export PGPASSWORD="${PGPASSWORD:-${POSTGRES_PASSWORD:?POSTGRES_PASSWORD or PGPASSWORD}}"
: "${SC_MIGRATOR_DB_PASSWORD:?}" "${SC_API_DB_PASSWORD:?}" "${SC_WORKER_DB_PASSWORD:?}"
PSQL=(psql -X -q -v ON_ERROR_STOP=1)
export PGOPTIONS="-c client_min_messages=warning"

for _ in $(seq 1 30); do pg_isready -q && break; sleep 1; done

if [[ "$("${PSQL[@]}" -d postgres -tAc "select 1 from pg_database where datname = 'shipcube'")" != 1 ]]; then
  "${PSQL[@]}" -d postgres -c "create database shipcube"
fi

# Logins (the Cloud SQL IAM users' names). Passwords come in as psql variables, never argv.
"${PSQL[@]}" -d shipcube \
  -v project="$PROJECT" \
  -v migrator_pw="$SC_MIGRATOR_DB_PASSWORD" -v api_pw="$SC_API_DB_PASSWORD" -v worker_pw="$SC_WORKER_DB_PASSWORD" <<'SQL'
select format('sc-migrator@%s.iam', :'project') as migrator,
       format('sc-api@%s.iam', :'project') as api,
       format('sc-worker@%s.iam', :'project') as worker \gset
select format('create role %I login', u) from unnest(array[:'migrator', :'api', :'worker']) u
 where not exists (select from pg_roles where rolname = u) \gexec
select format('alter role %I with login nosuperuser nocreatedb nocreaterole password %L', :'migrator', :'migrator_pw') \gexec
select format('alter role %I with login nosuperuser nocreatedb nocreaterole password %L', :'api', :'api_pw') \gexec
select format('alter role %I with login nosuperuser nocreatedb nocreaterole password %L', :'worker', :'worker_pw') \gexec
SQL

"${PSQL[@]}" -d shipcube -f "$HERE/bootstrap.sql" >/dev/null

PGUSER="sc-migrator@${PROJECT}.iam" PGPASSWORD="$SC_MIGRATOR_DB_PASSWORD" \
  "${PSQL[@]}" -d shipcube -f "$HERE/owner_defaults.sql" >/dev/null

echo "local database bootstrapped (${PGHOST}:${PGPORT}/shipcube); next: migrate as sc-migrator"
