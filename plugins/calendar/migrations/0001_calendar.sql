-- Runs with search_path set to this plugin's schema.
--
-- A read-only copy of a person's Google Calendar (DESIGN.md "Calendar").
-- A person reads it under row-level security and writes nothing: only the
-- kernel writes, as heycarl_worker, through the functions at the end, and
-- each of them names the owner. Google text is truncated by the kernel, not
-- checked here: one long description must not stop the sync for good.

create table account (
  owner_sub text primary key default heycarl_sdk.owner(),
  -- The primary calendar's id, which is the Google account's address.
  google_account text not null default '',
  connected_at timestamptz not null default now(),
  last_sync_at timestamptz,
  last_error text,
  needs_reconnect boolean not null default false
);

-- The refresh token, sealed with AES-GCM by the kernel under a key the
-- database never sees: nonce || ciphertext, with the owner as associated
-- data. No API role may read this table; the worker gets it through
-- refresh_token() only.
create table heycarl_private.calendar_google_token (
  owner_sub text primary key references account (owner_sub) on delete cascade,
  sealed bytea not null,
  scope text not null,
  updated_at timestamptz not null default now()
);

create table calendar (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner()
    references account (owner_sub) on delete cascade,
  google_id text not null,
  name text not null default '',
  color text not null default '',
  time_zone text not null default 'UTC',
  is_primary boolean not null default false,
  selected boolean not null default false,
  unique (owner_sub, google_id),
  unique (id, owner_sub)
);

-- Kept apart from calendar so that a person's select=* never meets a
-- column they may not read.
create table calendar_sync (
  calendar_id bigint primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  sync_token text,
  window_start timestamptz,
  window_end timestamptz,
  full_sync_at timestamptz,
  foreign key (calendar_id, owner_sub) references calendar (id, owner_sub) on delete cascade
);

create table event (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  calendar_id bigint not null,
  google_id text not null,
  recurring_event_id text,
  status text not null default 'confirmed',
  title text not null default '',
  description text not null default '',
  location text not null default '',
  all_day boolean not null default false,
  -- For an all-day event, midnight in the calendar's zone.
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  -- All-day events only. end_date is exclusive, as Google sends it.
  start_date date,
  end_date date,
  attendees text[] not null default '{}',
  organizer text not null default '',
  html_link text not null default '',
  updated_at timestamptz,
  unique (calendar_id, google_id),
  foreign key (calendar_id, owner_sub) references calendar (id, owner_sub) on delete cascade
);
create index event_owner_starts on event (owner_sub, starts_at);
create index event_recurring on event (calendar_id, recurring_event_id);
create index event_attendees on event using gin (attendees);

