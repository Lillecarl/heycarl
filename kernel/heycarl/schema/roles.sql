-- Run once by a superuser before the kernel's first start, and again on every
-- deploy: each statement is idempotent. The kernel's own login (heycarl_owner)
-- has no CREATEROLE, so it cannot make these itself.
--
-- psql -v database=heycarl -f roles.sql

do $$
declare
  r text;
begin
  foreach r in array array['heycarl_owner', 'heycarl_authenticator'] loop
    if not exists (select from pg_roles where rolname = r) then
      execute format('create role %I login', r);
    end if;
  end loop;
  foreach r in array array['heycarl_user', 'heycarl_worker'] loop
    if not exists (select from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
  end loop;
end
$$;

-- PostgREST switches to the role in the token's `role` claim, so it must be a
-- member of both and inherit the rights of neither.
alter role heycarl_authenticator noinherit;
grant heycarl_user, heycarl_worker to heycarl_authenticator;

select format('create database %I owner heycarl_owner', :'database')
where not exists (select from pg_database where datname = :'database')
\gexec

-- Since PostgreSQL 15 nobody but the owner may create in public. Revoke the
-- rest too: the API roles see only what a migration grants them.
\connect :database
revoke all on schema public from public;
grant usage on schema public to heycarl_owner;
