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
import socket
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
async def app(config: Config) -> AsyncIterator[FastAPI]:
    application = create_app(config, plugins.load_plugins(config.plugins_file))
    async with application.router.lifespan_context(application):
        yield application


# After `app`: the kernel migrates at startup and ends with a schema reload,
# and PostgREST answers 503 while it reloads. Started after the migration,
# it loads the schema once, before the first request.
@pytest.fixture
async def postgrest(config: Config, app: FastAPI) -> AsyncIterator[None]:
    loaded = plugins.load_plugins(config.plugins_file)
    schemas = ",".join([migrations.API_SCHEMA, *(p.schema for p in loaded)])
    admin_port = _free_port()
    env = {
        **os.environ,
        "PGRST_DB_URI": f"postgresql:///{DATABASE}?host={os.environ['PGHOST']}&user=heycarl_authenticator",
        "PGRST_DB_SCHEMAS": schemas,
        "PGRST_JWT_SECRET": config.jwt_secret_file.read_text(),
        "PGRST_SERVER_UNIX_SOCKET": str(config.postgrest_socket),
        "PGRST_DB_CHANNEL_ENABLED": "true",
        "PGRST_ADMIN_SERVER_HOST": "127.0.0.1",
        "PGRST_ADMIN_SERVER_PORT": str(admin_port),
    }
    async with await anyio.open_process(
        ["postgrest"], env=env, stdout=None, stderr=None
    ) as proc:
        try:
            await _wait_until_ready(admin_port)
            yield
        finally:
            proc.terminate()
            await proc.wait()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


async def _wait_until_ready(admin_port: int) -> None:
    # The main socket answers before the first schema load is done, and
    # PostgREST reloads once more when its listener connects. /ready is 200
    # only with a connection and a loaded schema cache. It has no event to
    # wait on, so this polls.
    with anyio.fail_after(20):
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{admin_port}") as c:
            while True:
                try:
                    if (await c.get("/ready")).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await anyio.sleep(0.1)


@pytest.fixture
def kernel(app: FastAPI, postgrest: None) -> Kernel:
    k: Kernel = app.state.kernel
    return k


@pytest.fixture
async def client(app: FastAPI, postgrest: None) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://kernel"
    ) as c:
        yield c


def as_user(sub: str) -> dict[str, str]:
    return {USER_HEADER: sub}
