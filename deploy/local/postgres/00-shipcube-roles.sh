#!/bin/sh
# Postgres first-boot init (docker-entrypoint-initdb.d). Creates the service logins with exactly the
# names of the Cloud SQL IAM users, then runs infra/db/bootstrap.sql unmodified, so roles, default
# session roles and grants match production. Local password only; never used anywhere else.
set -eu
psql -v ON_ERROR_STOP=1 -U postgres -d postgres -c "create database shipcube"
for u in sc-migrator sc-api sc-worker; do
  psql -v ON_ERROR_STOP=1 -U postgres -d shipcube -c "create role \"$u@shipcube-control-ks.iam\" login password 'local'"
done
psql -v ON_ERROR_STOP=1 -U postgres -d shipcube -f /shipcube/infra-db/bootstrap.sql
