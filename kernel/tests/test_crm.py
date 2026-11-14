"""The CRM plugin: PostgREST embedding, computed columns, RPC and reminders."""

from __future__ import annotations

import importlib
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any

import psycopg
import pytest

from .conftest import as_user, needs_database

if TYPE_CHECKING:
    import httpx
    from heycarl.app import Kernel
    from heycarl.config import Config

pytestmark = [pytest.mark.anyio, needs_database]

# Etc/GMT-3 is UTC+3 all year: no daylight saving, and no tz database
# needed on the Python side.
ZONE = "Etc/GMT-3"
OFFSET = timezone(timedelta(hours=3))


# Any: a PostgREST response body, a JSON object or array.
async def call(
    client: httpx.AsyncClient,
    user: str,
    method: str,
    path: str,
    body: object = None,
) -> Any:
    r = await client.request(
        method,
        f"/api/data/{path}",
        json=body,
        headers={**as_user(user), "prefer": "return=representation"},
    )
    assert r.is_success, f"{method} {path}: {r.status_code} {r.text}"
    return r.json() if r.content else None


async def contact(client: httpx.AsyncClient, user: str, first: str, last: str) -> int:
    rows = await call(
        client, user, "POST", "crm/contact", {"first_name": first, "last_name": last}
    )
    return int(rows[0]["id"])


async def test_one_request_embeds_a_whole_contact(client: httpx.AsyncClient) -> None:
    u = "crm-embed"
    anna = await contact(client, u, "Anna", "Svensson")
    bo = await contact(client, u, "Bo", "Svensson")
    await call(
        client,
        u,
        "POST",
        "crm/contact_field",
        {"contact_id": anna, "kind": "email", "value": "anna@example.com"},
    )
    await call(
        client,
        u,
        "POST",
        "crm/relationship",
        {"contact_id": anna, "other_id": bo, "kind": "parent"},
    )
    await call(client, u, "POST", "crm/note", {"contact_id": anna, "body": "Likes tea"})
    tag = await call(
        client,
        u,
        "POST",
        "crm/tag?on_conflict=owner_sub,name",
        {"name": "family"},
    )
    await call(
        client,
        u,
        "POST",
        "crm/contact_tag",
        {"contact_id": anna, "tag_id": tag[0]["id"]},
    )
    when = "2026-01-02T12:00:00+00:00"
    await call(
        client,
        u,
        "POST",
        "crm/rpc/log_activity",
        {
            "contact_ids": [anna, bo],
            "title": "Dinner",
            "kind": "meal",
            "happened_at": when,
        },
    )

    select = (
        "*,last_contacted,contact_field(kind,value),relations(kind,other_first_name),"
        "tag(name),note(body),activity(title)"
    )
    [row] = await call(client, u, "GET", f"crm/contact?id=eq.{anna}&select={select}")
    assert row["contact_field"] == [{"kind": "email", "value": "anna@example.com"}]
    assert row["relations"] == [{"kind": "parent", "other_first_name": "Bo"}]
    assert row["tag"] == [{"name": "family"}]
    assert row["note"] == [{"body": "Likes tea"}]
    assert row["activity"] == [{"title": "Dinner"}]
    assert datetime.fromisoformat(row["last_contacted"]) == datetime.fromisoformat(when)

    [other] = await call(
        client, u, "GET", f"crm/contact?id=eq.{bo}&select=relations(kind)"
    )
    assert other["relations"] == [{"kind": "child"}]

    timeline = await call(
        client, u, "GET", f"crm/timeline?contact_id=eq.{anna}&order=at.desc"
    )
    assert {t["kind"] for t in timeline} == {"note", "activity:meal"}


async def test_rows_point_only_at_the_owners_contacts(
    client: httpx.AsyncClient,
) -> None:
    alice = await contact(client, "crm-alice", "Secret", "Person")
    r = await client.post(
        "/api/data/crm/note",
        json={"contact_id": alice, "body": "planted"},
        headers=as_user("crm-mallory"),
    )
    assert r.status_code == 409, r.text
    assert await call(client, "crm-mallory", "GET", "crm/contact") == []
    assert await call(client, "crm-mallory", "GET", "crm/relation") == []


async def test_search_matches_fields_and_tags(client: httpx.AsyncClient) -> None:
    u = "crm-search"
    cid = await contact(client, u, "Cecilia", "Ek")
    await contact(client, u, "Dan", "Ek")
    await call(
        client,
        u,
        "POST",
        "crm/contact_field",
        {"contact_id": cid, "kind": "phone", "value": "+46 70 123 45 67"},
    )

    async def names(q: str) -> list[str]:
        rows = await call(client, u, "GET", f"crm/rpc/search?q={q}&select=first_name")
        return [row["first_name"] for row in rows]

    assert await names("123") == ["Cecilia"]
    assert await names("ek") == ["Cecilia", "Dan"]
    # A LIKE wildcard in the query is a literal character.
    assert await names("%") == []


def tomorrow() -> date:
    return datetime.now(OFFSET).date() + timedelta(days=1)


