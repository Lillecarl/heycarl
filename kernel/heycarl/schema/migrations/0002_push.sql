-- One row per device that accepted notifications: the browser's
-- PushSubscription, as `subscription.toJSON()` gives it.
create table heycarl_private.push_subscription (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  endpoint text not null unique check (endpoint like 'https://%'),
  p256dh text not null,
  auth text not null,
  user_agent text not null default '',
  created_at timestamptz not null default now(),
  last_success_at timestamptz,
  failures integer not null default 0
);

alter table heycarl_private.push_subscription enable row level security;
create policy own on heycarl_private.push_subscription to heycarl_user
  using (owner_sub = heycarl_sdk.sub())
  with check (owner_sub = heycarl_sdk.sub());
-- The kernel delivers for every user, and drops an endpoint the push
-- service says is gone.
create policy deliver on heycarl_private.push_subscription to heycarl_worker
  using (true);

grant usage on schema heycarl_private to heycarl_user, heycarl_worker;
grant select, delete on heycarl_private.push_subscription to heycarl_user;
grant insert (endpoint, p256dh, auth, user_agent) on heycarl_private.push_subscription to heycarl_user;
grant select, delete, update (last_success_at, failures) on heycarl_private.push_subscription to heycarl_worker;
