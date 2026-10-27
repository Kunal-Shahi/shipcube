-- migrate:no-transaction
create index concurrently widgets_name on widgets (name);
