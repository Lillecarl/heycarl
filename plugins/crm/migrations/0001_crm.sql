-- Runs with search_path set to this plugin's schema.
--
-- Every child table references its contact through (contact_id, owner_sub),
-- and row-level security pins owner_sub to the caller. So a row can point
-- only at a contact of the same owner, even though a foreign key check
-- itself ignores row-level security.

create table contact (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  first_name text not null check (length(first_name) between 1 and 100),
  last_name text not null default '' check (length(last_name) <= 100),
  nickname text not null default '' check (length(nickname) <= 100),
  company text not null default '' check (length(company) <= 200),
  job_title text not null default '' check (length(job_title) <= 200),
  description text not null default '' check (length(description) <= 10000),
  favorite boolean not null default false,
  archived boolean not null default false,
  stay_in_touch_days integer check (stay_in_touch_days > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, owner_sub)
);

create function touch() returns trigger
  language plpgsql
  as $$
begin
  new.updated_at := now();
  return new;
end
$$;
create trigger touch before update on contact for each row execute function touch();

create type field_kind as enum ('email', 'phone', 'address', 'website', 'social', 'other');

create table contact_field (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  contact_id bigint not null,
  kind field_kind not null,
  label text not null default '' check (length(label) <= 50),
  value text not null check (length(value) between 1 and 500),
  foreign key (contact_id, owner_sub) references contact (id, owner_sub) on delete cascade
);
create index contact_field_contact on contact_field (contact_id);

-- Shared by everyone. `relationship.other_id` is the `kind` of
-- `relationship.contact_id`; seen from the other side it is `reverse`.
create table relationship_kind (
  name text primary key,
  reverse text not null references relationship_kind (name),
  sort integer not null
);
insert into relationship_kind (name, reverse, sort) values
  ('partner', 'partner', 1),
  ('spouse', 'spouse', 2),
  ('ex-partner', 'ex-partner', 3),
  ('parent', 'child', 4),
  ('child', 'parent', 5),
  ('sibling', 'sibling', 6),
  ('grandparent', 'grandchild', 7),
  ('grandchild', 'grandparent', 8),
  ('relative', 'relative', 9),
  ('friend', 'friend', 10),
  ('best friend', 'best friend', 11),
  ('colleague', 'colleague', 12),
  ('manager', 'report', 13),
  ('report', 'manager', 14),
  ('mentor', 'mentee', 15),
  ('mentee', 'mentor', 16);

create table relationship (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  contact_id bigint not null,
  other_id bigint not null,
  kind text not null references relationship_kind (name),
  check (contact_id <> other_id),
  foreign key (contact_id, owner_sub) references contact (id, owner_sub) on delete cascade,
  foreign key (other_id, owner_sub) references contact (id, owner_sub) on delete cascade
);
create index relationship_contact on relationship (contact_id);
create index relationship_other on relationship (other_id);

-- Each relationship once from each side, with the other person's name.
create view relation with (security_invoker = true) as
  select e.relationship_id, e.contact_id, e.other_id, e.kind,
         o.first_name as other_first_name, o.last_name as other_last_name
    from (
      select r.id as relationship_id, r.contact_id, r.other_id, r.kind
        from relationship r
      union all
      select r.id, r.other_id, r.contact_id, k.reverse
        from relationship r
        join relationship_kind k on k.name = r.kind
    ) e
    join contact o on o.id = e.other_id;

create table note (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  contact_id bigint not null,
  body text not null check (length(body) between 1 and 20000),
  created_at timestamptz not null default now(),
  foreign key (contact_id, owner_sub) references contact (id, owner_sub) on delete cascade
);
create index note_contact on note (contact_id);

create type activity_kind as enum ('call', 'message', 'meeting', 'meal', 'visit', 'other');

create table activity (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  kind activity_kind not null default 'other',
  title text not null check (length(title) between 1 and 200),
  description text not null default '' check (length(description) <= 20000),
  happened_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (id, owner_sub)
);

-- The primary key holds every foreign key column: PostgREST then sees a
-- many-to-many join, and `contact?select=activity(*)` embeds through it.
create table activity_contact (
  owner_sub text not null default heycarl_sdk.owner(),
  activity_id bigint not null,
  contact_id bigint not null,
  primary key (activity_id, contact_id, owner_sub),
  foreign key (activity_id, owner_sub) references activity (id, owner_sub) on delete cascade,
  foreign key (contact_id, owner_sub) references contact (id, owner_sub) on delete cascade
);
create index activity_contact_contact on activity_contact (contact_id);

