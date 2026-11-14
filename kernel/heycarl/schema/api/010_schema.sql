-- The kernel recreates this schema on every start, so views and grants here
-- change without a migration. Every view is security_invoker: a definer view
-- would read as its owner and skip row-level security.
--
-- PostgREST builds its schema cache as heycarl_authenticator, before it
-- switches role, so that role needs USAGE too or every request is a 404.
grant usage on schema heycarl to heycarl_authenticator, heycarl_user, heycarl_worker;

create view heycarl.me with (security_invoker = true) as
  select heycarl_sdk.sub() as sub;
grant select on heycarl.me to heycarl_user;
