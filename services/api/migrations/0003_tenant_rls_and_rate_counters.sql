-- 0003 (proposed by security for services/api/migrations): row-level security on every
-- tenant table that 0001 left without it, and the gateway's rate_counters table
-- (database-design.md §3, §5). 0001 covered apps, access_grants, approvals, datasources
-- and audit_log. The worker role (BYPASSRLS) is unaffected.

-- Read: any member (viewer+). Write: the role below, in the caller's workspace.
--   developer: deployments, load_estimates, operations
--   admin:     gcp_links, caps, secret_refs, provider_configs, invites
--   (usage_daily and usage_events are written only by the worker)
do $$
declare
  t text;
  w text;
begin
  for t, w in values
    ('deployments','developer'), ('load_estimates','developer'), ('operations','developer'),
    ('gcp_links','admin'), ('caps','admin'), ('secret_refs','admin'),
    ('provider_configs','admin'), ('invites','admin'),
    ('usage_daily', null), ('usage_events', null)
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
    execute format('create policy %I on %I for select using (workspace_id = app_workspace() and app_has_role(''viewer''))',
                   t || '_read', t);
    if w is not null then
      execute format('create policy %I on %I for insert with check (workspace_id = app_workspace() and app_has_role(%L))',
                     t || '_insert', t, w);
      execute format('create policy %I on %I for update using (workspace_id = app_workspace() and app_has_role(%L)) with check (workspace_id = app_workspace())',
                     t || '_update', t, w);
      execute format('create policy %I on %I for delete using (workspace_id = app_workspace() and app_has_role(%L))',
                     t || '_delete', t, w);
    end if;
  end loop;
end $$;

-- invites with a null workspace_id are platform signup invites: worker/admin tooling only,
-- invisible to API callers (workspace_id = app_workspace() is never true for null).

-- API tokens: members see and revoke their own; admins see and revoke everyone's in the workspace.
alter table api_tokens enable row level security;
alter table api_tokens force row level security;
create policy api_tokens_read on api_tokens for select
  using (workspace_id = app_workspace() and (user_id = app_user() or app_has_role('admin')));
create policy api_tokens_create on api_tokens for insert
  with check (workspace_id = app_workspace() and user_id = app_user() and app_has_role('viewer'));
create policy api_tokens_update on api_tokens for update
  using (workspace_id = app_workspace() and (user_id = app_user() or app_has_role('admin')))
  with check (workspace_id = app_workspace());

-- Gateway rate counters (services/gateway/DESIGN.md): written by the gateway through the
-- worker role, never read by API callers.
create unlogged table rate_counters (
  app_id       uuid not null,
  kind         text not null check (kind in ('requests_per_minute','ai_tokens_per_day')),
  window_start timestamptz not null,
  count        bigint not null default 0,
  primary key (app_id, kind, window_start)
);
alter table rate_counters enable row level security;
alter table rate_counters force row level security;
