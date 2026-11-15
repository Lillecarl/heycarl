"""MCP tools for people: find, read, add, change, log what happened.

Each tool is a PostgREST request made as the connection's person, so
row-level security holds; the queries are the ones the page sends.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Final, Literal

from heycarl.mcpserver import call, confirm
from mcp.server.fastmcp import Context  # noqa: TC002 -- FastMCP reads it at runtime
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

SCOPE: Final = "crm"
READ: Final = ToolAnnotations(readOnlyHint=True)
WRITE: Final = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
SUMMARY: Final = (
    "id,first_name,last_name,nickname,company,job_title,favorite,"
    "last_contacted,touch_due"
)
DETAIL: Final = ",".join(
    [
        "*",
        "last_contacted",
        "touch_due",
        "contact_field(kind,label,value)",
        "important_date(id,kind,label,month,day,year,remind,next_occurrence,days_until,turns)",
        "relations(*)",
        "tag(name)",
        "gift(id,title,url,state,note)",
        "note(id,body,created_at)",
    ]
)
DETAIL_ORDER: Final = [
    ("contact_field.order", "kind,id"),
    ("important_date.order", "month,day"),
    ("note.order", "created_at.desc"),
    ("note.limit", "20"),
    ("tag.order", "name"),
]
ActivityKind = Literal["call", "message", "meeting", "meal", "visit", "other"]
FieldKind = Literal["email", "phone", "address", "website", "social", "other"]
DateKind = Literal["birthday", "anniversary", "memorial", "other"]

# Any: PostgREST rows, JSON of the shape the select asks for. A tool never
# returns Rows bare: FastMCP sends each item of a list as its own text block,
# so a client reads one person as an object and none as no answer.
Rows = list[dict[str, Any]]


async def search(ctx: Context, query: str) -> Rows:
    return await call(
        ctx, SCOPE, "GET", "rpc/search", params={"q": query, "select": SUMMARY}
    )


async def person_detail(ctx: Context, person_id: int) -> dict[str, Any]:
    rows: Rows = await call(
        ctx,
        SCOPE,
        "GET",
        "contact",
        params=[("id", f"eq.{person_id}"), ("select", DETAIL), *DETAIL_ORDER],
    )
    if not rows:
        raise ValueError(f"no person with id {person_id}")
    person = rows[0]
    person["timeline"] = await call(
        ctx,
        SCOPE,
        "GET",
        "timeline",
        params={"contact_id": f"eq.{person_id}", "order": "at.desc", "limit": "20"},
    )
    return person


async def upcoming(ctx: Context, days: int) -> dict[str, Rows]:
    dates = await call(
        ctx,
        SCOPE,
        "GET",
        "important_date",
        params={
            "select": "id,kind,label,year,next_occurrence,days_until,turns,"
            "contact(id,first_name,last_name)",
            "days_until": f"lte.{days}",
            "order": "days_until",
        },
    )
    due = await call(
        ctx,
        SCOPE,
        "GET",
        "contact",
        params={
            "select": "id,first_name,last_name,last_contacted,touch_due",
            "archived": "is.false",
            "touch_due": f"lte.{date.today().isoformat()}",
            "order": "touch_due",
        },
    )
    return {"dates": dates, "get_in_touch": due}


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ)
    async def people_search(ctx: Context, query: str) -> dict[str, Rows]:
        """Find people by name, nickname, company, contact detail or tag."""
        return {"people": await search(ctx, query)}

    @mcp.tool(annotations=READ)
    async def people_list(
        ctx: Context,
        tag: str | None = None,
        favorites_only: bool = False,
        include_archived: bool = False,
        limit: int = 100,
    ) -> dict[str, Rows]:
        """List people, favorites first. Filter by tag name if given."""
        select = SUMMARY + (",tag!inner(name)" if tag else "")
        params = [
            ("select", select),
            ("order", "favorite.desc,first_name,last_name"),
            ("limit", str(min(limit, 500))),
        ]
        if tag:
            params.append(("tag.name", f"eq.{tag}"))
        if favorites_only:
            params.append(("favorite", "is.true"))
        if not include_archived:
            params.append(("archived", "is.false"))
        return {"people": await call(ctx, SCOPE, "GET", "contact", params=params)}

    @mcp.tool(annotations=READ)
    async def people_get(ctx: Context, person_id: int) -> dict[str, Any]:
        """Everything about one person: details, dates, relations, tags,
        gift ideas, the latest notes and the last 20 activities."""
        return await person_detail(ctx, person_id)

    @mcp.tool(annotations=READ)
    async def people_upcoming(ctx: Context, days: int = 30) -> dict[str, Rows]:
        """Birthdays and other dates coming up within `days`, and people
        it is time to get in touch with."""
        return await upcoming(ctx, days)

    @mcp.tool(annotations=WRITE)
    async def people_add(
        ctx: Context,
        first_name: str,
        last_name: str = "",
        nickname: str = "",
        company: str = "",
        job_title: str = "",
        description: str = "",
        stay_in_touch_days: int | None = None,
    ) -> dict[str, Any]:
        """Add a person. Check people_search first: this never merges."""
        [row] = await call(
            ctx,
            SCOPE,
            "POST",
            "contact",
            body={
                "first_name": first_name,
                "last_name": last_name,
                "nickname": nickname,
                "company": company,
                "job_title": job_title,
                "description": description,
                "stay_in_touch_days": stay_in_touch_days,
            },
        )
        return row

    @mcp.tool(annotations=WRITE)
    async def people_update(
        ctx: Context, person_id: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """Change a person's fields: first_name, last_name, nickname, company,
        job_title, description, favorite, archived, stay_in_touch_days."""
        allowed = {
            "first_name",
            "last_name",
            "nickname",
            "company",
            "job_title",
            "description",
            "favorite",
            "archived",
            "stay_in_touch_days",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"cannot change {sorted(unknown)}")
        rows: Rows = await call(
            ctx,
            SCOPE,
            "PATCH",
            "contact",
            params={"id": f"eq.{person_id}"},
            body=changes,
        )
        if not rows:
            raise ValueError(f"no person with id {person_id}")
        return rows[0]

    @mcp.tool(annotations=WRITE)
    async def people_add_detail(
        ctx: Context, person_id: int, kind: FieldKind, value: str, label: str = ""
    ) -> dict[str, Any]:
        """Add a contact detail: an email, a phone number, an address, a
        website or a social profile. `label` is free text, like "work"."""
        [row] = await call(
            ctx,
            SCOPE,
            "POST",
            "contact_field",
            body={
                "contact_id": person_id,
                "kind": kind,
                "label": label,
                "value": value,
            },
        )
        return row

    @mcp.tool(annotations=WRITE)
    async def people_add_date(
        ctx: Context,
        person_id: int,
        month: int,
        day: int,
        kind: DateKind = "birthday",
        year: int | None = None,
        label: str = "",
    ) -> dict[str, Any]:
        """Add a date to remember. A reminder follows by itself."""
        [row] = await call(
            ctx,
            SCOPE,
            "POST",
            "important_date",
            body={
                "contact_id": person_id,
                "kind": kind,
                "month": month,
                "day": day,
                "year": year,
                "label": label,
            },
        )
        return row

    @mcp.tool(annotations=WRITE)
    async def people_add_note(
        ctx: Context, person_id: int, body: str
    ) -> dict[str, Any]:
        """Write a note about a person."""
        [row] = await call(
            ctx, SCOPE, "POST", "note", body={"contact_id": person_id, "body": body}
        )
        return row

    @mcp.tool(annotations=WRITE)
    async def people_log_activity(
        ctx: Context,
        person_ids: list[int],
        title: str,
        kind: ActivityKind = "other",
        description: str = "",
        happened_at: str | None = None,
    ) -> dict[str, Any]:
        """Record something done with one or more people: a call, a meal,
        a visit. `happened_at` is ISO 8601; leave it out for now."""
        body: dict[str, Any] = {
            "contact_ids": person_ids,
            "title": title,
            "kind": kind,
            "description": description,
        }
        if happened_at is not None:
            body["happened_at"] = happened_at
        return await call(ctx, SCOPE, "POST", "rpc/log_activity", body=body)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def people_delete(ctx: Context, person_id: int) -> str:
        """Delete a person with all their details, notes and dates. Asks the
        person to confirm. Archiving (people_update archived=true) keeps them."""
        [person] = await call(
            ctx,
            SCOPE,
            "GET",
            "contact",
            params={"id": f"eq.{person_id}", "select": "first_name,last_name"},
        ) or [None]
        if person is None:
            raise ValueError(f"no person with id {person_id}")
        name = f"{person['first_name']} {person['last_name']}".strip()
        await confirm(ctx, f"Delete {name} and everything recorded about them?")
        await call(ctx, SCOPE, "DELETE", "contact", params={"id": f"eq.{person_id}"})
        return f"deleted {name}"
