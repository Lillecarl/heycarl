-- A reminder's link opens when the person taps the notification. `like '/%'`
-- also matched '//evil.example', which a browser reads as another site.
update reminder set url = null where url !~ '^/([^/\\]|$)';
alter table reminder drop constraint reminder_url_check;
alter table reminder add constraint reminder_url_check check (url ~ '^/([^/\\]|$)');
