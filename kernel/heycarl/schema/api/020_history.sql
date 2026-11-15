-- History and trash for the calling person (DESIGN.md, migration 0007).
--
-- SECURITY DEFINER throughout: no API role reads heycarl_private. Each
-- function filters on the caller's sub. Table names come from the tracked
-- list, never from the request.
--
-- The helpers live in heycarl_history, dropped and recreated on every
-- start like the API schema: in heycarl_private, a changed signature
-- would fail `create or replace` and stop the kernel starting.
drop schema if exists heycarl_history cascade;
create schema heycarl_history;

create function heycarl_history.caller() returns text
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
create function heycarl_history.deleted(me text)
  returns setof heycarl_private.version
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select v.* from heycarl_private.version v
     where v.owner_sub = me and v.op = 'delete'
       and not exists (select from heycarl_private.version n
                        where n.rel = v.rel and n.key = v.key and n.id > v.id)
  $$;

create function heycarl_history.label(data jsonb) returns text
  language sql immutable
  as $$
    select left(coalesce(
      nullif(trim(concat_ws(' ', data ->> 'first_name', data ->> 'last_name')), ''),
      data ->> 'title', data ->> 'name', data ->> 'body', data ->> 'value',
      nullif(data ->> 'label', ''),
      case when data ? 'month' and data ? 'day'
        then concat_ws(' ', data ->> 'kind', (data ->> 'day') || '/' || (data ->> 'month'))
      end, ''), 120)
  $$;

-- A plugin's name, or core for the kernel's own tables (links).
create function heycarl_history.scope_name(nspname name) returns text
  language sql immutable
  as $$
    select case when nspname like 'heycarl\_plugin\_%'
                then substr(nspname, length('heycarl_plugin_') + 1)
                else 'core' end
  $$;

create function heycarl_history.describe(v heycarl_private.version) returns jsonb
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select jsonb_build_object(
      'version', v.id,
      'scope', heycarl_history.scope_name(n.nspname),
      'table', c.relname,
      'key', v.key,
      'label', heycarl_history.label(v.data))
      from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where c.oid = v.rel
  $$;

create function heycarl_history.key_condition(rel regclass) returns text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select string_agg(format('t.%I = k.%I', c, c), ' and ')
      from heycarl_private.tracked t, unnest(t.key_columns) c
     where t.rel = key_condition.rel
  $$;

create function heycarl_history.is_live(rel regclass, key jsonb) returns boolean
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
declare
  hit boolean;
begin
  execute format('select exists (select from %s t, jsonb_populate_record(null::%s, $1) k where %s)',
                 rel, rel, heycarl_history.key_condition(rel))
    into hit using key;
  return hit;
end
$$;

-- Back under its old key. Only the columns both the stored row and the
-- table have: one added since gets its default.
create function heycarl_history.reinsert(v heycarl_private.version) returns void
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
-- bookkeeping columns.
create function heycarl_history.rewrite(v heycarl_private.version) returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
declare
  t heycarl_private.tracked;
  sets text;
begin
  select * into strict t from heycarl_private.tracked where rel = v.rel;
  select string_agg(format('%I = k.%I', a.attname, a.attname), ', ') into sets
    from pg_attribute a
   where a.attrelid = v.rel and a.attnum > 0 and not a.attisdropped
     and a.attgenerated = '' and a.attidentity <> 'a' and v.data ? a.attname
     and a.attname <> 'owner_sub'
     and not a.attname = any (t.key_columns)
     and not a.attname = any (t.ignore_columns);
  if sets is not null then
    execute format('update %s t set %s from jsonb_populate_record(null::%s, $1) k'
                   ' where %s and t.owner_sub = $2', v.rel, sets, v.rel,
                   heycarl_history.key_condition(v.rel))
      using v.data, v.owner_sub;
  end if;
end
$$;

create function heycarl_history.remove(v heycarl_private.version) returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
begin
  execute format('delete from %s t using jsonb_populate_record(null::%s, $1) k'
                 ' where %s and t.owner_sub = $2', v.rel, v.rel,
                 heycarl_history.key_condition(v.rel))
    using v.key, v.owner_sub;
end
$$;

-- Plugins read this to tell a restore from a person's own change: the
-- reminders plugin does not re-arm a reminder whose old time comes back.
create function heycarl_history.restoring() returns void
  language sql volatile
  as $$ select set_config('heycarl.restoring', 'on', true) $$;

