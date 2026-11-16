"""Every /api error answers in PostgREST's shape: `{code, message, details, hint}`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from heycarl import errors
from pydantic import BaseModel

from .conftest import as_user, needs_database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.anyio

SHAPE = {"code", "message", "details", "hint"}


class Body(BaseModel):
    n: int


@pytest.fixture
async def small() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    errors.install(app)

    @app.get("/api/gone")
    async def api_gone() -> None:
        raise HTTPException(404, "no such thing")

    @app.post("/api/take")
    async def api_take(body: Body) -> int:
        return body.n

    @app.get("/page/gone")
    async def page_gone() -> None:
        raise HTTPException(404, "no such page")

    @app.get("/api/crash")
    async def api_crash() -> None:
        raise RuntimeError("secret internals")

    @app.get("/page/crash")
    async def page_crash() -> None:
        raise RuntimeError("secret internals")

    # The server re-raises after answering; the client still gets the answer.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://kernel") as c:
        yield c


async def test_a_crash_says_nothing_of_its_cause(small: httpx.AsyncClient) -> None:
    r = await small.get("/api/crash")
    assert r.status_code == 500
    found = r.json()
    assert found["code"] == "HC500"
    assert "secret" not in r.text

    page = await small.get("/page/crash")
    assert page.status_code == 500
    assert page.text == "Internal Server Error"


async def test_an_api_error_has_the_postgrest_shape(small: httpx.AsyncClient) -> None:
    r = await small.get("/api/gone")
    assert r.status_code == 404
    assert r.json() == {
        "code": "HC404",
        "message": "no such thing",
        "details": None,
        "hint": None,
    }


async def test_an_unknown_api_route_has_the_shape(small: httpx.AsyncClient) -> None:
    r = await small.get("/api/nothing-here")
    assert r.status_code == 404
    assert r.json()["code"] == "HC404"


async def test_an_invalid_body_lists_its_faults(small: httpx.AsyncClient) -> None:
    r = await small.post("/api/take", json={"n": "many"})
    assert r.status_code == 422
    found = r.json()
    assert set(found) == SHAPE
    assert found["code"] == "HC422"
    assert [d["loc"] for d in found["details"]] == [["body", "n"]]


async def test_other_paths_keep_fastapi_errors(small: httpx.AsyncClient) -> None:
    r = await small.get("/page/gone")
    assert r.status_code == 404
    assert r.json() == {"detail": "no such page"}


@needs_database
async def test_the_gate_answers_in_the_shape(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {
        "code": "HC401",
        "message": "sign in",
        "details": None,
        "hint": None,
    }


@needs_database
async def test_a_kernel_route_answers_in_the_shape(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/data/nope/todo", headers=as_user("alice"))
    assert r.status_code == 404
    assert r.json()["message"] == "no data scope 'nope'"


@needs_database
async def test_postgrest_errors_pass_through(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/data/todos/no_such_table", headers=as_user("alice"))
    assert r.status_code == 404
    found = r.json()
    assert set(found) == SHAPE
    assert found["code"].startswith("PGRST")
