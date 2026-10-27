-- Runs as sc-migrator, whose sessions start as shipcube_owner (bootstrap.sql), before every
-- migration run. Idempotent. Gives the service roles access to every table, sequence and
-- function shipcube_owner creates, now and in future migrations.
\set ON_ERROR_STOP on
select current_user as running_as;

alter default privileges in schema public
  grant select, insert, update, delete on tables to shipcube_app, shipcube_worker;
alter default privileges in schema public
  grant select on tables to shipcube_readonly;
alter default privileges in schema public
  grant usage, select on sequences to shipcube_app, shipcube_worker;
alter default privileges in schema public
  grant execute on functions to shipcube_app, shipcube_worker, shipcube_readonly;