-- Puts every record that transaction (tx, at) changed back to its state
-- before it. A record changed since is left alone and reported: undo never
-- overwrites later work. Steps run in passes, each in a savepoint, until a
-- pass makes no progress, because a child fails until its parent is back.
create function heycarl_history.undo(me text, of_tx xid8, of_at timestamptz) returns jsonb
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
declare
  rec record;
  pending jsonb := '[]';
  still jsonb;
  step jsonb;
  failed jsonb := '[]';
  skipped jsonb := '[]';
  progress boolean;
  done integer := 0;
  prior heycarl_private.version;
  newest heycarl_private.version;
begin
  perform heycarl_history.restoring();
  for rec in
    select v.rel, v.key, min(v.id) as first_id, max(v.id) as last_id
      from heycarl_private.version v
     where v.owner_sub = me and v.tx = of_tx and v.at = of_at
     group by v.rel, v.key
     order by min(v.id)
  loop
    select * into strict newest from heycarl_private.version
     where rel = rec.rel and key = rec.key order by id desc limit 1;
    if newest.id <> rec.last_id then
      skipped := skipped || heycarl_history.describe(newest);
      continue;
    end if;
    pending := pending || jsonb_build_object('first', rec.first_id, 'newest', newest.id);
  end loop;

  loop
    progress := false;
    still := '[]';
    for step in select * from jsonb_array_elements(pending) loop
      select * into strict newest from heycarl_private.version where id = (step ->> 'newest')::bigint;
      select * into prior from heycarl_private.version p
       where p.rel = newest.rel and p.key = newest.key and p.id < (step ->> 'first')::bigint
       order by p.id desc limit 1;
      begin
        if prior.id is null or prior.op = 'delete' then
          -- Made in that transaction: undoing it deletes it, to the trash.
          if newest.op <> 'delete' then
            perform heycarl_history.remove(newest);
          end if;
        elsif newest.op = 'delete' then
          -- Ignored columns change without a version, so the earlier state
          -- holds stale ones: a date would come back without its
          -- reminder_id, unlinked. The delete holds their last values.
          prior.data := prior.data || coalesce((
            select jsonb_object_agg(c, newest.data -> c)
              from heycarl_private.tracked t, unnest(t.ignore_columns) c
             where t.rel = newest.rel and newest.data ? c), '{}');
          if not heycarl_history.is_live(prior.rel, prior.key) then
            perform heycarl_history.reinsert(prior);
          end if;
        else
          perform heycarl_history.rewrite(prior);
        end if;
        done := done + 1;
        progress := true;
      exception when others then
        still := still || (step || jsonb_build_object('error', sqlerrm));
      end;
    end loop;
    pending := still;
    exit when not progress or jsonb_array_length(pending) = 0;
  end loop;

  for step in select * from jsonb_array_elements(pending) loop
    failed := failed || (heycarl_history.describe(
      (select v from heycarl_private.version v where v.id = (step ->> 'newest')::bigint))
      || jsonb_build_object('error', step ->> 'error'));
  end loop;
  return jsonb_build_object('done', done, 'failed', failed, 'changed_since', skipped);
end
$$;

-- The kernel sets via = app only for a browser session. An MCP token says
-- mcp:<client>, so no client can destroy what it deleted.
create function heycarl_history.app_only() returns void
  language plpgsql stable
  set search_path = pg_catalog, pg_temp
  as $$
begin
  if heycarl_sdk.claims() ->> 'via' is distinct from 'app' then
    raise exception 'only the app can delete history' using errcode = '42501';
  end if;
end
$$;

create function heycarl_history.purge(me text, of_tx xid8, of_at timestamptz) returns integer
  language plpgsql volatile
  set search_path = pg_catalog, pg_temp
  as $$
declare
  n integer;
  doomed jsonb;
  gone bigint[];
