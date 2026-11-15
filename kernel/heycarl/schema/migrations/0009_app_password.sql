-- Credentials for CalDAV and CardDAV clients (DESIGN.md "App passwords").
-- A person reaches their own rows through RLS; only the functions in
-- api/040_app_passwords.sql write the hash or read it.

-- Trusted since PostgreSQL 13: the database owner may create it.
create extension if not exists pgcrypto schema heycarl_private;

create table heycarl_private.app_password (
  -- The username a device sends.
  id text primary key check (id ~ '^[a-z0-9]{16}$'),
  owner_sub text not null default heycarl_sdk.owner(),
  email text,
  name text not null check (length(name) between 1 and 100),
  secret_hash bytea not null check (length(secret_hash) = 32),
  scopes text[] not null check (scopes in ('{read}', '{read,write}')),
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  expires_at timestamptz not null check (expires_at <= created_at + interval '366 days')
);
create index app_password_owner on heycarl_private.app_password (owner_sub);

alter table heycarl_private.app_password enable row level security;
create policy own on heycarl_private.app_password to heycarl_user
  using (owner_sub = heycarl_sdk.sub());
grant select (id, owner_sub, name, scopes, created_at, last_used_at, expires_at),
  delete on heycarl_private.app_password to heycarl_user;

-- A failed Basic sign-in, once by address and once by username. No API
-- role may touch it.
create table heycarl_private.auth_failure (
  key text not null,
  at timestamptz not null default now()
);
create index auth_failure_key on heycarl_private.auth_failure (key, at);
create index auth_failure_at on heycarl_private.auth_failure (at);
