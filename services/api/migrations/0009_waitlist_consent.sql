-- Request-access consent (#80): which version of the Terms and Privacy notice a person agreed to, and
-- when (set by the API, never by the request). Entries from before this migration have neither, and
-- stay decidable: the rule is enforced on INSERT only, by a trigger (a NOT VALID check would still be
-- evaluated on every UPDATE, so approving or rejecting an older entry would fail).
alter table waitlist add column terms_version text check (length(terms_version) between 1 and 32);
alter table waitlist add column terms_accepted_at timestamptz;

create function waitlist_require_consent() returns trigger language plpgsql as $$
begin
  if new.terms_version is null or new.terms_accepted_at is null then
    raise exception 'a waitlist entry must record consent (terms_version, terms_accepted_at)'
      using errcode = 'check_violation';
  end if;
  return new;
end $$;

create trigger waitlist_require_consent before insert on waitlist
  for each row execute function waitlist_require_consent();
