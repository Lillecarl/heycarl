-- App passwords (DESIGN.md "App passwords", migration 0009). The view and
-- RLS cover listing and revoking; these functions are the only code that
-- writes a hash or compares one.

create view heycarl.app_password with (security_invoker = true) as
  select id, name, scopes, created_at, last_used_at, expires_at
  from heycarl_private.app_password;
grant select, delete on heycarl.app_password to heycarl_user;

-- Definer: the caller may not choose the hash. The owner and the email
-- come from the token the kernel signed, never from an argument.
create function heycarl.app_password_create(
  name text, allow_write boolean default false, days integer default 365
) returns jsonb
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  me text := heycarl_history.caller();
  clean text := btrim(name);
  username text;
  secret text;
  made heycarl_private.app_password;
begin
  -- A credential comes only from a person at the app, never from MCP.
  if heycarl_sdk.claims() ->> 'via' is distinct from 'app' then
    raise exception 'only the app makes app passwords' using errcode = '42501';
  end if;
  if length(clean) not between 1 and 100 then
    raise exception 'a name has 1 to 100 characters' using errcode = '22023';
  end if;
  if days is null or days not between 1 and 365 then
    raise exception 'an app password lasts 1 to 365 days' using errcode = '22023';
  end if;
  -- Without the lock, concurrent requests each count below the limit.
  lock table heycarl_private.app_password in share row exclusive mode;
  if (select count(*) from heycarl_private.app_password p where p.owner_sub = me) >= 50 then
    raise exception 'at most 50 app passwords; revoke one' using errcode = 'PT409';
  end if;
  select string_agg(substr('abcdefghijklmnopqrstuvwxyz0123456789', get_byte(b, i) % 36 + 1, 1), '')
    into username
    from heycarl_private.gen_random_bytes(16) b, generate_series(0, 15) i;
  secret := translate(rtrim(encode(heycarl_private.gen_random_bytes(32), 'base64'), '='), '+/', '-_');
  insert into heycarl_private.app_password
    (id, owner_sub, email, name, secret_hash, scopes, expires_at)
  values (
    username, me, heycarl_sdk.claims() ->> 'email', clean,
    sha256(convert_to(secret, 'UTF8')),
    case when allow_write then '{read,write}'::text[] else '{read}'::text[] end,
    now() + make_interval(days => days))
  returning * into made;
  return jsonb_build_object(
    'id', made.id, 'name', made.name, 'scopes', made.scopes,
    'created_at', made.created_at, 'last_used_at', made.last_used_at,
    'expires_at', made.expires_at, 'username', made.id, 'secret', secret);
end
$$;

-- Checks one Basic credential, for heycarl_gate alone. Failures count per
-- username and per address; past a limit nothing is checked or written,
-- so the failure rows stay bounded too.
create function heycarl.dav_authenticate(username text, secret text, address text)
  returns jsonb
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  window_start timestamptz := now() - interval '15 minutes';
  user_key text := 'user:' || left(username, 100);
  addr_key text := 'addr:' || left(address, 64);
  by_user bigint;
  by_addr bigint;
  total bigint;
  p heycarl_private.app_password;
begin
  select count(*) filter (where key = user_key),
         count(*) filter (where key = addr_key),
         count(*)
    into by_user, by_addr, total
    from heycarl_private.auth_failure where at > window_start;
  if by_user >= 10 or by_addr >= 20 or total >= 10000 then
    return jsonb_build_object('status', 'throttled');
  end if;
  select * into p from heycarl_private.app_password a
   where a.id = username and a.expires_at > now();
  if p.id is null or p.secret_hash <> sha256(convert_to(secret, 'UTF8')) then
    insert into heycarl_private.auth_failure (key) values (user_key), (addr_key);
    return jsonb_build_object('status', 'wrong');
  end if;
  update heycarl_private.app_password a set last_used_at = now()
   where a.id = p.id
     and (a.last_used_at is null or a.last_used_at < now() - interval '1 minute');
  return jsonb_build_object(
    'status', 'ok', 'id', p.id, 'sub', p.owner_sub, 'email', p.email,
    'scopes', p.scopes);
end
$$;

-- For the kernel's purge job, as heycarl_worker.
create function heycarl.app_password_purge() returns void
  language sql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
    delete from heycarl_private.app_password where expires_at < now();
    delete from heycarl_private.auth_failure where at < now() - interval '15 minutes';
  $$;

revoke execute on function heycarl.app_password_create, heycarl.dav_authenticate,
  heycarl.app_password_purge from public;
grant execute on function heycarl.app_password_create to heycarl_user;
grant execute on function heycarl.dav_authenticate to heycarl_gate;
grant execute on function heycarl.app_password_purge to heycarl_worker;
