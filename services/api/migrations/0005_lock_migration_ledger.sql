-- Proposed by security for services/api/migrations (networking adopts it verbatim).
-- The migration ledger is written only by the migration role, which owns it (shipcube_owner).
-- owner_defaults.sql gives the service roles DML on every table in public, so the API and worker
-- logins could also forge or delete ledger rows: mark a missing migration as applied (check_schema
-- would then pass) or force a re-run. They keep SELECT, which check_schema needs at startup.
revoke insert, update, delete, truncate on schema_migrations from shipcube_app, shipcube_worker;
