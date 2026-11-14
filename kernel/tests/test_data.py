"""The data path end to end: kernel, real PostgREST, real PostgreSQL."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from heycarl import migrations, plugins

from .conftest import as_user, needs_database

if TYPE_CHECKING:
    import httpx
    from heycarl.config import Config

pytestmark = [pytest.mark.anyio, needs_database]


async def test_no_identity_is_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/data/todos/todo")).status_code == 401


async def test_unknown_scope_is_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/data/nope/todo", headers=as_user("alice"))
    assert r.status_code == 404


async def test_me_through_postgrest(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/data/core/me", headers=as_user("alice"))
    assert r.status_code == 200, r.text
    assert r.json() == [{"sub": "alice"}]


async def test_todos_are_private(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/data/todos/todo",
        json={"title": "buy milk"},
        headers={**as_user("alice"), "prefer": "return=representation"},
    )
    assert created.status_code == 201, created.text
    row = created.json()[0]
    assert row["owner_sub"] == "alice"

    mine = await client.get("/api/data/todos/todo", headers=as_user("alice"))
    assert [t["title"] for t in mine.json()] == ["buy milk"]
    theirs = await client.get("/api/data/todos/todo", headers=as_user("bob"))
    assert theirs.json() == []


async def test_owner_cannot_be_forged(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/data/todos/todo",
        json={"title": "x", "owner_sub": "bob"},
        headers=as_user("alice"),
    )
    # No column grant on owner_sub.
    assert r.status_code in {401, 403}, r.text


async def test_migrate_twice_is_a_no_op(config: Config) -> None:
    loaded = plugins.load_plugins(config.plugins_file)
    await migrations.migrate(config.database, loaded)
    await migrations.migrate(config.database, loaded)
