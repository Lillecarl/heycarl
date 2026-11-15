-- History and trash for every table a person writes (DESIGN.md).
select heycarl_sdk.track('contact', array['updated_at']);
select heycarl_sdk.track('contact_field');
select heycarl_sdk.track('relationship');
select heycarl_sdk.track('note');
select heycarl_sdk.track('activity');
select heycarl_sdk.track('activity_contact');
select heycarl_sdk.track('tag');
select heycarl_sdk.track('contact_tag');
select heycarl_sdk.track('gift');
select heycarl_sdk.track('preference');
-- sync_date keeps these in step with the reminder.
select heycarl_sdk.track('important_date', array['reminder_id', 'reminded_for']);
