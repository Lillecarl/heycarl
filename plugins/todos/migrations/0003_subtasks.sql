-- Subtasks. A todo hangs under at most one parent of the same person; the
-- composite key holds that even though a foreign key check ignores RLS.
-- Deleting a parent cascades to its subtree in one transaction, so the
-- trash holds the subtree as one entry and a restore brings it back whole.
alter table todo add constraint todo_id_owner unique (id, owner_sub);
alter table todo add column parent_id bigint;
alter table todo add constraint todo_parent
  foreign key (parent_id, owner_sub) references todo (id, owner_sub) on delete cascade;
alter table todo add constraint todo_not_own_parent check (parent_id <> id);
create index todo_parent_id on todo (parent_id) where parent_id is not null;

create function refuse_cycle() returns trigger
language plpgsql set search_path = heycarl_plugin_todos, pg_temp as $$
begin
  if new.parent_id is null then
    return new;
  end if;
  -- Two moves at once could each pass alone and make a loop together.
  perform pg_advisory_xact_lock(hashtext('heycarl_plugin_todos.todo'), hashtext(new.owner_sub));
  if exists (
    with recursive up (id, parent_id) as (
      select t.id, t.parent_id from todo t where t.id = new.parent_id
      union
      select t.id, t.parent_id from todo t join up on t.id = up.parent_id
    )
    select from up where up.id = new.id
  ) then
    raise exception 'a todo cannot be under its own subtask'
      using errcode = 'check_violation';
  end if;
  return new;
end
$$;

create trigger todo_refuse_cycle
  before insert or update of parent_id on todo
  for each row execute function refuse_cycle();

grant insert (parent_id), update (parent_id) on todo to heycarl_user;