create table tag (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  name text not null check (length(name) between 1 and 50),
  unique (owner_sub, name),
  unique (id, owner_sub)
);

create table contact_tag (
  owner_sub text not null default heycarl_sdk.owner(),
  contact_id bigint not null,
  tag_id bigint not null,
  primary key (contact_id, tag_id, owner_sub),
  foreign key (contact_id, owner_sub) references contact (id, owner_sub) on delete cascade,
  foreign key (tag_id, owner_sub) references tag (id, owner_sub) on delete cascade
);
create index contact_tag_tag on contact_tag (tag_id);

create type gift_state as enum ('idea', 'bought', 'given', 'received');

create table gift (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  contact_id bigint not null,
  title text not null check (length(title) between 1 and 200),
  url text not null default '' check (url = '' or url ~ '^https?://'),
  state gift_state not null default 'idea',
  note text not null default '' check (length(note) <= 2000),
  created_at timestamptz not null default now(),
  foreign key (contact_id, owner_sub) references contact (id, owner_sub) on delete cascade
);
create index gift_contact on gift (contact_id);

create function is_timezone(zone text) returns boolean
  language sql stable
  as $$ select exists (select 1 from pg_timezone_names where name = zone) $$;

-- The frontend writes the browser's zone here. Dates and reminder times
-- are in this zone; with no row, UTC at 09:00.
create table preference (
  owner_sub text primary key default heycarl_sdk.owner(),
  timezone text not null default 'UTC' check (is_timezone(timezone)),
  remind_time time not null default '09:00'
);

create type date_kind as enum ('birthday', 'anniversary', 'memorial', 'other');

create table important_date (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  contact_id bigint not null,
  kind date_kind not null default 'birthday',
  label text not null default '' check (length(label) <= 100),
  month integer not null check (month between 1 and 12),
  day integer not null,
  -- Null when unknown, as for a birthday without the year.
  year integer check (year between 1800 and 2200),
  remind boolean not null default true,
  -- The occurrence that reminder_id covers. Set only by sync_date.
  reminded_for date,
  -- A row in the reminders plugin. No foreign key: that plugin is optional.
  reminder_id bigint,
  created_at timestamptz not null default now(),
  check (day between 1 and case when month = 2 then 29 when month in (4, 6, 9, 11) then 30 else 31 end),
  check (year is null or month <> 2 or day <> 29
         or (year % 4 = 0 and (year % 100 <> 0 or year % 400 = 0))),
  foreign key (contact_id, owner_sub) references contact (id, owner_sub) on delete cascade
);
create index important_date_contact on important_date (contact_id);

-- Row-level security and grants.

alter table contact enable row level security;
alter table contact_field enable row level security;
alter table relationship enable row level security;
alter table note enable row level security;
alter table activity enable row level security;
alter table activity_contact enable row level security;
alter table tag enable row level security;
alter table contact_tag enable row level security;
alter table gift enable row level security;
alter table preference enable row level security;
alter table important_date enable row level security;

create policy own on contact to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on contact_field to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on relationship to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on note to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on activity to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on activity_contact to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on tag to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on contact_tag to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on gift to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on preference to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());
create policy own on important_date to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_crm to heycarl_user, heycarl_worker;
grant usage on type field_kind, activity_kind, gift_state, date_kind to heycarl_user;

grant select, delete on contact to heycarl_user;
grant insert (first_name, last_name, nickname, company, job_title, description, favorite, archived, stay_in_touch_days),
      update (first_name, last_name, nickname, company, job_title, description, favorite, archived, stay_in_touch_days)
  on contact to heycarl_user;

grant select, delete on contact_field to heycarl_user;
grant insert (contact_id, kind, label, value), update (kind, label, value) on contact_field to heycarl_user;

grant select on relationship_kind to heycarl_user;
grant select, delete on relationship to heycarl_user;
grant insert (contact_id, other_id, kind), update (kind) on relationship to heycarl_user;
grant select on relation to heycarl_user;

grant select, delete on note to heycarl_user;
grant insert (contact_id, body), update (body) on note to heycarl_user;

grant select, delete on activity to heycarl_user;
grant insert (kind, title, description, happened_at), update (kind, title, description, happened_at)
  on activity to heycarl_user;
grant select, delete on activity_contact to heycarl_user;
grant insert (activity_id, contact_id) on activity_contact to heycarl_user;

