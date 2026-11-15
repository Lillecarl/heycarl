"""MCP tools and a resource for reminders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from heycarl.mcpserver import call, confirm, static_context
from mcp.server.fastmcp import Context  # noqa: TC002 -- FastMCP reads it at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

SCOPE: Final = "reminders"
COLUMNS: Final = "id,title,body,due_at,state,sent_at"
# Any: PostgREST rows.
Rows = list[dict[str, Any]]


async def reminders(ctx: Context, include_sent: bool = False) -> Rows:
    params = {"select": COLUMNS, "order": "due_at"}
    if not include_sent:
        params["state"] = "in.(pending,sending,failed)"
    return await call(ctx, SCOPE, "GET", "reminder", params=params)


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def reminders_list(
        ctx: Context, include_sent: bool = False
    ) -> dict[str, Rows]:
        """List reminders, the soonest first. Birthdays from People are here too."""
        return {"reminders": await reminders(ctx, include_sent)}

    @mcp.resource(
        "heycarl://reminders",
        name="reminders",
        title="Reminders",
        description="Reminders not yet sent, the soonest first.",
        mime_type="application/json",
    )
    async def reminders_resource() -> dict[str, Rows]:
        return {"reminders": await reminders(static_context(mcp))}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def reminders_add(
        ctx: Context, title: str, due_at: str, body: str = ""
    ) -> dict[str, Any]:
        """Add a reminder. It arrives as a push notification on the person's
        devices at `due_at`, an ISO 8601 time with its offset."""
        [row] = await call(
            ctx,
            SCOPE,
            "POST",
            "reminder",
            body={"title": title, "due_at": due_at, "body": body},
        )
        return row

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def reminders_move(
        ctx: Context, reminder_id: int, due_at: str
    ) -> dict[str, Any]:
        """Move a reminder to a new time. A sent one is armed again."""
        rows: Rows = await call(
            ctx,
            SCOPE,
            "PATCH",
            "reminder",
            params={"id": f"eq.{reminder_id}"},
            body={"due_at": due_at},
        )
        if not rows:
            raise ValueError(f"no reminder with id {reminder_id}")
        return rows[0]

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def reminders_delete(ctx: Context, reminder_id: int) -> str:
        """Delete a reminder. Asks the person to confirm."""
        rows: Rows = await call(
            ctx,
            SCOPE,
            "GET",
            "reminder",
            params={"id": f"eq.{reminder_id}", "select": "title"},
        )
        if not rows:
            raise ValueError(f"no reminder with id {reminder_id}")
        await confirm(ctx, f"Delete the reminder {rows[0]['title']!r}?")
        await call(ctx, SCOPE, "DELETE", "reminder", params={"id": f"eq.{reminder_id}"})
        return "deleted"