async def reminders(client: httpx.AsyncClient, user: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = await call(
        client, user, "GET", "reminders/reminder?order=id"
    )
    return rows


async def birthday(client: httpx.AsyncClient, user: str, cid: int, on: date) -> int:
    await call(client, user, "POST", "crm/preference", {"timezone": ZONE})
    rows = await call(
        client,
        user,
        "POST",
        "crm/important_date",
        {
            "contact_id": cid,
            "kind": "birthday",
            "month": on.month,
            "day": on.day,
            "year": 1990,
        },
    )
    return int(rows[0]["id"])


async def test_a_birthday_keeps_a_reminder(client: httpx.AsyncClient) -> None:
    u = "crm-birthday"
    cid = await contact(client, u, "Eva", "Lind")
    on = tomorrow()
    did = await birthday(client, u, cid, on)

    [rem] = await reminders(client, u)
    assert datetime.fromisoformat(rem["due_at"]) == datetime.combine(
        on, time(9), OFFSET
    )
    assert rem["title"] == f"Eva Lind's birthday ({on.year - 1990})"
    assert rem["url"] == f"/p/crm/heycarl-crm#/contact/{cid}"

    upcoming = await call(
        client,
        u,
        "GET",
        "crm/important_date?select=id,next_occurrence,days_until,turns"
        "&days_until=lte.30&order=days_until",
    )
    assert upcoming == [
        {
            "id": did,
            "next_occurrence": on.isoformat(),
            "days_until": 1,
            "turns": on.year - 1990,
        }
    ]

    await call(
        client, u, "PATCH", f"crm/contact?id=eq.{cid}", {"first_name": "Evelina"}
    )
    [rem] = await reminders(client, u)
    assert rem["title"].startswith("Evelina Lind's")

    await call(client, u, "PATCH", f"crm/important_date?id=eq.{did}", {"remind": False})
    assert await reminders(client, u) == []

    await call(client, u, "PATCH", f"crm/important_date?id=eq.{did}", {"remind": True})
    [rem] = await reminders(client, u)

    # A reminder deleted by hand stays deleted for this occurrence.
    await call(client, u, "DELETE", f"reminders/reminder?id=eq.{rem['id']}")
    await call(
        client, u, "PATCH", f"crm/important_date?id=eq.{did}", {"label": "big one"}
    )
    assert await reminders(client, u) == []

    await call(client, u, "DELETE", f"crm/contact?id=eq.{cid}")
    assert await reminders(client, u) == []


async def test_touch_due_counts_days_in_the_owners_zone(
    client: httpx.AsyncClient,
) -> None:
    u = "crm-touch"
    await call(client, u, "POST", "crm/preference", {"timezone": ZONE})
    [row] = await call(
        client,
        u,
        "POST",
        "crm/contact",
        {"first_name": "Gus", "stay_in_touch_days": 7},
    )
    # 00:30 local is the evening before in UTC.
    local_day = datetime.now(OFFSET).date() - timedelta(days=1)
    at = datetime.combine(local_day, time(0, 30), OFFSET)
    await call(
        client,
        u,
        "POST",
        "crm/rpc/log_activity",
        {"contact_ids": [row["id"]], "title": "Call", "happened_at": at.isoformat()},
    )
    [due] = await call(
        client, u, "GET", f"crm/contact?id=eq.{row['id']}&select=touch_due"
    )
    assert due["touch_due"] == (local_day + timedelta(days=7)).isoformat()


async def test_a_passed_date_gets_next_years_reminder(
    client: httpx.AsyncClient, kernel: Kernel, config: Config
) -> None:
    u = "crm-rollover"
    cid = await contact(client, u, "Filip", "Berg")
    did = await birthday(client, u, cid, tomorrow())
    [rem] = await reminders(client, u)

    # As if the reminder fired and a year went by.
    async with await psycopg.AsyncConnection.connect(config.database) as conn:
        await conn.execute(
            "update heycarl_plugin_reminders.reminder set state = 'sent' where id = %s",
            (rem["id"],),
        )
        await conn.execute(
            "update heycarl_plugin_crm.important_date"
            " set reminded_for = reminded_for - interval '1 year' where id = %s",
            (did,),
        )

    crm = importlib.import_module("heycarl_plugin_crm")
    assert await crm.sync(kernel) >= 1
    sent, fresh = await reminders(client, u)
    assert (sent["state"], fresh["state"]) == ("sent", "pending")
    assert fresh["due_at"] == rem["due_at"]
    assert await crm.sync(kernel) == 0


@pytest.mark.parametrize(
    ("month", "day", "since", "expected"),
    [
        (2, 29, date(2027, 1, 1), date(2027, 2, 28)),
        (2, 29, date(2028, 1, 1), date(2028, 2, 29)),
        (3, 1, date(2027, 3, 1), date(2027, 3, 1)),
        (3, 1, date(2027, 3, 2), date(2028, 3, 1)),
    ],
)
@pytest.mark.usefixtures("app")
async def test_next_occurrence(
    config: Config, month: int, day: int, since: date, expected: date
) -> None:
    async with await psycopg.AsyncConnection.connect(config.database) as conn:
        cur = await conn.execute(
            "select heycarl_plugin_crm.next_on_or_after(%s, %s, %s)",
            (month, day, since),
        )
        assert await cur.fetchone() == (expected,)
