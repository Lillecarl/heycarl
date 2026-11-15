-- Links between any two entries (DESIGN.md "Links", migration 0008).
-- SECURITY DEFINER like History; helpers join heycarl_history, which
-- 020_history.sql recreates on every start.

-- A plugin table that may hold an entry, by its scope and name.
create function heycarl_history.entry_rel(scope text, tbl text) returns regclass
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select t.rel from heycarl_private.tracked t
      join pg_class c on c.oid = t.rel
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'heycarl_plugin_' || scope and c.relname = tbl
  $$;

-- The caller's live row under `key`, as jsonb, or null.
create function heycarl_history.live_row(rel regclass, key jsonb, me text) returns jsonb
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
declare
  found_row jsonb;
begin
  execute format('select to_jsonb(t) from %s t, jsonb_populate_record(null::%s, $1) k'
                 ' where %s and t.owner_sub = $2', rel, rel, heycarl_history.key_condition(rel))
    into found_row using key, me;
  return found_row;
end
$$;

-- What a page needs to show an entry and go to it. `url` is null when the
-- plugin names no route: the page then opens the plugin's own page.
create function heycarl_history.entry(rel regclass, data jsonb) returns jsonb
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
declare
  t heycarl_private.tracked;
  key jsonb;
  url text;
  k text;
  v text;
begin
  select * into strict t from heycarl_private.tracked where tracked.rel = entry.rel;
  key := heycarl_private.key_of(data, t.key_columns);
  url := t.route;
  for k, v in select * from jsonb_each_text(key) loop
    url := replace(url, '{' || k || '}', v);
  end loop;
  return (
    select jsonb_build_object(
      'scope', heycarl_history.scope_name(n.nspname),
      'table', c.relname,
      'key', key,
      'label', heycarl_history.label(data),
      'url', url)
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where c.oid = rel);
end
$$;

revoke all on all functions in schema heycarl_history from public;

create function heycarl.link(
  a_scope text, a_table text, a_key jsonb,
  b_scope text, b_table text, b_key jsonb,
  label text default ''
) returns jsonb
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
  wanted text := label;
  ar regclass := heycarl_history.entry_rel(a_scope, a_table);
  br regclass := heycarl_history.entry_rel(b_scope, b_table);
  a_row jsonb;
  b_row jsonb;
  ak jsonb;
  bk jsonb;
  made heycarl_private.link;
begin
  if ar is null or br is null then
    raise exception 'no such table' using errcode = 'PT404';
  end if;
  a_row := heycarl_history.live_row(ar, a_key, me);
  b_row := heycarl_history.live_row(br, b_key, me);
  if a_row is null or b_row is null then
    raise exception 'no such entry' using errcode = 'PT404';
  end if;
  -- The key as the table holds it, whatever extra the caller sent.
  ak := heycarl_private.key_of(a_row, (select key_columns from heycarl_private.tracked where rel = ar));
  bk := heycarl_private.key_of(b_row, (select key_columns from heycarl_private.tracked where rel = br));
  if (ar::oid, ak::text) = (br::oid, bk::text) then
    raise exception 'an entry cannot link to itself' using errcode = '22023';
  end if;
  if (ar::oid, ak::text) > (br::oid, bk::text) then
    select br, bk, ar, ak into ar, ak, br, bk;
  end if;
  insert into heycarl_private.link as l (owner_sub, a_rel, a_key, b_rel, b_key, label)
  values (me, ar, ak, br, bk, coalesce(wanted, ''))
  -- By name: a column list here would clash with the a_key parameter.
  on conflict on constraint link_pair
    do update set label = excluded.label
  returning * into made;
  return jsonb_build_object('id', made.id, 'label', made.label);
end
$$;

create function heycarl.unlink(id bigint) returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
begin
  delete from heycarl_private.link l where l.id = unlink.id and l.owner_sub = me;
  if not found then
    raise exception 'no link %', unlink.id using errcode = 'PT404';
  end if;
  return true;
end
$$;

-- The live entries linked to one entry. A link to a deleted entry stays
-- hidden until the entry comes back.
create function heycarl.links(of_scope text, of_table text, of_key jsonb)
  returns table (id bigint, label text, entry jsonb, created_at timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
  r regclass := heycarl_history.entry_rel(of_scope, of_table);
  k jsonb;
begin
  if r is null then
    raise exception 'no such table' using errcode = 'PT404';
  end if;
  k := heycarl_private.key_of(of_key, (select key_columns from heycarl_private.tracked t where t.rel = r));
  return query
    select l.id, l.label, heycarl_history.entry(o.rel, o.data), l.created_at
      from heycarl_private.link l
     cross join lateral (
       select case when l.a_rel = r and l.a_key = k then l.b_rel else l.a_rel end as rel,
              case when l.a_rel = r and l.a_key = k then l.b_key else l.a_key end as key) e
     cross join lateral (
       select e.rel, heycarl_history.live_row(e.rel, e.key, me) as data) o
     where l.owner_sub = me
       and ((l.a_rel = r and l.a_key = k) or (l.b_rel = r and l.b_key = k))
       and o.data is not null
     order by l.created_at;
end
$$;

-- Live entries whose label holds `q`, for a picker: every tracked plugin
-- table, rows with no label left out (join rows, preferences).
create function heycarl.entries(q text, max_rows integer default 20)
  returns table (entry jsonb)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
  pattern text := '%' || replace(replace(replace(q, '\', '\\'), '%', '\%'), '_', '\_') || '%';
  wanted integer := least(greatest(max_rows, 1), 100);
  t heycarl_private.tracked;
begin
  for t in
    select tr.* from heycarl_private.tracked tr
      join pg_class c on c.oid = tr.rel
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname like 'heycarl\_plugin\_%'
     order by n.nspname, c.relname
  loop
    return query execute format(
      'select heycarl_history.entry(%L, to_jsonb(x)) from %s x'
      ' where x.owner_sub = $1 and heycarl_history.label(to_jsonb(x)) <> $3'
      ' and heycarl_history.label(to_jsonb(x)) ilike $2 limit $4', t.rel, t.rel)
      using me, pattern, '', wanted;
  end loop;
end
$$;

revoke execute on function heycarl.link, heycarl.unlink, heycarl.links, heycarl.entries from public;
grant execute on function heycarl.link, heycarl.unlink, heycarl.links, heycarl.entries to heycarl_user;
