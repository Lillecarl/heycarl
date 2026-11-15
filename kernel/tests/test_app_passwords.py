"""App passwords and the /dav/ gate, attacked (DESIGN.md "App passwords")."""

from __future__ import annotations

import base64
import json
import secrets
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from heycarl import migrations, postgrest
from heycarl.tokens import Via, via_mcp

from .conftest import PUBLIC_URL, _conninfo, as_user, needs_database

if TYPE_CHECKING:
    import httpx
    from heycarl.app import Kernel

pytestmark = [pytest.mark.anyio, needs_database]
API = "/api/data/core/app_password"
CREATE = "/api/data/core/rpc/app_password_create"
PROPFIND = (
    b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:current-user-principal/><d:getetag/></d:prop></d:propfind>"
)


def basic(username: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{secret}".encode()).decode()


def dav(
    username: str, secret: str, address: str | None = None, **extra: str
) -> dict[str, str]:
    return {
        "authorization": basic(username, secret),
        "x-forwarded-for": address or new_address(),
        "depth": "0",
        **extra,
    }


def new_address() -> str:
    # The database outlives a test, and failures count per address.
    return ".".join(["10", *(str(secrets.randbelow(256)) for _ in range(3))])


def new_username() -> str:
    return "".join(
        secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(16)
    )


async def make(
    client: httpx.AsyncClient,
    session: dict[str, str],
    *,
    write: bool = False,
    days: int = 365,
) -> dict[str, Any]:
    r = await client.post(
        CREATE,
        headers=session,
        json={"name": "phone", "allow_write": write, "days": days},
    )
    assert r.status_code == 200, r.text
    made: dict[str, Any] = r.json()
    return made


async def as_role(
    kernel: Kernel, token: str, method: str, path: str, body: object = None
) -> httpx.Response:
    """Straight to PostgREST with a token the kernel could mint."""
    return await kernel.data.request(
        method,
        path,
        headers=postgrest.request_headers(
            {"content-type": "application/json"}, token, migrations.API_SCHEMA
        ),
        content=None if body is None else json.dumps(body),
    )


async def test_a_password_opens_dav_and_nothing_else(
    client: httpx.AsyncClient,
) -> None:
    session = as_user("dav-alice", "alice@example.com")
    made = await make(client, session)
    creds = dav(made["username"], made["secret"])
    r = await client.request("PROPFIND", "/dav/", headers=creds, content=PROPFIND)
    assert r.status_code == 207, r.text
    assert b"<d:href>/dav/</d:href>" in r.content
    assert b"404 Not Found" in r.content
    # Basic credentials count nowhere else.
    for path in ("/api/me", API, "/api/data/todos/todo"):
        assert (await client.get(path, headers=creds)).status_code == 401, path
    listed = (await client.get(API, headers=session)).json()
    assert [p["id"] for p in listed] == [made["username"]]
    assert "secret" not in listed[0]
    with psycopg.connect(_conninfo("heycarl_owner")) as conn:
        row = conn.execute(
            "select email, owner_sub from heycarl_private.app_password where id = %s",
            (made["username"],),
        ).fetchone()
    assert row == ("alice@example.com", "dav-alice")


async def test_the_hash_never_leaves_the_database(client: httpx.AsyncClient) -> None:
    session = as_user("dav-hash", "hash@example.com")
    await make(client, session)
    r = await client.get(API + "?select=secret_hash", headers=session)
    assert r.status_code == 400, r.text
    r = await client.get("/api/data/core/rpc/dav_authenticate", headers=session)
    assert r.status_code in {403, 404, 405}, r.text
    r = await client.post(
        "/api/data/core/rpc/dav_authenticate",
        headers=session,
        json={"username": "x", "secret": "y", "address": "z"},
    )
    assert r.status_code in {403, 404}, r.text


async def test_the_gate_role_can_do_nothing_else(kernel: Kernel) -> None:
    gate = kernel.minter.gate()
    for path in ("/app_password", "/push_subscription", "/me"):
        r = await as_role(kernel, gate, "GET", path)
        assert r.status_code in {401, 403}, (path, r.text)
    r = await as_role(kernel, gate, "POST", "/rpc/app_password_create", {"name": "x"})
    assert r.status_code in {401, 403}, r.text
    r = await as_role(kernel, gate, "POST", "/rpc/history", {})
    assert r.status_code in {401, 403, 404}, r.text


async def test_only_the_app_makes_one(kernel: Kernel) -> None:
    for via in (via_mcp("some-client"), "dav:x"):
        token = kernel.minter.user("dav-mcp", via, "mcp@example.com")
        r = await as_role(
            kernel, token, "POST", "/rpc/app_password_create", {"name": "x"}
        )
        assert r.status_code == 403, (via, r.text)
    worker = kernel.minter.worker()
    r = await as_role(kernel, worker, "POST", "/rpc/app_password_create", {"name": "x"})
    assert r.status_code in {401, 403}, r.text
    # The app may, and the owner comes from the token, not an argument.
    token = kernel.minter.user("dav-app", Via.APP, "app@example.com")
    r = await as_role(
        kernel,
        token,
        "POST",
        "/rpc/app_password_create",
        {"name": "x", "owner_sub": "someone-else"},
    )
    assert r.status_code in {300, 400, 404}, r.text


async def test_a_cookie_never_counts_on_dav(client: httpx.AsyncClient) -> None:
    session = as_user("dav-bob", "bob@example.com")
    for path in ("/dav/", "/dav", "/.well-known/carddav"):
        r = await client.request("PROPFIND", path, headers=session)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"].startswith("Basic ")
    # No route under /dav/ answers without a password, OPTIONS included.
    assert (await client.options("/dav/")).status_code == 401
    assert (await client.get("/dav/anything")).status_code == 401


async def test_wrong_credentials_look_alike(client: httpx.AsyncClient) -> None:
    made = await make(client, as_user("dav-carol", "carol@example.com"))
    answers = [
        await client.request("PROPFIND", "/dav/", headers=dav(u, s))
        for u, s in (
            (made["username"], "nope"),
            (new_username(), made["secret"]),
            ("alice@example.com", made["secret"]),
            ("x" * 5000, "y" * 5000),
        )
    ]
    assert {a.status_code for a in answers} == {401}
    assert len({a.content for a in answers}) == 1
    garbled = await client.request(
        "PROPFIND", "/dav/", headers={"authorization": "Basic !!!"}
    )
    assert garbled.status_code == 401


async def test_failures_are_throttled(client: httpx.AsyncClient) -> None:
    made = await make(client, as_user("dav-dave", "dave@example.com"))
    for _ in range(10):
        r = await client.request(
            "PROPFIND", "/dav/", headers=dav(made["username"], "x")
        )
        assert r.status_code == 401
    # Even the right secret waits now: nothing is checked.
    r = await client.request(
        "PROPFIND", "/dav/", headers=dav(made["username"], made["secret"])
    )
    assert r.status_code == 429
    assert r.headers["retry-after"] == "900"
    # Per address, whatever username it tries.
    address = new_address()
    for _ in range(20):
        r = await client.request(
            "PROPFIND", "/dav/", headers=dav(new_username(), "x", address)
        )
        assert r.status_code == 401
    other = await make(client, as_user("dav-dave2", "dave@example.com"))
    r = await client.request(
        "PROPFIND", "/dav/", headers=dav(other["username"], other["secret"], address)
    )
    assert r.status_code == 429


async def test_read_only_refuses_writes(client: httpx.AsyncClient) -> None:
    session = as_user("dav-erin", "erin@example.com")
    reader = await make(client, session)
    writer = await make(client, session, write=True)
    for method in ("PUT", "DELETE", "PROPPATCH", "MKCOL", "MOVE", "POST"):
        r = await client.request(
            method, "/dav/x", headers=dav(reader["username"], reader["secret"])
        )
        assert r.status_code == 403, method
        r = await client.request(
            method, "/dav/x", headers=dav(writer["username"], writer["secret"])
        )
        assert r.status_code in {404, 405}, method


async def test_revoked_and_expired_passwords_stop(client: httpx.AsyncClient) -> None:
    session = as_user("dav-frank", "frank@example.com")
    made = await make(client, session)
    creds = dav(made["username"], made["secret"])
    assert (await client.request("PROPFIND", "/dav/", headers=creds)).status_code == 207
    r = await client.delete(f"{API}?id=eq.{made['username']}", headers=session)
    assert r.status_code == 204
    assert (await client.request("PROPFIND", "/dav/", headers=creds)).status_code == 401

    made = await make(client, session)
    with psycopg.connect(_conninfo("heycarl_owner")) as conn:
        conn.execute(
            "update heycarl_private.app_password set expires_at = now() where id = %s",
            (made["username"],),
        )
    creds = dav(made["username"], made["secret"])
    assert (await client.request("PROPFIND", "/dav/", headers=creds)).status_code == 401


async def test_one_person_cannot_touch_anothers(client: httpx.AsyncClient) -> None:
    gina = as_user("dav-gina", "gina@example.com")
    mallory = as_user("dav-mallory", "mallory@example.com")
    made = await make(client, gina)
    r = await client.delete(
        f"{API}?id=eq.{made['username']}",
        headers={**mallory, "prefer": "return=representation"},
    )
    assert r.json() == []
    assert made["username"] not in [
        p["id"] for p in (await client.get(API, headers=mallory)).json()
    ]
    assert made["username"] in [
        p["id"] for p in (await client.get(API, headers=gina)).json()
    ]


async def test_making_one_needs_a_session_from_this_site(
    client: httpx.AsyncClient,
) -> None:
    body = {"name": "phone"}
    assert (await client.post(CREATE, json=body)).status_code == 401
    session = as_user("dav-hank", "hank@example.com")
    foreign = {**session, "origin": "https://evil.example"}
    assert (await client.post(CREATE, headers=foreign, json=body)).status_code == 403
    for bad in ({"name": "x" * 101}, {"name": "  "}, {"name": "a", "days": 400}):
        r = await client.post(CREATE, headers=session, json=bad)
        assert r.status_code == 400, (bad, r.text)


async def test_at_most_fifty_each(client: httpx.AsyncClient) -> None:
    session = as_user("dav-many", "many@example.com")
    for _ in range(50):
        await make(client, session)
    r = await client.post(CREATE, headers=session, json={"name": "one more"})
    assert r.status_code == 409, r.text


async def test_a_browser_cannot_carry_it_for_another_site(
    client: httpx.AsyncClient,
) -> None:
    made = await make(client, as_user("dav-ivy", "ivy@example.com"))
    for extra in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
        creds = dav(made["username"], made["secret"], **extra)
        r = await client.request("PROPFIND", "/dav/", headers=creds)
        assert r.status_code == 403, extra
    same = dav(made["username"], made["secret"], origin=PUBLIC_URL)
    assert (await client.request("PROPFIND", "/dav/", headers=same)).status_code == 207


@pytest.mark.parametrize("allowed_emails", [frozenset({"kept@example.com"})])
async def test_allowed_emails_holds_per_request(client: httpx.AsyncClient) -> None:
    made = await make(client, as_user("dav-jo", "kept@example.com"))
    creds = dav(made["username"], made["secret"])
    assert (await client.request("PROPFIND", "/dav/", headers=creds)).status_code == 207
    with psycopg.connect(_conninfo("heycarl_owner")) as conn:
        conn.execute(
            "update heycarl_private.app_password set email = 'gone@example.com'"
            " where id = %s",
            (made["username"],),
        )
    assert (await client.request("PROPFIND", "/dav/", headers=creds)).status_code == 401


async def test_hostile_xml_is_refused(client: httpx.AsyncClient) -> None:
    made = await make(client, as_user("dav-kim", "kim@example.com"))
    creds = dav(made["username"], made["secret"])
    laughs = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        b'<d:propfind xmlns:d="DAV:"><d:prop><d:displayname>&b;</d:displayname>'
        b"</d:prop></d:propfind>"
    )
    external = (
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<d:propfind xmlns:d="DAV:">&x;</d:propfind>'
    )
    for body in (laughs, external, b"<not xml", b"<d:x xmlns:d='DAV:'/>"):
        r = await client.request("PROPFIND", "/dav/", headers=creds, content=body)
        assert r.status_code == 400, body
    big = b"<a>" + b"x" * (65 * 1024) + b"</a>"
    r = await client.request("PROPFIND", "/dav/", headers=creds, content=big)
    assert r.status_code == 413
    deep = {**creds, "depth": "infinity"}
    r = await client.request("PROPFIND", "/dav/", headers=deep, content=PROPFIND)
    assert r.status_code == 403
    assert b"propfind-finite-depth" in r.content


async def test_discovery_follows_the_well_known_url(
    client: httpx.AsyncClient,
) -> None:
    made = await make(client, as_user("dav-lee", "lee@example.com"))
    creds = dav(made["username"], made["secret"])
    for path in ("/.well-known/caldav", "/.well-known/carddav"):
        r = await client.request("PROPFIND", path, headers=creds)
        assert r.status_code == 301, path
        assert r.headers["location"] == "/dav/"
    r = await client.options("/dav/", headers=creds)
    assert r.status_code == 200
    assert r.headers["dav"] == "1, 3"
