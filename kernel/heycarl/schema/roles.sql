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
  -- heycarl_gate checks an app password and may do nothing else.
  foreach r in array array['heycarl_user', 'heycarl_worker', 'heycarl_gate'] loop
    if not exists (select from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
  end loop;
end
$$;

-- PostgREST switches to the role in the token's `role` claim, so it must be a
-- member of each and inherit the rights of none.
alter role heycarl_authenticator noinherit;
grant heycarl_user, heycarl_worker, heycarl_gate to heycarl_authenticator;

select format('create database %I owner heycarl_owner', :'database')
where not exists (select from pg_database where datname = :'database')
\gexec

-- Since PostgreSQL 15 nobody but the owner may create in public. Revoke the
-- rest too: the API roles see only what a migration grants them.
-- Every role in the cluster may connect to a new database by default. Another
-- app's role on the same server gets no session here.
select format('revoke all on database %I from public', :'database') \gexec
select format('grant connect on database %I to heycarl_authenticator', :'database') \gexec

\connect :database
revoke all on schema public from public;
grant usage on schema public to heycarl_owner;
