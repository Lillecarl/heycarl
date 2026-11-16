"""MCP tools, a resource and a prompt for todos. The page needs no backend."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from heycarl.mcpserver import (
    UNTRUSTED,
    call,
    confirm,
    embed,
    kernel_of,
    static_context,
)
from mcp.server.mcpserver import (
    Context,
    Message,
    UserMessage,
)
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from heycarl.app import PluginContext

SCOPE: Final = "todos"
TAGS: Final = "tags"
COLUMNS: Final = "id,parent_id,list_id,title,notes,due,done_at,created_at"
# The name for "no list"; the schema refuses a list by that name.
INBOX: Final = "inbox"
# Any: PostgREST rows.
Rows = list[dict[str, Any]]
# A list by id, by name, or INBOX.
ListRef = int | str


def tree(rows: Rows) -> Rows:
    """Nests each row under its parent as `subtasks`, keeping the order.

    A row whose parent is not in `rows` (a done parent left out) is a root.
    """
    nodes = {row["id"]: {**row, "subtasks": []} for row in rows}
    roots: Rows = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        (parent["subtasks"] if parent is not None else roots).append(node)
    return roots


async def list_id(ctx: Context, ref: ListRef) -> int | None:
    """The id of a list named by id or name; None for the Inbox."""
    if isinstance(ref, str) and ref.strip().lower() == INBOX:
        return None
    # A name first: a list may be called "2026".
    tries = [{"name": f"eq.{ref}"}] if isinstance(ref, str) else []
    if isinstance(ref, int) or ref.isdigit():
        tries.append({"id": f"eq.{int(ref)}"})
    for params in tries:
        rows: Rows = await call(
            ctx, SCOPE, "GET", "list", params={**params, "select": "id"}
        )
        if rows:
            return int(rows[0]["id"])
    raise ToolError(f"no list {ref!r}; todos_lists shows them")


def need_tags(ctx: Context) -> None:
    if kernel_of(ctx).schema_for(TAGS) is None:
        raise ToolError("tags need the tags plugin, which this Hey Carl does not run")


async def tag_id(ctx: Context, name: str) -> int:
    """The id of the person's tag by name, in any case."""
    need_tags(ctx)
    # Every tag, not an ilike filter: a name may hold PostgREST's wildcards.
    rows: Rows = await call(ctx, TAGS, "GET", "tag", params={"select": "id,name"})
    wanted = name.strip().lower()
    for row in rows:
        if row["name"].lower() == wanted:
            return int(row["id"])
    raise ToolError(f"no tag {name!r}; tags_list shows them")


async def tagged_ids(ctx: Context, name: str) -> set[int]:
    keys: list[dict[str, Any]] = await call(
        ctx,
        TAGS,
        "POST",
        "rpc/tagged_keys",
        body={"tag_id": await tag_id(ctx, name), "of_scope": SCOPE, "of_table": "todo"},
    )
    return {int(k["id"]) for k in keys}


async def todos(
    ctx: Context,
    include_done: bool = False,
    ref: ListRef | None = None,
    tag: str | None = None,
) -> Rows:
    params = {"select": COLUMNS, "order": "due.asc.nullslast,created_at"}
    if not include_done:
        params["done_at"] = "is.null"
    if ref is not None:
        found = await list_id(ctx, ref)
        params["list_id"] = "is.null" if found is None else f"eq.{found}"
    rows: Rows = await call(ctx, SCOPE, "GET", "todo", params=params)
    if tag is not None:
        ids = await tagged_ids(ctx, tag)
        rows = [row for row in rows if row["id"] in ids]
    return tree(rows)


def counted(lists: Rows, open_todos: Rows) -> Rows:
    """The Inbox, then each list, with its number of open todos."""
    counts: dict[int | None, int] = {}
    for todo in open_todos:
        counts[todo["list_id"]] = counts.get(todo["list_id"], 0) + 1
    inbox = {"id": None, "name": INBOX, "open": counts.get(None, 0)}
    return [inbox, *({**row, "open": counts.get(row["id"], 0)} for row in lists)]


