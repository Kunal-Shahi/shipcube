-- Abuse strikes count once per source, not per operation (#97): the same signal on the same source,
-- however many estimates and deploys reuse it, is one strike. source_key is a content hash of the
-- screened source (abuse_screen.source_key: file names and contents, or the git commit). Rows without
-- one (sandbox runs that couldn't key their source) still dedupe per operation, as before.
alter table profiler_strikes add column source_key text;
create unique index profiler_strikes_per_source on profiler_strikes (workspace_id, source_key, signal)
  where source_key is not null;
