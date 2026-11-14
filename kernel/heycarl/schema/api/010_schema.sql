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

create view heycarl.push_subscription with (security_invoker = true) as
  select id, owner_sub, endpoint, p256dh, auth, user_agent, created_at,
         last_success_at, failures
  from heycarl_private.push_subscription;
grant select, delete on heycarl.push_subscription to heycarl_user;
grant insert (endpoint, p256dh, auth, user_agent) on heycarl.push_subscription to heycarl_user;
grant select, delete, update (last_success_at, failures) on heycarl.push_subscription to heycarl_worker;
