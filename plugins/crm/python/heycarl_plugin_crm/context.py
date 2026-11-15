"""MCP resources, prompts and completions for people."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from heycarl.mcpserver import UNTRUSTED, call, embed, static_context
from mcp.server.fastmcp import Context  # noqa: TC002 -- FastMCP reads it at runtime
from mcp.server.fastmcp.prompts.base import Message, UserMessage

from .tools import SCOPE, SUMMARY, Rows, person_detail, search, upcoming

if TYPE_CHECKING:
    from heycarl.app import PluginContext

PERSON_URI: Final = "heycarl://people/{person_id}"
LIST_LIMIT: Final = "500"


def full_name(person: dict[str, Any]) -> str:
    return f"{person['first_name']} {person['last_name']}".strip()


async def resolve(ctx: Context, person: str) -> tuple[dict[str, Any] | None, Rows]:
    """The one person `person` names, or None and the candidates."""
    found = await search(ctx, person)
    exact = [p for p in found if full_name(p).casefold() == person.casefold()]
    if len(exact) == 1:
        return exact[0], found
    if len(found) == 1:
        return found[0], found
    return None, found


def ambiguous(person: str, found: Rows) -> list[Message]:
    if not found:
        return [
            UserMessage(f"Hey Carl has nobody matching {person!r}. Ask me who I mean.")
        ]
    names = "; ".join(f"{full_name(p)} (id {p['id']})" for p in found[:20])
    return [
        UserMessage(
            f"{person!r} matches several people in Hey Carl: {names}."
            " Ask me which one I mean, then use people_get with that id."
        )
    ]


def register_context(ctx: PluginContext) -> None:
    mcp = ctx.mcp

    @mcp.resource(
        "heycarl://people",
        name="people",
        title="People",
        description="Everyone in the contact book, favorites first.",
        mime_type="application/json",
    )
    async def people_resource() -> dict[str, Rows]:
        return {"people": await everyone(static_context(mcp))}

    @mcp.resource(
        PERSON_URI,
        name="person",
        title="A person",
        description="One person with details, dates, relations, notes and timeline.",
        mime_type="application/json",
    )
    async def person_resource(ctx: Context, person_id: int) -> dict[str, Any]:
        return await person_detail(ctx, person_id)

    @mcp.resource(
        "heycarl://people/upcoming",
        name="people-upcoming",
        title="Coming up",
        description="Dates in the next 30 days, and people it is time to contact.",
        mime_type="application/json",
    )
    async def upcoming_resource() -> dict[str, Rows]:
        return await upcoming(static_context(mcp), 30)

    async def complete_person_id(c: Context, partial: str) -> list[str]:
        if partial.isdigit():
            return [partial]
        return [str(p["id"]) for p in await search(c, partial)]

    async def complete_person(c: Context, partial: str) -> list[str]:
        return [full_name(p) for p in await search(c, partial)]

    ctx.completions.add(PERSON_URI, "person_id")(complete_person_id)

    @mcp.prompt(
        title="Prepare to meet someone",
        description="Brief me before I see a person: what happened lately, what to"
        " ask about, dates and gift ideas.",
    )
    async def prepare_meeting(ctx: Context, person: str) -> list[Message]:
        found, candidates = await resolve(ctx, person)
        if found is None:
            return ambiguous(person, candidates)
        detail = await person_detail(ctx, found["id"])
        return [
            UserMessage(
                f"I am about to meet {full_name(found)}. From the record below,"
                " brief me in a few lines: who they are to me, what happened"
                " between us lately, open threads worth asking about, dates"
                " coming up and any gift ideas. End with two or three"
                " questions I could ask. " + UNTRUSTED
            ),
            embed(f"heycarl://people/{found['id']}", detail),
        ]

    ctx.completions.add("prepare_meeting", "person")(complete_person)

    @mcp.prompt(
        title="Who to catch up with",
        description="Birthdays and dates coming up, and people I have not been"
        " in touch with for too long, with a suggestion for each.",
    )
    async def catch_up(ctx: Context, days: int = 30) -> list[Message]:
        return [
            UserMessage(
                f"Here is what is coming up in the next {days} days, and who is"
                " due a message. For each, suggest one concrete thing to do this"
                " week: a message to send, a call, a gift. Keep it short. " + UNTRUSTED
            ),
            embed("heycarl://people/upcoming", await upcoming(ctx, days)),
        ]

    @mcp.prompt(
        title="Log a conversation",
        description="Turn what I tell you about a conversation into an activity"
        " and notes in Hey Carl.",
    )
    async def log_conversation(
        ctx: Context, person: str, what_happened: str
    ) -> list[Message]:
        found, candidates = await resolve(ctx, person)
        if found is None:
            return ambiguous(person, candidates)
        return [
            UserMessage(
                f"I talked with {full_name(found)} (id {found['id']}). What"
                f" happened, in my words: {what_happened}\n\n"
                "Log it with people_log_activity, choosing the kind. Put facts"
                " worth remembering (news, plans, preferences, dates) in"
                " people_add_note, one note per topic, and dates in"
                " people_add_date. Show me what you will write before you"
                " write it. Their current record is attached for context. " + UNTRUSTED
            ),
            embed(
                f"heycarl://people/{found['id']}", await person_detail(ctx, found["id"])
            ),
        ]

    ctx.completions.add("log_conversation", "person")(complete_person)


async def everyone(ctx: Context) -> Rows:
    return await call(
        ctx,
        SCOPE,
        "GET",
        "contact",
        params={
            "select": SUMMARY,
            "archived": "is.false",
            "order": "favorite.desc,first_name,last_name",
            "limit": LIST_LIMIT,
        },
    )
