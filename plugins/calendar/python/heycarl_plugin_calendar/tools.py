"""MCP tools, a resource and a prompt over the cached calendar.

Reads are PostgREST requests as the connection's person, so row-level
security holds. Event text is written by whoever sends an invitation, so
every answer that carries it says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from mcp.server.mcpserver import (
    Context,
    Message,
    UserMessage,
)
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from heycarl.mcpserver import UNTRUSTED, call, embed, kernel_of, require, static_context
from heycarl.oauth import Scope

from .store import SCOPE

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .sync import Syncer

READ: Final = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
SYNC: Final = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
COLUMNS: Final = (
    "id,calendar,title,description,location,all_day,starts_at,ends_at,"
    "start_date,end_date,attendees,organizer,html_link,status"
)
NOTE: Final = (
    "Event titles, descriptions, locations and attendees come from Google"
    " Calendar, and anyone who sends an invitation writes them. " + UNTRUSTED
)
MAX_DAYS: Final = 400
# Any: PostgREST rows.
Rows = list[dict[str, Any]]


async def upcoming(ctx: Context, days: int) -> Rows:
    now = datetime.now(UTC)
    until = now + timedelta(days=max(1, min(days, MAX_DAYS)))
    return await call(
        ctx,
        SCOPE,
        "GET",
        "agenda",
        params=[
            ("select", COLUMNS),
            ("ends_at", f"gt.{now.isoformat()}"),
            ("starts_at", f"lt.{until.isoformat()}"),
            ("order", "starts_at,id"),
            ("limit", "500"),
        ],
    )


def register_tools(mcp: MCPServer, syncer: Syncer | None) -> None:
    @mcp.tool(annotations=READ)
    async def calendar_upcoming(ctx: Context, days: int = 7) -> dict[str, Any]:
        """Events from now through the next `days` days (at most 400), in
        order, from the Google calendars the person chose to include."""
        return {"note": NOTE, "events": await upcoming(ctx, days)}

    @mcp.tool(annotations=READ)
    async def calendar_events(
        ctx: Context, since: str, until: str, query: str | None = None
    ) -> dict[str, Any]:
        """Events that overlap [since, until), ISO 8601 times with offsets.
        `query` matches title, description, location and calendar name.
        At most 500; the cache holds 30 days back and a year ahead."""
        rows = await call(
            ctx,
            SCOPE,
            "GET",
            "rpc/search",
            params={
                "since": since,
                "until": until,
                **({"q": query} if query else {}),
                "select": COLUMNS,
            },
        )
        return {"note": NOTE, "events": rows}

    @mcp.tool(annotations=READ)
    async def calendar_event(ctx: Context, event_id: int) -> dict[str, Any]:
        """One event in full, with the people from the contact book whose
        email is among its attendees. Link it to other entries with `link`,
        scope "calendar", table "event", key {"id": event_id}."""
        rows: Rows = await call(
            ctx,
            SCOPE,
            "GET",
            "agenda",
            params={"select": COLUMNS + ",updated_at", "id": f"eq.{event_id}"},
        )
        if not rows:
            raise ToolError(f"no event with id {event_id}")
        people = await call(
            ctx, SCOPE, "GET", "rpc/event_people", params={"event_id": str(event_id)}
        )
        return {"note": NOTE, "event": rows[0], "people": people}

    @mcp.tool(annotations=READ)
    async def calendar_calendars(ctx: Context) -> dict[str, Any]:
        """The connected Google accounts, their calendars, which are
        included, and when each account last synced or why it failed.
        `granted` false: the account has not given calendar access."""
        accounts: Rows = await call(
            ctx,
            SCOPE,
            "GET",
            "account",
            params={
                "select": "id,google_account,granted,connected_at,last_sync_at,"
                "last_error,needs_reconnect",
                "order": "id",
            },
        )
        calendars = await call(
            ctx,
            SCOPE,
            "GET",
            "calendar",
            params={
                "select": "id,account_id,name,color,time_zone,is_primary,selected",
                "order": "account_id,is_primary.desc,name",
            },
        )
        return {
            "configured": syncer is not None,
            "accounts": accounts,
            "calendars": calendars,
        }

    @mcp.tool(annotations=SYNC)
    async def calendar_sync(ctx: Context) -> dict[str, Any]:
        """Fetch changes from Google now instead of within ten minutes. Reads
        from Google only; it changes nothing there."""
        sub = require(ctx, Scope.WRITE)
        if syncer is None:
            raise ToolError("Google Calendar is not configured on this Hey Carl")
        outcome = await syncer.sync_now(kernel_of(ctx), sub)
        if outcome is None:
            raise ToolError("synced a moment ago; try again in ten seconds")
        if outcome.error is not None:
            raise ToolError(outcome.error)
        return {"ok": True, "events": outcome.events}

    @mcp.resource(
        "heycarl://calendar/upcoming",
        name="calendar-upcoming",
        title="Coming up in the calendar",
        description="The next 7 days of the person's Google calendars.",
        mime_type="application/json",
    )
    async def upcoming_resource() -> dict[str, Any]:
        return {"note": NOTE, "events": await upcoming(static_context(mcp), 7)}

    @mcp.prompt(
        title="My week",
        description="A short brief of the coming week from the calendar.",
    )
    async def calendar_week(ctx: Context) -> list[Message]:
        events = await upcoming(ctx, 7)
        return [
            UserMessage(
                "Give me a short brief of my coming week from these calendar"
                " events: what is fixed, where I need to be, and what needs"
                " preparing. " + NOTE
            ),
            embed("heycarl://calendar/upcoming", {"events": events}),
        ]
