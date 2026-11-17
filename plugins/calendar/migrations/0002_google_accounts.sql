-- Runs with search_path set to this plugin's schema.
--
-- The Google grant moves to the kernel's Google accounts (DESIGN.md "Google
-- accounts"). Each person's account and sealed token move as they are: the
-- seal's associated data is the owner, so the kernel opens it unchanged.
-- Calendars and events keep their ids, and so their links. The account has
-- no Google sub yet; the next connect with the same email adopts it.

with moved as (
  insert into heycarl_private.google_account
    (owner_sub, email, scopes, connected_at, needs_reconnect)
  select a.owner_sub, lower(a.google_account), string_to_array(t.scope, ' '),
         a.connected_at, a.needs_reconnect
    from account a
    join heycarl_private.calendar_google_token t using (owner_sub)
  returning id, owner_sub
)
insert into heycarl_private.google_token (account_id, sealed)
select m.id, t.sealed
  from moved m
  join heycarl_private.calendar_google_token t using (owner_sub);

create table sync_status (
  account_id bigint primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  last_sync_at timestamptz,
  last_error text,
  foreign key (account_id, owner_sub)
    references heycarl_private.google_account (id, owner_sub) on delete cascade
);
insert into sync_status (account_id, owner_sub, last_sync_at, last_error)
select g.id, g.owner_sub, a.last_sync_at, a.last_error
  from account a
  join heycarl_private.google_account g using (owner_sub);

alter table calendar add column account_id bigint;
update calendar c set account_id = g.id
  from heycarl_private.google_account g
 where g.owner_sub = c.owner_sub;
-- An account without a token had nothing to sync with.
delete from calendar where account_id is null;
alter table calendar alter column account_id set not null;
alter table calendar drop constraint calendar_owner_sub_fkey;
alter table calendar drop constraint calendar_owner_sub_google_id_key;
alter table calendar add unique (account_id, google_id);
alter table calendar add foreign key (account_id, owner_sub)
  references heycarl_private.google_account (id, owner_sub) on delete cascade;

drop function connect_account, refresh_token, owners, set_status, disconnect,
  put_calendars, sync_state;
drop table heycarl_private.calendar_google_token;
drop table account;

alter table sync_status enable row level security;
create policy own on sync_status to heycarl_user using (owner_sub = heycarl_sdk.sub());

-- The person's Google accounts as this plugin sees them.
create view account with (security_invoker = true) as
  select g.id, g.email as google_account,
         g.scopes @> array['https://www.googleapis.com/auth/calendar.readonly'] as granted,
         g.needs_reconnect, g.connected_at, s.last_sync_at,
         coalesce(g.last_error, s.last_error) as last_error
    from heycarl_private.google_account g
    left join sync_status s on s.account_id = g.id;
grant select on account, sync_status to heycarl_user;

-- For the kernel, as heycarl_worker, as before: definer, and each function
-- takes the owner.

create function set_status(owner text, account_id bigint, error text default null)
  returns void
  language sql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    insert into sync_status as s (account_id, owner_sub, last_sync_at, last_error)
    select g.id, g.owner_sub,
           case when set_status.error is null then now() end,
           left(set_status.error, 1000)
      from heycarl_private.google_account g
     where g.id = set_status.account_id and g.owner_sub = set_status.owner
    on conflict on constraint sync_status_pkey do update
      set last_error = excluded.last_error,
          last_sync_at = coalesce(excluded.last_sync_at, s.last_sync_at)
  $$;

-- The account's calendar list as Google has it now. A calendar gone from it
-- goes with its events; a new one starts selected only when it is the
-- primary.
create function put_calendars(owner text, account_id bigint, calendars jsonb)
  returns void
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
#variable_conflict use_column
begin
  if not exists (select from heycarl_private.google_account g
                  where g.id = put_calendars.account_id and g.owner_sub = put_calendars.owner) then
    raise exception 'no account' using errcode = 'PT404';
  end if;
  insert into calendar as c (owner_sub, account_id, google_id, name, color, time_zone,
                             is_primary, selected)
  select put_calendars.owner, put_calendars.account_id, x.google_id,
         left(coalesce(x.name, ''), 500), left(coalesce(x.color, ''), 20),
         coalesce(x.time_zone, 'UTC'), coalesce(x.is_primary, false),
         coalesce(x.is_primary, false)
    from jsonb_to_recordset(put_calendars.calendars)
      as x(google_id text, name text, color text, time_zone text, is_primary boolean)
  on conflict (account_id, google_id) do update
    set name = excluded.name, color = excluded.color,
        time_zone = excluded.time_zone, is_primary = excluded.is_primary;
  delete from calendar c
   where c.account_id = put_calendars.account_id
     and c.google_id not in (select x ->> 'google_id' from jsonb_array_elements(put_calendars.calendars) x);
end
$$;

create function sync_state(owner text, account_id bigint)
  returns table (calendar_id bigint, google_id text, time_zone text, sync_token text,
                 window_start timestamptz, window_end timestamptz, full_sync_at timestamptz)
  language sql stable security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    select c.id, c.google_id, c.time_zone, s.sync_token, s.window_start, s.window_end,
           s.full_sync_at
      from calendar c left join calendar_sync s on s.calendar_id = c.id
     where c.owner_sub = sync_state.owner and c.account_id = sync_state.account_id
       and c.selected
     order by c.id
  $$;

-- The account a calendar belongs to, or null when it is not the owner's.
create function calendar_account(owner text, calendar_id bigint) returns bigint
  language sql stable security definer
  set search_path = heycarl_plugin_calendar, pg_temp
  as $$
    select c.account_id from calendar c
     where c.id = calendar_account.calendar_id and c.owner_sub = calendar_account.owner
  $$;

revoke execute on function set_status, put_calendars, sync_state, calendar_account
  from public;
grant execute on function set_status, put_calendars, sync_state, calendar_account
  to heycarl_worker;