def register(ctx: PluginContext) -> None:
    mcp = ctx.mcp

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def todos_list(
        ctx: Context,
        include_done: bool = False,
        list: ListRef | None = None,
        tag: str | None = None,
    ) -> dict[str, Rows]:
        """List todos as a tree: each todo holds its `subtasks`. The soonest
        due come first, those with no due date last. With `list` (an id, a
        name, or "inbox" for todos in no list), only that list. With `tag`
        (a tag name), only the todos that carry it; a tagged subtask of an
        untagged todo shows at the top level."""
        return {"todos": await todos(ctx, include_done, list, tag)}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def todos_lists(ctx: Context) -> dict[str, Rows]:
        """The todo lists, such as a shopping list, with the number of open
        todos in each. The first is the Inbox: todos in no list."""
        lists: Rows = await call(
            ctx,
            SCOPE,
            "GET",
            "list",
            params={"select": "id,name,position", "order": "position,name"},
        )
        open_todos: Rows = await call(
            ctx,
            SCOPE,
            "GET",
            "todo",
            params={"select": "list_id", "done_at": "is.null"},
        )
        return {"lists": counted(lists, open_todos)}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_list_add(ctx: Context, name: str) -> dict[str, Any]:
        """Make a todo list, such as "Shopping"."""
        [row] = await call(ctx, SCOPE, "POST", "list", body={"name": name})
        return row

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_list_rename(
        ctx: Context, list: ListRef, name: str
    ) -> dict[str, Any]:
        """Rename a todo list."""
        found = await list_id(ctx, list)
        if found is None:
            raise ToolError("the Inbox has no name to change")
        [row] = await call(
            ctx,
            SCOPE,
            "PATCH",
            "list",
            params={"id": f"eq.{found}"},
            body={"name": name},
        )
        return row

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def todos_list_delete(ctx: Context, list: ListRef) -> str:
        """Delete a todo list and every todo in it. They go to the trash
        together. Asks the person to confirm."""
        found = await list_id(ctx, list)
        if found is None:
            raise ToolError("the Inbox cannot be deleted")
        [row] = await call(
            ctx, SCOPE, "GET", "list", params={"id": f"eq.{found}", "select": "name"}
        )
        held: Rows = await call(
            ctx, SCOPE, "GET", "todo", params={"list_id": f"eq.{found}", "select": "id"}
        )
        await confirm(
            ctx, f"Delete the list {row['name']!r} and its {len(held)} todos?"
        )
        await call(ctx, SCOPE, "DELETE", "list", params={"id": f"eq.{found}"})
        return "deleted"

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_add(
        ctx: Context,
        title: str,
        notes: str = "",
        due: str | None = None,
        parent_id: int | None = None,
        list: ListRef | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """Add a todo. `due` is an ISO 8601 time, with its offset. With
        `parent_id`, the todo is a subtask of that todo, in its list. With
        `list` (an id or a name), it goes in that list; with neither, in the
        Inbox. With `tag`, the todo carries that tag, made if missing."""
        if tag is not None:
            need_tags(ctx)
        body: dict[str, object] = {
            "title": title,
            "notes": notes,
            "due": due,
            "parent_id": parent_id,
        }
        if list is not None and parent_id is None:
            body["list_id"] = await list_id(ctx, list)
        [row] = await call(ctx, SCOPE, "POST", "todo", body=body)
        if tag is not None:
            await call(
                ctx,
                TAGS,
                "POST",
                "rpc/tag_entry",
                body={
                    "of_scope": SCOPE,
                    "of_table": "todo",
                    "of_key": {"id": row["id"]},
                    "name": tag,
                },
            )
        return row

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_move(
        ctx: Context,
        todo_id: int,
        parent_id: int | None = None,
        list: ListRef | None = None,
    ) -> dict[str, Any]:
        """Make a todo a subtask of `parent_id`, or a top-level todo with no
        parent_id. With `list` and no parent_id, it goes to the top of that
        list ("inbox" for no list). Its own subtasks move with it."""
        body: dict[str, object] = {"parent_id": parent_id}
        if list is not None and parent_id is None:
            body["list_id"] = await list_id(ctx, list)
        rows: Rows = await call(
            ctx,
            SCOPE,
            "PATCH",
            "todo",
            params={"id": f"eq.{todo_id}"},
            body=body,
        )
        if not rows:
            raise ToolError(f"no todo with id {todo_id}")
        return rows[0]

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_done(
        ctx: Context, todo_id: int, done: bool = True
    ) -> dict[str, Any]:
        """Mark a todo done, or open again with done=false."""
        rows: Rows = await call(
            ctx,
            SCOPE,
            "PATCH",
            "todo",
            params={"id": f"eq.{todo_id}"},
            body={"done_at": datetime.now(UTC).isoformat() if done else None},
        )
        if not rows:
            raise ToolError(f"no todo with id {todo_id}")
        return rows[0]

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def todos_delete(ctx: Context, todo_id: int) -> str:
        """Delete a todo and its subtasks. They go to the trash together.
        Asks the person to confirm."""
        rows: Rows = await call(
            ctx, SCOPE, "GET", "todo", params={"id": f"eq.{todo_id}", "select": "title"}
        )
        if not rows:
            raise ToolError(f"no todo with id {todo_id}")
        await confirm(ctx, f"Delete the todo {rows[0]['title']!r}?")
        await call(ctx, SCOPE, "DELETE", "todo", params={"id": f"eq.{todo_id}"})
        return "deleted"

    @mcp.resource(
        "heycarl://todos",
        name="todos",
        title="Open todos",
        description="Todos not yet done, the soonest due first.",
        mime_type="application/json",
    )
    async def todos_resource() -> dict[str, Rows]:
        return {"todos": await todos(static_context(mcp))}

    @mcp.prompt(
        title="Plan my day",
        description="A short plan for today from my open todos and the"
        " reminders due soon.",
    )
    async def plan_my_day(ctx: Context) -> list[Message]:
        messages: list[Message] = [
            UserMessage(
                f"It is {datetime.now(UTC):%A %Y-%m-%d %H:%M} UTC. From my open"
                " todos and reminders below, make a plan for today: what to do"
                " first, what can wait, and anything overdue. Offer to mark"
                " things done with todos_done when I tell you. " + UNTRUSTED
            ),
            embed("heycarl://todos", {"todos": await todos(ctx)}),
        ]
        # Reminders is another plugin; the plan works without it.
        if kernel_of(ctx).schema_for("reminders") is not None:
            reminders = await call(
                ctx,
                "reminders",
                "GET",
                "reminder",
                params={
                    "select": "id,title,body,due_at",
                    "state": "in.(pending,sending)",
                    "order": "due_at",
                    "limit": "50",
                },
            )
            messages.append(embed("heycarl://reminders", {"reminders": reminders}))
        return messages