begin
  select coalesce(jsonb_agg(jsonb_build_object('rel', d.rel::oid, 'key', d.key)), '[]')
    into doomed
    from heycarl_history.deleted(me) d
   where of_tx is null or (d.tx = of_tx and d.at = of_at);
  -- Links to a purged entry go too, and then their history: a statement of
  -- its own, so the delete's versions exist before they are removed.
  with dropped as (
    delete from heycarl_private.link l
     using jsonb_array_elements(doomed) x
     where l.owner_sub = me
       and ((l.a_rel = (x ->> 'rel')::oid::regclass and l.a_key = x -> 'key')
         or (l.b_rel = (x ->> 'rel')::oid::regclass and l.b_key = x -> 'key'))
    returning l.id)
  select coalesce(array_agg(id), '{}') into gone from dropped;
  delete from heycarl_private.version v
   using jsonb_array_elements(doomed) x
   where v.owner_sub = me and v.rel = (x ->> 'rel')::oid::regclass and v.key = x -> 'key';
  get diagnostics n = row_count;
  delete from heycarl_private.version v
   where v.owner_sub = me and v.rel = 'heycarl_private.link'::regclass
     and (v.key ->> 'id')::bigint = any (gone);
  return n;
end
$$;

revoke all on schema heycarl_history from public;

-- The API.

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
    select v.id, heycarl_history.scope_name(n.nspname), c.relname, v.key,
           v.op, v.data, v.at, v.via, heycarl_history.label(v.data),
           not exists (select from heycarl_private.version w
                        where w.rel = v.rel and w.key = v.key and w.id > v.id)
      from heycarl_private.version v
      join pg_class c on c.oid = v.rel
      join pg_namespace n on n.oid = c.relnamespace
     where v.owner_sub = heycarl_history.caller()
       and (of_scope is null or heycarl_history.scope_name(n.nspname) = of_scope)
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
           jsonb_agg(heycarl_history.describe(d) order by d.id)
      from heycarl_history.deleted(heycarl_history.caller()) d
     group by d.tx, d.at
     order by d.at desc
  $$;

-- Undoes the whole transaction that made this version: a mass change,
-- or a delete with everything it took along.
create function heycarl.undo(version bigint) returns jsonb
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
  v heycarl_private.version;
begin
  select * into v from heycarl_private.version where id = undo.version and owner_sub = me;
  if not found then
    -- PTnnn: PostgREST answers with HTTP status nnn.
    raise exception 'no version %', undo.version using errcode = 'PT404';
  end if;
  return heycarl_history.undo(me, v.tx, v.at);
end
$$;

-- One record back to this version's state. A trash entry's delete version
-- brings back the whole entry.
create function heycarl.restore(version bigint) returns jsonb
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
  v heycarl_private.version;
  newest heycarl_private.version;
  result jsonb := jsonb_build_object('done', 0, 'failed', '[]'::jsonb, 'changed_since', '[]'::jsonb);
begin
  select * into v from heycarl_private.version where id = restore.version and owner_sub = me;
  if not found then
    raise exception 'no version %', restore.version using errcode = 'PT404';
  end if;
  select * into strict newest from heycarl_private.version
   where rel = v.rel and key = v.key order by id desc limit 1;
  if v.op = 'delete' and v.id <> newest.id then
    raise exception 'this record came back after that delete' using errcode = 'PT409';
  end if;
  if newest.op = 'delete' then
    result := heycarl_history.undo(me, newest.tx, newest.at);
  end if;
  if v.op <> 'delete' and v.id <> newest.id then
    perform heycarl_history.restoring();
    begin
      perform heycarl_history.rewrite(v);
      result := jsonb_set(result, '{done}', to_jsonb((result ->> 'done')::integer + 1));
    exception when others then
      result := jsonb_set(result, '{failed}', (result -> 'failed')
        || (heycarl_history.describe(v) || jsonb_build_object('error', sqlerrm)));
    end;
  end if;
  return result;
end
$$;

-- Deletes a trash entry for good: every version of its records.
create function heycarl.purge(entry bigint) returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
  e heycarl_private.version;
begin
  perform heycarl_history.app_only();
  select * into e from heycarl_history.deleted(me) where id = purge.entry;
  if not found then
    raise exception 'no trash entry %', purge.entry using errcode = 'PT404';
  end if;
  return heycarl_history.purge(me, e.tx, e.at);
end
$$;

create function heycarl.empty_trash() returns integer
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
begin
  perform heycarl_history.app_only();
  return heycarl_history.purge(me, null, null);
end
$$;

revoke execute on function heycarl.history, heycarl.trash, heycarl.undo,
  heycarl.restore, heycarl.purge, heycarl.empty_trash from public;
grant execute on function heycarl.history, heycarl.trash, heycarl.undo,
  heycarl.restore, heycarl.purge, heycarl.empty_trash to heycarl_user;
