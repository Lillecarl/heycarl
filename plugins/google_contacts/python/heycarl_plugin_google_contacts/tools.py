"""MCP tools over the Google contacts cache and its links to the contact
book. Every read and write is a PostgREST request as the connection's
person, so row-level security holds, and a copy into the contact book is
the person's own write, with history and undo."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from heycarl.mcpserver import UNTRUSTED, call, kernel_of, require
from heycarl.oauth import Scope

from .sync import SCOPE

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .sync import Syncer

READ: Final = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE: Final = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
SYNC: Final = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
COLUMNS: Final = (
    "id,google_account,display_name,given_name,family_name,nickname,company,"
    "job_title,biography,birthday_year,birthday_month,birthday_day,emails,phones,"
    "addresses,urls,contact_id,contact_first_name,contact_last_name"
)
NOTE: Final = (
    "Google contact fields come from the person's Google account, and other"
    " people's shared profiles can fill some of them. " + UNTRUSTED
)
LIMIT: Final = 200
# Any: PostgREST rows.
Rows = list[dict[str, Any]]


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("*", "\\*")
    return f"*{escaped}*"


def register_tools(mcp: MCPServer, syncer: Syncer | None) -> None:
    @mcp.tool(annotations=READ)
    async def google_contacts_list(
        ctx: Context, query: str | None = None, linked: bool | None = None
    ) -> dict[str, Any]:
        """Google contacts from every connected account, at most 200, by
        name. `query` matches the name. `linked` true: only those linked to
        a contact in the book (`contact_id`); false: only those without."""
        params: list[tuple[str, str]] = [
            ("select", COLUMNS),
            ("order", "display_name,id"),
            ("limit", str(LIMIT)),
        ]
        if query:
            params.append(("display_name", f"ilike.{_like(query)}"))
        if linked is not None:
            params.append(("contact_id", "not.is.null" if linked else "is.null"))
        rows: Rows = await call(ctx, SCOPE, "GET", "entry", params=params)
        return {"note": NOTE, "contacts": rows}

    @mcp.tool(annotations=READ)
    async def google_contacts_suggestions(ctx: Context) -> dict[str, Any]:
        """Likely links for Google contacts without one: a contact in the
        book with the same email, phone number or full name. Found in Hey
        Carl only; Google is not asked."""
        rows: Rows = await call(ctx, SCOPE, "GET", "rpc/suggestions")
        return {"note": NOTE, "suggestions": rows}

    @mcp.tool(annotations=WRITE)
    async def google_contacts_link(
        ctx: Context, person_id: int, contact_id: int
    ) -> dict[str, Any]:
        """Link a Google contact (`id` from google_contacts_list) to a
        contact in the book. Hey Carl keeps the link; Google is not told."""
        await call(
            ctx,
            SCOPE,
            "POST",
            "match",
            body={"person_id": person_id, "contact_id": contact_id},
        )
        return {"linked": True}

    @mcp.tool(annotations=WRITE)
    async def google_contacts_unlink(ctx: Context, person_id: int) -> dict[str, Any]:
        """Remove a Google contact's link. Neither contact changes."""
        await call(
            ctx, SCOPE, "DELETE", "match", params={"person_id": f"eq.{person_id}"}
        )
        return {"unlinked": True}

    @mcp.tool(annotations=WRITE)
    async def google_contacts_copy(
        ctx: Context, person_id: int, fields: list[str]
    ) -> dict[str, Any]:
        """Copy fields of a linked Google contact into its contact in the
        book: first_name, last_name, nickname, company, job_title,
        description (a text field replaces the book's when Google's is not
        empty); emails, phones, addresses, urls (adds the values the book
        lacks); birthday. Undo reverses it."""
        written = await call(
            ctx,
            SCOPE,
            "POST",
            "rpc/copy_fields",
            body={"person_id": person_id, "fields": fields},
        )
        return {"written": written}

    @mcp.tool(annotations=WRITE)
    async def google_contacts_import(ctx: Context, person_id: int) -> dict[str, Any]:
        """A new contact in the book from a Google contact without a link,
        with every field copied and the link made."""
        contact_id = await call(
            ctx, SCOPE, "POST", "rpc/import_person", body={"person_id": person_id}
        )
        return {"contact_id": contact_id}

    @mcp.tool(annotations=SYNC)
    async def google_contacts_sync(ctx: Context) -> dict[str, Any]:
        """Fetch changes from Google now instead of within ten minutes. Reads
        from Google only; it changes nothing there."""
        sub = require(ctx, Scope.WRITE)
        if syncer is None:
            raise ToolError("Google is not configured on this Hey Carl")
        outcome = await syncer.sync_now(kernel_of(ctx), sub)
        if outcome is None:
            raise ToolError("synced a moment ago; try again in ten seconds")
        if outcome.error is not None:
            raise ToolError(outcome.error)
        return {"ok": True, "contacts": outcome.contacts}
