-- Pre-screen strikes count once per source, not per operation (#97): the same prescreen_* signal on the
-- same source, however many estimates and deploys reuse it, is one strike. source_key identifies exactly
-- the content that was screened (abuse_screen.source_key: a hash of the files read, or the git commit
-- checked out). Unkeyed rows, and every runtime sandbox signal, still count per operation.
alter table profiler_strikes add column source_key text;
create unique index profiler_strikes_per_source on profiler_strikes (workspace_id, source_key, signal)
  where source_key is not null;
