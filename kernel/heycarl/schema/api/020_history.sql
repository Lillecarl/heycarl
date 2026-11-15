-- History and trash for the calling person (DESIGN.md, migration 0007).
--
-- SECURITY DEFINER throughout: no API role reads heycarl_private. Each
-- function filters on the caller's sub, and the helpers in heycarl_private
-- are revoked from everyone else. Table names come from the tracked list,
-- never from the request.

create or replace function heycarl_private.caller() returns text
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
begin
  if heycarl_sdk.sub() is null then
    raise exception 'no person calls' using errcode = '42501';
  end if;
  return heycarl_sdk.sub();
end
$$;

-- A record's newest version is a delete: it is in the trash.
create or replace function heycarl_private.deleted(me text)
  returns setof heycarl_private.version
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select v.* from heycarl_private.version v
     where v.owner_sub = me and v.op = 'delete'
       and not exists (select from heycarl_private.version n
                        where n.rel = v.rel and n.key = v.key and n.id > v.id)
  $$;

create or replace function heycarl_private.label(data jsonb) returns text
  language sql immutable
  as $$
    select left(coalesce(
      nullif(trim(concat_ws(' ', data ->> 'first_name', data ->> 'last_name')), ''),
      data ->> 'title', data ->> 'name', data ->> 'body', data ->> 'value',
      nullif(data ->> 'label', ''), ''), 120)
  $$;

create or replace function heycarl_private.describe(v heycarl_private.version) returns jsonb
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select jsonb_build_object(
      'version', v.id,
      'scope', regexp_replace(n.nspname, '^heycarl_plugin_', ''),
      'table', c.relname,
      'key', v.key,
      'label', heycarl_private.label(v.data))
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where c.oid = v.rel
  $$;

create or replace function heycarl_private.is_live(rel regclass, key jsonb) returns boolean
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
declare
  cond text;
  hit boolean;
begin
  select string_agg(format('t.%I = k.%I', c, c), ' and ') into cond
    from unnest((select key_columns from heycarl_private.tracked where tracked.rel = is_live.rel)) c;
  execute format('select exists (select from %s t, jsonb_populate_record(null::%s, $1) k where %s)',
                 rel, rel, cond)
    into hit using key;
  return hit;
end
$$;

-- Back under its old key. Only the columns both the stored row and the
-- table have: one added since gets its default.
create or replace function heycarl_private.reinsert(v heycarl_private.version) returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
declare
  cols text;
begin
  select string_agg(quote_ident(a.attname), ', ' order by a.attnum) into cols
    from pg_attribute a
   where a.attrelid = v.rel and a.attnum > 0 and not a.attisdropped
     and a.attgenerated = '' and v.data ? a.attname;
  execute format('insert into %s (%s) overriding system value select %s'
                 ' from jsonb_populate_record(null::%s, $1)', v.rel, cols, cols, v.rel)
    using v.data;
end
$$;

-- A live row gets a stored state back, except its key, its owner and its
-- bookkeeping columns: an old reminder state must not send it again.
create or replace function heycarl_private.rewrite(v heycarl_private.version) returns boolean
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
declare
  t heycarl_private.tracked;
  sets text;
  cond text;
  n integer;
begin
  select * into strict t from heycarl_private.tracked where rel = v.rel;
  select string_agg(format('%I = r.%I', a.attname, a.attname), ', ') into sets
    from pg_attribute a
   where a.attrelid = v.rel and a.attnum > 0 and not a.attisdropped
     and a.attgenerated = '' and a.attidentity <> 'a' and v.data ? a.attname
     and a.attname <> 'owner_sub'
     and not a.attname = any (t.key_columns)
     and not a.attname = any (t.ignore_columns);
  if sets is null then
    return false;
  end if;
  select string_agg(format('t.%I = r.%I', c, c), ' and ') into cond from unnest(t.key_columns) c;
  execute format('update %s t set %s from jsonb_populate_record(null::%s, $1) r'
                 ' where %s and t.owner_sub = $2', v.rel, sets, v.rel, cond)
    using v.data, v.owner_sub;
  get diagnostics n = row_count;
  return n > 0;
end
$$;

revoke execute on function heycarl_private.caller, heycarl_private.deleted,
  heycarl_private.label, heycarl_private.describe, heycarl_private.is_live,
  heycarl_private.reinsert, heycarl_private.rewrite from public;

