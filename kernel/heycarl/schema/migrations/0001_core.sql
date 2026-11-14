-- Identity helpers for policies and defaults, here and in plugins.
--
-- `request.jwt.claims` is set by PostgREST from the token the kernel minted.
-- It is safe to trust only because heycarl_user and heycarl_worker cannot run
-- arbitrary SQL: they reach the database only through PostgREST.
create schema heycarl_sdk;
grant usage on schema heycarl_sdk to heycarl_user, heycarl_worker;

create function heycarl_sdk.claims() returns jsonb
  language sql stable
  as $$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;

-- The signed-in person, or null for a worker.
create function heycarl_sdk.sub() returns text
  language sql stable
  as $$ select heycarl_sdk.claims() ->> 'sub' $$;

-- The owner of a new row: the caller. A worker has no caller and must name
-- the owner itself, so this raises rather than store a null.
create function heycarl_sdk.owner() returns text
  language plpgsql stable
  as $$
begin
  if heycarl_sdk.sub() is null then
    raise exception 'no caller: set owner_sub explicitly' using errcode = '42501';
  end if;
  return heycarl_sdk.sub();
end
$$;

grant execute on all functions in schema heycarl_sdk to heycarl_user, heycarl_worker;
