-- Links between any two entries (DESIGN.md "Links"). An entry is a row in
-- a tracked table; the API in api/030_links.sql checks both ends.

alter table heycarl_private.tracked add column route text;

-- For plugin migrations: where the app shows one entry, with {column}
-- for each key column, e.g. '/p/crm/heycarl-crm#/contact/{id}'.
create function heycarl_sdk.entry_route(tbl regclass, route text) returns void
  language sql
  set search_path = pg_catalog, pg_temp
  as $$ update heycarl_private.tracked t set route = entry_route.route where t.rel = tbl $$;
revoke execute on function heycarl_sdk.entry_route from public;

-- No foreign key to the ends: they live in any table. A link to a deleted
-- entry stays, hidden, and shows again when the entry comes back.
create table heycarl_private.link (
  id bigint generated always as identity primary key,
  owner_sub text not null,
  a_rel regclass not null references heycarl_private.tracked,
  a_key jsonb not null,
  b_rel regclass not null references heycarl_private.tracked,
  b_key jsonb not null,
  label text not null default '' check (length(label) <= 200),
  created_at timestamptz not null default now(),
  -- Undirected: the ends in a fixed order, so a pair has one link.
  check ((a_rel::oid, a_key::text) < (b_rel::oid, b_key::text)),
  constraint link_pair unique (owner_sub, a_rel, a_key, b_rel, b_key)
);
create index link_b on heycarl_private.link (owner_sub, b_rel, b_key);

select heycarl_sdk.track('heycarl_private.link');
