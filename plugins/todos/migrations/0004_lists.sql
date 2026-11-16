-- Lists. A todo with no list is in the Inbox. A subtask is always in its
-- parent's list. Deleting a list cascades to its todos in one transaction,
-- so the trash holds the list and its todos as one entry.
create table list (
  id bigint generated always as identity primary key,
  owner_sub text not null default heycarl_sdk.owner(),
  -- MCP tools name the Inbox "inbox".
  name text not null check (length(name) between 1 and 200 and lower(name) <> 'inbox'),
  position integer not null default 0,
  created_at timestamptz not null default now(),
  constraint list_id_owner unique (id, owner_sub),
  constraint list_name unique (owner_sub, name)
);

alter table list enable row level security;
create policy own on list to heycarl_user
  using (owner_sub = heycarl_sdk.sub())
  with check (owner_sub = heycarl_sdk.sub());

grant select, delete on list to heycarl_user;
grant insert (name, position), update (name, position) on list to heycarl_user;

alter table todo add column list_id bigint;
alter table todo add constraint todo_list
  foreign key (list_id, owner_sub) references list (id, owner_sub) on delete cascade;
create index todo_list_id on todo (list_id) where list_id is not null;

create function follow_parent_list() returns trigger
language plpgsql set search_path = heycarl_plugin_todos, pg_temp as $$
declare
  parent_list bigint;
begin
  if new.parent_id is null then
    return new;
  end if;
  select t.list_id into parent_list from todo t where t.id = new.parent_id;
  if not found then
    -- The foreign key reports the missing parent.
    return new;
  end if;
  if tg_op = 'INSERT' and new.list_id is null
     or tg_op = 'UPDATE' and new.parent_id is distinct from old.parent_id then
    new.list_id := parent_list;
  elsif new.list_id is distinct from parent_list then
    raise exception 'a subtask is in its parent''s list'
      using errcode = 'check_violation';
  end if;
  return new;
end
$$;

create trigger todo_follow_parent_list
  before insert or update of parent_id, list_id on todo
  for each row execute function follow_parent_list();

create function carry_list_to_subtasks() returns trigger
language plpgsql set search_path = heycarl_plugin_todos, pg_temp as $$
begin
  update todo set list_id = new.list_id
   where parent_id = new.id and list_id is distinct from new.list_id;
  return null;
end
$$;

-- Also of parent_id: UPDATE OF fires on the columns a statement sets, and a
-- move sets only parent_id while follow_parent_list changes list_id.
create trigger todo_carry_list
  after update of parent_id, list_id on todo
  for each row when (new.list_id is distinct from old.list_id)
  execute function carry_list_to_subtasks();

grant insert (list_id), update (list_id) on todo to heycarl_user;

select heycarl_sdk.track('list');
select heycarl_sdk.entry_route('list', '/p/todos/heycarl-todos#/list/{id}');
