"""MCP tools for todos. The page needs no backend; these are the only Python."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from heycarl.mcpserver import call, confirm
from mcp.server.fastmcp import Context  # noqa: TC002 -- FastMCP reads it at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from heycarl.app import PluginContext

SCOPE: Final = "todos"
COLUMNS: Final = "id,title,notes,due,done_at,created_at"
# Any: PostgREST rows.
Rows = list[dict[str, Any]]


def register(ctx: PluginContext) -> None:
    mcp = ctx.mcp

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def todos_list(ctx: Context, include_done: bool = False) -> Rows:
        """List todos, the soonest due first; those with no due date last."""
        params = {"select": COLUMNS, "order": "due.asc.nullslast,created_at"}
        if not include_done:
            params["done_at"] = "is.null"
        return await call(ctx, SCOPE, "GET", "todo", params=params)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def todos_add(
        ctx: Context, title: str, notes: str = "", due: str | None = None
    ) -> dict[str, Any]:
        """Add a todo. `due` is an ISO 8601 time, with its offset."""
        [row] = await call(
            ctx,
            SCOPE,
            "POST",
            "todo",
            body={"title": title, "notes": notes, "due": due},
        )
        return row

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
            raise ValueError(f"no todo with id {todo_id}")
        return rows[0]

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def todos_delete(ctx: Context, todo_id: int) -> str:
        """Delete a todo for good. Asks the person to confirm."""
        rows: Rows = await call(
            ctx, SCOPE, "GET", "todo", params={"id": f"eq.{todo_id}", "select": "title"}
        )
        if not rows:
            raise ValueError(f"no todo with id {todo_id}")
        await confirm(ctx, f"Delete the todo {rows[0]['title']!r}?")
        await call(ctx, SCOPE, "DELETE", "todo", params={"id": f"eq.{todo_id}"})
        return "deleted"
