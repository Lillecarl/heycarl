"""MCP tools and a resource for tags. The page needs no backend.

A tag is an entry, and tagging is a core link to it, so any entry of any
plugin takes tags. The functions in migrations/0001_tag.sql run as the
connection's person; the core link API checks both ends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from heycarl.mcpserver import call, confirm, static_context
from mcp.server.mcpserver import Context  # noqa: TC002 -- MCPServer reads it at runtime
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from heycarl.app import PluginContext

SCOPE: Final = "tags"
READ: Final = ToolAnnotations(readOnlyHint=True)
WRITE: Final = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
# Tagging twice leaves one link; untagging twice fails the second time.
SETTLE: Final = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True
)
COLUMNS: Final = "id,name,color,entries,created_at"
# Any: PostgREST rows. A tool never returns Rows bare: MCPServer sends each
# item of a list as its own text block.
Row = dict[str, Any]
Rows = list[Row]
# Any: an entry key holds whatever columns its table is keyed on.
Key = dict[str, Any]


async def tags(ctx: Context) -> Rows:
    return await call(
        ctx, SCOPE, "GET", "tag_summary", params={"select": COLUMNS, "order": "name"}
    )


async def tag_row(ctx: Context, tag_id: int) -> Row:
    rows: Rows = await call(
        ctx,
        SCOPE,
        "GET",
        "tag_summary",
        params={"select": COLUMNS, "id": f"eq.{tag_id}"},
    )
    if not rows:
        raise ToolError(f"no tag with id {tag_id}")
    return rows[0]


async def rpc(ctx: Context, function: str, args: dict[str, object]) -> Any:
    """A function by POST; a None argument takes the function's default."""
    body = {k: v for k, v in args.items() if v is not None}
    return await call(ctx, SCOPE, "POST", f"rpc/{function}", body=body)


def entry_args(scope: str, table: str, key: Key) -> dict[str, object]:
    return {"of_scope": scope, "of_table": table, "of_key": key}


def register(ctx: PluginContext) -> None:
    mcp = ctx.mcp

    @mcp.tool(annotations=READ)
    async def tags_list(ctx: Context) -> dict[str, Rows]:
        """List tags by name, each with how many entries carry it. Use the
        ids here for the other tags tools."""
        return {"tags": await tags(ctx)}

    @mcp.tool(annotations=WRITE)
    async def tags_add(ctx: Context, name: str, color: str | None = None) -> Row:
        """Make a tag. Names are unique per person, in any case. `color` is
        a base16 role from base00 to base0F, such as base0D (blue). `tag`
        also makes a missing tag, so this is for a tag nothing carries yet."""
        [row] = await call(
            ctx, SCOPE, "POST", "tag", body={"name": name.strip(), "color": color}
        )
        return await tag_row(ctx, row["id"])

    @mcp.tool(annotations=WRITE)
    async def tags_rename(
        ctx: Context, tag_id: int, name: str | None = None, color: str | None = None
    ) -> Row:
        """Rename a tag or change its colour; every tagged entry follows.
        `color` is a base16 role from base00 to base0F, or "" for none."""
        changes: dict[str, object] = {}
        if name is not None:
            changes["name"] = name.strip()
        if color is not None:
            changes["color"] = color or None
        if not changes:
            raise ToolError("nothing to change")
        rows: Rows = await call(
            ctx, SCOPE, "PATCH", "tag", params={"id": f"eq.{tag_id}"}, body=changes
        )
        if not rows:
            raise ToolError(f"no tag with id {tag_id}")
        return await tag_row(ctx, tag_id)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def tags_delete(ctx: Context, tag_id: int) -> str:
        """Delete a tag. Asks the person to confirm. The tagged entries stay;
        the tag goes to the trash, and `restore` brings it back on them."""
        row = await tag_row(ctx, tag_id)
        await confirm(
            ctx, f"Delete the tag {row['name']!r}, now on {row['entries']} entries?"
        )
        await call(ctx, SCOPE, "DELETE", "tag", params={"id": f"eq.{tag_id}"})
        return "deleted"

    @mcp.tool(annotations=SETTLE)
    async def tag(
        ctx: Context,
        scope: str,
        table: str,
        key: Key,
        name: str,
        color: str | None = None,
    ) -> Row:
        """Tag an entry of any kind, for example scope "todos", table
        "todo", key {"id": 3}, name "shopping". Makes the tag when the
        person has none by that name; `color` (a base16 role, base00 to
        base0F) applies only then. entries_search finds an entry's scope,
        table and key."""
        row: Row = await rpc(
            ctx, "tag_entry", {**entry_args(scope, table, key), "name": name, "color": color}
        )
        return row

    @mcp.tool(annotations=WRITE)
    async def untag(
        ctx: Context, scope: str, table: str, key: Key, tag_id: int
    ) -> str:
        """Take a tag off an entry. The history keeps it, so `undo` puts it
        back."""
        await rpc(ctx, "untag", {**entry_args(scope, table, key), "tag_id": tag_id})
        return "untagged"

    @mcp.tool(annotations=READ)
    async def tags_of(ctx: Context, scope: str, table: str, key: Key) -> dict[str, Rows]:
        """The tags on one entry, by name."""
        return {"tags": await rpc(ctx, "tags_of", entry_args(scope, table, key))}

    @mcp.tool(annotations=READ)
    async def tagged(
        ctx: Context, tag_id: int, scope: str | None = None, table: str | None = None
    ) -> dict[str, Any]:
        """Every entry that carries a tag, with the scope, table and key that
        other tools take. `scope` and `table` narrow it to one kind, such
        as scope "todos", table "todo"."""
        row = await tag_row(ctx, tag_id)
        found: Rows = await rpc(ctx, "tagged", {"tag_id": tag_id})
        entries = [
            r["entry"]
            for r in found
            if (scope is None or r["entry"]["scope"] == scope)
            and (table is None or r["entry"]["table"] == table)
        ]
        return {"tag": row, "entries": entries}

    @mcp.resource(
        "heycarl://tags",
        name="tags",
        title="Tags",
        description="Every tag, with how many entries carry it.",
        mime_type="application/json",
    )
    async def tags_resource() -> dict[str, Rows]:
        return {"tags": await tags(static_context(mcp))}
