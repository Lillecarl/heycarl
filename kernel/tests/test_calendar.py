"""The calendar plugin against a fake Google (fake_google.py): connect with
PKCE, sync in full and by token, a 410, a cancelled event, a refresh,
revoke on disconnect. Nobody reads another's events or token."""

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

    from heycarl.config import Config

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]

PLUGIN = "/api/plugins/calendar"
DATA = "/api/data/calendar"
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
def no_sync_limit(monkeypatch: pytest.MonkeyPatch, app: object) -> None:
    sync = importlib.import_module("heycarl_plugin_calendar.sync")
    monkeypatch.setattr(sync, "MANUAL_SYNC_SECONDS", 0.0)
    # The job a connect wakes would race each test's own syncs for the
    # fake's 410s and counters; the VM test keeps it.
    monkeypatch.setattr(sync.Syncer, "wake", lambda _self: None)


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


async def start(client: httpx.AsyncClient, browser: dict[str, str]) -> str:
    """The callback path Google sends the browser to, after consent."""
    url = (await ok(client, browser, "POST", f"{PLUGIN}/connect"))["url"]
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert query["scope"] == "https://www.googleapis.com/auth/calendar.readonly"
    assert (query["code_challenge_method"], query["access_type"], query["prompt"]) == (
        "S256",
        "offline",
        "consent",
    )
    assert query["redirect_uri"] == PUBLIC_URL + PLUGIN + "/callback"
    async with httpx.AsyncClient() as browser_at_google:
        consent = await browser_at_google.get(url)
    assert consent.status_code == 302, consent.text
    return consent.headers["location"].removeprefix(PUBLIC_URL)


async def connect(client: httpx.AsyncClient, browser: dict[str, str]) -> None:
    r = await client.get(await start(client, browser), headers=browser)
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/p/calendar/heycarl-calendar"


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
    for path in ["connect", "sync", "disconnect"]:
        r = await client.post(f"{PLUGIN}/{path}", headers=me)
        assert r.status_code == 409, (path, r.text)
    assert await ok(client, me, "GET", f"{DATA}/event") == []


async def test_connect_sync_and_privacy(client: httpx.AsyncClient, fake: Fake) -> None:
    alice = as_user("cal-alice")
    bob = as_user("cal-bob")
    assert (await ok(client, alice, "GET", f"{PLUGIN}/status"))["configured"] is True
    await connect(client, alice)

    [account] = await ok(client, alice, "GET", f"{DATA}/account")
    assert (account["google_account"], account["needs_reconnect"]) == (PRIMARY, False)
    calendars = await ok(
        client, alice, "GET", f"{DATA}/calendar?order=is_primary.desc,name"
    )
    assert [(c["google_id"], c["selected"]) for c in calendars] == [
        (PRIMARY, True),
        (TEAM, False),
    ]

    result = await sync(client, alice)
    assert result["events"] == 6
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
    for path in ["account", "calendar", "event", "agenda", "calendar_sync"]:
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
        ("refresh_token", {"owner": "cal-alice"}),
        ("owners", {}),
        ("disconnect", {"owner": "cal-alice"}),
        (
            "apply_events",
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
            "select_calendar",
            {
                "owner": "cal-alice",
                "calendar_id": calendars[0]["id"],
                "selected": False,
            },
        ),
    ]:
        for who in [alice, bob]:
            r = await client.post(f"{DATA}/rpc/{fn}", json=args, headers=who)
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
        "select sealed from heycarl_private.calendar_google_token where owner_sub = %s",
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
    await connect(client, me)
    [account] = await ok(client, me, "GET", f"{DATA}/account")
    assert (account["needs_reconnect"], account["last_error"]) == (False, None)


async def test_the_state_belongs_to_one_session(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    alice = as_user("cal-state")
    callback = await start(client, alice)
    # Another person, another session of the same person, a forged state,
    # no session: each is refused, and nothing is stored.
    assert (
        await client.get(callback, headers=as_user("cal-state-bob"))
    ).status_code == 403
    callback = await start(client, alice)
    assert (await client.get(callback, headers=as_user("cal-state"))).status_code == 403
    forged = callback.replace("state=", "state=x")
    assert (await client.get(forged, headers=alice)).status_code == 400
    callback = await start(client, alice)
    assert (await client.get(callback)).status_code == 401
    assert await ok(client, alice, "GET", f"{DATA}/account") == []
    # A state is used once.
    callback = await start(client, alice)
    assert (await client.get(callback, headers=alice)).status_code == 303
    assert (await client.get(callback, headers=alice)).status_code == 400
    # Google's error, with a state that is not pending.
    r = await client.get(
        f"{PLUGIN}/callback?state=nope&error=access_denied", headers=alice
    )
    assert r.status_code == 400


async def test_disconnect_revokes_and_forgets(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("cal-bye")
    await connect(client, me)
    await sync(client, me)
    [refresh] = fake.control("state", {})["refresh_tokens"]
    assert await ok(client, me, "POST", f"{PLUGIN}/disconnect") == {
        "disconnected": True,
        "revoked": True,
    }
    assert fake.control("state", {})["revoked"] == [refresh]
    for path in ["account", "calendar", "event"]:
        assert await ok(client, me, "GET", f"{DATA}/{path}") == [], path
    assert (
        await owner_rows(
            "select 1 from heycarl_private.calendar_google_token where owner_sub = %s",
            "cal-bye",
        )
        == []
    )
    assert await ok(client, me, "POST", "/api/data/core/rpc/trash", {}) == []


async def test_people_at_an_event(client: httpx.AsyncClient, fake: Fake) -> None:
    me = as_user("cal-crm")
    write = {**me, "prefer": "return=representation"}
    [anna] = await ok(
        client, write, "POST", "/api/data/crm/contact", {"first_name": "Anna"}
    )
    await ok(
        client,
        write,
        "POST",
        "/api/data/crm/contact_field",
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
    other = as_user("cal-crm-other")
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
        assert listed["account"]["google_account"] == PRIMARY
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
            path="rpc/refresh_token",
            query={"owner": "cal-mcp"},
        )
