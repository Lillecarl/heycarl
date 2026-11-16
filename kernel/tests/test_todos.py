"""Subtasks: a todo hangs under one of the same person's todos, never in a
loop, and a deleted parent takes its subtree to the trash as one entry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from .conftest import as_user, needs_database
from .test_mcp import Plain, answering, connected, failure, served, tool

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]


async def request(
    client: httpx.AsyncClient, user: str, method: str, path: str, body: object = None
) -> httpx.Response:
    return await client.request(
        method,
        f"/api/data/{path}",
        json=body,
        headers={**as_user(user), "prefer": "return=representation"},
    )


# Any: a PostgREST response body.
async def ok(
    client: httpx.AsyncClient, user: str, method: str, path: str, body: object = None
) -> Any:
    r = await request(client, user, method, path, body)
    assert r.is_success, f"{method} {path}: {r.status_code} {r.text}"
    return r.json() if r.content else None


async def add(
    client: httpx.AsyncClient, user: str, title: str, parent: int | None = None
) -> int:
    [row] = await ok(
        client, user, "POST", "todos/todo", {"title": title, "parent_id": parent}
    )
    return int(row["id"])


async def parents(client: httpx.AsyncClient, user: str) -> dict[str, int | None]:
    rows = await ok(client, user, "GET", "todos/todo?select=id,title,parent_id")
    by_id = {r["id"]: r["title"] for r in rows}
    return {r["title"]: by_id.get(r["parent_id"]) for r in rows}


async def test_a_todo_is_never_its_own_ancestor(client: httpx.AsyncClient) -> None:
    u = "todo-cycle"
    a = await add(client, u, "a")
    b = await add(client, u, "b", a)
    c = await add(client, u, "c", b)
    for todo, parent in ((a, c), (a, b), (a, a)):
        r = await request(
            client, u, "PATCH", f"todos/todo?id=eq.{todo}", {"parent_id": parent}
        )
        assert r.status_code == 400, r.text
    await ok(client, u, "PATCH", f"todos/todo?id=eq.{c}", {"parent_id": a})
    await ok(client, u, "PATCH", f"todos/todo?id=eq.{b}", {"parent_id": None})
    assert await parents(client, u) == {"a": None, "b": None, "c": "a"}


async def test_nobody_hangs_a_todo_under_another_persons(
    client: httpx.AsyncClient,
) -> None:
    theirs = await add(client, "todo-owner", "private")
    mine = await add(client, "todo-intruder", "mine")
    r = await request(
        client, "todo-intruder", "POST", "todos/todo", {"title": "x", "parent_id": theirs}
    )
    assert r.status_code == 409, r.text
    r = await request(
        client, "todo-intruder", "PATCH", f"todos/todo?id=eq.{mine}", {"parent_id": theirs}
    )
    assert r.status_code == 409, r.text
    assert await parents(client, "todo-owner") == {"private": None}


async def test_a_deleted_subtree_is_one_trash_entry_and_comes_back(
    client: httpx.AsyncClient,
) -> None:
    u = "todo-trash"
    a = await add(client, u, "a")
    b = await add(client, u, "b", a)
    await add(client, u, "c", b)
    await add(client, u, "other")
    await ok(client, u, "DELETE", f"todos/todo?id=eq.{a}")
    assert await parents(client, u) == {"other": None}
    [entry] = await ok(client, u, "POST", "core/rpc/trash", {})
    await ok(client, u, "POST", "core/rpc/restore", {"version": entry["entry"]})
    assert await parents(client, u) == {"a": None, "b": "a", "c": "b", "other": None}
    [back] = await ok(client, u, "GET", f"todos/todo?id=eq.{b}")
    assert back["parent_id"] == a


async def new_list(client: httpx.AsyncClient, user: str, name: str) -> int:
    [row] = await ok(client, user, "POST", "todos/list", {"name": name})
    return int(row["id"])


async def lists_of(client: httpx.AsyncClient, user: str) -> dict[str, int | None]:
    rows = await ok(client, user, "GET", "todos/todo?select=title,list_id")
    return {r["title"]: r["list_id"] for r in rows}


async def test_nobody_puts_a_todo_in_another_persons_list(
    client: httpx.AsyncClient,
) -> None:
    theirs = await new_list(client, "list-owner", "Shopping")
    mine = await add(client, "list-intruder", "mine")
    r = await request(
        client, "list-intruder", "POST", "todos/todo", {"title": "x", "list_id": theirs}
    )
    assert r.status_code == 409, r.text
    r = await request(
        client, "list-intruder", "PATCH", f"todos/todo?id=eq.{mine}", {"list_id": theirs}
    )
    assert r.status_code == 409, r.text
    assert await ok(client, "list-intruder", "GET", "todos/list") == []
    r = await request(client, "list-intruder", "POST", "todos/list", {"name": "Inbox"})
    assert r.status_code == 400, r.text


async def test_a_subtask_is_in_its_parents_list(client: httpx.AsyncClient) -> None:
    u = "list-follow"
    shop = await new_list(client, u, "Shopping")
    work = await new_list(client, u, "Work")
    a = await add(client, u, "a")
    await ok(client, u, "PATCH", f"todos/todo?id=eq.{a}", {"list_id": shop})
    b = await add(client, u, "b", a)
    c = await add(client, u, "c", b)
    assert await lists_of(client, u) == {"a": shop, "b": shop, "c": shop}
    r = await request(client, u, "PATCH", f"todos/todo?id=eq.{c}", {"list_id": work})
    assert r.status_code == 400, r.text
    await ok(client, u, "PATCH", f"todos/todo?id=eq.{a}", {"list_id": work})
    assert await lists_of(client, u) == {"a": work, "b": work, "c": work}
    other = await add(client, u, "other")
    await ok(client, u, "PATCH", f"todos/todo?id=eq.{b}", {"parent_id": other})
    assert await lists_of(client, u) == {"a": work, "b": None, "c": None, "other": None}


async def test_a_deleted_list_takes_its_todos_and_comes_back(
    client: httpx.AsyncClient,
) -> None:
    u = "list-trash"
    shop = await new_list(client, u, "Shopping")
    milk = await add(client, u, "milk")
    await ok(client, u, "PATCH", f"todos/todo?id=eq.{milk}", {"list_id": shop})
    await add(client, u, "oat", milk)
    await add(client, u, "inbox item")
    await ok(client, u, "DELETE", f"todos/list?id=eq.{shop}")
    assert await lists_of(client, u) == {"inbox item": None}
    [entry] = await ok(client, u, "POST", "core/rpc/trash", {})
    await ok(client, u, "POST", "core/rpc/restore", {"version": entry["entry"]})
    assert await lists_of(client, u) == {"milk": shop, "oat": shop, "inbox item": None}
    assert await parents(client, u) == {"milk": None, "oat": "milk", "inbox item": None}


async def test_the_tools_keep_lists(served: Plain) -> None:
    asked: list[str] = []
    async with connected(
        served, sub="list-mcp-sub", elicitation=answering(True, asked)
    ) as session:
        shop = await tool(session, "todos_list_add", name="Shopping")
        await tool(session, "todos_add", title="Milk", list="Shopping")
        await tool(session, "todos_add", title="Call mum")
        lists = await tool(session, "todos_lists")
        assert [(x["name"], x["open"]) for x in lists] == [("inbox", 1), ("Shopping", 1)]
        [milk] = await tool(session, "todos_list", list=shop["id"])
        assert milk["title"] == "Milk"
        [call] = await tool(session, "todos_list", list="inbox")
        await tool(session, "todos_move", todo_id=call["id"], list="Shopping")
        assert await tool(session, "todos_list", list="inbox") == []
        await tool(session, "todos_list_rename", list="Shopping", name="Groceries")
        assert len(await tool(session, "todos_list", list="Groceries")) == 2
        assert "no list" in await failure(session, "todos_list", list="Nope")
        await tool(session, "todos_list_delete", list="Groceries")
        assert asked == ["Delete the list 'Groceries' and its 2 todos?"]
        assert await tool(session, "todos_list") == []
        assert await tool(session, "todos_lists") == [
            {"id": None, "name": "inbox", "open": 0}
        ]


async def test_the_tools_add_move_and_list_subtasks(served: Plain) -> None:
    async with connected(served, sub="todo-mcp-sub") as session:
        trip = await tool(session, "todos_add", title="Trip")
        bag = await tool(session, "todos_add", title="Pack", parent_id=trip["id"])
        tea = await tool(session, "todos_add", title="Tea")
        [root, other] = await tool(session, "todos_list")
        assert (root["title"], [s["title"] for s in root["subtasks"]]) == (
            "Trip",
            ["Pack"],
        )
        assert other["subtasks"] == []
        await tool(session, "todos_move", todo_id=tea["id"], parent_id=bag["id"])
        [root] = await tool(session, "todos_list")
        assert root["subtasks"][0]["subtasks"][0]["title"] == "Tea"
        assert "own subtask" in await failure(
            session, "todos_move", todo_id=trip["id"], parent_id=tea["id"]
        )
        await tool(session, "todos_move", todo_id=tea["id"])
        assert len(await tool(session, "todos_list")) == 2
        await tool(session, "todos_done", todo_id=trip["id"])
        titles = [t["title"] for t in await tool(session, "todos_list")]
        assert titles == ["Pack", "Tea"]
