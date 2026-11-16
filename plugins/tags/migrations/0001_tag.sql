-- Runs with search_path set to this plugin's schema.
--
-- A tag is an entry like any other, and tagging is a core link to it
-- (DESIGN.md "Links"). Every plugin's entries take tags with no code of
-- their own, and a deleted tag hides its links until a restore.
--
-- Every function here runs as the caller. The link machinery it calls
-- (heycarl.links, heycarl.link, heycarl.unlink) checks the caller itself.
-- They are plpgsql, not sql: the kernel recreates the heycarl schema after
-- the migrations, so a sql body naming heycarl.links fails to create.

create table tag (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  name text not null check (length(btrim(name)) between 1 and 100 and name = btrim(name)),
  -- A base16 role; the page draws the chip in var(--hc-<color>).
  color text check (color ~ '^base0[0-9A-F]$'),
  created_at timestamptz not null default now(),
  unique (id, owner_sub)
);
create unique index tag_name on tag (owner_sub, lower(name));

alter table tag enable row level security;
create policy own on tag to heycarl_user
  using (owner_sub = heycarl_sdk.sub()) with check (owner_sub = heycarl_sdk.sub());

grant usage on schema heycarl_plugin_tags to heycarl_user;
grant select, delete on tag to heycarl_user;
grant insert (name, color), update (name, color) on tag to heycarl_user;

-- The caller's tags on one entry, with the link that holds each.
create function tags_of(of_scope text, of_table text, of_key jsonb)
  returns table (link_id bigint, id bigint, name text, color text)
  language plpgsql stable
  set search_path = heycarl_plugin_tags, pg_temp
  as $$
begin
  return query
    select l.id, t.id, t.name, t.color
      from heycarl.links(of_scope, of_table, of_key) l
      join tag t on t.id = (l.entry -> 'key' ->> 'id')::bigint
     where l.entry ->> 'scope' = 'tags' and l.entry ->> 'table' = 'tag'
     order by lower(t.name);
end
$$;

-- The live entries that carry a tag. PT404 when the tag is not the caller's.
create function tagged(tag_id bigint)
  returns table (link_id bigint, entry jsonb, created_at timestamptz)
  language plpgsql stable
  set search_path = heycarl_plugin_tags, pg_temp
  as $$
begin
  if not exists (select from tag t where t.id = tagged.tag_id) then
    raise exception 'no tag with id %', tagged.tag_id using errcode = 'PT404';
  end if;
  return query
    select l.id, l.entry, l.created_at
      from heycarl.links('tags', 'tag', jsonb_build_object('id', tagged.tag_id)) l
     where not (l.entry ->> 'scope' = 'tags' and l.entry ->> 'table' = 'tag');
end
$$;

-- The keys of one table's entries that carry a tag, for a plugin to filter
-- its own rows: todos/todo?id=in.(...) from the ids here.
create function tagged_keys(tag_id bigint, of_scope text, of_table text)
  returns setof jsonb
  language plpgsql stable
  set search_path = heycarl_plugin_tags, pg_temp
  as $$
begin
  return query
    select g.entry -> 'key'
      from tagged(tagged_keys.tag_id) g
     where g.entry ->> 'scope' = of_scope and g.entry ->> 'table' = of_table;
end
$$;

-- Tag an entry with a tag by name, made when the caller has none by that
-- name (any case). A colour given here sets the colour of a new tag only.
create function tag_entry(of_scope text, of_table text, of_key jsonb, name text, color text default null)
  returns jsonb
  language plpgsql volatile
  set search_path = heycarl_plugin_tags, pg_temp
  as $$
#variable_conflict use_column
declare
  wanted text := btrim(tag_entry.name);
  found tag;
  made jsonb;
begin
  if of_scope = 'tags' and of_table = 'tag' then
    raise exception 'a tag cannot carry a tag' using errcode = '22023';
  end if;
  select * into found from tag t where lower(t.name) = lower(wanted);
  if found.id is null then
    insert into tag (name, color) values (wanted, tag_entry.color) returning * into found;
  end if;
  made := heycarl.link(of_scope, of_table, of_key, 'tags', 'tag', jsonb_build_object('id', found.id));
  return jsonb_build_object(
    'link_id', (made ->> 'id')::bigint, 'id', found.id, 'name', found.name, 'color', found.color);
end
$$;

-- Take a tag off an entry. PT404 when the entry does not carry it.
create function untag(of_scope text, of_table text, of_key jsonb, tag_id bigint)
  returns boolean
  language plpgsql volatile
  set search_path = heycarl_plugin_tags, pg_temp
  as $$
declare
  holder bigint;
begin
  select o.link_id into holder from tags_of(of_scope, of_table, of_key) o where o.id = untag.tag_id;
  if holder is null then
    raise exception 'the entry does not carry tag %', untag.tag_id using errcode = 'PT404';
  end if;
  return heycarl.unlink(holder);
end
$$;

revoke execute on function tags_of, tagged, tagged_keys, tag_entry, untag from public;
grant execute on function tags_of, tagged, tagged_keys, tag_entry, untag to heycarl_user;

-- Each tag with how many live entries carry it.
create view tag_summary with (security_invoker = true) as
  select t.id, t.name, t.color, t.created_at,
         (select count(*) from tagged(t.id)) as entries
    from tag t;
grant select on tag_summary to heycarl_user;

select heycarl_sdk.track('tag');
select heycarl_sdk.entry_route('tag', '/p/tags/heycarl-tags#/tag/{id}');
