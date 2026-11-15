"""The counters plugin: values summed in SQL, and nobody else's counter
can be read, changed or counted on, through the app or through MCP."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from mcp import types

from .conftest import as_user, needs_database
from .test_mcp import Plain, answering, connected, failure, served, tool

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]

DATA = "/api/data/counters"
RPCS = {
    "bump": {},
    "set_value": {"target": 9},
    "reset_value": {},
    "undo_last": {},
}


async def request(
    client: httpx.AsyncClient,
    user: str,
    method: str,
    path: str,
    body: object = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.request(
        method,
        f"{DATA}/{path}",
        json=body,
        params=params,
        headers={**as_user(user), "prefer": "return=representation"},
    )


# Any: a PostgREST response body.
async def ok(
    client: httpx.AsyncClient,
    user: str,
    method: str,
    path: str,
    body: object = None,
    params: dict[str, str] | None = None,
) -> Any:
    r = await request(client, user, method, path, body, params)
    assert r.is_success, f"{method} {path}: {r.status_code} {r.text}"
    return r.json() if r.content else None


async def make(client: httpx.AsyncClient, user: str, name: str, **extra: object) -> int:
    [row] = await ok(client, user, "POST", "counter", {"name": name, **extra})
    return int(row["id"])


async def value(
    client: httpx.AsyncClient, user: str, counter_id: int
) -> tuple[int, int]:
    [row] = await ok(client, user, "GET", f"counter_value?id=eq.{counter_id}")
    return row["value"], row["events"]


async def test_counting_setting_resetting_and_undoing(
    client: httpx.AsyncClient,
) -> None:
    u = "cnt-count"
    cid = await make(client, u, "Coffee", unit="cups", step=2)
    assert await value(client, u, cid) == (0, 0)

    bump = await ok(client, u, "POST", "rpc/bump", {"counter_id": cid})
    assert (bump["value"], bump["unit"]) == (2, "cups")
    await ok(client, u, "POST", "rpc/bump", {"counter_id": cid, "delta": -1})
    await ok(
        client, u, "POST", "rpc/bump", {"counter_id": cid, "delta": 5, "note": "party"}
    )
    assert await value(client, u, cid) == (6, 3)

    assert (
        await ok(client, u, "POST", "rpc/set_value", {"counter_id": cid, "target": 10})
    )["value"] == 10
    # Already there: no event with delta 0.
    await ok(client, u, "POST", "rpc/set_value", {"counter_id": cid, "target": 10})
    assert await value(client, u, cid) == (10, 4)

    undone = await ok(client, u, "POST", "rpc/undo_last", {"counter_id": cid})
    assert undone["delta"] == 4
    assert await value(client, u, cid) == (6, 3)

    assert (await ok(client, u, "POST", "rpc/reset_value", {"counter_id": cid}))[
        "value"
    ] == 0
    events = await ok(client, u, "GET", f"event?counter_id=eq.{cid}&order=id.desc")
    assert [(e["delta"], e["note"]) for e in events] == [
        (-6, ""),
        (5, "party"),
        (-1, ""),
        (2, ""),
    ]

    r = await request(client, u, "POST", "event", {"counter_id": cid, "delta": 0})
    assert r.status_code == 400, r.text

    days = await ok(
        client,
        u,
        "GET",
        "rpc/daily_totals",
        params={"counter_id": str(cid), "zone": "UTC"},
    )
    assert len(days) == 30
    assert days[-1]["day"] == datetime.now(UTC).date().isoformat()
    assert {k: days[-1][k] for k in ("total", "added", "removed", "events")} == {
        "total": 0,
        "added": 7,
        "removed": 7,
        "events": 4,
    }
    assert all(d["events"] == 0 for d in days[:-1])

    hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    since = {"counter_id": str(cid), "since": hour_ago}
    assert await ok(client, u, "GET", "rpc/count_since", params=since) == [
        {"total": 0, "events": 4}
    ]
    assert await ok(
        client, u, "GET", "rpc/count_since", params={**since, "positive_only": "true"}
    ) == [{"total": 7, "events": 2}]

    # A backdated event counts on its own day, not since an hour ago.
    three_days_ago = datetime.now(UTC) - timedelta(days=3)
    await ok(
        client,
        u,
        "POST",
        "rpc/bump",
        {"counter_id": cid, "delta": 3, "at": three_days_ago.isoformat()},
    )
    [day] = await ok(
        client,
        u,
        "GET",
        "rpc/daily_totals",
        params={
            "counter_id": str(cid),
            "since": three_days_ago.date().isoformat(),
            "until": three_days_ago.date().isoformat(),
        },
    )
    assert (day["total"], day["events"]) == (3, 1)
    assert (await ok(client, u, "GET", "rpc/count_since", params=since))[0][
        "total"
    ] == 0
    r = await request(
        client,
        u,
        "GET",
        "rpc/daily_totals",
        params={"counter_id": str(cid), "since": "2000-01-01", "until": "2026-01-01"},
    )
    assert r.status_code == 400, r.text


async def test_nobody_else_reads_changes_or_counts_on_a_counter(
    client: httpx.AsyncClient,
) -> None:
    alice, bob = "cnt-alice", "cnt-bob"
    cid = await make(client, alice, "Pushups", step=10)
    for _ in range(3):
        await ok(client, alice, "POST", "rpc/bump", {"counter_id": cid})
    [event] = await ok(client, alice, "GET", f"event?counter_id=eq.{cid}&limit=1")

    assert (await client.get(f"{DATA}/counter_value")).status_code == 401
    assert (
        await client.post(f"{DATA}/rpc/bump", json={"counter_id": cid})
    ).status_code == 401

    for path in ["counter", "counter_value", "event", f"event?counter_id=eq.{cid}"]:
        assert await ok(client, bob, "GET", path) == [], path

    # The composite foreign key: an event of Bob's names a counter of Bob's.
    r = await request(client, bob, "POST", "event", {"counter_id": cid, "delta": 1})
    assert r.status_code == 409, r.text
    # And owner_sub has no column grant, so Bob cannot sign as Alice.
    r = await request(
        client,
        bob,
        "POST",
        "event",
        {"counter_id": cid, "delta": 1, "owner_sub": alice},
    )
    assert r.status_code in {401, 403}, r.text
    r = await request(client, bob, "POST", "counter", {"name": "x", "owner_sub": alice})
    assert r.status_code in {401, 403}, r.text

    # Events cannot move to another counter: they have no update grant.
    own = await make(client, bob, "Mine")
    [mine] = await ok(client, bob, "POST", "event", {"counter_id": own, "delta": 1})
    r = await request(
        client, bob, "PATCH", f"event?id=eq.{mine['id']}", {"counter_id": cid}
    )
    assert r.status_code in {401, 403}, r.text

    for fn, extra in RPCS.items():
        r = await request(
            client, bob, "POST", f"rpc/{fn}", {"counter_id": cid, **extra}
        )
        assert r.status_code == 404, (fn, r.text)
        assert "no counter" in r.text, r.text
    for fn, extra in [
        ("daily_totals", {}),
        ("count_since", {"since": "2000-01-01T00:00:00Z"}),
    ]:
        r = await request(
            client, bob, "GET", f"rpc/{fn}", params={"counter_id": str(cid), **extra}
        )
        assert r.status_code == 404, (fn, r.text)

    # Row-level security hides the rows, so these succeed and touch nothing.
    assert (
        await ok(client, bob, "PATCH", f"counter?id=eq.{cid}", {"name": "Bob's"}) == []
    )
    assert await ok(client, bob, "DELETE", f"event?id=eq.{event['id']}") == []
    assert await ok(client, bob, "DELETE", f"counter?id=eq.{cid}") == []

    [row] = await ok(client, alice, "GET", f"counter_value?id=eq.{cid}")
    assert (row["name"], row["value"], row["events"]) == ("Pushups", 30, 3)
    assert await value(client, bob, own) == (1, 1)


async def test_a_deleted_counter_comes_back_from_the_trash(
    client: httpx.AsyncClient,
) -> None:
    u = "cnt-trash"
    cid = await make(client, u, "Books")
    for delta in (1, 2, 3):
        await ok(client, u, "POST", "rpc/bump", {"counter_id": cid, "delta": delta})
    await ok(client, u, "DELETE", f"counter?id=eq.{cid}")
    assert await ok(client, u, "GET", "counter") == []
    assert await ok(client, u, "GET", "event") == []

    me = {**as_user(u), "prefer": "return=representation"}
    # The counter and its events went in one transaction: one entry.
    [entry] = (
        await client.post("/api/data/core/rpc/trash", json={}, headers=me)
    ).json()
    assert len(entry["items"]) == 4, entry
    r = await client.post(
        "/api/data/core/rpc/restore", json={"version": entry["entry"]}, headers=me
    )
    assert r.is_success and r.json()["failed"] == [], r.text
    assert await value(client, u, cid) == (6, 3)


async def test_counters_through_mcp(served: Plain) -> None:
    asked: list[str] = []
    async with connected(
        served, sub="cnt-mcp", elicitation=answering(True, asked)
    ) as session:
        hints = {t.name: t.annotations for t in (await session.list_tools()).tools}
        listing, delete, bump = (
            hints["counters_list"],
            hints["counters_delete"],
            hints["counters_bump"],
        )
        assert listing is not None and listing.read_only_hint is True
        assert delete is not None and delete.destructive_hint is True
        assert bump is not None and bump.destructive_hint is False

        # `tool` takes the tool's name as `name`, so this one goes direct.
        added = await session.call_tool(
            "counters_add", {"name": "Water", "unit": "glasses"}
        )
        assert not added.is_error, added.content
        made = added.structured_content
        assert made is not None
        cid = made["id"]
        assert (made["value"], made["step"]) == (0, 1)
        assert (await tool(session, "counters_bump", counter_id=cid))["value"] == 1
        assert (
            await tool(
                session, "counters_bump", counter_id=cid, delta=4, note="hot day"
            )
        )["value"] == 5
        assert "must not be 0" in await failure(
            session, "counters_bump", counter_id=cid, delta=0
        )
        assert (await tool(session, "counters_set", counter_id=cid, value=2))[
            "value"
        ] == 2
        assert (await tool(session, "counters_reset", counter_id=cid))["value"] == 0
        undo = await tool(session, "counters_undo", counter_id=cid)
        assert (undo["undone"]["delta"], undo["counter"]["value"]) == (-2, 2)

        history = await tool(session, "counters_history", counter_id=cid, limit=2)
        assert [e["delta"] for e in history["events"]] == [-3, 4]
        days = await tool(
            session, "counters_daily", counter_id=cid, zone="Europe/Stockholm"
        )
        assert len(days) == 30 and days[-1]["total"] == 2
        since = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        assert await tool(
            session,
            "counters_count_since",
            counter_id=cid,
            since=since,
            positive_only=True,
        ) == {"total": 5, "events": 2}

        renamed = await tool(
            session, "counters_update", counter_id=cid, changes={"name": "Water today"}
        )
        assert (renamed["name"], renamed["value"]) == ("Water today", 2)
        assert "cannot change" in await failure(
            session, "counters_update", counter_id=cid, changes={"owner_sub": "x"}
        )
        [listed] = await tool(session, "counters_list")
        assert listed["id"] == cid

        result = await session.read_resource("heycarl://counters")
        [content] = result.contents
        assert isinstance(content, types.TextResourceContents), content
        assert [c["name"] for c in json.loads(content.text)["counters"]] == [
            "Water today"
        ]

    async with connected(served, sub="cnt-mcp-other") as session:
        assert await tool(session, "counters_list") == []
        for name, args in [
            ("counters_bump", {}),
            ("counters_set", {"value": 7}),
            ("counters_reset", {}),
            ("counters_undo", {}),
            ("counters_history", {}),
            ("counters_daily", {}),
            ("counters_update", {"changes": {"name": "mine"}}),
            ("counters_delete", {}),
        ]:
            assert "no counter" in await failure(
                session, name, counter_id=cid, **args
            ), name

    async with connected(served, sub="cnt-mcp", scopes=("read",)) as session:
        assert len(await tool(session, "counters_daily", counter_id=cid)) == 30
        assert "write scope" in await failure(session, "counters_bump", counter_id=cid)

    async with connected(served, sub="cnt-mcp") as session:
        assert "cannot ask" in await failure(session, "counters_delete", counter_id=cid)
    async with connected(
        served, sub="cnt-mcp", elicitation=answering(True, asked)
    ) as session:
        assert await tool(session, "counters_delete", counter_id=cid) == "deleted"
        assert await tool(session, "counters_list") == []
    assert asked == ["Delete the counter 'Water today' (2) and its history?"]
