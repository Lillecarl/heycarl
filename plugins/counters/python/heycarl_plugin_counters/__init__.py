"""MCP tools and a resource for counters. The page needs no backend.

Every sum is SQL: the counter_value view and the functions in
migrations/0001_counter.sql. A tool is one PostgREST request as the
connection's person, so row-level security holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from heycarl.mcpserver import call, confirm, static_context
from mcp.server.mcpserver import Context  # noqa: TC002 -- MCPServer reads it at runtime
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from heycarl.app import PluginContext

SCOPE: Final = "counters"
READ: Final = ToolAnnotations(readOnlyHint=True)
WRITE: Final = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
# Set and reset end at the same value however often they run.
SETTLE: Final = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True
)
COLUMNS: Final = "id,name,unit,notes,step,value,events,last_at,created_at"
EVENT_COLUMNS: Final = "id,delta,note,at"
EDITABLE: Final = frozenset({"name", "unit", "notes", "step"})
# Any: PostgREST rows. A tool never returns Rows bare: MCPServer sends each
# item of a list as its own text block.
Row = dict[str, Any]
Rows = list[Row]


async def counters(ctx: Context) -> Rows:
    return await call(
        ctx,
        SCOPE,
        "GET",
        "counter_value",
        params={"select": COLUMNS, "order": "name,id"},
    )


async def counter(ctx: Context, counter_id: int) -> Row:
    rows: Rows = await call(
        ctx,
        SCOPE,
        "GET",
        "counter_value",
        params={"select": COLUMNS, "id": f"eq.{counter_id}"},
    )
    if not rows:
        raise ToolError(f"no counter with id {counter_id}")
    return rows[0]


async def rpc(ctx: Context, function: str, args: dict[str, object]) -> Any:
    """A write function; a None argument takes the function's default."""
    body = {k: v for k, v in args.items() if v is not None}
    return await call(ctx, SCOPE, "POST", f"rpc/{function}", body=body)


async def read_rpc(ctx: Context, function: str, args: dict[str, object]) -> Any:
    """A stable function, by GET, so the `read` scope may call it."""
    params = {
        k: str(v).lower() if isinstance(v, bool) else str(v)
        for k, v in args.items()
        if v is not None
    }
    return await call(ctx, SCOPE, "GET", f"rpc/{function}", params=params)


