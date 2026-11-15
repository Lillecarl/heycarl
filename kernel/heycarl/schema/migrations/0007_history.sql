-- History and trash (DESIGN.md). Every tracked table keeps each state of
-- each row here. No API role reads these tables; the functions in
-- api/020_history.sql do, as their owner, for the calling person only.

create table heycarl_private.tracked (
  rel regclass primary key,
  key_columns text[] not null,
  -- Bookkeeping: a change to these alone makes no version, and restoring
  -- a live row leaves them as they are.
  ignore_columns text[] not null default '{}'
);

create table heycarl_private.version (
  id bigint generated always as identity primary key,
  rel regclass not null references heycarl_private.tracked on delete cascade,
  owner_sub text not null,
  key jsonb not null,
  op text not null check (op in ('insert', 'update', 'delete')),
  -- The state after an insert or update, the last state on a delete.
  data jsonb not null,
  tx xid8 not null default pg_current_xact_id(),
  at timestamptz not null default now(),
  -- The token's via claim: app, mcp:<client_id> or kernel. Null for a job
  -- or a row that existed before its table was tracked.
  via text
);
create index version_record on heycarl_private.version (rel, key, id);
create index version_owner on heycarl_private.version (owner_sub, id);
-- The trash reads deletes only; without this it reads all history.
create index version_deletes on heycarl_private.version (owner_sub, id) where op = 'delete';
create index version_tx on heycarl_private.version (tx, at);

create function heycarl_private.key_of(data jsonb, key_columns text[]) returns jsonb
  language sql immutable
  as $$ select jsonb_object_agg(k, data -> k) from unnest(key_columns) k $$;

create function heycarl_private.record_version() returns trigger
  language plpgsql security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  t heycarl_private.tracked;
  state jsonb;
begin
  select * into strict t from heycarl_private.tracked where rel = tg_relid;
  state := case when tg_op = 'DELETE' then to_jsonb(old) else to_jsonb(new) end;
  if tg_op = 'UPDATE' and (state - t.ignore_columns) = (to_jsonb(old) - t.ignore_columns) then
    return null;
  end if;
  insert into heycarl_private.version (rel, owner_sub, key, op, data, via)
  values (tg_relid, state ->> 'owner_sub', heycarl_private.key_of(state, t.key_columns),
          lower(tg_op), state, heycarl_sdk.claims() ->> 'via');
  return null;
end
$$;
revoke execute on function heycarl_private.record_version from public;

-- For plugin migrations: `select heycarl_sdk.track('contact', array['updated_at'])`.
create function heycarl_sdk.track(tbl regclass, ignore text[] default '{}') returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
declare
  keys text[];
begin
  select array_agg(a.attname::text order by array_position(i.indkey::int2[], a.attnum))
    into keys
    from pg_index i
    join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any (i.indkey)
   where i.indrelid = tbl and i.indisprimary;
  if keys is null then
    raise exception '% has no primary key', tbl;
  end if;
  if not exists (select from pg_attribute
                  where attrelid = tbl and attname = 'owner_sub' and not attisdropped) then
    raise exception '% has no owner_sub', tbl;
  end if;
  insert into heycarl_private.tracked (rel, key_columns, ignore_columns)
  values (tbl, keys, ignore);
  execute format(
    'create trigger heycarl_version after insert or update or delete on %s'
    ' for each row execute function heycarl_private.record_version()', tbl);
  execute format(
    'insert into heycarl_private.version (rel, owner_sub, key, op, data)'
    ' select %L, t.owner_sub, heycarl_private.key_of(to_jsonb(t), %L), ''insert'', to_jsonb(t)'
    ' from %s t', tbl, keys, tbl);
end
$$;
revoke execute on function heycarl_sdk.track from public;