create function heycarl.history(
  of_scope text default null,
  of_table text default null,
  of_key jsonb default null,
  before bigint default null,
  max_rows integer default 50
) returns table (
  id bigint, scope text, "table" text, key jsonb, op text, data jsonb,
  at timestamptz, via text, label text, newest boolean
)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select v.id, regexp_replace(n.nspname, '^heycarl_plugin_', ''), c.relname, v.key,
           v.op, v.data, v.at, v.via, heycarl_private.label(v.data),
           not exists (select from heycarl_private.version w
                        where w.rel = v.rel and w.key = v.key and w.id > v.id)
      from heycarl_private.version v
      join pg_class c on c.oid = v.rel
      join pg_namespace n on n.oid = c.relnamespace
     where v.owner_sub = heycarl_private.caller()
       and (of_scope is null or n.nspname = 'heycarl_plugin_' || of_scope)
       and (of_table is null or c.relname = of_table)
       and (of_key is null or v.key = of_key)
       and (before is null or v.id < before)
     order by v.id desc
     limit least(greatest(max_rows, 1), 500)
  $$;

-- One entry per transaction: a contact and the notes and dates its
-- delete took with it.
create function heycarl.trash() returns table (
  entry bigint, at timestamptz, via text, items jsonb
)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select min(d.id), d.at, min(d.via),
           jsonb_agg(heycarl_private.describe(d) order by d.id)
      from heycarl_private.deleted(heycarl_private.caller()) d
     group by d.tx, d.at
     order by d.at desc
  $$;

create function heycarl.restore(version bigint) returns jsonb
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_private.caller();
  v heycarl_private.version;
  newest heycarl_private.version;
  pending bigint[] := '{}';
  failed jsonb := '{}';
  r heycarl_private.version;
  pid bigint;
  progress boolean;
  restored integer := 0;
  updated boolean := false;
begin
  select * into v from heycarl_private.version
   where id = restore.version and owner_sub = me;
  if not found then
    -- PTnnn: PostgREST answers with HTTP status nnn.
    raise exception 'no version %', restore.version using errcode = 'PT404';
  end if;
  select * into strict newest from heycarl_private.version
   where rel = v.rel and key = v.key order by id desc limit 1;

  if newest.op = 'delete' then
    select coalesce(array_agg(d.id order by d.id), '{}') into pending
      from heycarl_private.deleted(me) d
     where d.tx = newest.tx and d.at = newest.at;
    -- A child fails until its parent is back, so pass again while a pass
    -- adds something. What still fails stays in the trash.
    loop
      progress := false;
      foreach pid in array pending loop
        select * into strict r from heycarl_private.version where id = pid;
        begin
          if not heycarl_private.is_live(r.rel, r.key) then
            perform heycarl_private.reinsert(r);
            restored := restored + 1;
          end if;
          pending := array_remove(pending, pid);
          failed := failed - pid::text;
          progress := true;
        exception when others then
          failed := failed || jsonb_build_object(pid::text, sqlerrm);
        end;
      end loop;
      exit when not progress or cardinality(pending) = 0;
    end loop;
  end if;

  if v.op <> 'delete' and v.id <> newest.id then
    updated := heycarl_private.rewrite(v);
  end if;

  return jsonb_build_object(
    'restored', restored,
    'updated', updated,
    'failed', (
      select coalesce(jsonb_agg(heycarl_private.describe(x) || jsonb_build_object('error', f.value)), '[]')
        from jsonb_each_text(failed) f
        join heycarl_private.version x on x.id = f.key::bigint));
end
$$;

create or replace function heycarl_private.purge_app_only() returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
begin
  -- The kernel sets via = app only for a browser session. An MCP token
  -- says mcp:<client>, so no client can destroy what it deleted.
  if heycarl_sdk.claims() ->> 'via' is distinct from 'app' then
    raise exception 'only the app can delete history' using errcode = '42501';
  end if;
end
$$;
revoke execute on function heycarl_private.purge_app_only from public;

-- Deletes a trash entry for good: every version of its records.
create function heycarl.purge(entry bigint) returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_private.caller();
  e heycarl_private.version;
  n integer;
begin
  perform heycarl_private.purge_app_only();
  select * into e from heycarl_private.deleted(me) where id = purge.entry;
  if not found then
    raise exception 'no trash entry %', purge.entry using errcode = 'PT404';
  end if;
  with doomed as materialized (
    select d.rel, d.key from heycarl_private.deleted(me) d
     where d.tx = e.tx and d.at = e.at)
  delete from heycarl_private.version v using doomed
   where v.rel = doomed.rel and v.key = doomed.key and v.owner_sub = me;
  get diagnostics n = row_count;
  return n;
end
$$;

create function heycarl.empty_trash() returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_private.caller();
  n integer;
begin
  perform heycarl_private.purge_app_only();
  with doomed as materialized (
    select d.rel, d.key from heycarl_private.deleted(me) d)
  delete from heycarl_private.version v using doomed
   where v.rel = doomed.rel and v.key = doomed.key and v.owner_sub = me;
  get diagnostics n = row_count;
  return n;
end
$$;

revoke execute on function heycarl.history, heycarl.trash, heycarl.restore,
  heycarl.purge, heycarl.empty_trash from public;
grant execute on function heycarl.history, heycarl.trash, heycarl.restore,
  heycarl.purge, heycarl.empty_trash to heycarl_user;