alter table account enable row level security;
alter table calendar enable row level security;
alter table calendar_sync enable row level security;
alter table event enable row level security;
alter table heycarl_private.calendar_google_token enable row level security;
create policy own on account to heycarl_user using (owner_sub = heycarl_sdk.sub());
create policy own on calendar to heycarl_user using (owner_sub = heycarl_sdk.sub());
create policy own on event to heycarl_user using (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_calendar to heycarl_user, heycarl_worker;
grant select on account, calendar, event to heycarl_user;

create view agenda with (security_invoker = true) as
  select e.id, e.calendar_id, c.name as calendar, c.color, e.google_id,
         e.recurring_event_id, e.status, e.title, e.description, e.location,
         e.all_day, e.starts_at, e.ends_at, e.start_date, e.end_date,
         e.attendees, e.organizer, e.html_link, e.updated_at
    from event e
    join calendar c on c.id = e.calendar_id and c.owner_sub = e.owner_sub;
grant select on agenda to heycarl_user;

-- For the person, as the person: row-level security holds inside.

-- Events that overlap [since, until), whose text holds `q` when given.
create function search(q text default null, since timestamptz default now(),
                       until timestamptz default now() + interval '30 days')
  returns setof agenda
  language sql stable
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    select * from agenda a
     where a.ends_at > search.since and a.starts_at < search.until
       and (search.q is null or search.q = ''
            or concat_ws(' ', a.title, a.description, a.location, a.calendar)
               ilike '%' || replace(replace(replace(search.q, '\', '\\'), '%', '\%'), '_', '\_') || '%')
     order by a.starts_at, a.id
     limit 500
  $$;

-- The people in the contact book whose email is an attendee of the event.
-- Empty without the CRM plugin.
create function event_people(event_id bigint)
  returns table (contact_id bigint, first_name text, last_name text, email text)
  language plpgsql stable
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
#variable_conflict use_column
begin
  if to_regclass('heycarl_plugin_crm.contact_field') is null then
    return;
  end if;
  return query
    select distinct c.id, c.first_name, c.last_name, lower(f.value)
      from event e
      join heycarl_plugin_crm.contact_field f
        on f.kind = 'email' and lower(f.value) = any (e.attendees)
      join heycarl_plugin_crm.contact c on c.id = f.contact_id
     where e.id = event_people.event_id
     order by c.first_name, c.last_name, c.id;
end
$$;

revoke execute on function search, event_people from public;
grant execute on function search, event_people to heycarl_user;

-- For the kernel, as heycarl_worker. Definer: the worker holds no grant on
-- the tables, and every function takes the owner as an argument.

create function connect_account(owner text, google_account text, sealed text, scope text)
  returns void
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
#variable_conflict use_column
begin
  insert into account as a (owner_sub, google_account)
  values (connect_account.owner, connect_account.google_account)
  on conflict (owner_sub) do update
    set google_account = coalesce(nullif(excluded.google_account, ''), a.google_account),
        connected_at = now(),
        last_error = null, needs_reconnect = false;
  insert into heycarl_private.calendar_google_token as t (owner_sub, sealed, scope)
  values (connect_account.owner, decode(connect_account.sealed, 'base64'), connect_account.scope)
  on conflict (owner_sub) do update
    set sealed = excluded.sealed, scope = excluded.scope, updated_at = now();
end
$$;

-- The sealed refresh token, base64, or null when the owner has none.
create function refresh_token(owner text) returns text
  language sql stable security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    select encode(t.sealed, 'base64') from heycarl_private.calendar_google_token t
     where t.owner_sub = refresh_token.owner
  $$;

-- Everyone the sync job visits: connected, and not waiting to reconnect.
create function owners() returns setof text
  language sql stable security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    select a.owner_sub from account a
      join heycarl_private.calendar_google_token t using (owner_sub)
     where not a.needs_reconnect
     order by a.owner_sub
  $$;

create function set_status(owner text, error text default null,
                           needs_reconnect boolean default false)
  returns void
  language sql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    update account a
       set last_error = left(set_status.error, 1000),
           needs_reconnect = set_status.needs_reconnect,
           last_sync_at = case when set_status.error is null then now() else a.last_sync_at end
     where a.owner_sub = set_status.owner
  $$;

-- Deletes the token, calendars and events. True when there was an account.
create function disconnect(owner text) returns boolean
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
#variable_conflict use_column
begin
  delete from account a where a.owner_sub = disconnect.owner;
  return found;
end
$$;

-- The calendar list as Google has it now. A calendar gone from it goes
-- with its events; a new one starts selected only when it is the primary.
create function put_calendars(owner text, calendars jsonb) returns void
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
#variable_conflict use_column
begin
  if not exists (select from account a where a.owner_sub = put_calendars.owner) then
    raise exception 'no account' using errcode = 'PT404';
  end if;
  insert into calendar as c (owner_sub, google_id, name, color, time_zone, is_primary, selected)
  select put_calendars.owner, x.google_id, left(coalesce(x.name, ''), 500),
         left(coalesce(x.color, ''), 20), coalesce(x.time_zone, 'UTC'),
         coalesce(x.is_primary, false), coalesce(x.is_primary, false)
    from jsonb_to_recordset(put_calendars.calendars)
      as x(google_id text, name text, color text, time_zone text, is_primary boolean)
  on conflict (owner_sub, google_id) do update
    set name = excluded.name, color = excluded.color,
        time_zone = excluded.time_zone, is_primary = excluded.is_primary;
  delete from calendar c
   where c.owner_sub = put_calendars.owner
     and c.google_id not in (select x ->> 'google_id' from jsonb_array_elements(put_calendars.calendars) x);
end
$$;

-- Turning a calendar off drops its events and its sync state; turning it
-- on makes the next sync a full one. False when it is not the owner's.
create function select_calendar(owner text, calendar_id bigint, selected boolean)
  returns boolean
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
#variable_conflict use_column
begin
  update calendar c set selected = select_calendar.selected
   where c.id = select_calendar.calendar_id and c.owner_sub = select_calendar.owner;
  if not found then
    return false;
  end if;
  if not select_calendar.selected then
    delete from event e where e.calendar_id = select_calendar.calendar_id;
    delete from calendar_sync s where s.calendar_id = select_calendar.calendar_id;
  end if;
  return true;
end
$$;

create function sync_state(owner text)
  returns table (calendar_id bigint, google_id text, time_zone text, sync_token text,
                 window_start timestamptz, window_end timestamptz, full_sync_at timestamptz)
  language sql stable security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    select c.id, c.google_id, c.time_zone, s.sync_token, s.window_start, s.window_end,
           s.full_sync_at
      from calendar c left join calendar_sync s on s.calendar_id = c.id
     where c.owner_sub = sync_state.owner and c.selected
     order by c.id
  $$;

-- One sync of one calendar, in one transaction: upsert `events` by their
-- Google id, so an event keeps its id and its links across a full resync;
-- delete `removed` (cancelled, or moved out of the window) and their
-- recurring instances; on a full sync, delete what Google no longer sent;
-- drop what fell out of the window. Returns the events cached.
create function apply_events(
  owner text, calendar_id bigint, full_sync boolean, events jsonb, removed text[],
  sync_token text, window_start timestamptz, window_end timestamptz
) returns integer
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
#variable_conflict use_column
declare
  zone text;
  kept integer;
begin
  select c.time_zone into zone from calendar c
   where c.id = apply_events.calendar_id and c.owner_sub = apply_events.owner and c.selected;
  if not found then
    raise exception 'no selected calendar %', apply_events.calendar_id using errcode = 'PT404';
  end if;
  if not exists (select from pg_timezone_names where name = zone) then
    zone := 'UTC';
  end if;

  insert into event as e (owner_sub, calendar_id, google_id, recurring_event_id, status,
                          title, description, location, all_day, starts_at, ends_at,
                          start_date, end_date, attendees, organizer, html_link, updated_at)
  select apply_events.owner, apply_events.calendar_id, x.google_id, x.recurring_event_id,
         coalesce(x.status, 'confirmed'), coalesce(x.title, ''), coalesce(x.description, ''),
         coalesce(x.location, ''), x.start_date is not null,
         coalesce(x.starts_at, x.start_date::timestamp at time zone zone),
         coalesce(x.ends_at, x.end_date::timestamp at time zone zone),
         x.start_date, x.end_date, coalesce(x.attendees, '{}'), coalesce(x.organizer, ''),
         coalesce(x.html_link, ''), x.updated_at
    from jsonb_to_recordset(apply_events.events) as x(
           google_id text, recurring_event_id text, status text, title text,
           description text, location text, starts_at timestamptz, ends_at timestamptz,
           start_date date, end_date date, attendees text[], organizer text,
           html_link text, updated_at timestamptz)
  on conflict (calendar_id, google_id) do update
    set recurring_event_id = excluded.recurring_event_id, status = excluded.status,
        title = excluded.title, description = excluded.description,
        location = excluded.location, all_day = excluded.all_day,
        starts_at = excluded.starts_at, ends_at = excluded.ends_at,
        start_date = excluded.start_date, end_date = excluded.end_date,
        attendees = excluded.attendees, organizer = excluded.organizer,
        html_link = excluded.html_link, updated_at = excluded.updated_at;

  delete from event e
   where e.calendar_id = apply_events.calendar_id
     and (e.google_id = any (apply_events.removed)
          or e.recurring_event_id = any (apply_events.removed)
          or e.ends_at <= apply_events.window_start
          or e.starts_at >= apply_events.window_end
          or (apply_events.full_sync and e.google_id not in (
                select x ->> 'google_id' from jsonb_array_elements(apply_events.events) x)));

  insert into calendar_sync as s (calendar_id, owner_sub, sync_token, window_start,
                                  window_end, full_sync_at)
  values (apply_events.calendar_id, apply_events.owner, apply_events.sync_token,
          apply_events.window_start, apply_events.window_end,
          case when apply_events.full_sync then now() end)
  on conflict on constraint calendar_sync_pkey do update
    set sync_token = excluded.sync_token, window_start = excluded.window_start,
        window_end = excluded.window_end,
        full_sync_at = coalesce(excluded.full_sync_at, s.full_sync_at);

  select count(*) into kept from event e where e.calendar_id = apply_events.calendar_id;
  return kept;
end
$$;

revoke execute on function connect_account, refresh_token, owners, set_status, disconnect,
  put_calendars, select_calendar, sync_state, apply_events from public;
grant execute on function connect_account, refresh_token, owners, set_status, disconnect,
  put_calendars, select_calendar, sync_state, apply_events to heycarl_worker;

-- Tracked so that an event can be linked (DESIGN.md "Links"). Its trigger
-- goes at once: Google owns these rows, so they have no history and no
-- trash, and restore or undo can never write into the cache.
select heycarl_sdk.track('event');
drop trigger heycarl_version on event;
select heycarl_sdk.entry_route('event', '/p/calendar/heycarl-calendar#/event/{id}');
