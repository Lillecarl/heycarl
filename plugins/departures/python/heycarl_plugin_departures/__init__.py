"""SL departures from saved stops (DESIGN.md "Departures").

A stop is a row the person owns: an SL site and a filter. Departures are
SL's public data, fetched on demand by `sl.SL` and never stored.
`HEYCARL_SL_URL` replaces SL's address, for tests only.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

import anyio
from heycarl.mcpserver import call, confirm
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .routes import register_routes
from .sl import (
    BASE_URL,
    MAX_SHOWN,
    SL,
    ZONE,
    Filter,
    SLError,
    Transport,
    select,
    soonest,
)

if TYPE_CHECKING:
    from heycarl.app import PluginContext

SCOPE: Final = "departures"
READ: Final = ToolAnnotations(readOnlyHint=True)
WRITE: Final = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
COLUMNS: Final = "id,name,site_id,site_name,transport,lines,direction,created_at"
# Any: PostgREST rows. A tool never returns Rows bare: MCPServer sends each
# item of a list as its own text block.
Row = dict[str, Any]
Rows = list[Row]


async def stop_rows(ctx: Context, stop_id: int | None = None) -> Rows:
    params = {"select": COLUMNS, "order": "name,id"}
    if stop_id is not None:
        params["id"] = f"eq.{stop_id}"
    rows: Rows = await call(ctx, SCOPE, "GET", "stop", params=params)
    if stop_id is not None and not rows:
        raise ToolError(f"no stop with id {stop_id}")
    return rows


def stop_filter(row: Row) -> Filter:
    return Filter(
        transport=row["transport"], lines=row["lines"], direction=row["direction"]
    )


def minutes(expected: datetime, now: datetime) -> int:
    return max(0, round((expected - now).total_seconds() / 60))


def register(ctx: PluginContext) -> None:
    sl = SL(base_url=os.environ.get("HEYCARL_SL_URL") or BASE_URL)
    register_routes(ctx.router, sl)
    mcp = ctx.mcp

    async def site_name(site_id: int) -> str:
        try:
            site = (await sl.sites()).sites.get(site_id)
        except SLError as e:
            raise ToolError(str(e)) from e
        if site is None:
            raise ToolError(f"SL has no site {site_id}; see departures_sites")
        return site.name

    @mcp.tool(annotations=READ)
    async def departures_sites(ctx: Context, query: str) -> dict[str, list[Row]]:
        """Find SL sites (stations and stops) by name, for `departures_add_stop`."""
        try:
            found = (await sl.sites()).search(query)
        except SLError as e:
            raise ToolError(str(e)) from e
        return {"sites": [s.model_dump() for s in found]}

    @mcp.tool(annotations=READ)
    async def departures_stops(ctx: Context) -> dict[str, Rows]:
        """The saved stops: an SL site and a filter. `transport` null is
        every mode, `lines` empty is every line, `direction` null is both
        (SL's direction_code, 1 or 2). Link a stop to a place (scope
        "departures", table "stop", key {"id": N}) to see it under the
        place."""
        return {"stops": await stop_rows(ctx)}

    @mcp.tool(annotations=WRITE)
    async def departures_add_stop(
        ctx: Context,
        name: str,
        site_id: int,
        transport: Transport | None = None,
        lines: list[int] | None = None,
        direction: int | None = None,
    ) -> Row:
        """Save a stop. `site_id` from `departures_sites`; `direction` is
        SL's direction_code (1 or 2), which `departures_next` shows."""
        body = {
            "name": name.strip(),
            "site_id": site_id,
            "site_name": await site_name(site_id),
            "transport": transport,
            "lines": lines or [],
            "direction": direction,
        }
        [row] = await call(ctx, SCOPE, "POST", "stop", body=body)
        [made] = await stop_rows(ctx, row["id"])
        return made

    @mcp.tool(annotations=WRITE)
    async def departures_update_stop(
        ctx: Context,
        stop_id: int,
        name: str | None = None,
        transport: Transport | None = None,
        every_mode: bool = False,
        lines: list[int] | None = None,
        direction: int | None = None,
        both_directions: bool = False,
    ) -> Row:
        """Change a stop. Only the fields given change: `lines` [] is every
        line, `every_mode` clears `transport`, `both_directions` clears
        `direction`."""
        changes: dict[str, object] = {}
        if name is not None:
            changes["name"] = name.strip()
        if every_mode or transport is not None:
            changes["transport"] = None if every_mode else transport
        if lines is not None:
            changes["lines"] = lines
        if both_directions or direction is not None:
            changes["direction"] = None if both_directions else direction
        if not changes:
            raise ToolError("nothing to change")
        rows: Rows = await call(
            ctx, SCOPE, "PATCH", "stop", params={"id": f"eq.{stop_id}"}, body=changes
        )
        if not rows:
            raise ToolError(f"no stop with id {stop_id}")
        [row] = await stop_rows(ctx, stop_id)
        return row

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def departures_delete_stop(ctx: Context, stop_id: int) -> str:
        """Delete a stop. Asks the person to confirm. It goes to the trash,
        and `restore` brings it back with its links."""
        [row] = await stop_rows(ctx, stop_id)
        await confirm(ctx, f"Delete the stop {row['name']!r}?")
        await call(ctx, SCOPE, "DELETE", "stop", params={"id": f"eq.{stop_id}"})
        return "deleted"

    @mcp.tool(annotations=READ)
    async def departures_next(
        ctx: Context, stop_id: int | None = None, limit: int = 6
    ) -> dict[str, Rows]:
        """The next departures from one saved stop, or from every one, in
        the next hour: the first `limit`, plus the next one of each line
        and direction they leave out. `minutes` counts from now; `notices`
        are SL's messages, such as a cancellation."""
        limit = max(1, min(limit, MAX_SHOWN))
        stops = await stop_rows(ctx, stop_id)
        boards: list[Row] = [{} for _ in stops]

        async def one(i: int, row: Row) -> None:
            try:
                board = soonest(
                    select(await sl.board(row["site_id"]), stop_filter(row)), limit
                )
            except SLError as e:
                boards[i] = {"stop": row, "error": str(e)}
                return
            now = datetime.now(ZONE)
            boards[i] = {
                "stop": row,
                "notices": board.notices,
                "departures": [
                    {
                        **d.model_dump(mode="json"),
                        "minutes": minutes(d.expected, now),
                    }
                    for d in board.departures
                ],
            }

        async with anyio.create_task_group() as tg:
            for i, row in enumerate(stops):
                tg.start_soon(one, i, row)
        return {"stops": boards}
