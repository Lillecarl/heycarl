"""MCP tools for places. The page needs no backend.

A place is a named point with a radius. Entries bind to it by a core link,
or by a tag the place carries. A position given to `places_near` or
`places_here` is an argument only; nothing stores it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from heycarl.mcpserver import call, confirm
from mcp.server.mcpserver import Context  # noqa: TC002 -- MCPServer reads it at runtime
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from heycarl.app import PluginContext

SCOPE: Final = "places"
READ: Final = ToolAnnotations(readOnlyHint=True)
WRITE: Final = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
COLUMNS: Final = "id,name,latitude,longitude,radius,notes,created_at"
# Any: PostgREST rows. A tool never returns Rows bare: MCPServer sends each
# item of a list as its own text block.
Row = dict[str, Any]
Rows = list[Row]


async def place_row(ctx: Context, place_id: int) -> Row:
    rows: Rows = await call(
        ctx, SCOPE, "GET", "place", params={"select": COLUMNS, "id": f"eq.{place_id}"}
    )
    if not rows:
        raise ToolError(f"no place with id {place_id}")
    return rows[0]


def point(latitude: float, longitude: float) -> dict[str, str]:
    return {"lat": str(latitude), "lon": str(longitude)}


def register(ctx: PluginContext) -> None:
    mcp = ctx.mcp

    @mcp.tool(annotations=READ)
    async def places_list(ctx: Context) -> dict[str, Rows]:
        """List places by name: a point (WGS 84 degrees) and a radius in
        metres. Bind an entry to a place with `link` (scope "places", table
        "place", key {"id": N}), or tag the place and the entry alike."""
        rows: Rows = await call(
            ctx, SCOPE, "GET", "place", params={"select": COLUMNS, "order": "name"}
        )
        return {"places": rows}

    @mcp.tool(annotations=WRITE)
    async def places_add(
        ctx: Context,
        name: str,
        latitude: float,
        longitude: float,
        radius: int = 100,
        notes: str = "",
    ) -> Row:
        """Make a place. `radius` is in metres, 10 to 50000."""
        body = {
            "name": name.strip(),
            "latitude": latitude,
            "longitude": longitude,
            "radius": radius,
            "notes": notes,
        }
        [row] = await call(ctx, SCOPE, "POST", "place", body=body)
        return await place_row(ctx, row["id"])

    @mcp.tool(annotations=WRITE)
    async def places_update(
        ctx: Context,
        place_id: int,
        name: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius: int | None = None,
        notes: str | None = None,
    ) -> Row:
        """Change a place. Only the fields given change."""
        changes: dict[str, object] = {
            k: v
            for k, v in {
                "name": name.strip() if name is not None else None,
                "latitude": latitude,
                "longitude": longitude,
                "radius": radius,
                "notes": notes,
            }.items()
            if v is not None
        }
        if not changes:
            raise ToolError("nothing to change")
        rows: Rows = await call(
            ctx, SCOPE, "PATCH", "place", params={"id": f"eq.{place_id}"}, body=changes
        )
        if not rows:
            raise ToolError(f"no place with id {place_id}")
        return await place_row(ctx, place_id)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def places_delete(ctx: Context, place_id: int) -> str:
        """Delete a place. Asks the person to confirm. It goes to the trash,
        and `restore` brings it back with its links."""
        row = await place_row(ctx, place_id)
        await confirm(ctx, f"Delete the place {row['name']!r}?")
        await call(ctx, SCOPE, "DELETE", "place", params={"id": f"eq.{place_id}"})
        return "deleted"

    @mcp.tool(annotations=READ)
    async def places_near(
        ctx: Context, latitude: float, longitude: float, within: int = 5000
    ) -> dict[str, Rows]:
        """Places within `within` metres (10 to 100000) of a point, nearest
        first, with the distance in metres and whether the point is inside
        the place's radius. Nothing stores the point."""
        rows: Rows = await call(
            ctx,
            SCOPE,
            "GET",
            "rpc/near",
            params={**point(latitude, longitude), "within": str(within)},
        )
        return {"places": rows}

    @mcp.tool(annotations=READ)
    async def places_here(
        ctx: Context, latitude: float, longitude: float
    ) -> dict[str, Rows]:
        """The entries bound to the places a point is inside: linked to the
        place, or carrying a tag the place carries (`via_tag`). Nothing
        stores the point."""
        rows: Rows = await call(
            ctx, SCOPE, "GET", "rpc/here", params=point(latitude, longitude)
        )
        return {"entries": rows}
