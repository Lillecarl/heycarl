-- A restore that brings back an old time must not send the reminder
-- again: history marks its transaction with heycarl.restoring.
create or replace function rearm() returns trigger
  language plpgsql
  as $$
begin
  if new.due_at is distinct from old.due_at
     and coalesce(current_setting('heycarl.restoring', true), '') <> 'on' then
    new.state := 'pending';
    new.attempts := 0;
    new.claimed_at := null;
    new.sent_at := null;
    new.last_error := null;
  end if;
  return new;
end
$$;
