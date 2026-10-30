-- Public waitlist with manual approval (waitlist.py). A platform table: no workspace, no tenant RLS.
-- The API reaches it as shipcube_app only in transactions it marks itself (app.via, set by db.tx and
-- never from a request): 'waitlist' for the public sign-up, 'platform_admin' for a verified admin.

create table waitlist (
  id           uuid primary key,
  email        citext not null unique,
  name         text not null check (length(name) between 1 and 100),
  use_case     text not null check (length(use_case) between 1 and 500),
  status       text not null default 'pending' check (status in ('pending','approved','rejected')),
  created_at   timestamptz not null default now(),
  decided_at   timestamptz,
  decided_by   citext,                 -- the platform admin's email
  workspace_id uuid references workspaces(id) on delete set null
);
create index waitlist_status_created on waitlist (status, created_at);

alter table waitlist enable row level security;
alter table waitlist force row level security;
-- Sign-up only inserts; duplicates are skipped with on conflict do nothing.
create policy waitlist_signup on waitlist for insert to shipcube_app
  with check (current_setting('app.via', true) = 'waitlist' and status = 'pending' and decided_at is null
              and workspace_id is null);
create policy waitlist_admin on waitlist for all to shipcube_app
  using (current_setting('app.via', true) = 'platform_admin')
  with check (current_setting('app.via', true) = 'platform_admin');
-- /v1/me tells a signed-in person without a workspace whether they're on the list (their own row only).
create policy waitlist_self on waitlist for select to shipcube_app
  using (current_setting('app.via', true) = 'waitlist_self'
         and lower(email) = lower(current_setting('app.email', true)));
revoke delete, truncate on waitlist from shipcube_app;
grant select, insert, update on waitlist to shipcube_app;

-- Sign-up rate limits share rate_counters: anonymous per client (IPv4, or IPv6 /64) and in total;
-- signed-in per account and in total. Keys are keyed digests, never addresses or emails.
alter table rate_counters drop constraint rate_counters_kind_check;
alter table rate_counters add constraint rate_counters_kind_check check (kind in (
  'requests_per_minute', 'ai_tokens_per_day', 'spend_microusd_per_month',
  'workspace_requests_per_minute', 'workspace_ai_tokens_per_day',
  'waitlist_ip_per_hour', 'waitlist_global_per_hour', 'waitlist_account_per_hour', 'waitlist_signed_in_per_hour'));
create policy rate_counters_waitlist on rate_counters for all to shipcube_app
  using (current_setting('app.via', true) = 'waitlist' and kind like 'waitlist\_%')
  with check (current_setting('app.via', true) = 'waitlist' and kind like 'waitlist\_%');

-- Approving creates the requester's workspace with the default caps; caps' tenant policy needs a
-- workspace member, which a brand-new workspace's pending owner can't be in this transaction.
create policy caps_platform_admin on caps for insert to shipcube_app
  with check (current_setting('app.via', true) = 'platform_admin' and app_id is null);
grant select on waitlist to shipcube_worker;
