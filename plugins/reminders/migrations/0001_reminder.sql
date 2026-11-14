-- Runs with search_path set to this plugin's schema.
create type delivery as enum ('pending', 'sending', 'sent', 'failed');

create table reminder (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  title text not null check (length(title) between 1 and 200),
  body text not null default '',
  due_at timestamptz not null,
  -- Route the notification opens; null opens the reminders page.
  url text check (url like '/%'),
  state delivery not null default 'pending',
  attempts integer not null default 0,
  claimed_at timestamptz,
  sent_at timestamptz,
  last_error text,
  created_at timestamptz not null default now()
);
create index reminder_due on reminder (due_at) where state in ('pending', 'sending');

-- Moving a reminder's time re-arms it, whatever happened to it before.
create function rearm() returns trigger
  language plpgsql
  as $$
begin
  if new.due_at is distinct from old.due_at then
    new.state := 'pending';
    new.attempts := 0;
    new.claimed_at := null;
    new.sent_at := null;
    new.last_error := null;
  end if;
  return new;
end
$$;
create trigger rearm before update on reminder for each row execute function rearm();

alter table reminder enable row level security;
create policy own on reminder to heycarl_user
  using (owner_sub = heycarl_sdk.sub())
  with check (owner_sub = heycarl_sdk.sub());
create policy deliver on reminder to heycarl_worker using (true);

grant usage on schema heycarl_plugin_reminders to heycarl_user, heycarl_worker;
grant usage on type delivery to heycarl_user, heycarl_worker;
grant select, delete on reminder to heycarl_user;
grant insert (title, body, due_at, url), update (title, body, due_at, url) on reminder to heycarl_user;
grant select, update (state, attempts, claimed_at, sent_at, last_error) on reminder to heycarl_worker;

-- When a pending reminder may next be tried: its time, or after a failed
-- attempt one minute per attempt later, so a phone with no subscription
-- does not burn every attempt in one second.
create function ready_at(r reminder) returns timestamptz
  language sql immutable
  as $$
    select greatest(r.due_at, coalesce(r.claimed_at + r.attempts * interval '1 minute', r.due_at))
  $$;

-- Due reminders for one delivery pass, marked as taken. A claim older than
-- five minutes is a kernel that died mid-delivery, so it is taken again:
-- delivery is at least once. SKIP LOCKED keeps two passes off the same row.
create function claim_due(max_rows integer default 20) returns setof reminder
  language sql volatile
  set search_path = heycarl_plugin_reminders, pg_temp
  as $$
    update reminder r
       set state = 'sending', claimed_at = now(), attempts = r.attempts + 1
     where r.id in (
       select id from reminder c
        where (c.state = 'pending' and ready_at(c) <= now())
           or (c.state = 'sending' and c.claimed_at < now() - interval '5 minutes')
        order by c.due_at
        for update skip locked
        limit max_rows)
    returning r.*
  $$;
revoke execute on function claim_due from public;
grant execute on function claim_due to heycarl_worker;

-- When the next pass is due: the earliest pending reminder, if any.
create function next_due() returns timestamptz
  language sql stable
  set search_path = heycarl_plugin_reminders, pg_temp
  as $$ select min(ready_at(r)) from reminder r where state = 'pending' $$;
revoke execute on function next_due from public;
grant execute on function next_due to heycarl_worker;
