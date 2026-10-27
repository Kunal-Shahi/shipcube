-- 0002: what the durable operations runner needs on top of security's `operations` table.
-- Proposed to security for docs/research/database-design.md (PR #8 description).

-- Rollback to an earlier revision is its own kind of operation.
alter table operations drop constraint operations_kind_check;
alter table operations add constraint operations_kind_check check (kind in (
  'estimate','deploy','rollback','delete','restore','purge','set_caps','set_access','profile','link_verify','grant_sync'));

alter table operations
  -- The request the agent made (normalized, no secret values) and what came back.
  add column input            jsonb not null default '{}',
  add column result           jsonb,
  add column error_code       text,
  -- Step progress for the agent: [{name, status}] and the step currently running.
  add column steps            jsonb not null default '[]',
  add column step_index       int not null default 0,
  add column progress_message text,
  -- Approval fields mirrored for cheap polling; `approvals` stays the source of truth.
  add column approval_url     text,
  add column approval_reasons text[] not null default '{}',
  add column approval_actions text[] not null default '{}',
  add column approval_expires_at timestamptz,
  -- Durable queue: a worker leases an operation, heartbeats while a step runs, and on a
  -- retryable failure schedules the next attempt with backoff. An expired lease means the
  -- worker died; any worker may then resume from step_index (every step is idempotent).
  add column locked_by        text,
  add column locked_until     timestamptz,
  add column next_attempt_at  timestamptz not null default now(),
  add column max_attempts     int not null default 5,
  add column cancel_requested boolean not null default false,
  add column started_at       timestamptz,
  add column finished_at      timestamptz,
  add column timings          jsonb not null default '{}';

create index operations_due on operations (next_attempt_at) where status in ('queued','running');

-- Workers wake on NOTIFY instead of waiting for the next poll.
create function operations_notify() returns trigger language plpgsql as $$
begin
  perform pg_notify('shipcube_operations', new.id::text);
  return new;
end $$;
create trigger operations_notify after insert or update of status on operations
  for each row when (new.status = 'queued') execute function operations_notify();
