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
COLUMNS: Final = "id,parent_id,title,notes,due,done_at,created_at"
# Any: PostgREST rows.
Rows = list[dict[str, Any]]


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


async def todos(ctx: Context, include_done: bool = False) -> Rows:
    params = {"select": COLUMNS, "order": "due.asc.nullslast,created_at"}
    if not include_done:
        params["done_at"] = "is.null"
    return tree(await call(ctx, SCOPE, "GET", "todo", params=params))


def register(ctx: PluginContext) -> None:
    mcp = ctx.mcp

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def todos_list(ctx: Context, include_done: bool = False) -> dict[str, Rows]:
        """List todos as a tree: each todo holds its `subtasks`. The soonest
        due come first, those with no due date last."""
        return {"todos": await todos(ctx, include_done)}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_add(
        ctx: Context,
        title: str,
        notes: str = "",
        due: str | None = None,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        """Add a todo. `due` is an ISO 8601 time, with its offset. With
        `parent_id`, the todo is a subtask of that todo."""
        [row] = await call(
            ctx,
            SCOPE,
            "POST",
            "todo",
            body={"title": title, "notes": notes, "due": due, "parent_id": parent_id},
        )
        return row

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_move(
        ctx: Context, todo_id: int, parent_id: int | None = None
    ) -> dict[str, Any]:
        """Make a todo a subtask of `parent_id`, or a top-level todo with no
        parent_id. Its own subtasks move with it."""
        rows: Rows = await call(
            ctx,
            SCOPE,
            "PATCH",
            "todo",
            params={"id": f"eq.{todo_id}"},
            body={"parent_id": parent_id},
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
