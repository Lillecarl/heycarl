-- Runs with search_path set to this plugin's schema.
--
-- A read-only copy of each connected Google account's contacts, and the
-- person's own links between those and the contact book (DESIGN.md "Google
-- Contacts"). Only the kernel writes the copy, as heycarl_worker, through
-- the definer functions below. The person writes links under RLS, and
-- copies fields into the contact book as themselves, through the prm
-- plugin's own tables, so its history and undo hold. Google never hears of
-- any of it.

create table sync_state (
  account_id bigint primary key,
  owner_sub text not null,
  sync_token text,
  full_sync_at timestamptz,
  foreign key (account_id, owner_sub)
    references heycarl_private.google_account (id, owner_sub) on delete cascade
);

create table sync_status (
  account_id bigint primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  last_sync_at timestamptz,
  last_error text,
  foreign key (account_id, owner_sub)
    references heycarl_private.google_account (id, owner_sub) on delete cascade
);

-- One Google contact. Multi-valued fields are arrays of {value, label}.
-- The kernel truncates Google's text; no CHECK here, so one long value
-- cannot stop the sync.
create table person (
  id bigint generated always as identity primary key,
  owner_sub text not null,
  account_id bigint not null,
  -- people/{id}. It may change when Google links a contact and a profile;
  -- a sync carries the old name, and the row keeps its id and its link.
  resource_name text not null,
  display_name text not null default '',
  given_name text not null default '',
  family_name text not null default '',
  nickname text not null default '',
  company text not null default '',
  job_title text not null default '',
  biography text not null default '',
  birthday_year integer,
  birthday_month integer,
  birthday_day integer,
  emails jsonb not null default '[]',
  phones jsonb not null default '[]',
  addresses jsonb not null default '[]',
  urls jsonb not null default '[]',
  updated_at timestamptz not null default now(),
  unique (account_id, resource_name),
  unique (id, owner_sub),
  foreign key (account_id, owner_sub)
    references heycarl_private.google_account (id, owner_sub) on delete cascade
);
create index person_owner on person (owner_sub);

-- The person's own link: this Google contact is that contact in the book.
-- A Google contact has at most one contact. A contact may have several
-- Google contacts: the same person in two accounts, or a duplicate in one.
-- The composite keys pin both ends to the owner.
create table match (
  person_id bigint primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  contact_id bigint not null,
  created_at timestamptz not null default now(),
  foreign key (person_id, owner_sub) references person (id, owner_sub) on delete cascade,
  foreign key (contact_id, owner_sub)
    references heycarl_plugin_prm.contact (id, owner_sub) on delete cascade
);
create index match_contact on match (contact_id);

