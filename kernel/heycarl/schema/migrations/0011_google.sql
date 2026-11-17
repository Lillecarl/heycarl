-- Google accounts (DESIGN.md "Google accounts"). A person reads their own
-- accounts under RLS and writes nothing; the kernel writes as
-- heycarl_worker through the functions in api/060_google.sql.

create table heycarl_private.google_account (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  -- Null only for an account that a migration carried over without it;
  -- the next connect with the same email fills it in.
  google_sub text,
  email text not null default '',
  scopes text[] not null default '{}',
  connected_at timestamptz not null default now(),
  needs_reconnect boolean not null default false,
  last_error text,
  unique (owner_sub, google_sub),
  -- Plugin rows reference this pair, so a row cannot point at another
  -- owner's account.
  unique (id, owner_sub)
);

-- The refresh token, sealed by the kernel under a key the database never
-- sees: nonce || ciphertext, with the owner as associated data. No API role
-- may read this table.
create table heycarl_private.google_token (
  account_id bigint primary key
    references heycarl_private.google_account (id) on delete cascade,
  sealed bytea not null,
  updated_at timestamptz not null default now()
);

alter table heycarl_private.google_account enable row level security;
alter table heycarl_private.google_token enable row level security;
create policy own on heycarl_private.google_account to heycarl_user
  using (owner_sub = heycarl_sdk.sub());
grant select (id, owner_sub, email, scopes, connected_at, needs_reconnect, last_error)
  on heycarl_private.google_account to heycarl_user;
