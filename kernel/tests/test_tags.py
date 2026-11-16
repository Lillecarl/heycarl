"""The tags plugin: a tag is an entry, tagging is a core link to it, and
nobody sees or uses another person's tags, through the app or through MCP."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from .conftest import as_user, needs_database
from .test_history import call
from .test_mcp import Plain, answering, connected, failure, served, tool

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]


def todo_entry(todo_id: int) -> dict[str, object]:
    return {"of_scope": "todos", "of_table": "todo", "of_key": {"id": todo_id}}


async def tag(
    client: httpx.AsyncClient, me: dict[str, str], todo_id: int, name: str, **extra: object
) -> dict[str, Any]:
    row: dict[str, Any] = await call(
        client, me, "POST", "tags/rpc/tag_entry", {**todo_entry(todo_id), "name": name, **extra}
    )
    return row


async def test_tagging_makes_the_tag_once_and_filters_by_table(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("tag-app")
    [milk] = await call(client, me, "POST", "todos/todo", {"title": "Milk"})
    [eggs] = await call(client, me, "POST", "todos/todo", {"title": "Eggs"})
    [anna] = await call(client, me, "POST", "crm/contact", {"first_name": "Anna"})

    first = await tag(client, me, milk["id"], "Shopping", color="base0B")
    again = await tag(client, me, eggs["id"], "shopping", color="base08")
    assert again["id"] == first["id"]
    assert (again["name"], again["color"]) == ("Shopping", "base0B")
    twice = await tag(client, me, milk["id"], "SHOPPING")
    assert twice["link_id"] == first["link_id"]
    await call(
        client, me, "POST", "tags/rpc/tag_entry",
        {"of_scope": "crm", "of_table": "contact", "of_key": {"id": anna["id"]}, "name": "Shopping"},
    )

    [summary] = await call(client, me, "GET", "tags/tag_summary")
    assert (summary["name"], summary["entries"]) == ("Shopping", 3)
    keys = await call(
        client, me, "POST", "tags/rpc/tagged_keys",
        {"tag_id": first["id"], "of_scope": "todos", "of_table": "todo"},
    )
    assert sorted(k["id"] for k in keys) == sorted([milk["id"], eggs["id"]])
    [on_milk] = await call(client, me, "POST", "tags/rpc/tags_of", todo_entry(milk["id"]))
    assert on_milk["name"] == "Shopping"

    assert await call(
        client, me, "POST", "tags/rpc/untag", {**todo_entry(milk["id"]), "tag_id": first["id"]}
    ) is True
    assert await call(client, me, "POST", "tags/rpc/tags_of", todo_entry(milk["id"])) == []
    r = await client.post(
        "/api/data/tags/rpc/untag",
        json={**todo_entry(milk["id"]), "tag_id": first["id"]},
        headers=me,
    )
    assert r.status_code == 404, r.text


async def test_names_are_unique_in_any_case_and_colours_are_roles(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("tag-names")
    await call(client, me, "POST", "tags/tag", {"name": "Home"})
    for body in ({"name": "home"}, {"name": "x", "color": "red"}, {"name": " "}):
        r = await client.post("/api/data/tags/tag", json=body, headers=me)
        assert r.status_code in {400, 409}, (body, r.text)


async def test_nobody_reaches_another_persons_tags(client: httpx.AsyncClient) -> None:
    owner = as_user("tag-owner")
    [theirs] = await call(client, owner, "POST", "todos/todo", {"title": "Secret"})
    made = await tag(client, owner, theirs["id"], "Private")

    me = as_user("tag-other")
    [mine] = await call(client, me, "POST", "todos/todo", {"title": "Mine"})
    assert await call(client, me, "GET", "tags/tag") == []
    assert await call(client, me, "GET", "tags/tag_summary") == []
    for path, body in (
        ("tagged", {"tag_id": made["id"]}),
        ("tagged_keys", {"tag_id": made["id"], "of_scope": "todos", "of_table": "todo"}),
        ("untag", {**todo_entry(theirs["id"]), "tag_id": made["id"]}),
        ("tag_entry", {**todo_entry(theirs["id"]), "name": "Mine too"}),
    ):
        r = await client.post(f"/api/data/tags/rpc/{path}", json=body, headers=me)
        assert r.status_code == 404, (path, r.text)
    assert await call(client, me, "POST", "tags/rpc/tags_of", todo_entry(theirs["id"])) == []
    # A link from my todo to their tag: the core refuses the far end.
    r = await client.post(
        "/api/data/core/rpc/link",
        json={**{k.replace("of_", "a_"): v for k, v in todo_entry(mine["id"]).items()},
              "b_scope": "tags", "b_table": "tag", "b_key": {"id": made["id"]}},
        headers=me,
    )
    assert r.status_code == 404, r.text
    r = await client.patch(
        f"/api/data/tags/tag?id=eq.{made['id']}",
        json={"name": "Taken"},
        headers={**me, "prefer": "return=representation"},
    )
    assert r.json() == [], r.text
    # The failed tag_entry made no tag either.
    assert await call(client, me, "GET", "tags/tag") == []
    [still] = await call(client, owner, "GET", "tags/tag_summary")
    assert (still["name"], still["entries"]) == ("Private", 1)


async def test_tagged_shows_only_live_entries_and_a_tag_comes_back(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("tag-trash")
    [milk] = await call(client, me, "POST", "todos/todo", {"title": "Milk"})
    [bread] = await call(client, me, "POST", "todos/todo", {"title": "Bread"})
    made = await tag(client, me, milk["id"], "Shopping")
    await tag(client, me, bread["id"], "Shopping")

    await call(client, me, "DELETE", f"todos/todo?id=eq.{bread['id']}")
    found = await call(client, me, "POST", "tags/rpc/tagged", {"tag_id": made["id"]})
    assert [f["entry"]["label"] for f in found] == ["Milk"]

    await call(client, me, "DELETE", f"tags/tag?id=eq.{made['id']}")
    assert await call(client, me, "POST", "tags/rpc/tags_of", todo_entry(milk["id"])) == []
    entries = (await client.post("/api/data/core/rpc/trash", json={}, headers=me)).json()
    deleted_tag = next(e for e in entries if e["items"][0]["table"] == "tag")
    r = await client.post(
        "/api/data/core/rpc/restore", json={"version": deleted_tag["entry"]}, headers=me
    )
    assert r.is_success and r.json()["failed"] == [], r.text
    [back] = await call(client, me, "POST", "tags/rpc/tags_of", todo_entry(milk["id"]))
    assert back["id"] == made["id"]


async def test_tags_through_mcp(served: Plain) -> None:
    asked: list[str] = []
    async with connected(
        served, sub="tag-mcp", elicitation=answering(True, asked)
    ) as session:
        hints = {t.name: t.annotations for t in (await session.list_tools()).tools}
        delete = hints["tags_delete"]
        assert delete is not None and delete.destructive_hint is True
        listing = hints["tags_list"]
        assert listing is not None and listing.read_only_hint is True

        [todo] = await tool(
            session, "data_write", scope="todos", method="POST", path="todo",
            body={"title": "Oat milk"},
        )
        key = {"id": todo["id"]}
        # `tool` takes the tool's name as `name`, so this one goes direct.
        tagged = await session.call_tool(
            "tag", {"scope": "todos", "table": "todo", "key": key, "name": "Groceries"}
        )
        assert not tagged.is_error, tagged.content
        made = tagged.structured_content
        assert made is not None and made["name"] == "Groceries"
        tid = made["id"]

        of = await tool(session, "tags_of", scope="todos", table="todo", key=key)
        assert [t["name"] for t in of] == ["Groceries"]
        found = await tool(session, "tagged", tag_id=tid, scope="todos")
        assert [e["key"] for e in found["entries"]] == [key]
        assert (await tool(session, "tags_list"))[0]["entries"] == 1

        result = await session.call_tool(
            "tags_rename", {"tag_id": tid, "name": "Shop", "color": "base0D"}
        )
        renamed = result.structured_content
        assert not result.is_error and renamed is not None, result.content
        assert (renamed["name"], renamed["color"]) == ("Shop", "base0D")
        assert "nothing to change" in await failure(session, "tags_rename", tag_id=tid)

        assert await tool(
            session, "untag", scope="todos", table="todo", key=key, tag_id=tid
        ) == "untagged"
        assert await tool(session, "tags_delete", tag_id=tid) == "deleted"
        assert "Shop" in asked[-1]
        assert await tool(session, "tags_list") == []


async def test_todos_filter_by_tag_within_a_list(served: Plain) -> None:
    async with connected(served, sub="tag-todos") as session:
        shop = await tool(session, "todos_list_add", name="Shopping")
        milk = await tool(session, "todos_add", title="Milk", list="Shopping", tag="dairy")
        await tool(session, "todos_add", title="Bread", list="Shopping")
        cheese = await tool(session, "todos_add", title="Cheese", parent_id=milk["id"], tag="Dairy")
        elsewhere = await tool(session, "todos_add", title="Yoghurt", tag="dairy")

        dairy = await tool(session, "todos_list", list=shop["id"], tag="DAIRY")
        assert [t["title"] for t in dairy] == ["Milk"]
        assert [s["title"] for s in dairy[0]["subtasks"]] == ["Cheese"]
        everywhere = await tool(session, "todos_list", tag="dairy")
        assert {t["id"] for t in everywhere} == {milk["id"], elsewhere["id"]}
        # Without a tag, nothing changes: the whole list, as a tree.
        whole = await tool(session, "todos_list", list="Shopping")
        assert sorted(t["title"] for t in whole) == ["Bread", "Milk"]
        assert "no tag" in await failure(session, "todos_list", tag="nope")
        assert cheese["parent_id"] == milk["id"]
        [tag_row] = await tool(session, "tags_list")
        assert (tag_row["name"], tag_row["entries"]) == ("dairy", 3)
