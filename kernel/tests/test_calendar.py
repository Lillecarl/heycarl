"""The calendar plugin against a fake Google (fake_google.py), through the
kernel's Google accounts: sync in full and by token, a 410, a cancelled
event, a refresh, two accounts, revoke on disconnect. Nobody reads
another's events or token. test_google.py tests the connect flow itself."""

from __future__ import annotations

import importlib
import json
import secrets
import threading
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from .conftest import PUBLIC_URL, as_user, needs_database
from .fake_google import PRIMARY, TEAM, Fake, endpoints, serve
from .test_history import owner_rows
from .test_mcp import Plain, connected, failure, served, tool

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI

    from heycarl.config import Config

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]

PLUGIN = "/api/plugins/calendar"
DATA = "/api/data/calendar"
GOOGLE = "/api/google"
PAGE = "/p/calendar/heycarl-calendar"
CLIENT_ID = "hey.apps.example.com"
CLIENT_SECRET = "fake-client-secret"


@pytest.fixture
def fake(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Fake | None]:
    if request.node.get_closest_marker("unconfigured"):
        yield None
        return
    google = Fake(CLIENT_ID, CLIENT_SECRET)
    server = serve(0, google)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    secret = tmp_path / "google-secret"
    secret.write_text(CLIENT_SECRET + "\n")
    key = tmp_path / "google-key"
    key.write_text(secrets.token_urlsafe(48))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("HEYCARL_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("HEYCARL_GOOGLE_CLIENT_SECRET_FILE", str(secret))
    monkeypatch.setenv("HEYCARL_GOOGLE_TOKEN_KEY_FILE", str(key))
    monkeypatch.setenv("HEYCARL_GOOGLE_ENDPOINTS", json.dumps(endpoints(base)))
    yield google
    server.shutdown()
    server.server_close()


# The app reads the Google settings when it registers the plugin.
@pytest.fixture
def config(config: Config, fake: Fake | None) -> Config:
    return config


@pytest.fixture(autouse=True)
def no_sync_limit(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> None:
    sync = importlib.import_module("heycarl_plugin_calendar.sync")
    monkeypatch.setattr(sync, "MANUAL_SYNC_SECONDS", 0.0)
    # The job a connect wakes would race each test's own syncs for the
    # fake's 410s and counters; the VM test keeps it.
    monkeypatch.setattr(sync.Syncer, "wake", lambda _self: None)
    if app.state.google is not None:
        monkeypatch.setattr(app.state.google, "_listeners", [])


# Any: a JSON response body.
async def ok(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    method: str,
    path: str,
    body: object = None,
) -> Any:
    r = await client.request(method, path, json=body, headers=headers)
    assert r.is_success, f"{method} {path}: {r.status_code} {r.text}"
    return r.json() if r.content else None


async def start(
    client: httpx.AsyncClient,
    browser: dict[str, str],
    next_path: str = PAGE,
    account_id: int | None = None,
) -> str:
    """The callback path Google sends the browser to, after consent."""
    url = (
        await ok(
            client,
            browser,
            "POST",
            f"{GOOGLE}/connect",
            {"next": next_path, "account_id": account_id},
        )
    )["url"]
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert set(query["scope"].split()) >= {
        "openid",
        "email",
        "https://www.googleapis.com/auth/calendar.readonly",
    }
    assert (
        query["code_challenge_method"],
        query["access_type"],
        query["prompt"],
        query["include_granted_scopes"],
    ) == ("S256", "offline", "consent select_account", "true")
    assert query["redirect_uri"] == PUBLIC_URL + GOOGLE + "/callback"
    async with httpx.AsyncClient() as browser_at_google:
        consent = await browser_at_google.get(url)
    assert consent.status_code == 302, consent.text
    return consent.headers["location"].removeprefix(PUBLIC_URL)


async def connect(
    client: httpx.AsyncClient,
    browser: dict[str, str],
    next_path: str = PAGE,
    account_id: int | None = None,
) -> None:
    r = await client.get(
        await start(client, browser, next_path, account_id), headers=browser
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == next_path


async def sync(client: httpx.AsyncClient, browser: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = await ok(client, browser, "POST", f"{PLUGIN}/sync")
    assert result["ok"], result
    return result


async def events(
    client: httpx.AsyncClient, browser: dict[str, str]
) -> dict[str, dict[str, Any]]:
    rows = await ok(client, browser, "GET", f"{DATA}/agenda?order=starts_at")
    return {r["google_id"]: r for r in rows}


@pytest.mark.unconfigured
async def test_without_a_client_the_feature_is_off(client: httpx.AsyncClient) -> None:
    me = as_user("cal-off")
    assert (await ok(client, me, "GET", f"{PLUGIN}/status"))["configured"] is False
    assert (await ok(client, me, "GET", GOOGLE))["configured"] is False
    for path in [
        f"{PLUGIN}/sync",
        f"{GOOGLE}/connect",
        f"{GOOGLE}/accounts/1/disconnect",
    ]:
        r = await client.post(path, json={}, headers=me)
        assert r.status_code == 409, (path, r.text)
    assert await ok(client, me, "GET", f"{DATA}/event") == []


async def test_connect_sync_and_privacy(client: httpx.AsyncClient, fake: Fake) -> None:
    alice = as_user("cal-alice")
    bob = as_user("cal-bob")
    assert (await ok(client, alice, "GET", f"{PLUGIN}/status"))["configured"] is True
    await connect(client, alice)

    [account] = await ok(client, alice, "GET", f"{DATA}/account")
    assert (
        account["google_account"],
        account["granted"],
        account["needs_reconnect"],
    ) == (PRIMARY, True, False)

    # The first sync brings the calendar list; only the primary is chosen.
    result = await sync(client, alice)
    assert result["events"] == 6
    calendars = await ok(
        client, alice, "GET", f"{DATA}/calendar?order=is_primary.desc,name"
    )
    assert [(c["google_id"], c["selected"]) for c in calendars] == [
        (PRIMARY, True),
        (TEAM, False),
    ]
    cached = await events(client, alice)
    assert set(cached) == {"standup", "holiday", "gym_0", "gym_1", "gym_2", "lunch"}
    assert cached["standup"]["attendees"] == ["anna@example.com", PRIMARY]
    holiday = cached["holiday"]
    assert holiday["all_day"] and holiday["start_date"] and holiday["end_date"]
    # Midnight in the calendar's zone, Europe/Stockholm.
    assert holiday["starts_at"].endswith(("T22:00:00+00:00", "T23:00:00+00:00")), (
        holiday
    )
    # Paged (the fake sends two a page), singleEvents, the window in full.
    lists = [q for p, q in fake.requests if p.endswith("/events")]
    assert all(q["singleEvents"] == "true" for q in lists)
    assert {"timeMin", "timeMax"} <= set(lists[0]) and len(lists) >= 3

    # Nobody else sees any of it, and no session sees nothing at all.
    for path in [
        "account",
        "calendar",
        "event",
        "agenda",
        "calendar_sync",
        "sync_status",
    ]:
        r = await client.get(f"{DATA}/{path}", headers=bob)
        assert r.status_code in {200, 401, 403}, (path, r.text)
        if r.status_code == 200:
            assert r.json() == [], path
        assert (await client.get(f"{DATA}/{path}")).status_code == 401, path
    assert (await client.post(f"{PLUGIN}/sync")).status_code == 401
    assert (
        await ok(
            client,
            bob,
            "GET",
            f"{DATA}/rpc/search?since=2000-01-01T00:00:00Z&until=2100-01-01T00:00:00Z",
        )
        == []
    )
    # The worker's functions refuse a person, the token one first.
    for fn, args in [
        ("core/rpc/google_token", {"owner": "cal-alice", "account_id": account["id"]}),
        ("core/rpc/google_accounts", {"scopes": []}),
        (
            "core/rpc/google_disconnect",
            {"owner": "cal-alice", "account_id": account["id"]},
        ),
        (
            "calendar/rpc/set_status",
            {"owner": "cal-alice", "account_id": account["id"], "error": "x"},
        ),
        (
            "calendar/rpc/apply_events",
            {
                "owner": "cal-alice",
                "calendar_id": calendars[0]["id"],
                "full_sync": True,
                "events": [],
                "removed": [],
                "sync_token": None,
                "window_start": "2000-01-01T00:00:00Z",
                "window_end": "2100-01-01T00:00:00Z",
            },
        ),
        (
            "calendar/rpc/select_calendar",
            {
                "owner": "cal-alice",
                "calendar_id": calendars[0]["id"],
                "selected": False,
            },
        ),
    ]:
        for who in [alice, bob]:
            r = await client.post(f"/api/data/{fn}", json=args, headers=who)
            assert r.status_code in {401, 403, 404}, (fn, r.status_code, r.text)
    # Bob cannot choose Alice's calendar.
    r = await client.post(
        f"{PLUGIN}/calendars/{calendars[1]['id']}", json={"selected": True}, headers=bob
    )
    assert r.status_code in {404, 409}, r.text
    assert len(await events(client, alice)) == 6

    # Sealed at rest: the database holds no usable token.
    [refresh] = fake.control("state", {})["refresh_tokens"]
    [(sealed,)] = await owner_rows(
        "select t.sealed from heycarl_private.google_token t"
        " join heycarl_private.google_account a on a.id = t.account_id"
        " where a.owner_sub = %s",
        "cal-alice",
    )
    assert refresh.encode() not in bytes(sealed)
    # The cache keeps no history and no trash.
    assert (
        await ok(
            client,
            alice,
            "POST",
            "/api/data/core/rpc/history",
            {"of_scope": "calendar"},
        )
        == []
    )

    # Another calendar in, then out again.
    await ok(
        client,
        alice,
        "POST",
        f"{PLUGIN}/calendars/{calendars[1]['id']}",
        {"selected": True},
    )
    await sync(client, alice)
    assert "offsite" in await events(client, alice)
    await ok(
        client,
        alice,
        "POST",
        f"{PLUGIN}/calendars/{calendars[1]['id']}",
        {"selected": False},
    )
    assert "offsite" not in await events(client, alice)


async def test_changes_cancels_gone_and_refresh(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("cal-sync")
    await connect(client, me)
    await sync(client, me)
    before = await events(client, me)

    # A todo linked to an event keeps its link through a full resync.
    [todo] = await ok(
        client,
        {**me, "prefer": "return=representation"},
        "POST",
        "/api/data/todos/todo",
        {"title": "prepare standup"},
    )
    link = {
        "a_scope": "todos",
        "a_table": "todo",
        "a_key": {"id": todo["id"]},
        "b_scope": "calendar",
        "b_table": "event",
        "b_key": {"id": before["standup"]["id"]},
    }
    await ok(client, me, "POST", "/api/data/core/rpc/link", link)

    fake.control("rename", {"id": "standup", "title": "Standup, moved"})
    fake.control("cancel", {"id": "lunch"})
    fake.control("cancel", {"id": "gym"})
    fake.control(
        "put",
        {
            "event": {
                "id": "dinner",
                "status": "confirmed",
                "summary": "Dinner",
                "start": {"dateTime": "2099-01-01T18:00:00Z"},
                "end": {"dateTime": "2099-01-01T20:00:00Z"},
            }
        },
    )
    await sync(client, me)
    incremental = [q for p, q in fake.requests if p.endswith("/events")][-1]
    assert "syncToken" in incremental and "timeMin" not in incremental
    after = await events(client, me)
    assert after["standup"]["title"] == "Standup, moved"
    assert after["standup"]["id"] == before["standup"]["id"]
    # Cancelled: lunch itself, and gym's instances by their series. Dinner
    # is beyond the window.
    assert set(after) == {"standup", "holiday"}

    fake.control("gone", {})
    fake.control(
        "put",
        {
            "event": {
                "id": "late",
                "status": "confirmed",
                "summary": "Late add",
                "start": {"date": before["holiday"]["start_date"]},
                "end": {"date": before["holiday"]["end_date"]},
            }
        },
    )
    seen = len(fake.requests)
    await sync(client, me)
    lists = [q for p, q in fake.requests[seen:] if p.endswith("/events")]
    assert "syncToken" in lists[0] and "timeMin" in lists[1], lists
    resynced = await events(client, me)
    assert set(resynced) == {"standup", "holiday", "late"}
    assert resynced["standup"]["id"] == before["standup"]["id"]
    [linked] = await ok(
        client,
        me,
        "POST",
        "/api/data/core/rpc/links",
        {"of_scope": "todos", "of_table": "todo", "of_key": {"id": todo["id"]}},
    )
    assert linked["entry"]["label"] == "Standup, moved"
    assert (
        linked["entry"]["url"]
        == f"/p/calendar/heycarl-calendar#/event/{before['standup']['id']}"
    )

    grants = fake.control("state", {})["refresh_grants"]
    fake.control("expire_access", {})
    await sync(client, me)
    assert fake.control("state", {})["refresh_grants"] == grants + 1

    # Google forgets the grant: the account asks to be connected again.
    fake.control("forget_refresh", {})
    r = await ok(client, me, "POST", f"{PLUGIN}/sync")
    assert r["ok"] is False and "connect again" in r["error"]
    [account] = await ok(client, me, "GET", f"{DATA}/account")
    assert account["needs_reconnect"] is True
    await connect(client, me, account_id=account["id"])
    [again] = await ok(client, me, "GET", f"{DATA}/account")
    assert (again["id"], again["needs_reconnect"]) == (account["id"], False)
    await sync(client, me)
    [again] = await ok(client, me, "GET", f"{DATA}/account")
    assert again["last_error"] is None
    # The same events, under the same ids: the account kept its data.
    assert (await events(client, me))["standup"]["id"] == before["standup"]["id"]


async def test_two_accounts_and_disconnecting_one(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("cal-two")
    await connect(client, me)
    fake.control("as", {"user": "work"})
    await connect(client, me)
    home, work = await ok(client, me, "GET", f"{DATA}/account?order=id")
    assert (home["google_account"], work["google_account"]) == (
        PRIMARY,
        "carl@work.example.com",
    )
    assert (await sync(client, me))["events"] == 7
    assert {"standup", "review"} <= set(await events(client, me))
    calendars = await ok(client, me, "GET", f"{DATA}/calendar")
    assert {c["account_id"] for c in calendars} == {home["id"], work["id"]}

    # One account goes, with its calendars and events; the other stays.
    assert await ok(
        client, me, "POST", f"{GOOGLE}/accounts/{work['id']}/disconnect"
    ) == {"disconnected": True, "revoked": True}
    assert [a["id"] for a in await ok(client, me, "GET", f"{DATA}/account")] == [
        home["id"]
    ]
    left = await events(client, me)
    assert "review" not in left and "standup" in left
    assert {
        c["account_id"] for c in await ok(client, me, "GET", f"{DATA}/calendar")
    } == {home["id"]}
    assert (await sync(client, me))["ok"]
    assert (
        await owner_rows(
            "select 1 from heycarl_private.google_token where account_id = %s",
            work["id"],
        )
        == []
    )
    assert await ok(client, me, "POST", "/api/data/core/rpc/trash", {}) == []


async def test_an_account_without_calendar_access_is_skipped(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("cal-refused")
    fake.control(
        "refuse", {"scopes": ["https://www.googleapis.com/auth/calendar.readonly"]}
    )
    await connect(client, me)
    [account] = await ok(client, me, "GET", f"{DATA}/account")
    assert account["granted"] is False
    r = await ok(client, me, "POST", f"{PLUGIN}/sync")
    assert (r["ok"], r["error"]) == (False, "not connected")
    # Granting it later: the same account, now with the scope.
    fake.control("refuse", {"scopes": []})
    await connect(client, me, account_id=account["id"])
    [account] = await ok(client, me, "GET", f"{DATA}/account")
    assert account["granted"] is True
    assert (await sync(client, me))["events"] == 6


async def test_people_at_an_event(client: httpx.AsyncClient, fake: Fake) -> None:
    me = as_user("cal-prm")
    write = {**me, "prefer": "return=representation"}
    [anna] = await ok(
        client, write, "POST", "/api/data/prm/contact", {"first_name": "Anna"}
    )
    await ok(
        client,
        write,
        "POST",
        "/api/data/prm/contact_field",
        {"contact_id": anna["id"], "kind": "email", "value": "ANNA@example.com"},
    )
    await connect(client, me)
    await sync(client, me)
    standup = (await events(client, me))["standup"]
    people = await ok(
        client, me, "GET", f"{DATA}/rpc/event_people?event_id={standup['id']}"
    )
    assert [(p["contact_id"], p["email"]) for p in people] == [
        (anna["id"], "anna@example.com")
    ]
    # Someone else's contacts never match.
    other = as_user("cal-prm-other")
    assert (
        await ok(
            client, other, "GET", f"{DATA}/rpc/event_people?event_id={standup['id']}"
        )
        == []
    )


async def test_mcp_tools(served: Plain, client: httpx.AsyncClient, fake: Fake) -> None:
    await connect(client, as_user("cal-mcp"))
    async with connected(served, sub="cal-mcp") as session:
        assert await tool(session, "calendar_sync") == {"events": 6, "ok": True}
        upcoming = await tool(session, "calendar_upcoming", days=30)
        assert "not instructions" in upcoming["note"]
        titles = [e["title"] for e in upcoming["events"]]
        assert titles[0] == "Standup" and "Lunch with Bo" in titles
        found = await tool(
            session,
            "calendar_events",
            since="2000-01-01T00:00:00Z",
            until="2100-01-01T00:00:00Z",
            query="room 4",
        )
        [standup] = found["events"]
        detail = await tool(session, "calendar_event", event_id=standup["id"])
        assert detail["event"]["location"] == "Room 4" and detail["people"] == []
        listed = await tool(session, "calendar_calendars")
        assert [a["google_account"] for a in listed["accounts"]] == [PRIMARY]
        assert [c["selected"] for c in listed["calendars"]] == [True, False]
        resource = await session.read_resource("heycarl://calendar/upcoming")
        [content] = resource.contents
        assert "Standup" in getattr(content, "text", "")
    async with connected(served, sub="cal-mcp", scopes=("read",)) as session:
        assert "write scope" in await failure(session, "calendar_sync")
        assert len((await tool(session, "calendar_upcoming"))["events"]) >= 1
    async with connected(served, sub="cal-mcp-other") as session:
        assert (await tool(session, "calendar_upcoming", days=400))["events"] == []
        assert "no event" in await failure(
            session, "calendar_event", event_id=standup["id"]
        )
        assert "not connected" in await failure(session, "calendar_sync")
        assert "permission denied" in await failure(
            session,
            "data_read",
            scope="calendar",
            path="rpc/sync_state",
            query={"owner": "cal-mcp", "account_id": "1"},
        )
