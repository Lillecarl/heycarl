"""Links between any entries, and how people are related, through the app
and through MCP."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from .conftest import as_user, needs_database
from .test_history import call, owner_rows
from .test_mcp import Plain, answering, connected, failure, served, tool

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]


async def links_of(
    client: httpx.AsyncClient, me: dict[str, str], scope: str, table: str, key: int
) -> list[dict[str, Any]]:
    return await call(
        client, me, "POST", "core/rpc/links",
        {"of_scope": scope, "of_table": table, "of_key": {"id": key}},
    )


async def test_any_entry_links_to_any_other(client: httpx.AsyncClient) -> None:
    me = as_user("link-app")
    [anna] = await call(client, me, "POST", "crm/contact", {"first_name": "Anna"})
    [todo] = await call(client, me, "POST", "todos/todo", {"title": "Buy tea for Anna"})
    link = {
        "a_scope": "todos", "a_table": "todo", "a_key": {"id": todo["id"]},
        "b_scope": "crm", "b_table": "contact", "b_key": {"id": anna["id"]},
    }
    made = await call(client, me, "POST", "core/rpc/link", {**link, "label": "gift"})
    # Again, from the other side: still one link, with the new label.
    again = await call(
        client, me, "POST", "core/rpc/link",
        {"a_scope": "crm", "a_table": "contact", "a_key": {"id": anna["id"]},
         "b_scope": "todos", "b_table": "todo", "b_key": {"id": todo["id"]},
         "label": "birthday gift"},
    )
    assert again["id"] == made["id"]

    [from_todo] = await links_of(client, me, "todos", "todo", todo["id"])
    assert from_todo["label"] == "birthday gift"
    assert from_todo["entry"] == {
        "scope": "crm", "table": "contact", "key": {"id": anna["id"]},
        "label": "Anna", "url": f"/p/crm/heycarl-crm#/contact/{anna['id']}",
    }
    [from_anna] = await links_of(client, me, "crm", "contact", anna["id"])
    assert from_anna["entry"]["label"] == "Buy tea for Anna"
    assert from_anna["entry"]["url"] is None

    found = await call(client, me, "POST", "core/rpc/entries", {"q": "tea"})
    assert [f["entry"]["label"] for f in found] == ["Buy tea for Anna"]

    r = await client.post(
        "/api/data/core/rpc/link",
        json={**link, "b_scope": "todos", "b_table": "todo", "b_key": {"id": todo["id"]}},
        headers=me,
    )
    assert r.status_code == 400, r.text


async def test_nobody_links_to_another_persons_entry(client: httpx.AsyncClient) -> None:
    [theirs] = await call(client, as_user("link-owner"), "POST", "todos/todo", {"title": "x"})
    me = as_user("link-other")
    [mine] = await call(client, me, "POST", "todos/todo", {"title": "y"})
    r = await client.post(
        "/api/data/core/rpc/link",
        json={"a_scope": "todos", "a_table": "todo", "a_key": {"id": mine["id"]},
              "b_scope": "todos", "b_table": "todo", "b_key": {"id": theirs["id"]}},
        headers=me,
    )
    assert r.status_code == 404, r.text
    assert await call(client, me, "POST", "core/rpc/entries", {"q": "x"}) == []


async def test_a_link_follows_its_entry_through_the_trash(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("link-trash")
    [bo] = await call(client, me, "POST", "crm/contact", {"first_name": "Bo"})
    [todo] = await call(client, me, "POST", "todos/todo", {"title": "Call Bo"})
    await call(
        client, me, "POST", "core/rpc/link",
        {"a_scope": "todos", "a_table": "todo", "a_key": {"id": todo["id"]},
         "b_scope": "crm", "b_table": "contact", "b_key": {"id": bo["id"]}},
    )
    await call(client, me, "DELETE", f"todos/todo?id=eq.{todo['id']}")
    assert await links_of(client, me, "crm", "contact", bo["id"]) == []

    [entry] = await call(client, me, "POST", "core/rpc/trash", {})
    await call(client, me, "POST", "core/rpc/restore", {"version": entry["entry"]})
    assert len(await links_of(client, me, "crm", "contact", bo["id"])) == 1

    # Unlinking is undoable like any change.
    [link] = await links_of(client, me, "crm", "contact", bo["id"])
    await call(client, me, "POST", "core/rpc/unlink", {"id": link["id"]})
    newest = (await call(client, me, "POST", "core/rpc/history", {"max_rows": 1}))[0]
    assert (newest["scope"], newest["table"], newest["op"]) == ("core", "link", "delete")
    await call(client, me, "POST", "core/rpc/undo", {"version": newest["id"]})
    assert len(await links_of(client, me, "crm", "contact", bo["id"])) == 1

    # Purging the todo takes its link, and the link's history, with it.
    await call(client, me, "DELETE", f"todos/todo?id=eq.{todo['id']}")
    [entry] = await call(client, me, "POST", "core/rpc/trash", {})
    await call(client, me, "POST", "core/rpc/purge", {"entry": entry["entry"]})
    left = await owner_rows(
        "select count(*) from heycarl_private.link where owner_sub = %s", "link-trash"
    )
    assert left == [(0,)]
    assert await call(client, me, "POST", "core/rpc/trash", {}) == []


async def test_mcp_links_entries_and_relates_people(served: Plain) -> None:
    async with connected(served, sub="link-mcp", elicitation=answering(True, [])) as s:
        anna = await tool(s, "people_add", first_name="Anna")
        mia = await tool(s, "people_add", first_name="Mia")
        rel = await tool(s, "people_relate", person_id=mia["id"], other_id=anna["id"], kind="parent")
        person = await tool(s, "people_get", person_id=anna["id"])
        assert [(r["kind"], r["other_first_name"]) for r in person["relations"]] == [("child", "Mia")]
        assert "Input should be" in await failure(
            s, "people_relate", person_id=mia["id"], other_id=anna["id"], kind="nemesis"
        )

        todo = await tool(s, "todos_add", title="Plan Mia's party")
        [found] = await tool(s, "entries_search", query="party")
        made = await tool(
            s, "link", scope=found["scope"], table=found["table"], key=found["key"],
            other_scope="crm", other_table="contact", other_key={"id": mia["id"]},
            label="host",
        )
        [linked] = await tool(s, "links_list", scope="todos", table="todo", key={"id": todo["id"]})
        assert (linked["id"], linked["label"], linked["entry"]["label"]) == (made["id"], "host", "Mia")
        assert await tool(s, "unlink", link_id=made["id"]) == "unlinked"
        assert await tool(s, "links_list", scope="todos", table="todo", key={"id": todo["id"]}) == []

        assert await tool(s, "people_unrelate", relationship_id=rel["id"]) == "removed"
        assert (await tool(s, "people_get", person_id=anna["id"]))["relations"] == []