alter table sync_state enable row level security;
alter table sync_status enable row level security;
alter table person enable row level security;
alter table match enable row level security;
create policy own on sync_status to heycarl_user using (owner_sub = heycarl_sdk.sub());
create policy own on person to heycarl_user using (owner_sub = heycarl_sdk.sub());
create policy own on match to heycarl_user
  using (owner_sub = heycarl_sdk.sub())
  with check (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_google_contacts to heycarl_user, heycarl_worker;
grant select on sync_status, person to heycarl_user;
grant select, delete, insert (person_id, contact_id) on match to heycarl_user;

-- The person's Google accounts as this plugin sees them.
create view account with (security_invoker = true) as
  select g.id, g.email as google_account,
         g.scopes @> array['https://www.googleapis.com/auth/contacts.readonly'] as granted,
         g.needs_reconnect, g.connected_at, s.last_sync_at,
         coalesce(g.last_error, s.last_error) as last_error
    from heycarl_private.google_account g
    left join sync_status s on s.account_id = g.id;

-- Each Google contact with the account it came from and its link.
create view entry with (security_invoker = true) as
  select p.id, p.account_id, g.email as google_account, p.display_name,
         p.given_name, p.family_name, p.nickname, p.company, p.job_title,
         p.biography, p.birthday_year, p.birthday_month, p.birthday_day,
         p.emails, p.phones, p.addresses, p.urls, p.updated_at,
         m.contact_id, c.first_name as contact_first_name,
         c.last_name as contact_last_name
    from person p
    join heycarl_private.google_account g on g.id = p.account_id
    left join match m on m.person_id = p.id
    left join heycarl_plugin_prm.contact c on c.id = m.contact_id;
grant select on account, entry to heycarl_user;

-- For the person, as the person: row-level security holds inside, on this
-- plugin's tables and on the contact book's.

-- Digits only, the last nine: "+46 70-123 45 67" and "070 123 45 67" meet.
create function phone_key(value text) returns text
  language sql immutable
  as $$
    select nullif(right(regexp_replace(value, '\D', '', 'g'), 9), '')
  $$;

create function name_key(first text, last text) returns text
  language sql immutable
  as $$
    select nullif(lower(trim(concat_ws(' ', nullif(trim(first), ''), nullif(trim(last), '')))), '')
  $$;

-- Likely links for Google contacts that have none: a contact in the book
-- with the same email, the same phone number or the same full name. Local
-- only: nothing here leaves the database.
create function suggestions()
  returns table (person_id bigint, contact_id bigint, first_name text, last_name text,
                 reason text)
  language sql stable
  set search_path = heycarl_plugin_google_contacts, pg_temp
  as $$
    with open as (
      select p.* from person p
       where not exists (select from match m where m.person_id = p.id)
    ),
    found as (
      select o.id as person_id, f.contact_id, 'email' as reason, 1 as rank
        from open o
        cross join lateral jsonb_array_elements(o.emails) e
        join heycarl_plugin_prm.contact_field f
          on f.kind = 'email' and lower(f.value) = lower(e ->> 'value')
      union all
      select o.id, f.contact_id, 'phone', 2
        from open o
        cross join lateral jsonb_array_elements(o.phones) e
        join heycarl_plugin_prm.contact_field f
          on f.kind = 'phone' and length(phone_key(f.value)) >= 7
         and phone_key(f.value) = phone_key(e ->> 'value')
      union all
      select o.id, c.id, 'name', 3
        from open o
        join heycarl_plugin_prm.contact c
          on name_key(c.first_name, c.last_name) = name_key(o.given_name, o.family_name)
    )
    select distinct on (x.person_id, x.contact_id)
           x.person_id, x.contact_id, c.first_name, c.last_name, x.reason
      from found x
      join heycarl_plugin_prm.contact c on c.id = x.contact_id
     where not c.archived
     order by x.person_id, x.contact_id, x.rank
  $$;

-- The field names `copy_fields` takes.
create function copyable() returns text[]
  language sql immutable
  as $$
    select array['first_name', 'last_name', 'nickname', 'company', 'job_title',
                 'description', 'emails', 'phones', 'addresses', 'urls', 'birthday']
  $$;

-- Copies `fields` of a linked Google contact into its contact. A name or
-- text field replaces the contact's when Google's is not empty. A
-- multi-valued field adds the values the contact lacks, compared without
-- case. A birthday is added when the contact has none on that day. Returns
-- the number of values written. Runs as the caller: prm's RLS, grants,
-- CHECKs and history all hold.
create function copy_fields(person_id bigint, fields text[]) returns integer
  language plpgsql volatile
  set search_path = heycarl_plugin_google_contacts, pg_temp
  as $$
declare
  p person;
  target bigint;
  was heycarl_plugin_prm.contact;
  now_is heycarl_plugin_prm.contact;
  written integer := 0;
  n integer;
  unknown text[];
begin
  unknown := array(select unnest(copy_fields.fields) except select unnest(copyable()));
  if cardinality(unknown) > 0 then
    raise exception 'unknown fields: %', array_to_string(unknown, ', ')
      using errcode = '22023';
  end if;
  select * into p from person x where x.id = copy_fields.person_id;
  select m.contact_id into target from match m where m.person_id = copy_fields.person_id;
  if p.id is null or target is null then
    raise exception 'no linked Google contact %', copy_fields.person_id using errcode = 'PT404';
  end if;

  select * into was from heycarl_plugin_prm.contact c where c.id = target;
  now_is := was;
  if 'first_name' = any (copy_fields.fields) and trim(p.given_name) <> '' then
    now_is.first_name := left(trim(p.given_name), 100);
  end if;
  if 'last_name' = any (copy_fields.fields) and p.family_name <> '' then
    now_is.last_name := left(p.family_name, 100);
  end if;
  if 'nickname' = any (copy_fields.fields) and p.nickname <> '' then
    now_is.nickname := left(p.nickname, 100);
  end if;
  if 'company' = any (copy_fields.fields) and p.company <> '' then
    now_is.company := left(p.company, 200);
  end if;
  if 'job_title' = any (copy_fields.fields) and p.job_title <> '' then
    now_is.job_title := left(p.job_title, 200);
  end if;
  if 'description' = any (copy_fields.fields) and p.biography <> '' then
    now_is.description := left(p.biography, 10000);
  end if;
  -- An unchanged row is not written, so it makes no version in history.
  if now_is is distinct from was then
    update heycarl_plugin_prm.contact c
       set first_name = now_is.first_name, last_name = now_is.last_name,
           nickname = now_is.nickname, company = now_is.company,
           job_title = now_is.job_title, description = now_is.description
     where c.id = target;
    written := written + 1;
  end if;

  insert into heycarl_plugin_prm.contact_field (contact_id, kind, label, value)
  select distinct on (kind, lower(value)) target, kind, label, value
    from (
      select k.kind::heycarl_plugin_prm.field_kind as kind,
             left(coalesce(e ->> 'label', ''), 50) as label,
             left(trim(e ->> 'value'), 500) as value
        from (values ('emails', 'email'), ('phones', 'phone'),
                     ('addresses', 'address'), ('urls', 'website')) as k(field, kind)
        cross join lateral jsonb_array_elements(
          case k.field when 'emails' then p.emails when 'phones' then p.phones
                       when 'addresses' then p.addresses else p.urls end) e
       where k.field = any (copy_fields.fields)
    ) v
   where v.value <> ''
     and not exists (
       select from heycarl_plugin_prm.contact_field f
        where f.contact_id = target and f.kind = v.kind and lower(f.value) = lower(v.value));
  get diagnostics n = row_count;
  written := written + n;

  if 'birthday' = any (copy_fields.fields) and p.birthday_month is not null
     and p.birthday_day is not null
     and not exists (
       select from heycarl_plugin_prm.important_date d
        where d.contact_id = target and d.kind = 'birthday'
          and d.month = p.birthday_month and d.day = p.birthday_day) then
    insert into heycarl_plugin_prm.important_date (contact_id, kind, month, day, year)
    values (target, 'birthday', p.birthday_month, p.birthday_day, p.birthday_year);
    written := written + 1;
  end if;
  return written;
end
$$;

-- A new contact in the book from a Google contact that has no link, with
-- every field copied, and the link. Returns the contact's id.
create function import_person(person_id bigint) returns bigint
  language plpgsql volatile
  set search_path = heycarl_plugin_google_contacts, pg_temp
  as $$
declare
  p person;
  made bigint;
begin
  select * into p from person x where x.id = import_person.person_id;
  if p.id is null then
    raise exception 'no Google contact %', import_person.person_id using errcode = 'PT404';
  end if;
  if exists (select from match m where m.person_id = import_person.person_id) then
    raise exception 'Google contact % is already linked', import_person.person_id
      using errcode = '23505';
  end if;
  insert into heycarl_plugin_prm.contact (first_name)
  values (left(coalesce(nullif(trim(p.given_name), ''), nullif(trim(p.display_name), ''),
                        nullif(trim(p.emails -> 0 ->> 'value'), ''), 'Unnamed'), 100))
  returning id into made;
  insert into match (person_id, contact_id) values (p.id, made);
  perform copy_fields(p.id, array_remove(copyable(), 'first_name'));
  return made;
end
$$;

revoke execute on function suggestions, copy_fields, import_person, copyable,
  phone_key, name_key from public;
grant execute on function suggestions, copy_fields, import_person, copyable,
  phone_key, name_key to heycarl_user;

-- For the kernel, as heycarl_worker. Definer: the worker holds no grant on
-- the tables, and every function takes the owner.

create function set_status(owner text, account_id bigint, error text default null)
  returns void
  language sql volatile security definer
  set search_path = heycarl_plugin_google_contacts, pg_temp
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

create function sync_token(owner text, account_id bigint)
  returns table (sync_token text, full_sync_at timestamptz)
  language sql stable security definer
  set search_path = heycarl_plugin_google_contacts, pg_temp
  as $$
    select s.sync_token, s.full_sync_at from sync_state s
     where s.account_id = sync_token.account_id and s.owner_sub = sync_token.owner
  $$;

-- One sync of one account, in one transaction. `renamed` ([{from, to}])
-- first, so a renamed contact keeps its row and its link; then upsert
-- `people` by resource name; delete `removed`; on a full sync, delete what
-- Google no longer sent. Returns the contacts cached.
create function apply_people(owner text, account_id bigint, full_sync boolean,
                             people jsonb, removed text[], renamed jsonb,
                             sync_token text)
  returns integer
  language plpgsql volatile security definer
  set search_path = heycarl_plugin_google_contacts, pg_temp
  as $$
#variable_conflict use_column
declare
  kept integer;
begin
  if not exists (select from heycarl_private.google_account g
                  where g.id = apply_people.account_id and g.owner_sub = apply_people.owner) then
    raise exception 'no account' using errcode = 'PT404';
  end if;

  update person p set resource_name = r ->> 'to'
    from jsonb_array_elements(apply_people.renamed) r
   where p.account_id = apply_people.account_id and p.resource_name = r ->> 'from'
     and not exists (select from person q where q.account_id = apply_people.account_id
                                            and q.resource_name = r ->> 'to');

  insert into person as p (owner_sub, account_id, resource_name, display_name, given_name,
                           family_name, nickname, company, job_title, biography,
                           birthday_year, birthday_month, birthday_day, emails, phones,
                           addresses, urls, updated_at)
  select apply_people.owner, apply_people.account_id, x.resource_name,
         coalesce(x.display_name, ''), coalesce(x.given_name, ''),
         coalesce(x.family_name, ''), coalesce(x.nickname, ''), coalesce(x.company, ''),
         coalesce(x.job_title, ''), coalesce(x.biography, ''),
         x.birthday_year, x.birthday_month, x.birthday_day,
         coalesce(x.emails, '[]'), coalesce(x.phones, '[]'),
         coalesce(x.addresses, '[]'), coalesce(x.urls, '[]'), now()
    from jsonb_to_recordset(apply_people.people) as x(
           resource_name text, display_name text, given_name text, family_name text,
           nickname text, company text, job_title text, biography text,
           birthday_year integer, birthday_month integer, birthday_day integer,
           emails jsonb, phones jsonb, addresses jsonb, urls jsonb)
  on conflict (account_id, resource_name) do update
    set display_name = excluded.display_name, given_name = excluded.given_name,
        family_name = excluded.family_name, nickname = excluded.nickname,
        company = excluded.company, job_title = excluded.job_title,
        biography = excluded.biography, birthday_year = excluded.birthday_year,
        birthday_month = excluded.birthday_month, birthday_day = excluded.birthday_day,
        emails = excluded.emails, phones = excluded.phones,
        addresses = excluded.addresses, urls = excluded.urls, updated_at = now()
    where row(p.display_name, p.given_name, p.family_name, p.nickname, p.company,
              p.job_title, p.biography, p.birthday_year, p.birthday_month,
              p.birthday_day, p.emails, p.phones, p.addresses, p.urls)
          is distinct from
          row(excluded.display_name, excluded.given_name, excluded.family_name,
              excluded.nickname, excluded.company, excluded.job_title,
              excluded.biography, excluded.birthday_year, excluded.birthday_month,
              excluded.birthday_day, excluded.emails, excluded.phones,
              excluded.addresses, excluded.urls);

  delete from person p
   where p.account_id = apply_people.account_id
     and (p.resource_name = any (apply_people.removed)
          or (apply_people.full_sync and p.resource_name not in (
                select x ->> 'resource_name' from jsonb_array_elements(apply_people.people) x)));

  insert into sync_state as s (account_id, owner_sub, sync_token, full_sync_at)
  values (apply_people.account_id, apply_people.owner, apply_people.sync_token,
          case when apply_people.full_sync then now() end)
  on conflict on constraint sync_state_pkey do update
    set sync_token = excluded.sync_token,
        full_sync_at = coalesce(excluded.full_sync_at, s.full_sync_at);

  select count(*) into kept from person p where p.account_id = apply_people.account_id;
  return kept;
end
$$;

revoke execute on function set_status, sync_token, apply_people from public;
grant execute on function set_status, sync_token, apply_people to heycarl_worker;

-- The person's own writes keep history: an unlink can be undone.
select heycarl_sdk.track('match');

-- Tracked so that a Google contact can be linked to any entry (DESIGN.md
-- "Links"). Its trigger goes at once: Google owns these rows, so they have
-- no history and no trash.
select heycarl_sdk.track('person');
drop trigger heycarl_version on person;
select heycarl_sdk.entry_route('person',
  '/p/google_contacts/heycarl-google-contacts#/person/{id}');
