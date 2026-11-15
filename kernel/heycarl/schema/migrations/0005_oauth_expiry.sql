-- A grant ends at a fixed time however often it refreshes, and a refresh
-- token presented after its rotation ends its grant (OAuth 2.1 section 4.3.1).

alter table heycarl_private.oauth_grant add column expires_at timestamptz;
update heycarl_private.oauth_grant set expires_at = created_at + interval '90 days';
alter table heycarl_private.oauth_grant alter column expires_at set not null;

-- Refresh tokens already rotated, kept until they would have expired.
create table heycarl_private.oauth_spent (
  token_hash text primary key,
  grant_id bigint not null references heycarl_private.oauth_grant on delete cascade,
  expires_at timestamptz not null
);
