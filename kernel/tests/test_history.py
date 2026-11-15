"""History and trash: every change keeps a version, deletes go to a trash
only the app can empty, and a hostile MCP client can destroy nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from heycarl import migrations, postgrest
from heycarl.tokens import via_mcp

from .conftest import _conninfo, as_user, needs_database
from .test_mcp import Plain, answering, connected, failure, served, tool

if TYPE_CHECKING:
    import httpx
    from heycarl.app import Kernel

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]


# Any: a PostgREST response body.
async def call(
    client: httpx.AsyncClient, headers: dict[str, str], method: str, path: str, body: object = None
) -> Any:
    r = await client.request(
        method,
        f"/api/data/{path}",
        json=body,
        headers={**headers, "prefer": "return=representation"},
    )
    assert r.is_success, f"{method} {path}: {r.status_code} {r.text}"
    return r.json() if r.content else None


async def owner_rows(sql: str, *args: object) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(
        _conninfo("heycarl_owner"), autocommit=True
    ) as conn:
        cur = await conn.execute(sql.encode(), args)
        return [] if cur.description is None else await cur.fetchall()


async def test_every_table_a_person_may_change_is_tracked(kernel: Kernel) -> None:
    holes = await owner_rows(
        "select c.oid::regclass::text from pg_class c"
        " join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname like 'heycarl\\_plugin\\_%%' and c.relkind = 'r'"
        " and (has_table_privilege('heycarl_user', c.oid, 'DELETE')"
        "      or has_any_column_privilege('heycarl_user', c.oid, 'UPDATE'))"
        " and c.oid not in (select rel from heycarl_private.tracked)"
    )
    assert holes == []


async def test_the_app_sees_versions_restores_and_purges(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("hist-app")
    [todo] = await call(client, me, "POST", "todos/todo", {"title": "first"})
    key = f"todos/todo?id=eq.{todo['id']}"
    await call(client, me, "PATCH", key, {"title": "second"})
    await call(client, me, "PATCH", key, {"title": "third"})
    versions = await call(
        client, me, "POST", "core/rpc/history",
        {"of_scope": "todos", "of_table": "todo", "of_key": {"id": todo["id"]}},
    )
    assert [v["data"]["title"] for v in versions] == ["third", "second", "first"]
    assert [v["op"] for v in versions] == ["update", "update", "insert"]
    assert {v["via"] for v in versions} == {"app"}
    assert [v["newest"] for v in versions] == [True, False, False]

    result = await call(client, me, "POST", "core/rpc/restore", {"version": versions[-1]["id"]})
    assert result == {"done": 1, "failed": [], "changed_since": []}
    [now] = await call(client, me, "GET", key)
    assert now["title"] == "first"

    await call(client, me, "DELETE", key)
    assert await call(client, me, "GET", key) == []
    [entry] = await call(client, me, "POST", "core/rpc/trash", {})
    assert [i["label"] for i in entry["items"]] == ["first"]
    await call(client, me, "POST", "core/rpc/restore", {"version": entry["entry"]})
    [back] = await call(client, me, "GET", key)
    assert back == now

    await call(client, me, "DELETE", key)
    [entry] = await call(client, me, "POST", "core/rpc/trash", {})
    removed = await call(client, me, "POST", "core/rpc/purge", {"entry": entry["entry"]})
    assert removed >= 5
    assert await call(client, me, "POST", "core/rpc/trash", {}) == []
    assert (
        await call(
            client, me, "POST", "core/rpc/history",
            {"of_scope": "todos", "of_key": {"id": todo["id"]}},
        )
        == []
    )


async def test_nobody_restores_or_reads_another_persons_history(
    client: httpx.AsyncClient,
) -> None:
    [todo] = await call(client, as_user("hist-owner"), "POST", "todos/todo", {"title": "mine"})
    [version] = await call(
        client, as_user("hist-owner"), "POST", "core/rpc/history", {"of_key": {"id": todo["id"]}}
    )
    other = as_user("hist-other")
    r = await client.post(
        "/api/data/core/rpc/restore", json={"version": version["id"]}, headers=other
    )
    assert r.status_code == 404, r.text
    assert await call(client, other, "POST", "core/rpc/history", {}) == []


async def test_bookkeeping_alone_makes_no_version(client: httpx.AsyncClient) -> None:
    me = as_user("hist-book")
    [rem] = await call(
        client, me, "POST", "reminders/reminder",
        {"title": "Call", "due_at": "2030-01-01T09:00:00Z"},
    )
    await owner_rows(
        "update heycarl_plugin_reminders.reminder set state = 'sent', attempts = 1"
        " where id = %s",
        rem["id"],
    )
    versions = await call(
        client, me, "POST", "core/rpc/history",
        {"of_scope": "reminders", "of_key": {"id": rem["id"]}},
    )
    assert [v["op"] for v in versions] == ["insert"]
    # Restoring the old state, its old time included, must not send it again:
    # a moved time re-arms a reminder, but not when a restore moves it.
    await call(
        client, me, "PATCH", f"reminders/reminder?id=eq.{rem['id']}",
        {"title": "Ring", "due_at": "2030-01-02T09:00:00Z"},
    )
    await owner_rows(
        "update heycarl_plugin_reminders.reminder set state = 'sent' where id = %s",
        rem["id"],
    )
    await call(client, me, "POST", "core/rpc/restore", {"version": versions[0]["id"]})
    [now] = await call(client, me, "GET", f"reminders/reminder?id=eq.{rem['id']}")
    assert (now["title"], now["due_at"][:10], now["state"]) == ("Call", "2030-01-01", "sent")


async def test_undo_reverses_a_change_to_many_rows_in_one_call(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("hist-undo")
    for title in ("a", "b", "c"):
        await call(client, me, "POST", "todos/todo", {"title": title})
    await call(client, me, "PATCH", "todos/todo?id=gt.0", {"title": "spam"})
    # Then the person fixes one by hand: undo must leave that alone.
    [first] = await call(client, me, "GET", "todos/todo?order=id&limit=1")
    await call(client, me, "PATCH", f"todos/todo?id=eq.{first['id']}", {"title": "mine"})
    mass = (await call(client, me, "POST", "core/rpc/history", {"max_rows": 2}))[1]
    assert mass["data"]["title"] == "spam"
    result = await call(client, me, "POST", "core/rpc/undo", {"version": mass["id"]})
    assert result["done"] == 2
    assert [c["label"] for c in result["changed_since"]] == ["mine"]
    titles = [t["title"] for t in await call(client, me, "GET", "todos/todo?order=id")]
    assert titles == ["mine", "b", "c"]
    # An undo is a change too: undoing it brings the spam back.
    newest = (await call(client, me, "POST", "core/rpc/history", {"max_rows": 1}))[0]
    await call(client, me, "POST", "core/rpc/undo", {"version": newest["id"]})
    titles = [t["title"] for t in await call(client, me, "GET", "todos/todo?order=id")]
    assert titles == ["mine", "spam", "spam"]


async def test_restoring_a_delete_that_was_undone_is_a_conflict(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("hist-stale")
    [todo] = await call(client, me, "POST", "todos/todo", {"title": "x"})
    await call(client, me, "DELETE", f"todos/todo?id=eq.{todo['id']}")
    [entry] = await call(client, me, "POST", "core/rpc/trash", {})
    await call(client, me, "POST", "core/rpc/restore", {"version": entry["entry"]})
    r = await client.post(
        "/api/data/core/rpc/restore", json={"version": entry["entry"]}, headers=me
    )
    assert r.status_code == 409, r.text


CRM_TABLES = (
    "contact", "contact_field", "relationship", "note", "activity",
    "activity_contact", "tag", "contact_tag", "gift", "important_date",
)


async def graph(sub: str) -> dict[str, list[Any]]:
    """Every row the person owns in the CRM, reminders and todos."""
    tables = [f"heycarl_plugin_crm.{t}" for t in CRM_TABLES] + [
        "heycarl_plugin_reminders.reminder",
        "heycarl_plugin_todos.todo",
    ]
    return {
        t: [
            r[0]
            for r in await owner_rows(
                f"select to_jsonb(x) from {t} x where owner_sub = %s"  # noqa: S608 -- names above
                " order by to_jsonb(x)::text",
                sub,
            )
        ]
        for t in tables
    }


async def test_a_hostile_client_destroys_nothing(
    served: Plain, client: httpx.AsyncClient, kernel: Kernel
) -> None:
    sub = "hist-victim"
    me = as_user(sub)
    anna = (await call(client, me, "POST", "crm/contact", {"first_name": "Anna"}))[0]["id"]
    bo = (await call(client, me, "POST", "crm/contact", {"first_name": "Bo"}))[0]["id"]
    for path, body in [
        ("crm/contact_field", {"contact_id": anna, "kind": "email", "value": "a@x.se"}),
        ("crm/note", {"contact_id": anna, "body": "Likes tea."}),
        ("crm/gift", {"contact_id": anna, "title": "Tea"}),
        ("crm/relationship", {"contact_id": anna, "other_id": bo, "kind": "sibling"}),
        ("crm/important_date", {"contact_id": anna, "month": 3, "day": 14}),
        ("todos/todo", {"title": "Buy tea"}),
    ]:
        await call(client, me, "POST", path, body)
    tag = (await call(client, me, "POST", "crm/tag", {"name": "family"}))[0]["id"]
    await call(client, me, "POST", "crm/contact_tag", {"contact_id": anna, "tag_id": tag})
    activity = (
        await call(client, me, "POST", "crm/rpc/log_activity",
                   {"contact_ids": [anna, bo], "title": "Lunch"})
    )["id"]
    before = await graph(sub)
    assert before["heycarl_plugin_reminders.reminder"], "the date made a reminder"

    async with connected(served, sub=sub, elicitation=answering(True, [])) as session:
        names = {t.name for t in (await session.list_tools()).tools}
        assert not {n for n in names if "purge" in n or "empty" in n}
        await tool(session, "people_delete", person_id=anna)
        for path, row in [("activity", activity), ("tag", tag)]:
            await tool(session, "data_write", scope="crm", method="DELETE",
                       path=path, query={"id": f"eq.{row}"})
        await tool(session, "data_write", scope="todos", method="DELETE",
                   path="todo", query={"title": "eq.Buy tea"})
        for name, args in [
            ("data_read", {"scope": "core", "path": "rpc/trash"}),
            ("data_write", {"scope": "core", "method": "POST", "path": "rpc/empty_trash"}),
        ]:
            assert "no data scope" in await failure(session, name, **args)
        entries = await tool(session, "trash")
        assert len(entries) == 4
        assert {e["via"] for e in entries} == {f"mcp:{(await tool(session, 'whoami'))['client_id']}"}

    # A token minted for MCP cannot purge even straight at PostgREST.
    r = await kernel.data.post(
        "/rpc/empty_trash",
        headers=postgrest.request_headers(
            {"content-type": "application/json"},
            kernel.minter.user(sub, via_mcp("anyone")),
            migrations.API_SCHEMA,
        ),
        content=b"{}",
    )
    assert r.status_code == 403, r.text

    async with connected(served, sub=sub) as session:
        entries = await tool(session, "trash")
        # Oldest first: the contact's entry fails its links to the activity
        # and the tag, which were deleted after it.
        first = await tool(session, "restore", version=entries[-1]["entry"])
        assert {f["table"] for f in first["failed"]} == {"activity_contact", "contact_tag"}
        for e in entries[:-1]:
            assert (await tool(session, "restore", version=e["entry"]))["failed"] == []
        again = await tool(session, "restore", version=first["failed"][0]["version"])
        assert again["failed"] == []
        assert await tool(session, "trash") == []
    assert await graph(sub) == before
