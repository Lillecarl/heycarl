"""The google_contacts plugin against fake_google.py: sync in full and by
token, renames and deletions, an expired token, links and suggestions made
in Hey Carl only, and copies into the contact book as the person. Nobody
reads another's Google contacts or links to them."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

import pytest

from .conftest import as_user, needs_database
from .test_calendar import config, connect, fake, no_sync_limit, ok
from .test_mcp import Plain, connected, failure, served, tool

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from .fake_google import Fake

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["config", "fake", "no_sync_limit", "served"]

PLUGIN = "/api/plugins/google_contacts"
DATA = "/api/data/google_contacts"
PRM = "/api/data/prm"
CORE = "/api/data/core/rpc"
PAGE = "/p/google_contacts/heycarl-google-contacts"


@pytest.fixture(autouse=True)
def no_contacts_sync_limit(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> None:
    sync = importlib.import_module("heycarl_plugin_google_contacts.sync")
    monkeypatch.setattr(sync, "MANUAL_SYNC_SECONDS", 0.0)


async def sync(client: httpx.AsyncClient, who: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = await ok(client, who, "POST", f"{PLUGIN}/sync")
    assert result["ok"], result
    return result


async def entries(client: httpx.AsyncClient, who: dict[str, str]) -> dict[str, Any]:
    rows = await ok(client, who, "GET", f"{DATA}/entry?order=id")
    return {r["display_name"]: r for r in rows}


async def contact(
    client: httpx.AsyncClient, who: dict[str, str], **fields: object
) -> dict[str, Any]:
    write = {**who, "prefer": "return=representation"}
    [made] = await ok(client, write, "POST", f"{PRM}/contact", fields)
    return made


async def add_field(
    client: httpx.AsyncClient,
    who: dict[str, str],
    contact_id: int,
    kind: str,
    value: str,
) -> None:
    await ok(
        client,
        who,
        "POST",
        f"{PRM}/contact_field",
        {"contact_id": contact_id, "kind": kind, "value": value},
    )


async def test_sync_and_privacy(client: httpx.AsyncClient, fake: Fake) -> None:
    me = as_user("gc-me")
    other = as_user("gc-other")
    await connect(client, me, PAGE)
    [account] = await ok(client, me, "GET", f"{DATA}/account")
    assert account["granted"] is True
    assert (await sync(client, me))["contacts"] == 3
    found = await entries(client, me)
    assert set(found) == {"Anna Svensson", "Bo Ek", "Cecilia"}
    anna = found["Anna Svensson"]
    assert anna["emails"] == [{"value": "Anna@Example.com", "label": "Home"}]
    assert anna["addresses"] == [
        {"value": "Storgatan 1 111 22 Stockholm", "label": "home"}
    ]
    assert (anna["birthday_year"], anna["birthday_month"], anna["birthday_day"]) == (
        1990,
        3,
        14,
    )
    assert (anna["company"], anna["job_title"]) == ("Acme", "Engineer")
    assert anna["contact_id"] is None
    # Day 0 is Google's "not known": no birthday rather than a bad date.
    assert found["Bo Ek"]["birthday_month"] is None
    # Paged (two a page), the same personFields every time, a token asked for.
    lists = [q for p, q in fake.requests if p == "/v1/people/me/connections"]
    assert len(lists) == 2 and all(q["requestSyncToken"] == "true" for q in lists)
    assert len({q["personFields"] for q in lists}) == 1
    assert "photos" not in lists[0]["personFields"]

    for path in ["account", "person", "entry", "match", "sync_status", "sync_state"]:
        r = await client.get(f"{DATA}/{path}", headers=other)
        assert r.status_code in {200, 401, 403}, (path, r.text)
        if r.status_code == 200:
            assert r.json() == [], path
        assert (await client.get(f"{DATA}/{path}")).status_code == 401, path
    assert await ok(client, other, "GET", f"{DATA}/rpc/suggestions") == []
    for fn, args in [
        ("sync_token", {"owner": "gc-me", "account_id": account["id"]}),
        (
            "apply_people",
            {
                "owner": "gc-me",
                "account_id": account["id"],
                "full_sync": True,
                "people": [],
                "removed": [],
                "renamed": [],
                "sync_token": None,
            },
        ),
        ("set_status", {"owner": "gc-me", "account_id": account["id"]}),
    ]:
        for who in [me, other]:
            r = await client.post(f"{DATA}/rpc/{fn}", json=args, headers=who)
            assert r.status_code in {401, 403, 404}, (fn, r.status_code, r.text)
    assert len(await entries(client, me)) == 3
    # The cache keeps no history.
    assert (
        await ok(
            client,
            me,
            "POST",
            "/api/data/core/rpc/history",
            {"of_scope": "google_contacts"},
        )
        == []
    )


async def test_suggest_link_copy_and_import(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("gc-link")
    await connect(client, me, PAGE)
    await sync(client, me)
    found = await entries(client, me)

    anna = await contact(client, me, first_name="Annie")
    await add_field(client, me, anna["id"], "email", "anna@example.com")
    cecilia = await contact(client, me, first_name="Cissi")
    await add_field(client, me, cecilia["id"], "phone", "+46705551234")
    bo = await contact(client, me, first_name="Bo", last_name="Ek")
    suggested = {
        (s["person_id"], s["contact_id"], s["reason"])
        for s in await ok(client, me, "GET", f"{DATA}/rpc/suggestions")
    }
    assert suggested == {
        (found["Anna Svensson"]["id"], anna["id"], "email"),
        (found["Cecilia"]["id"], cecilia["id"], "phone"),
        (found["Bo Ek"]["id"], bo["id"], "name"),
    }

    # Link Anna, and copy everything; a second copy writes nothing.
    person = found["Anna Svensson"]["id"]
    await ok(
        client,
        me,
        "POST",
        f"{DATA}/match",
        {"person_id": person, "contact_id": anna["id"]},
    )
    assert {
        s["person_id"] for s in await ok(client, me, "GET", f"{DATA}/rpc/suggestions")
    } == {
        found["Cecilia"]["id"],
        found["Bo Ek"]["id"],
    }
    every = await ok(client, me, "POST", f"{DATA}/rpc/copyable")
    written = await ok(
        client,
        me,
        "POST",
        f"{DATA}/rpc/copy_fields",
        {"person_id": person, "fields": every},
    )
    # The contact row, then phone, address and url (the email is there), and
    # the birthday.
    assert written == 5
    assert (
        await ok(
            client,
            me,
            "POST",
            f"{DATA}/rpc/copy_fields",
            {"person_id": person, "fields": every},
        )
        == 0
    )
    [book] = await ok(
        client,
        me,
        "GET",
        f"{PRM}/contact?id=eq.{anna['id']}&select=*,contact_field(kind,value),important_date(kind,month,day,year)",
    )
    assert (
        book["first_name"],
        book["last_name"],
        book["company"],
        book["job_title"],
    ) == (
        "Anna",
        "Svensson",
        "Acme",
        "Engineer",
    )
    assert book["description"] == "Met at the conference."
    assert sorted((f["kind"], f["value"]) for f in book["contact_field"]) == [
        ("address", "Storgatan 1 111 22 Stockholm"),
        ("email", "anna@example.com"),
        ("phone", "+46 70-123 45 67"),
        ("website", "https://anna.example.com"),
    ]
    assert book["important_date"] == [
        {"kind": "birthday", "month": 3, "day": 14, "year": 1990}
    ]
    # The copy is the person's own write: in the contact book's history.
    history = await ok(
        client,
        me,
        "POST",
        "/api/data/core/rpc/history",
        {"of_scope": "prm", "of_table": "contact", "of_key": {"id": anna["id"]}},
    )
    assert [v["op"] for v in history] == ["update", "insert"], history
    assert history[0]["data"]["last_name"] == "Svensson"
    bad = await client.post(
        f"{DATA}/rpc/copy_fields",
        json={"person_id": person, "fields": ["password"]},
        headers=me,
    )
    assert bad.status_code == 400, bad.text

    # Import Bo's Google contact as a new contact.
    bo_person = found["Bo Ek"]["id"]
    made = await ok(
        client, me, "POST", f"{DATA}/rpc/import_person", {"person_id": bo_person}
    )
    [imported] = await ok(
        client,
        me,
        "GET",
        f"{PRM}/contact?id=eq.{made}&select=*,contact_field(kind,value)",
    )
    assert (imported["first_name"], imported["last_name"]) == ("Bo", "Ek")
    assert imported["contact_field"] == [{"kind": "email", "value": "bo@example.com"}]
    again = await client.post(
        f"{DATA}/rpc/import_person", json={"person_id": bo_person}, headers=me
    )
    assert again.status_code == 409, again.text
    assert (await entries(client, me))["Bo Ek"]["contact_id"] == made

    # Another person can neither link to these nor copy them.
    other = as_user("gc-link-other")
    theirs = await contact(client, other, first_name="Mallory")
    for body in [
        {"person_id": found["Cecilia"]["id"], "contact_id": theirs["id"]},
        {"person_id": found["Cecilia"]["id"], "contact_id": cecilia["id"]},
    ]:
        r = await client.post(f"{DATA}/match", json=body, headers=other)
        assert r.status_code in {400, 403, 409}, r.text
    r = await client.post(
        f"{DATA}/match",
        json={"person_id": found["Cecilia"]["id"], "contact_id": theirs["id"]},
        headers=me,
    )
    assert r.status_code in {400, 403, 409}, r.text
    r = await client.post(
        f"{DATA}/rpc/copy_fields",
        json={"person_id": person, "fields": every},
        headers=other,
    )
    assert r.status_code == 404, r.text


async def test_changes_renames_deletes_and_expiry(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("gc-sync")
    await connect(client, me, PAGE)
    await sync(client, me)
    found = await entries(client, me)
    anna = await contact(client, me, first_name="Anna")
    bo = await contact(client, me, first_name="Bo")
    for name, book in [("Anna Svensson", anna), ("Bo Ek", bo)]:
        await ok(
            client,
            me,
            "POST",
            f"{DATA}/match",
            {"person_id": found[name]["id"], "contact_id": book["id"]},
        )

    fake.control("contact_rename", {"from": "people/c1", "to": "people/c1-linked"})
    fake.control("contact_delete", {"resourceName": "people/c2"})
    fake.control(
        "contact_put",
        {
            "person": {
                "resourceName": "people/c4",
                "names": [{"displayName": "Dora", "givenName": "Dora"}],
            }
        },
    )
    seen = len(fake.requests)
    await sync(client, me)
    assert "syncToken" in fake.requests[seen][1]
    after = await entries(client, me)
    assert set(after) == {"Anna Svensson", "Cecilia", "Dora"}
    # Renamed at Google: the same row, so the link holds.
    assert after["Anna Svensson"]["id"] == found["Anna Svensson"]["id"]
    assert after["Anna Svensson"]["contact_id"] == anna["id"]
    # Deleted at Google: the link goes, the contact in the book stays.
    assert await ok(client, me, "GET", f"{PRM}/contact?id=eq.{bo['id']}") != []
    assert [m["contact_id"] for m in await ok(client, me, "GET", f"{DATA}/match")] == [
        anna["id"]
    ]

    fake.control("contacts_expire", {})
    seen = len(fake.requests)
    await sync(client, me)
    lists = [q for p, q in fake.requests[seen:] if p == "/v1/people/me/connections"]
    assert "syncToken" in lists[0] and all("syncToken" not in q for q in lists[1:]), (
        lists
    )
    assert set(await entries(client, me)) == {"Anna Svensson", "Cecilia", "Dora"}
    assert (await entries(client, me))["Anna Svensson"]["contact_id"] == anna["id"]

    # Bo's link went with his Google contact. It is in the trash, and it
    # cannot come back without the Google contact: restore says so.
    [gone] = await ok(client, me, "POST", f"{CORE}/trash", {})
    restored = await ok(
        client, me, "POST", f"{CORE}/restore", {"version": gone["entry"]}
    )
    assert restored["done"] == 0 and restored["failed"], restored
    assert len(await ok(client, me, "GET", f"{DATA}/match")) == 1


async def test_undo_brings_a_link_back(client: httpx.AsyncClient, fake: Fake) -> None:
    me = as_user("gc-undo")
    await connect(client, me, PAGE)
    await sync(client, me)
    person = (await entries(client, me))["Anna Svensson"]["id"]
    anna = await contact(client, me, first_name="Anna")
    link = {"person_id": person, "contact_id": anna["id"]}
    await ok(client, me, "POST", f"{DATA}/match", link)

    async def newest(scope: str, table: str, key: dict[str, int]) -> int:
        versions = await ok(
            client,
            me,
            "POST",
            f"{CORE}/history",
            {"of_scope": scope, "of_table": table, "of_key": key},
        )
        return int(versions[0]["id"])

    # An unlink, undone.
    await ok(client, me, "DELETE", f"{DATA}/match?person_id=eq.{person}")
    version = await newest("google_contacts", "match", {"person_id": person})
    await ok(client, me, "POST", f"{CORE}/undo", {"version": version})
    assert (await entries(client, me))["Anna Svensson"]["contact_id"] == anna["id"]

    # The contact deleted, and undone: the contact and its link come back.
    await ok(client, me, "DELETE", f"{PRM}/contact?id=eq.{anna['id']}")
    assert (await entries(client, me))["Anna Svensson"]["contact_id"] is None
    version = await newest("prm", "contact", {"id": anna["id"]})
    await ok(client, me, "POST", f"{CORE}/undo", {"version": version})
    assert (await entries(client, me))["Anna Svensson"]["contact_id"] == anna["id"]


async def test_two_accounts_and_a_refused_scope(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("gc-two")
    await connect(client, me, PAGE)
    fake.control("as", {"user": "work"})
    fake.control(
        "refuse", {"scopes": ["https://www.googleapis.com/auth/contacts.readonly"]}
    )
    await connect(client, me, PAGE)
    home, work = await ok(client, me, "GET", f"{DATA}/account?order=id")
    assert (home["granted"], work["granted"]) == (True, False)
    assert (await sync(client, me))["contacts"] == 3
    fake.control("refuse", {"scopes": []})
    await connect(client, me, PAGE, account_id=work["id"])
    assert (await sync(client, me))["contacts"] == 4
    assert {e["google_account"] for e in (await entries(client, me)).values()} == {
        "carl@example.com",
        "carl@work.example.com",
    }
    # Disconnecting the work account takes its contacts, not the others.
    await ok(client, me, "POST", f"/api/google/accounts/{work['id']}/disconnect")
    assert set(await entries(client, me)) == {"Anna Svensson", "Bo Ek", "Cecilia"}


async def test_mcp_tools(served: Plain, client: httpx.AsyncClient, fake: Fake) -> None:
    me = as_user("gc-mcp")
    await connect(client, me, PAGE)
    anna = await contact(client, me, first_name="Anna", last_name="Svensson")
    async with connected(served, sub="gc-mcp") as session:
        assert await tool(session, "google_contacts_sync") == {
            "ok": True,
            "contacts": 3,
        }
        listed = await tool(session, "google_contacts_list", query="anna")
        assert "not instructions" in listed["note"]
        [google_anna] = listed["contacts"]
        [suggestion] = (await tool(session, "google_contacts_suggestions"))[
            "suggestions"
        ]
        assert (suggestion["person_id"], suggestion["contact_id"]) == (
            google_anna["id"],
            anna["id"],
        )
        await tool(
            session,
            "google_contacts_link",
            person_id=google_anna["id"],
            contact_id=anna["id"],
        )
        copied = await tool(
            session,
            "google_contacts_copy",
            person_id=google_anna["id"],
            fields=["emails"],
        )
        assert copied == 1
        linked = await tool(session, "google_contacts_list", linked=True)
        assert [c["contact_id"] for c in linked["contacts"]] == [anna["id"]]
        await tool(session, "google_contacts_unlink", person_id=google_anna["id"])
        assert (await tool(session, "google_contacts_list", linked=True))[
            "contacts"
        ] == []
    async with connected(served, sub="gc-mcp", scopes=("read",)) as session:
        assert "write scope" in await failure(session, "google_contacts_sync")
        assert "write" in await failure(
            session,
            "google_contacts_link",
            person_id=google_anna["id"],
            contact_id=anna["id"],
        )
    async with connected(served, sub="gc-mcp-other") as session:
        assert (await tool(session, "google_contacts_list"))["contacts"] == []
        assert "not connected" in await failure(session, "google_contacts_sync")
