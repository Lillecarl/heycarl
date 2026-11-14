-- Browser sessions and logins in progress. Only the kernel reads these, as
-- heycarl_owner; PostgREST's roles get no grant, so no API path reaches them.
-- Each key is a SHA-256 of a random value the browser holds: a copy of
-- this table signs nobody in.

create table heycarl_private.session (
  token_hash text primary key,
  sub text not null,
  email text,
  name text,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);
create index session_expires on heycarl_private.session (expires_at);

-- One OIDC login between the redirect to the provider and the callback.
create table heycarl_private.login (
  state_hash text primary key,
  nonce text not null,
  code_verifier text not null,
  next text not null check (next like '/%' and next not like '//%'),
  expires_at timestamptz not null
);
