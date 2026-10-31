-- Request-access consent (#80): which version of the Terms and Privacy notice a person agreed to, and
-- when (set by the API, never by the request). Entries from before this migration have neither; every
-- new one must (NOT VALID: the check applies to rows written from now on).
alter table waitlist add column terms_version text check (length(terms_version) between 1 and 32);
alter table waitlist add column terms_accepted_at timestamptz;
alter table waitlist add constraint waitlist_terms_recorded
  check (terms_version is not null and terms_accepted_at is not null) not valid;
