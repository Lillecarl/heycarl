-- Runs with search_path set to this plugin's schema.
create table todo (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  title text not null check (length(title) between 1 and 500),
  notes text not null default '',
  due timestamptz,
  done_at timestamptz,
  created_at timestamptz not null default now()
);
create index todo_open on todo (owner_sub, due) where done_at is null;

alter table todo enable row level security;
create policy own on todo to heycarl_user
  using (owner_sub = heycarl_sdk.sub())
  with check (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_todos to heycarl_user;
grant select, delete on todo to heycarl_user;
grant insert (title, notes, due, done_at), update (title, notes, due, done_at) on todo to heycarl_user;
