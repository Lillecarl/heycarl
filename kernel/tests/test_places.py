"""The places plugin: distances in SQL, entries bound by a link or a shared
tag, and nobody sees or reaches another person's places."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from .conftest import as_user, needs_database
from .test_history import call

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.anyio, needs_database]

# Stockholm Central, and Stureplan about 1 km north-east of it.
CENTRAL = (59.33029, 18.05891)
STUREPLAN = (59.33553, 18.07384)
# 300 m north of Central.
AWAY = (59.33299, 18.05891)


def place(point: tuple[float, float]) -> dict[str, float]:
    return {"latitude": point[0], "longitude": point[1]}


def at(point: tuple[float, float]) -> dict[str, float]:
    return {"lat": point[0], "lon": point[1]}


def place_end(place_id: int) -> dict[str, object]:
    return {"a_scope": "places", "a_table": "place", "a_key": {"id": place_id}}


async def make(
    client: httpx.AsyncClient,
    me: dict[str, str],
    name: str,
    point: tuple[float, float],
    radius: int,
) -> int:
    [row] = await call(
        client, me, "POST", "places/place", {"name": name, **place(point), "radius": radius}
    )
    return int(row["id"])


async def test_near_orders_by_distance_and_says_inside(
    client: httpx.AsyncClient,
) -> None:
    me = as_user("place-near")
    central = await make(client, me, "Central", CENTRAL, 200)
    sture = await make(client, me, "Stureplan", STUREPLAN, 100)

    near: list[dict[str, Any]] = await call(
        client, me, "POST", "places/rpc/near", {**at(CENTRAL), "within": 5000}
    )
    assert [(p["id"], p["inside"]) for p in near] == [(central, True), (sture, False)]
    assert near[0]["distance"] == 0
    assert 1000 < near[1]["distance"] < 1100, near[1]
    close = await call(client, me, "POST", "places/rpc/near", {**at(CENTRAL), "within": 500})
    assert close == [near[0]]

    for body in ({"lat": 91, "lon": 0}, {"lat": 0, "lon": 181}, {**at(CENTRAL), "within": 5}):
        r = await client.post("/api/data/places/rpc/near", json=body, headers=me)
        assert r.status_code == 400, (body, r.text)
    for body in (
        {"name": "x", "latitude": 91, "longitude": 0},
        {"name": "x", **place(CENTRAL), "radius": 5},
        {"name": " x", **place(CENTRAL)},
    ):
        r = await client.post("/api/data/places/place", json=body, headers=me)
        assert r.status_code == 400, (body, r.text)


async def test_here_finds_linked_and_tagged_entries(client: httpx.AsyncClient) -> None:
    me = as_user("place-here")
    shop = await make(client, me, "Hardware shop", CENTRAL, 150)
    await make(client, me, "Elsewhere", STUREPLAN, 100)
    [hose] = await call(client, me, "POST", "todos/todo", {"title": "Buy a hose"})
    [screws] = await call(client, me, "POST", "todos/todo", {"title": "Buy screws"})
    [both] = await call(client, me, "POST", "todos/todo", {"title": "Ask about returns"})

    for todo in (hose, both):
        await call(
            client, me, "POST", "core/rpc/link",
            {**place_end(shop), "b_scope": "todos", "b_table": "todo", "b_key": {"id": todo["id"]}},
        )
    for scope, table, key in (
        ("places", "place", {"id": shop}),
        ("todos", "todo", {"id": screws["id"]}),
        ("todos", "todo", {"id": both["id"]}),
    ):
        await call(
            client, me, "POST", "tags/rpc/tag_entry",
            {"of_scope": scope, "of_table": table, "of_key": key, "name": "hardware"},
        )

    found: list[dict[str, Any]] = await call(client, me, "POST", "places/rpc/here", at(CENTRAL))
    got = {(f["place_id"], f["entry"]["label"], f["via_tag"]) for f in found}
    assert got == {
        (shop, "Buy a hose", None),
        (shop, "Ask about returns", None),
        (shop, "Buy screws", "hardware"),
    }, found
    assert len(found) == 3, found

    # Outside the shop's radius, nothing is here.
    assert await call(client, me, "POST", "places/rpc/here", at(AWAY)) == []

    # A deleted todo is hidden; a deleted place binds nothing.
    await call(client, me, "DELETE", f"todos/todo?id=eq.{hose['id']}")
    left = await call(client, me, "POST", "places/rpc/here", at(CENTRAL))
    assert {f["entry"]["label"] for f in left} == {"Ask about returns", "Buy screws"}
    await call(client, me, "DELETE", f"places/place?id=eq.{shop}")
    assert await call(client, me, "POST", "places/rpc/here", at(CENTRAL)) == []


async def test_nobody_reaches_another_persons_places(client: httpx.AsyncClient) -> None:
    owner = as_user("place-owner")
    theirs = await make(client, owner, "Their home", CENTRAL, 100)
    [secret] = await call(client, owner, "POST", "todos/todo", {"title": "Secret"})
    await call(
        client, owner, "POST", "core/rpc/link",
        {**place_end(theirs), "b_scope": "todos", "b_table": "todo", "b_key": {"id": secret["id"]}},
    )

    me = as_user("place-other")
    assert await call(client, me, "GET", "places/place") == []
    assert await call(client, me, "POST", "places/rpc/near", at(CENTRAL)) == []
    assert await call(client, me, "POST", "places/rpc/here", at(CENTRAL)) == []
    r = await client.patch(
        f"/api/data/places/place?id=eq.{theirs}",
        json={"name": "Mine"},
        headers={**me, "prefer": "return=representation"},
    )
    assert r.json() == [], r.text
    [mine] = await call(client, me, "POST", "todos/todo", {"title": "Mine"})
    r = await client.post(
        "/api/data/core/rpc/link",
        json={**place_end(theirs), "b_scope": "todos", "b_table": "todo", "b_key": {"id": mine["id"]}},
        headers=me,
    )
    assert r.status_code == 404, r.text
    [still] = await call(client, owner, "GET", "places/place")
    assert still["name"] == "Their home"
