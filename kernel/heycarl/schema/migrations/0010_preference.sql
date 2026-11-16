-- A person's own settings for the app. One row per person, reached through
-- RLS; api/050_preferences.sql exposes it.
create table heycarl_private.preference (
  owner_sub text primary key default heycarl_sdk.owner(),
  -- A base16 slug from themes.json; null follows the system.
  theme text check (theme ~ '^[a-z0-9][a-z0-9-]{0,99}$'),
  updated_at timestamptz not null default now()
);

alter table heycarl_private.preference enable row level security;
create policy own on heycarl_private.preference to heycarl_user
  using (owner_sub = heycarl_sdk.sub())
  with check (owner_sub = heycarl_sdk.sub());
grant select, insert (theme), update (theme, updated_at) on heycarl_private.preference to heycarl_user;
