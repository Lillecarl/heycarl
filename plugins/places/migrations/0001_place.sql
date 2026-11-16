-- Runs with search_path set to this plugin's schema.
--
-- A place is a named point with a radius. Entries bind to a place by a
-- core link, or by a tag the place carries: a todo tagged "hardware" is
-- here at every place tagged "hardware" (DESIGN.md "Places").
--
-- The caller's position is an argument, never a row: nothing here stores
-- where the person is. The page sends it in a POST body, so no URL, and
-- so no access log, carries it.
--
-- `near` and `here` are plpgsql: the kernel recreates the heycarl schema
-- after the migrations, so a sql body naming heycarl.links fails to create.

create table place (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  name text not null check (length(btrim(name)) between 1 and 100 and name = btrim(name)),
  latitude double precision not null check (latitude between -90 and 90),
  longitude double precision not null check (longitude between -180 and 180),
  -- Metres. A phone's fix indoors is often 20 to 50 m off.
  radius integer not null default 100 check (radius between 10 and 50000),
  notes text not null default '' check (length(notes) <= 2000),
  created_at timestamptz not null default now(),
  unique (id, owner_sub)
);
create index place_owner on place (owner_sub, name);

alter table place enable row level security;
create policy own on place to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_places to heycarl_user;
grant select, delete on place to heycarl_user;
grant insert (name, latitude, longitude, radius, notes),
      update (name, latitude, longitude, radius, notes) on place to heycarl_user;

-- Great-circle distance in metres (haversine, mean Earth radius). Off by
-- under 0.5 % against the ellipsoid, which is far below a phone's error.
create function metres(lat1 double precision, lon1 double precision,
                       lat2 double precision, lon2 double precision)
  returns double precision
  language sql immutable strict parallel safe
  as $$
    select 2 * 6371008.8 * asin(least(1, sqrt(
      sin(radians(lat2 - lat1) / 2) ^ 2
      + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lon2 - lon1) / 2) ^ 2)))
  $$;

create function check_point(latitude double precision, longitude double precision)
  returns void
  language plpgsql immutable
  as $$
begin
  if latitude is null or longitude is null
     or latitude not between -90 and 90 or longitude not between -180 and 180 then
    raise exception 'a position needs a latitude from -90 to 90 and a longitude from -180 to 180'
      using errcode = '22023';
  end if;
end
$$;

-- The caller's places within `within` metres of (lat, lon), nearest first.
-- `inside` says the point is within the place's own radius. The inputs are
-- not named latitude and longitude: plpgsql refuses an input that shares a
-- name with an output column.
create function near(lat double precision, lon double precision, within integer default 5000)
  returns table (id bigint, name text, latitude double precision, longitude double precision,
                 radius integer, notes text, distance integer, inside boolean)
  language plpgsql stable
  set search_path = heycarl_plugin_places, pg_temp
  as $$
#variable_conflict use_column
begin
  perform check_point(near.lat, near.lon);
  if within not between 10 and 100000 then
    raise exception 'within must be 10 to 100000 metres' using errcode = '22023';
  end if;
  return query
    select p.id, p.name, p.latitude, p.longitude, p.radius, p.notes,
           round(d.m)::integer, d.m <= p.radius
      from place p
     cross join lateral (select metres(near.lat, near.lon, p.latitude, p.longitude) as m) d
     where d.m <= near.within
     order by d.m, p.id;
end
$$;

-- What is bound to the places the point is inside: entries linked to the
-- place, and entries that carry a tag the place carries (`via_tag`). An
-- entry bound both ways comes once, as a direct link.
create function here(lat double precision, lon double precision)
  returns table (place_id bigint, place_name text, distance integer,
                 via_tag text, entry jsonb)
  language plpgsql stable
  set search_path = heycarl_plugin_places, pg_temp
  as $$
begin
  return query
    with inside as (
      select n.id, n.name, n.distance
        from near(here.lat, here.lon, 50000) n
       where n.inside
    ),
    linked as (
      select i.id, i.name, i.distance, l.entry
        from inside i
       cross join lateral heycarl.links('places', 'place', jsonb_build_object('id', i.id)) l
    ),
    bound as (
      select k.id, k.name, k.distance, null::text as via_tag, k.entry
        from linked k
       where not (k.entry ->> 'scope' = 'tags' and k.entry ->> 'table' = 'tag')
      union all
      select k.id, k.name, k.distance, k.entry ->> 'label', t.entry
        from linked k
       cross join lateral heycarl.links('tags', 'tag', k.entry -> 'key') t
       where k.entry ->> 'scope' = 'tags' and k.entry ->> 'table' = 'tag'
         and not (t.entry ->> 'scope' = 'places' and t.entry ->> 'table' = 'place')
    )
    select distinct on (b.distance, b.id, b.entry ->> 'scope', b.entry ->> 'table', b.entry -> 'key')
           b.id, b.name, b.distance, b.via_tag, b.entry
      from bound b
     order by b.distance, b.id, b.entry ->> 'scope', b.entry ->> 'table', b.entry -> 'key',
              b.via_tag nulls first;
end
$$;

revoke execute on function metres, check_point, near, here from public;
grant execute on function metres, check_point, near, here to heycarl_user;

select heycarl_sdk.track('place');
select heycarl_sdk.entry_route('place', '/p/places/heycarl-places#/place/{id}');
