-- Runs with search_path set to this plugin's schema.
--
-- A stop is a saved SL site with a filter: which mode, which lines, which
-- direction (DESIGN.md "Departures"). The departures themselves are public
-- data, fetched by the kernel when a page asks, and never stored.
--
-- Link a stop to a place, or tag both alike, and it shows its departures
-- under "What's here?".

create table stop (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  name text not null check (length(btrim(name)) between 1 and 100 and name = btrim(name)),
  -- SL's site id and name, from the site search.
  site_id integer not null check (site_id between 1 and 99999999),
  site_name text not null check (length(site_name) between 1 and 200),
  -- SL's transport_mode; null is every mode.
  transport text check (transport in ('BUS', 'METRO', 'TRAIN', 'TRAM', 'SHIP', 'FERRY', 'TAXI')),
  -- SL line ids; empty is every line.
  lines integer[] not null default '{}'
    check (cardinality(lines) <= 20 and array_position(lines, null) is null),
  -- SL's direction_code, 1 or 2; null is both. Not the destination text:
  -- a short-turn train carries another destination on the same direction.
  direction smallint check (direction in (1, 2)),
  created_at timestamptz not null default now(),
  unique (id, owner_sub)
);
create index stop_owner on stop (owner_sub, name);

alter table stop enable row level security;
create policy own on stop to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_departures to heycarl_user;
grant select, delete on stop to heycarl_user;
grant insert (name, site_id, site_name, transport, lines, direction),
      update (name, site_id, site_name, transport, lines, direction) on stop to heycarl_user;

select heycarl_sdk.track('stop');
select heycarl_sdk.entry_route('stop', '/p/departures/heycarl-departures#/stop/{id}');
