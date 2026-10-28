-- RLS and token-login checks. Run inside a transaction that is ROLLED BACK (check.sh does
-- this): it creates fixtures for two workspaces, then asserts. Any failed expectation raises
-- an exception, so psql exits non-zero. Policies are evaluated the same way for the owner
-- and for shipcube_app (FORCE ROW LEVEL SECURITY), so the checks are meaningful as either.
\set ON_ERROR_STOP on

do $$
declare
  ua uuid := gen_random_uuid(); ub uuid := gen_random_uuid();
  wa uuid := gen_random_uuid(); wb uuid := gen_random_uuid();
  app_b uuid := gen_random_uuid(); tok_a uuid := gen_random_uuid();
  secret_a bytea := digest('rls-check-secret-a', 'sha256');
  t text; n bigint; ok int := 0;
  tenant_tables text[] := array['gcp_links','apps','deployments','load_estimates','operations','caps',
                                'secret_refs','provider_configs','invites','api_tokens','usage_daily','usage_events',
                                'access_grants','approvals','datasources','audit_log','rate_counters'];
  fixture_tables text[] := array['gcp_links','apps','deployments','load_estimates','operations','caps',
                                 'secret_refs','provider_configs','invites','api_tokens'];
begin
  -- 1. RLS enabled and forced on every tenant table (12 from 0003 + 5 from 0001).
  foreach t in array tenant_tables loop
    select count(*) into n from pg_class c join pg_namespace s on s.oid = c.relnamespace
     where s.nspname = 'public' and c.relname = t and c.relrowsecurity and c.relforcerowsecurity;
    if n <> 1 then raise exception 'FAIL: % does not have forced RLS', t; end if;
  end loop;
  raise notice 'PASS 1: forced RLS on all % tenant tables', array_length(tenant_tables, 1);

  -- Fixtures: users, workspaces and memberships have no RLS.
  insert into users (id, firebase_uid, email) values
    (ua, 'rls-check-' || ua, 'rls-a-' || ua || '@check.invalid'),
    (ub, 'rls-check-' || ub, 'rls-b-' || ub || '@check.invalid');
  insert into workspaces (id, slug, name, created_by) values
    (wa, 'rlscheck-a-' || left(wa::text, 8), 'RLS check A', ua),
    (wb, 'rlscheck-b-' || left(wb::text, 8), 'RLS check B', ub);
  insert into memberships (workspace_id, user_id, role) values (wa, ua, 'owner'), (wb, ub, 'owner');

  -- Workspace B's rows, written as B's owner through the policies.
  perform set_config('app.user_id', ub::text, true), set_config('app.workspace_id', wb::text, true),
          set_config('app.via', 'console', true);
  insert into gcp_links (id, workspace_id, project_id, deployer_sa, runtime_sa, status, linked_by)
    values (gen_random_uuid(), wb, 'rls-check-' || left(wb::text, 8), 'd@x.invalid', 'r@x.invalid', 'pending', ub);
  insert into apps (id, workspace_id, slug, name, tier, kind, region, created_by)
    values (app_b, wb, 'rls-check', 'RLS check', 'T1', 'service', 'asia-south1', ub);
  insert into deployments (id, workspace_id, app_id, config, status, requested_via)
    values (gen_random_uuid(), wb, app_b, '{}', 'queued', 'console');
  insert into load_estimates (id, workspace_id, app_id, workload_class, recommended_tier)
    values (gen_random_uuid(), wb, app_b, 'steady', 'T1');
  insert into operations (id, workspace_id, app_id, kind, target_project, status, idem_key, request_hash)
    values (gen_random_uuid(), wb, app_b, 'deploy', 'shipcube-apps-ks', 'queued', 'rls-check', '\x00');
  insert into caps (id, workspace_id, app_id, kind, limit_value) values (gen_random_uuid(), wb, app_b, 'max_instances', 2);
  insert into secret_refs (id, workspace_id, kind, label, resource_name, version)
    values (gen_random_uuid(), wb, 'ai_key', 'rls', 'projects/x/secrets/rls-check-' || wb, '1');
  insert into provider_configs (id, workspace_id, provider, model) values (gen_random_uuid(), wb, 'meta', 'muse-spark');
  insert into invites (id, workspace_id, email, expires_at) values (gen_random_uuid(), wb, 'x@check.invalid', now() + interval '1 day');
  insert into api_tokens (id, workspace_id, user_id, kind, name, prefix, token_hmac, scopes, expires_at)
    values (gen_random_uuid(), wb, ub, 'pat', 'rls-b', 'sc_pat_rlsb', digest('rls-check-secret-b', 'sha256'), '{apps:read}', now() + interval '1 day');

  -- B sees its own rows (so the zero counts below mean something).
  foreach t in array fixture_tables loop
    execute format('select count(*) from %I where workspace_id = $1', t) into n using wb;
    if n < 1 then raise exception 'FAIL: B cannot see its own % row', t; end if;
  end loop;

  -- 2. As A: none of B's rows are visible, and B's app can't be modified.
  perform set_config('app.user_id', ua::text, true), set_config('app.workspace_id', wa::text, true);
  foreach t in array fixture_tables loop
    execute format('select count(*) from %I where workspace_id = $1', t) into n using wb;
    if n <> 0 then raise exception 'FAIL: A can read % row(s) of B in %', n, t; end if;
  end loop;
  update apps set name = 'hijacked' where id = app_b;
  get diagnostics n = row_count;
  if n <> 0 then raise exception 'FAIL: A updated B''s app'; end if;
  begin
    insert into apps (id, workspace_id, slug, name, tier, kind, region, created_by)
      values (gen_random_uuid(), wb, 'rls-intruder', 'x', 'T1', 'service', 'asia-south1', ua);
    raise exception 'FAIL: A inserted an app into B';
  exception when insufficient_privilege then null;  -- new row violates row-level security policy
  end;
  raise notice 'PASS 2: workspace A reads 0 of B''s rows in % tables and cannot write B', array_length(fixture_tables, 1);

  -- A mints a token for itself.
  insert into api_tokens (id, workspace_id, user_id, kind, name, prefix, token_hmac, scopes, expires_at)
    values (tok_a, wa, ua, 'pat', 'rls-a', 'sc_pat_rlsa', secret_a, '{apps:read,apps:deploy}', now() + interval '30 days');

  -- 3. Token login exactly as auth.verify_pat: no workspace context, only app.token_hmac.
  perform set_config('app.user_id', '', true), set_config('app.workspace_id', '', true),
          set_config('app.via', 'mcp', true), set_config('app.token_hmac', encode(secret_a, 'hex'), true);
  update api_tokens t set last_used_at = now() from users u
   where t.token_hmac = secret_a and t.prefix = 'sc_pat_rlsa' and t.revoked_at is null and t.expires_at > now()
     and u.id = t.user_id and u.disabled_at is null;
  get diagnostics n = row_count;
  if n <> 1 then raise exception 'FAIL: token lookup returned % rows (expected 1)', n; end if;
  select count(*) into n from memberships m join users u on u.id = m.user_id
   where m.user_id = ua and u.disabled_at is null and m.workspace_id = wa;
  if n <> 1 then raise exception 'FAIL: membership lookup after token login'; end if;
  -- Only that one row is visible.
  select count(*) into n from api_tokens;
  if n <> 1 then raise exception 'FAIL: token context exposes % token rows (expected 1)', n; end if;
  raise notice 'PASS 3: PAT lookup + last_used_at update + membership lookup succeed; only that token row is visible';

  -- 4. Wrong secret, and no secret: nothing.
  perform set_config('app.token_hmac', encode(digest('wrong', 'sha256'), 'hex'), true);
  select count(*) into n from api_tokens;
  if n <> 0 then raise exception 'FAIL: wrong HMAC sees % token rows', n; end if;
  perform set_config('app.token_hmac', '', true);
  select count(*) into n from api_tokens;
  if n <> 0 then raise exception 'FAIL: no context sees % token rows', n; end if;
  raise notice 'PASS 4: wrong or missing HMAC sees 0 tokens';

  -- 5. Migration ledgers are written only by the migration role. WARN until proposed/0005 (and the
  --    catalog's equivalent) are applied; then this becomes a FAIL.
  foreach t in array array['public.schema_migrations', 'catalog.catalog_schema_migrations'] loop
    if to_regclass(t) is not null then
      select count(*) into n from unnest(array['shipcube_app', 'shipcube_worker']) r,
             unnest(array['INSERT', 'UPDATE', 'DELETE']) p
       where has_table_privilege(r, t, p);
      if n > 0 then
        raise notice 'WARN 5: service roles can write the migration ledger % (apply proposed/0005)', t;
      else
        raise notice 'PASS 5: % is read-only to the service roles', t;
      end if;
    end if;
  end loop;

  -- 6. shipcube-data's catalog (schema `catalog`, once migrated): every tenant table has forced RLS,
  --    is owned by shipcube_owner, and has only the policy workspace_id = app_workspace() for
  --    reads and writes. Then a cross-workspace read and write on catalog.settings.
  if to_regclass('catalog.catalog_schema_migrations') is not null then  -- bootstrap creates the empty schema
    select count(*) into n from pg_class c join pg_namespace s on s.oid = c.relnamespace
     where s.nspname = 'catalog' and c.relkind in ('r', 'p') and c.relname <> 'catalog_schema_migrations'
       and not (c.relrowsecurity and c.relforcerowsecurity and pg_get_userbyid(c.relowner) = 'shipcube_owner');
    if n <> 0 then raise exception 'FAIL: % catalog tables lack forced RLS or are not owned by shipcube_owner', n; end if;
    select count(*) into n from pg_class c join pg_namespace s on s.oid = c.relnamespace
     where s.nspname = 'catalog' and c.relkind in ('r', 'p') and c.relname <> 'catalog_schema_migrations'
       and not exists (select from pg_policies p where p.schemaname = 'catalog' and p.tablename = c.relname);
    if n <> 0 then raise exception 'FAIL: % catalog tables have no policy', n; end if;
    -- Permissive policies are OR-ed: one loose policy would undo the rest, so check them all.
    select count(*) into n from pg_policies p
     where p.schemaname = 'catalog'
       and not (p.permissive = 'PERMISSIVE' and p.cmd = 'ALL'
                and regexp_replace(replace(coalesce(p.qual, ''), 'public.', ''), '[\s()]', '', 'g') = 'workspace_id=app_workspace'
                and regexp_replace(replace(coalesce(p.with_check, ''), 'public.', ''), '[\s()]', '', 'g') = 'workspace_id=app_workspace');
    if n <> 0 then raise exception 'FAIL: % catalog policies are not exactly workspace_id = app_workspace()', n; end if;

    perform set_config('app.workspace_id', wb::text, true);
    insert into catalog.settings (key, value) values ('rls-check', 'b');
    perform set_config('app.workspace_id', wa::text, true);
    select count(*) into n from catalog.settings where workspace_id = wb;
    if n <> 0 then raise exception 'FAIL: workspace A reads % of B''s catalog rows', n; end if;
    begin
      insert into catalog.settings (workspace_id, key, value) values (wb, 'rls-check-a', 'a');
      raise exception 'FAIL: workspace A wrote a catalog row into B';
    exception when insufficient_privilege then null;
    end;
    perform set_config('app.workspace_id', '', true);
    select count(*) into n from catalog.settings;
    if n <> 0 then raise exception 'FAIL: no workspace context sees % catalog rows', n; end if;
    select count(*) into n from pg_class c join pg_namespace s on s.oid = c.relnamespace
     where s.nspname = 'catalog' and c.relkind in ('r', 'p') and c.relname <> 'catalog_schema_migrations';
    raise notice 'PASS 6: catalog: forced RLS + exact workspace policy on % tables; A can''t read or write B; no context sees nothing', n;
  else
    raise notice 'SKIP 6: catalog not migrated';
  end if;

  raise notice 'ALL CHECKS PASSED (running as %)', current_user;
end $$;