grant select, delete on tag to heycarl_user;
grant insert (name), update (name) on tag to heycarl_user;
grant select, delete on contact_tag to heycarl_user;
grant insert (contact_id, tag_id) on contact_tag to heycarl_user;

grant select, delete on gift to heycarl_user;
grant insert (contact_id, title, url, state, note), update (title, url, state, note) on gift to heycarl_user;

grant select on preference to heycarl_user;
grant insert (timezone, remind_time), update (timezone, remind_time) on preference to heycarl_user;

grant select, delete on important_date to heycarl_user;
grant insert (contact_id, kind, label, month, day, year, remind),
      update (kind, label, month, day, year, remind)
  on important_date to heycarl_user;

-- Computed columns: PostgREST selects, filters and orders by these as if
-- they were columns, e.g. `contact?select=*,last_contacted`.

create function last_contacted(c contact) returns timestamptz
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
    select max(a.happened_at)
      from activity a
      join activity_contact ac on ac.activity_id = a.id
     where ac.contact_id = c.id
  $$;

-- When to get in touch next, for a contact with stay_in_touch_days.
create function touch_due(c contact) returns date
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
    select (coalesce(last_contacted(c), c.created_at)
            + make_interval(days => c.stay_in_touch_days))::date
     where c.stay_in_touch_days is not null
  $$;

create function owner_zone(owner text) returns text
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$ select coalesce((select timezone from preference where owner_sub = owner), 'UTC') $$;

-- 29 February falls on the 28th in a common year.
create function occurrence(m integer, d integer, y integer) returns date
  language sql immutable
  as $$
    select case
      when m = 2 and d = 29 and not (y % 4 = 0 and (y % 100 <> 0 or y % 400 = 0))
        then make_date(y, 2, 28)
      else make_date(y, m, d)
    end
  $$;

create function next_on_or_after(m integer, d integer, since date) returns date
  language sql immutable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
    select case
      when occurrence(m, d, extract(year from since)::integer) >= since
        then occurrence(m, d, extract(year from since)::integer)
      else occurrence(m, d, extract(year from since)::integer + 1)
    end
  $$;

-- Today or later, in the owner's zone.
create function next_occurrence(d important_date) returns date
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
    select next_on_or_after(d.month, d.day, (now() at time zone owner_zone(d.owner_sub))::date)
  $$;

create function days_until(d important_date) returns integer
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
    select next_occurrence(d) - (now() at time zone owner_zone(d.owner_sub))::date
  $$;

-- The age or the anniversary count at the next occurrence; null without a year.
create function turns(d important_date) returns integer
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$ select extract(year from next_occurrence(d))::integer - d.year $$;

-- A computed relationship: `contact?select=*,relations(*)`.
create function relations(c contact) returns setof relation
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$ select * from relation where contact_id = c.id $$;

-- One contact's history, newest first through `order=at.desc`.
create view timeline with (security_invoker = true) as
  select n.contact_id, 'note' as kind, n.id as ref_id, n.created_at as at,
         '' as title, n.body
    from note n
  union all
  select ac.contact_id, 'activity:' || a.kind, a.id, a.happened_at, a.title, a.description
    from activity a
    join activity_contact ac on ac.activity_id = a.id
  union all
  select g.contact_id, 'gift:' || g.state, g.id, g.created_at, g.title, g.note
    from gift g;
grant select on timeline to heycarl_user;

-- RPC.

