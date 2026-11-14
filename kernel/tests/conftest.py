"""Fixtures for the integration tests: kernel, real PostgREST, real PostgreSQL.

They need PGHOST (postgresqlTestHook in the Nix check), the roles from
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
from heycarl.app import Kernel, create_app
from heycarl.config import Config
from heycarl.identity import USER_HEADER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

DATABASE = "heycarl"

needs_database = pytest.mark.skipif(
    "PGHOST" not in os.environ or shutil.which("postgrest") is None,
    reason="needs PostgreSQL (PGHOST) and postgrest",
)


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
        vapid_key_file=tmp_path / "state" / "vapid.pem",
        public_url="https://hey.example.com",
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
async def app(config: Config, postgrest: None) -> AsyncIterator[FastAPI]:
    application = create_app(config, plugins.load_plugins(config.plugins_file))
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def kernel(app: FastAPI) -> Kernel:
    k: Kernel = app.state.kernel
    return k


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://kernel"
    ) as c:
        yield c


def as_user(sub: str) -> dict[str, str]:
    return {USER_HEADER: sub}
