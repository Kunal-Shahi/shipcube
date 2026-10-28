-- One-time (idempotent) database bootstrap, run as the built-in `postgres` user by
-- infra/db/bootstrap.sh. Creates what schema migrations can't create for themselves on
-- Cloud SQL: extensions, the four roles from database-design.md §5, and the mapping from
-- each service's IAM database user to its role. Schema migrations (services/api/migrations)
-- then run as sc-migrator, acting as shipcube_owner.
\set ON_ERROR_STOP on

create extension if not exists citext;
create extension if not exists pgcrypto;

do $$ begin
  if not exists (select from pg_roles where rolname = 'shipcube_owner')    then create role shipcube_owner nologin; end if;
  if not exists (select from pg_roles where rolname = 'shipcube_app')      then create role shipcube_app nologin; end if;
  if not exists (select from pg_roles where rolname = 'shipcube_worker')   then create role shipcube_worker nologin bypassrls; end if;
  if not exists (select from pg_roles where rolname = 'shipcube_readonly') then create role shipcube_readonly nologin; end if;
end $$;

-- Migrations create objects in public as shipcube_owner; everyone else only uses them.
grant usage, create on schema public to shipcube_owner;
grant usage on schema public to shipcube_app, shipcube_worker, shipcube_readonly;
revoke create on schema public from public;

-- Each service's IAM database user can act only as its own role.
grant shipcube_owner  to "sc-migrator@shipcube-control-ks.iam";
grant shipcube_app    to "sc-api@shipcube-control-ks.iam";
grant shipcube_worker to "sc-worker@shipcube-control-ks.iam";

-- Start every session in that role, so objects a migration creates are owned by
-- shipcube_owner and the API's queries are subject to RLS even before SET LOCAL ROLE.
do $$ begin
  execute 'alter role "sc-migrator@shipcube-control-ks.iam" set role = ''shipcube_owner''';
  execute 'alter role "sc-api@shipcube-control-ks.iam" set role = ''shipcube_app''';
  execute 'alter role "sc-worker@shipcube-control-ks.iam" set role = ''shipcube_worker''';
exception when insufficient_privilege then
  raise notice 'cannot set default roles on IAM users (%); callers must SET ROLE themselves', sqlerrm;
end $$;

-- Default privileges on what shipcube_owner creates are set by owner_defaults.sql, which
-- runs as sc-migrator (acting as shipcube_owner) before each migration: on Cloud SQL,
-- postgres is not a member of shipcube_owner and can't set them on its behalf.

-- Report.
select r.rolname, r.rolbypassrls,
       array(select b.rolname from pg_auth_members m join pg_roles b on b.oid = m.roleid where m.member = r.oid) as member_of,
       (select setconfig from pg_db_role_setting s where s.setrole = r.oid and s.setdatabase = 0) as settings
from pg_roles r
where r.rolname like 'shipcube_%' or r.rolname like 'sc-%'
order by 1;
