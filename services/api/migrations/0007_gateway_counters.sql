-- The AI gateway's caps, shared across its instances (#20). The gateway reserves and settles through
-- the API's /internal/gateway/* routes (gateway_counters.py), which verify sc-gateway's Google ID
-- token and run as shipcube_app with app.via = 'gateway'. Numbered 0007 so it never collides with
-- 0006 (profiler guard); migrations apply by name, in any order.
--
-- rate_counters (0003) gains the kinds the gateway needs: workspace-wide counters (keyed by the
-- workspace's id in app_id) and spend in micro-dollars per month.

-- 0003 made it UNLOGGED; it now holds the monthly spend the spend cap protects, which a crash must
-- not reset. A few small row updates per gateway request is well within a logged table's write rate.
alter table rate_counters set logged;

alter table rate_counters drop constraint rate_counters_kind_check;
alter table rate_counters add constraint rate_counters_kind_check check (kind in (
  'requests_per_minute', 'ai_tokens_per_day', 'spend_microusd_per_month',
  'workspace_requests_per_minute', 'workspace_ai_tokens_per_day'));

-- Only a transaction the API marked as the verified gateway sees or changes counters. app.via is set
-- by the API itself (db.tx), never from a request: PAT and console callers are 'mcp' or 'console'.
create policy rate_counters_gateway on rate_counters for all to shipcube_app
  using (current_setting('app.via', true) = 'gateway')
  with check (current_setting('app.via', true) = 'gateway');
grant select, insert, update, delete on rate_counters to shipcube_app;
