"""The data path end to end: kernel, real PostgREST, real PostgreSQL.

Needs PGHOST (postgresqlTestHook in the Nix check), the roles from
roles.sql in database `heycarl`, `postgrest` on PATH, and HEYCARL_TEST_PLUGINS
naming the plugin directories.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from typing import TYPE_CHECKING

import anyio
import httpx
import pytest

from heycarl import migrations, plugins
from heycarl.app import create_app
from heycarl.config import Config
from heycarl.identity import USER_HEADER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        "PGHOST" not in os.environ or shutil.which("postgrest") is None,
        reason="needs PostgreSQL (PGHOST) and postgrest",
    ),
]

DATABASE = "heycarl"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _conninfo(user: str) -> str:
    return f"host={os.environ['PGHOST']} dbname={DATABASE} user={user}"


@pytest.fixture(scope="module")
def plugin_dirs() -> list[str]:
    return json.loads(os.environ.get("HEYCARL_TEST_PLUGINS", "[]"))


@pytest.fixture
def config(tmp_path: Path, plugin_dirs: list[str]) -> Config:
    secret = tmp_path / "jwt-secret"
    secret.write_text(secrets.token_urlsafe(48))
    plugins_file = tmp_path / "plugins.json"
    plugins_file.write_text(json.dumps(plugin_dirs))
    return Config(
        database=_conninfo("heycarl_owner"),
        postgrest_socket=tmp_path / "postgrest.sock",
        jwt_secret_file=secret,
        plugins_file=plugins_file,
        static_dir=None,
    )


@pytest.fixture
async def postgrest(config: Config) -> AsyncIterator[None]:
    loaded = plugins.load_plugins(config.plugins_file)
    await migrations.migrate(config.database, loaded)
    schemas = ",".join([migrations.API_SCHEMA, *(p.schema for p in loaded)])
    env = {
        **os.environ,
        "PGRST_DB_URI": f"postgresql:///{DATABASE}?host={os.environ['PGHOST']}&user=heycarl_authenticator",
        "PGRST_DB_SCHEMAS": schemas,
        "PGRST_JWT_SECRET": config.jwt_secret_file.read_text(),
        "PGRST_SERVER_UNIX_SOCKET": str(config.postgrest_socket),
        "PGRST_DB_CHANNEL_ENABLED": "true",
    }
    async with await anyio.open_process(
        ["postgrest"], env=env, stdout=None, stderr=None
    ) as proc:
        try:
            await _wait_for_socket(config.postgrest_socket)
            yield
        finally:
            proc.terminate()
            await proc.wait()


async def _wait_for_socket(path: Path) -> None:
    # PostgREST announces readiness only by answering on its socket.
    with anyio.fail_after(20):
        while True:
            try:
                async with httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(uds=str(path)),
                    base_url="http://p",
                ) as c:
                    if (await c.get("/")).status_code < 500:
                        return
            except httpx.TransportError:
                pass
            await anyio.sleep(0.1)


@pytest.fixture
async def client(config: Config, postgrest: None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(config, plugins.load_plugins(config.plugins_file))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://kernel"
        ) as c,
    ):
        yield c


def as_user(sub: str) -> dict[str, str]:
    return {USER_HEADER: sub}


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
