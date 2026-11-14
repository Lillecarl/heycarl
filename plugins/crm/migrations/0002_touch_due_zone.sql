-- The due date in the owner's zone, as next_occurrence does: in UTC, an
-- activity logged at local midnight east of Greenwich fell on the day before.
create or replace function touch_due(c contact) returns date
  language sql stable
  set search_path = heycarl_plugin_crm, pg_temp
  as $$
    select ((coalesce(last_contacted(c), c.created_at) at time zone owner_zone(c.owner_sub))
            + make_interval(days => c.stay_in_touch_days))::date
     where c.stay_in_touch_days is not null
  $$;