create function search(q text) returns setof contact
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
    with p as (
      select '%' || replace(replace(replace(q, '\', '\\'), '%', '\%'), '_', '\_') || '%' as pattern
    )
    select c.*
      from contact c, p
     where concat_ws(' ', c.first_name, c.last_name, c.nickname, c.company, c.job_title) ilike p.pattern
        or exists (
          select 1 from contact_field f
           where f.contact_id = c.id and f.value ilike p.pattern)
        or exists (
          select 1 from contact_tag ct join tag t on t.id = ct.tag_id
           where ct.contact_id = c.id and t.name ilike p.pattern)
     order by c.favorite desc, c.first_name, c.last_name
  $$;

-- An activity with everyone who took part, in one call.
create function log_activity(
  contact_ids bigint[],
  title text,
  kind activity_kind default 'other',
  description text default '',
  happened_at timestamptz default now()
) returns activity
  language plpgsql volatile
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
declare
  created activity;
begin
  insert into activity (kind, title, description, happened_at)
  values (log_activity.kind, log_activity.title, log_activity.description, log_activity.happened_at)
  returning * into created;
  insert into activity_contact (activity_id, contact_id)
  select created.id, unnest(log_activity.contact_ids);
  return created;
end
$$;

-- Reminders for important dates, in the reminders plugin.
--
-- These run as heycarl_owner, which owns the reminders table as well, so
-- they create reminders for any owner. They name the owner on each row.
-- Without the reminders plugin they do nothing.

-- Keeps one date's reminder in step with the date. Returns true when it
-- created a reminder or moved one to a new occurrence.
create function sync_date(d important_date) returns boolean
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
declare
  occ date;
  due timestamptz;
  who text;
  heading text;
  detail text;
  rid bigint;
begin
  if to_regclass('heycarl_plugin_reminders.reminder') is null then
    return false;
  end if;
  if not d.remind then
    delete from heycarl_plugin_reminders.reminder r
     where r.id = d.reminder_id and r.state = 'pending';
    update important_date set reminder_id = null, reminded_for = null
     where id = d.id and reminder_id is not null;
    return false;
  end if;

  select trim(c.first_name || ' ' || c.last_name) into who
    from contact c where c.id = d.contact_id;
  occ := next_occurrence(d);
  due := (occ + coalesce((select remind_time from preference where owner_sub = d.owner_sub), '09:00'))
         at time zone owner_zone(d.owner_sub);
  heading := left(case d.kind
    when 'birthday' then who || '''s birthday'
                         || coalesce(' (' || (extract(year from occ)::integer - d.year) || ')', '')
    when 'anniversary' then who || ': anniversary'
                            || coalesce(' (' || (extract(year from occ)::integer - d.year) || ')', '')
    else who || ': ' || coalesce(nullif(d.label, ''), d.kind::text)
  end, 200);
  detail := d.label;

  -- A deleted reminder stays deleted until the next occurrence.
  if d.reminded_for is not distinct from occ then
    update heycarl_plugin_reminders.reminder r
       set due_at = due, title = heading, body = detail
     where r.id = d.reminder_id and r.state = 'pending'
       and (r.due_at, r.title, r.body) is distinct from (due, heading, detail);
    return false;
  end if;

  update heycarl_plugin_reminders.reminder r
     set due_at = due, title = heading, body = detail
   where r.id = d.reminder_id and r.state = 'pending'
  returning r.id into rid;
  if rid is null then
    insert into heycarl_plugin_reminders.reminder (owner_sub, title, body, due_at, url)
    values (d.owner_sub, heading, detail, due, '/p/crm/heycarl-crm#/contact/' || d.contact_id)
    returning id into rid;
  end if;
  update important_date set reminder_id = rid, reminded_for = occ where id = d.id;
  return true;
end
$$;
revoke execute on function sync_date from public;

create function important_date_changed() returns trigger
  language plpgsql security definer
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
begin
  if tg_op = 'DELETE' then
    if to_regclass('heycarl_plugin_reminders.reminder') is not null then
      delete from heycarl_plugin_reminders.reminder r
       where r.id = old.reminder_id and r.state = 'pending';
    end if;
  else
    perform sync_date(new);
  end if;
  return null;
end
$$;
-- Not on reminder_id or reminded_for: sync_date writes those itself.
create trigger sync_reminder
  after insert or delete or update of contact_id, kind, label, month, day, year, remind
  on important_date for each row execute function important_date_changed();

create function contact_renamed() returns trigger
  language plpgsql security definer
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
begin
  perform sync_date(d) from important_date d where d.contact_id = new.id;
  return null;
end
$$;
create trigger sync_reminders
  after update of first_name, last_name on contact
  for each row execute function contact_renamed();

create function preference_changed() returns trigger
  language plpgsql security definer
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
begin
  if tg_op = 'INSERT' or (old.timezone, old.remind_time) is distinct from (new.timezone, new.remind_time) then
    perform sync_date(d) from important_date d where d.owner_sub = new.owner_sub;
  end if;
  return null;
end
$$;
create trigger sync_reminders
  after insert or update on preference
  for each row execute function preference_changed();

-- Called by the kernel job: a date that has passed gets next year's reminder.
create function sync_reminders() returns integer
  language sql volatile security definer
  set search_path = heycarl_plugin_crm, pg_temp
  as $$ select count(*)::integer from important_date d where d.remind and sync_date(d) $$;
revoke execute on function sync_reminders from public;
grant execute on function sync_reminders to heycarl_worker;
