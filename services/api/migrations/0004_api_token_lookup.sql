-- 0004 (networking): let PAT verification find its token under row-level security.
--
-- 0003 limits api_tokens to the caller's workspace, but a PAT is verified before the workspace
-- is known. The API therefore sets `app.token_hmac` (hex of HMAC-SHA-256(pepper, secret)) for the
-- lookup transaction; these policies expose exactly the one row whose secret the request
-- presented, and let it record `last_used_at`. Knowing the HMAC requires the secret and the
-- pepper, so this reveals nothing a caller doesn't already hold.

create policy api_tokens_authenticate on api_tokens for select
  using (token_hmac = decode(nullif(current_setting('app.token_hmac', true), ''), 'hex'));

create policy api_tokens_touch on api_tokens for update
  using (token_hmac = decode(nullif(current_setting('app.token_hmac', true), ''), 'hex'))
  with check (token_hmac = decode(nullif(current_setting('app.token_hmac', true), ''), 'hex'));
