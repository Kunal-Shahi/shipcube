-- Profiler sandbox guard (docs/research/profiler-sandbox.md §6–8), platform-owned.
--
-- profiler_spend: the worst-case cost of each profiled estimate, added by the worker when it starts
--   the run (§7.1). Today's global and per-workspace totals decide whether the next estimate is
--   profiled or answered statically with reason=budget. Billing data lags by hours; this doesn't.
-- profiler_flags: switches the worker sets and reads on every run. `brake` is set by the janitor when
--   more profiler executions exist than the global ceiling allows (§8, global brake).
-- profiler_strikes: one row per abuse signal a profiled run raised (the profiler's
--   profile.measurements.abuse_signals: egress_flood, mining_pool, fork_bomb, disk_flood, cpu_pinned,
--   result_forged). 3 strikes in 24 h suspend the workspace's profiling for 24 h (§8).
-- profiler_suspensions: a suspended workspace's estimates run static (reason=suspended). `until` null
--   means until a person clears it (`admin profiler --unsuspend`), which a mining_pool signal requires.
--
-- Both are written and read only by the worker and the operator CLI (shipcube_worker, BYPASSRLS).
-- RLS is forced with no policies, so API callers (shipcube_app) see nothing.

create table profiler_spend (
  day          date not null,
  workspace_id uuid not null references workspaces(id) on delete cascade,
  runs         integer not null default 0,
  usd          numeric(10,4) not null default 0,
  updated_at   timestamptz not null default now(),
  primary key (day, workspace_id)
);
alter table profiler_spend enable row level security;
alter table profiler_spend force row level security;

create table profiler_flags (
  name       text primary key check (name in ('brake')),
  until      timestamptz not null,
  reason     text not null,
  set_at     timestamptz not null default now()
);
alter table profiler_flags enable row level security;
alter table profiler_flags force row level security;

create table profiler_strikes (
  id           bigserial primary key,
  workspace_id uuid not null references workspaces(id) on delete cascade,
  operation_id uuid not null references operations(id) on delete cascade,
  signal       text not null,
  created_at   timestamptz not null default now(),
  unique (operation_id, signal)
);
create index profiler_strikes_recent_idx on profiler_strikes (workspace_id, created_at);
alter table profiler_strikes enable row level security;
alter table profiler_strikes force row level security;

create table profiler_suspensions (
  workspace_id uuid primary key references workspaces(id) on delete cascade,
  until        timestamptz,
  reason       text not null,
  set_at       timestamptz not null default now()
);
alter table profiler_suspensions enable row level security;
alter table profiler_suspensions force row level security;

-- Admission and the claim's global ceiling count estimates by status and workspace.
create index operations_estimates_idx on operations (workspace_id, status, created_at) where kind = 'estimate';

-- Production gets these from owner_defaults.sql; spelled out so every environment matches. The API
-- role has no business here at all (RLS would already show it nothing).
grant select, insert, update, delete on profiler_spend, profiler_flags, profiler_strikes, profiler_suspensions
  to shipcube_worker;
grant usage on sequence profiler_strikes_id_seq to shipcube_worker;
revoke all on profiler_spend, profiler_flags, profiler_strikes, profiler_suspensions from shipcube_app;