def register(ctx: PluginContext) -> None:
    mcp = ctx.mcp

    @mcp.tool(annotations=READ)
    async def counters_list(ctx: Context) -> dict[str, Rows]:
        """List counters with their current values, by name. Use the ids
        here for every other counters tool."""
        return {"counters": await counters(ctx)}

    @mcp.tool(annotations=WRITE)
    async def counters_add(
        ctx: Context, name: str, unit: str = "", notes: str = "", step: int = 1
    ) -> Row:
        """Make a counter at 0. `step` is what one + or - adds or takes away.
        Check counters_list first: this never merges."""
        [row] = await call(
            ctx,
            SCOPE,
            "POST",
            "counter",
            body={"name": name, "unit": unit, "notes": notes, "step": step},
        )
        return await counter(ctx, row["id"])

    @mcp.tool(annotations=WRITE)
    async def counters_update(
        ctx: Context, counter_id: int, changes: dict[str, Any]
    ) -> Row:
        """Rename a counter or change its unit, notes or step. `changes`
        holds any of: name, unit, notes, step. The value stays as it is."""
        unknown = set(changes) - EDITABLE
        if unknown:
            raise ToolError(
                f"cannot change {sorted(unknown)}; allowed: {sorted(EDITABLE)}"
            )
        if not changes:
            raise ToolError("nothing to change")
        rows: Rows = await call(
            ctx,
            SCOPE,
            "PATCH",
            "counter",
            params={"id": f"eq.{counter_id}"},
            body=changes,
        )
        if not rows:
            raise ToolError(f"no counter with id {counter_id}")
        return await counter(ctx, counter_id)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def counters_delete(ctx: Context, counter_id: int) -> str:
        """Delete a counter with all its events. Asks the person to confirm.
        The delete goes to the trash, and `restore` brings it back."""
        row = await counter(ctx, counter_id)
        await confirm(
            ctx, f"Delete the counter {row['name']!r} ({row['value']}) and its history?"
        )
        await call(ctx, SCOPE, "DELETE", "counter", params={"id": f"eq.{counter_id}"})
        return "deleted"

    @mcp.tool(annotations=WRITE)
    async def counters_bump(
        ctx: Context,
        counter_id: int,
        delta: int | None = None,
        note: str = "",
        at: str | None = None,
    ) -> Row:
        """Count: add `delta`, or the counter's step when it is left out.
        A negative delta counts down. `at` is an ISO 8601 time with its
        offset, for something that happened earlier; the default is now."""
        if delta == 0:
            raise ToolError("delta must not be 0")
        return await rpc(
            ctx,
            "bump",
            {"counter_id": counter_id, "delta": delta, "note": note, "at": at},
        )

    @mcp.tool(annotations=SETTLE)
    async def counters_set(
        ctx: Context, counter_id: int, value: int, note: str = ""
    ) -> Row:
        """Set a counter to `value`. Adds one event with the difference, so
        the history keeps every earlier count."""
        return await rpc(
            ctx,
            "set_value",
            {"counter_id": counter_id, "target": value, "note": note},
        )

    @mcp.tool(annotations=SETTLE)
    async def counters_reset(ctx: Context, counter_id: int, note: str = "") -> Row:
        """Set a counter back to 0, as one event; the history stays."""
        return await rpc(ctx, "reset_value", {"counter_id": counter_id, "note": note})

    @mcp.tool(annotations=WRITE)
    async def counters_undo(ctx: Context, counter_id: int) -> dict[str, Any]:
        """Take back the newest event of a counter. It goes to the trash, so
        `undo` with its version brings it back. `undone` is null when the
        counter has no events."""
        undone = await rpc(ctx, "undo_last", {"counter_id": counter_id})
        return {"undone": undone, "counter": await counter(ctx, counter_id)}

    @mcp.tool(annotations=READ)
    async def counters_history(
        ctx: Context, counter_id: int, limit: int = 50
    ) -> dict[str, Any]:
        """A counter with its newest events first, up to `limit` (500 at
        most)."""
        row = await counter(ctx, counter_id)
        events: Rows = await call(
            ctx,
            SCOPE,
            "GET",
            "event",
            params={
                "select": EVENT_COLUMNS,
                "counter_id": f"eq.{counter_id}",
                "order": "id.desc",
                "limit": str(max(1, min(limit, 500))),
            },
        )
        return {"counter": row, "events": events}

    @mcp.tool(annotations=READ)
    async def counters_daily(
        ctx: Context,
        counter_id: int,
        since: str | None = None,
        until: str | None = None,
        zone: str = "UTC",
    ) -> dict[str, Rows]:
        """Totals per day: the sum, what was added, what was taken away and
        how many events. `since` and `until` are dates (YYYY-MM-DD), both
        included; the default is the last 30 days. Days are in `zone`, an
        IANA time zone such as Europe/Stockholm."""
        days = await read_rpc(
            ctx,
            "daily_totals",
            {"counter_id": counter_id, "since": since, "until": until, "zone": zone},
        )
        return {"days": days}

    @mcp.tool(annotations=READ)
    async def counters_count_since(
        ctx: Context, counter_id: int, since: str, positive_only: bool = False
    ) -> dict[str, Any]:
        """How much a counter counted since a time (ISO 8601 with offset):
        the sum of the events since then, or only of the additions when
        `positive_only`, as for "how many coffees since Monday"."""
        [row] = await read_rpc(
            ctx,
            "count_since",
            {"counter_id": counter_id, "since": since, "positive_only": positive_only},
        )
        return row

    @mcp.resource(
        "heycarl://counters",
        name="counters",
        title="Counters",
        description="Every counter with its current value, by name.",
        mime_type="application/json",
    )
    async def counters_resource() -> dict[str, Rows]:
        return {"counters": await counters(static_context(mcp))}
