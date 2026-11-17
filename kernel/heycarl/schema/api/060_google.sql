-- Google accounts (migration 0011). The view is the person's, under RLS.
-- The functions are the kernel's, as heycarl_worker: definer, because the
-- worker holds no grant on the tables, and each one takes the owner.

create view heycarl.google_account with (security_invoker = true) as
  select id, email, scopes, connected_at, needs_reconnect, last_error
    from heycarl_private.google_account;
grant select on heycarl.google_account to heycarl_user;

-- The account's id. The same Google account connected again keeps its row
-- and its plugins' data; a row carried over without a Google sub is
-- adopted by its email.
create function heycarl.google_connect(owner text, google_sub text, email text,
                                       scopes text[], sealed text)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
declare
  found_id bigint;
begin
  select a.id into found_id from heycarl_private.google_account a
   where a.owner_sub = google_connect.owner
     and (a.google_sub = google_connect.google_sub
          or (a.google_sub is null and a.email = google_connect.email))
   order by a.google_sub is null
   limit 1
   for update;
  if found_id is null then
    insert into heycarl_private.google_account (owner_sub, google_sub, email, scopes)
    values (google_connect.owner, google_connect.google_sub, google_connect.email,
            google_connect.scopes)
    returning id into found_id;
  else
    update heycarl_private.google_account a
       set google_sub = google_connect.google_sub,
           email = google_connect.email, scopes = google_connect.scopes,
           connected_at = now(), needs_reconnect = false, last_error = null
     where a.id = found_id;
  end if;
  insert into heycarl_private.google_token as t (account_id, sealed)
  values (found_id, decode(google_connect.sealed, 'base64'))
  on conflict (account_id) do update set sealed = excluded.sealed, updated_at = now();
  return found_id;
end
$$;

-- A refresh that brought a new refresh token, or new scopes.
create function heycarl.google_put_token(owner text, account_id bigint, sealed text,
                                         scopes text[])
  returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
begin
  update heycarl_private.google_account a
     set scopes = coalesce(google_put_token.scopes, a.scopes)
   where a.id = google_put_token.account_id and a.owner_sub = google_put_token.owner;
  if not found then
    return false;
  end if;
  update heycarl_private.google_token t
     set sealed = decode(google_put_token.sealed, 'base64'), updated_at = now()
   where t.account_id = google_put_token.account_id;
  return true;
end
$$;

-- The sealed refresh token, base64, or null when there is none.
create function heycarl.google_token(owner text, account_id bigint) returns text
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select encode(t.sealed, 'base64')
      from heycarl_private.google_token t
      join heycarl_private.google_account a on a.id = t.account_id
     where a.id = google_token.account_id and a.owner_sub = google_token.owner
  $$;

-- The accounts a plugin's job visits, everyone's or one owner's: they hold
-- every scope it needs, and they are not waiting to be connected again.
create function heycarl.google_accounts(scopes text[], owner text default null)
  returns table (owner_sub text, account_id bigint, email text)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select a.owner_sub, a.id, a.email
      from heycarl_private.google_account a
     where not a.needs_reconnect and a.scopes @> google_accounts.scopes
       and (google_accounts.owner is null or a.owner_sub = google_accounts.owner)
     order by a.owner_sub, a.id
  $$;

-- One account, whatever its state: for a sync the person asked for.
create function heycarl.google_account_of(owner text, account_id bigint)
  returns table (owner_sub text, account_id bigint, email text, scopes text[],
                 needs_reconnect boolean)
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select a.owner_sub, a.id, a.email, a.scopes, a.needs_reconnect
      from heycarl_private.google_account a
     where a.id = google_account_of.account_id and a.owner_sub = google_account_of.owner
  $$;

create function heycarl.google_set_status(owner text, account_id bigint,
                                          error text default null,
                                          needs_reconnect boolean default false)
  returns void
  language sql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
    update heycarl_private.google_account a
       set last_error = left(google_set_status.error, 1000),
           needs_reconnect = google_set_status.needs_reconnect
     where a.id = google_set_status.account_id and a.owner_sub = google_set_status.owner
  $$;

-- Deletes the account, its token and, by cascade, every plugin's copy.
create function heycarl.google_disconnect(owner text, account_id bigint) returns boolean
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
begin
  delete from heycarl_private.google_account a
   where a.id = google_disconnect.account_id and a.owner_sub = google_disconnect.owner;
  return found;
end
$$;

revoke execute on function heycarl.google_connect, heycarl.google_put_token,
  heycarl.google_token, heycarl.google_accounts, heycarl.google_account_of,
  heycarl.google_set_status, heycarl.google_disconnect from public;
grant execute on function heycarl.google_connect, heycarl.google_put_token,
  heycarl.google_token, heycarl.google_accounts, heycarl.google_account_of,
  heycarl.google_set_status, heycarl.google_disconnect to heycarl_worker;
