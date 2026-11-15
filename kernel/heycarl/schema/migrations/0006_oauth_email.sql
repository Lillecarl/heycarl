-- The address that consented, so a grant ends when allowedEmails drops it.
alter table heycarl_private.oauth_grant add column email text;
update heycarl_private.oauth_grant g set email = (
  select s.email from heycarl_private.session s
  where s.sub = g.sub and s.email is not null
  order by s.expires_at desc limit 1
);
