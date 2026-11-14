-- The OAuth authorization server for MCP clients (kernel/heycarl/oauth.py).
-- Only the kernel reads these, as heycarl_owner. Codes and tokens are keyed
-- by their SHA-256: a copy of these tables grants nothing.

-- Registered through /register by anyone. A client alone reaches no data:
-- every grant needs a person's consent.
create table heycarl_private.oauth_client (
  client_id text primary key,
  -- The SDK's OAuthClientInformationFull, client secret included: the
  -- token endpoint compares it in plain text.
  info jsonb not null,
  created_at timestamptz not null default now()
);

-- An authorization request between /authorize and the consent page.
create table heycarl_private.oauth_request (
  id_hash text primary key,
  client_id text not null references heycarl_private.oauth_client on delete cascade,
  -- The SDK's AuthorizationParams.
  params jsonb not null,
  expires_at timestamptz not null
);

-- One person's consent to one client. Revoking any token of it ends it all.
create table heycarl_private.oauth_grant (
  id bigint generated always as identity primary key,
  client_id text not null references heycarl_private.oauth_client on delete cascade,
  sub text not null,
  scopes text[] not null,
  resource text,
  created_at timestamptz not null default now()
);

create table heycarl_private.oauth_code (
  code_hash text primary key,
  grant_id bigint not null references heycarl_private.oauth_grant on delete cascade,
  code_challenge text not null,
  redirect_uri text not null,
  redirect_uri_explicit boolean not null,
  expires_at timestamptz not null
);

create table heycarl_private.oauth_token (
  token_hash text primary key,
  grant_id bigint not null references heycarl_private.oauth_grant on delete cascade,
  kind text not null check (kind in ('access', 'refresh')),
  -- A subset of the grant's: a refresh may ask for less.
  scopes text[] not null,
  expires_at timestamptz not null
);
create index oauth_token_grant on heycarl_private.oauth_token (grant_id);
create index oauth_token_expires on heycarl_private.oauth_token (expires_at);
