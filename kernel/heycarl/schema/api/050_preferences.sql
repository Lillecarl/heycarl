-- Preferences (migration 0010). Invoker throughout: RLS decides whose row.

create view heycarl.preference with (security_invoker = true) as
  select theme, updated_at from heycarl_private.preference;
grant select on heycarl.preference to heycarl_user;

create function heycarl.preference_set_theme(theme text) returns void
  language sql volatile security invoker
  set search_path = pg_catalog, pg_temp
  as $$
    insert into heycarl_private.preference as p (theme) values (preference_set_theme.theme)
    on conflict (owner_sub) do update set theme = excluded.theme, updated_at = now();
  $$;

revoke execute on function heycarl.preference_set_theme from public;
grant execute on function heycarl.preference_set_theme to heycarl_user;
