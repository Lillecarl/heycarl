-- Runs with search_path set to this plugin's schema.
--
-- A counter's value is the sum of its events. An event references its
-- counter through (counter_id, owner_sub), and row-level security pins
-- owner_sub to the caller, so an event can only count on the caller's own
-- counter: a foreign key check itself ignores row-level security.

create table counter (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  name text not null check (length(name) between 1 and 100),
  unit text not null default '' check (length(unit) <= 50),
  notes text not null default '' check (length(notes) <= 2000),
  step bigint not null default 1 check (step between 1 and 1000000000),
  created_at timestamptz not null default now(),
  unique (id, owner_sub)
);
create index counter_owner on counter (owner_sub, name);

create table event (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  counter_id bigint not null,
  -- Bounded so that a value stays exact in a JavaScript number.
  delta bigint not null check (delta <> 0 and delta between -1000000000000 and 1000000000000),
  note text not null default '' check (length(note) <= 500),
  at timestamptz not null default now(),
  foreign key (counter_id, owner_sub) references counter (id, owner_sub) on delete cascade
);
create index event_counter_at on event (counter_id, at);
create index event_counter_id on event (counter_id, id);

alter table counter enable row level security;
alter table event enable row level security;
create policy own on counter to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on event to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_counters to heycarl_user;
grant select, delete on counter to heycarl_user;
grant insert (name, unit, notes, step), update (name, unit, notes, step) on counter to heycarl_user;
-- No update: an event is a fact. Undo deletes it, and the trash keeps it.
grant select, delete on event to heycarl_user;
grant insert (counter_id, delta, note, at) on event to heycarl_user;

create view counter_value with (security_invoker = true) as
  select c.id, c.name, c.unit, c.notes, c.step, c.created_at,
         coalesce(s.value, 0)::bigint as value, s.events, s.last_at
    from counter c
    left join lateral (
      select sum(e.delta) as value, count(*) as events, max(e.at) as last_at
        from event e
       where e.counter_id = c.id
    ) s on true;
grant select on counter_value to heycarl_user;

-- RPC. Each runs as the caller, so row-level security holds inside it.
-- PT404 is PostgREST's code for a 404; it answers 500 to P0002.
-- "Newest event" is the highest id everywhere: the last one recorded,
-- even when its `at` is backdated.

create function bump(
  counter_id bigint,
  delta bigint default null,
  note text default '',
  at timestamptz default now()
) returns counter_value
  language plpgsql volatile
  set search_path = heycarl_plugin_counters, pg_temp
  as $$
declare
  default_step bigint;
  result counter_value;
begin
  select c.step into default_step from counter c where c.id = bump.counter_id;
  if not found then
    raise exception 'no counter with id %', bump.counter_id using errcode = 'PT404';
  end if;
  insert into event (counter_id, delta, note, at)
  values (bump.counter_id, coalesce(bump.delta, default_step), bump.note, bump.at);
  select * into result from counter_value v where v.id = bump.counter_id;
  return result;
end
$$;

-- Adds the difference as one event. The row lock makes the read and the
-- insert one step: a concurrent event waits for it.
create function set_value(counter_id bigint, target bigint, note text default '')
  returns counter_value
  language plpgsql volatile
  set search_path = heycarl_plugin_counters, pg_temp
  as $$
declare
  was bigint;
  result counter_value;
begin
  perform from counter c where c.id = set_value.counter_id for update;
  if not found then
    raise exception 'no counter with id %', set_value.counter_id using errcode = 'PT404';
  end if;
  select coalesce(sum(e.delta), 0) into was from event e where e.counter_id = set_value.counter_id;
  if was <> set_value.target then
    insert into event (counter_id, delta, note)
    values (set_value.counter_id, set_value.target - was, set_value.note);
  end if;
  select * into result from counter_value v where v.id = set_value.counter_id;
  return result;
end
$$;

create function reset_value(counter_id bigint, note text default '') returns counter_value
  language sql volatile
  set search_path = heycarl_plugin_counters, pg_temp
  as $$ select set_value(reset_value.counter_id, 0, reset_value.note) $$;

-- Deletes the newest event, which goes to the trash. Null when there is none.
create function undo_last(counter_id bigint) returns event
  language plpgsql volatile
  set search_path = heycarl_plugin_counters, pg_temp
  as $$
declare
  removed event;
begin
  perform from counter c where c.id = undo_last.counter_id for update;
  if not found then
    raise exception 'no counter with id %', undo_last.counter_id using errcode = 'PT404';
  end if;
  delete from event e
   where e.id = (select n.id from event n where n.counter_id = undo_last.counter_id
                  order by n.id desc limit 1)
  returning e.* into removed;
  return removed;
end
$$;

-- One row per day from `since` to `until`, days without events included.
-- Days are in `zone`; both ends default to the last 30 days there.
create function daily_totals(
  counter_id bigint,
  since date default null,
  until date default null,
  zone text default 'UTC'
) returns table (day date, total bigint, added bigint, removed bigint, events bigint)
  language plpgsql stable
  set search_path = heycarl_plugin_counters, pg_temp
  as $$
#variable_conflict use_column
declare
  last_day date := coalesce(daily_totals.until, (now() at time zone daily_totals.zone)::date);
  first_day date := coalesce(daily_totals.since, last_day - 29);
begin
  if not exists (select from counter c where c.id = daily_totals.counter_id) then
    raise exception 'no counter with id %', daily_totals.counter_id using errcode = 'PT404';
  end if;
  if last_day < first_day or last_day - first_day > 3660 then
    raise exception 'the range must be 1 to 3661 days' using errcode = '22023';
  end if;
  return query
    select d.day,
           coalesce(sum(e.delta), 0)::bigint,
           coalesce(sum(e.delta) filter (where e.delta > 0), 0)::bigint,
           coalesce(-sum(e.delta) filter (where e.delta < 0), 0)::bigint,
           count(e.id)
      from (select g::date as day
              from generate_series(first_day, last_day, interval '1 day') g) d
      left join event e
        on e.counter_id = daily_totals.counter_id
       and e.at >= d.day::timestamp at time zone daily_totals.zone
       and e.at < (d.day + 1)::timestamp at time zone daily_totals.zone
     group by d.day
     order by d.day;
end
$$;

-- The sum of the events at or after `since`; only the additions when
-- `positive_only`, as for "how many coffees since Monday".
create function count_since(counter_id bigint, since timestamptz, positive_only boolean default false)
  returns table (total bigint, events bigint)
  language plpgsql stable
  set search_path = heycarl_plugin_counters, pg_temp
  as $$
#variable_conflict use_column
begin
  if not exists (select from counter c where c.id = count_since.counter_id) then
    raise exception 'no counter with id %', count_since.counter_id using errcode = 'PT404';
  end if;
  return query
    select coalesce(sum(e.delta), 0)::bigint, count(*)
      from event e
     where e.counter_id = count_since.counter_id
       and e.at >= count_since.since
       and (e.delta > 0 or not count_since.positive_only);
end
$$;

revoke execute on function bump, set_value, reset_value, undo_last, daily_totals, count_since
  from public;
grant execute on function bump, set_value, reset_value, undo_last, daily_totals, count_since
  to heycarl_user;

-- History and trash (DESIGN.md). Deleting a counter cascades to its
-- events in one transaction, so they are one trash entry.
select heycarl_sdk.track('counter');
select heycarl_sdk.track('event');
select heycarl_sdk.entry_route('counter', '/p/counters/heycarl-counters#/counter/{id}');
