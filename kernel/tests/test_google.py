"""The kernel's Google accounts against fake_google.py: the connect flow,
one row per Google account, granted scopes, a carried-over account, and
nobody reads another's accounts or tokens."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .conftest import as_user, needs_database
from .fake_google import USERS
from .test_calendar import GOOGLE, config, connect, fake, no_sync_limit, ok, start
from .test_history import owner_rows

if TYPE_CHECKING:
    import httpx

    from .fake_google import Fake

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["config", "fake", "no_sync_limit"]

ACCOUNTS = "/api/data/core/google_account"


async def test_the_state_belongs_to_one_session(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    alice = as_user("g-state")
    callback = await start(client, alice)
    # Another person, another session of the same person, a forged state,
    # no session: each is refused, and nothing is stored.
    assert (
        await client.get(callback, headers=as_user("g-state-bob"))
    ).status_code == 403
    callback = await start(client, alice)
    assert (await client.get(callback, headers=as_user("g-state"))).status_code == 403
    forged = callback.replace("state=", "state=x")
    assert (await client.get(forged, headers=alice)).status_code == 400
    callback = await start(client, alice)
    assert (await client.get(callback)).status_code == 401
    assert await ok(client, alice, "GET", ACCOUNTS) == []
    # A state is used once.
    callback = await start(client, alice)
    assert (await client.get(callback, headers=alice)).status_code == 303
    assert (await client.get(callback, headers=alice)).status_code == 400
    # Google's error, with a state that is not pending.
    r = await client.get(
        f"{GOOGLE}/callback?state=nope&error=access_denied", headers=alice
    )
    assert r.status_code == 400


async def test_next_stays_on_this_site(client: httpx.AsyncClient, fake: Fake) -> None:
    me = as_user("g-next")
    for target in ["https://evil.example.com/", "//evil.example.com", "evil"]:
        r = await client.get(await start(client, me, target), headers=me)
        assert (r.status_code, r.headers["location"]) == (303, "/"), target


async def test_one_row_per_google_account(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("g-rows")
    await connect(client, me)
    await connect(client, me)
    fake.control("as", {"user": "work"})
    await connect(client, me)
    rows = await ok(client, me, "GET", f"{ACCOUNTS}?order=id")
    assert [r["email"] for r in rows] == [USERS["carl"], USERS["work"]]
    assert all({"openid", "email"} <= set(r["scopes"]) for r in rows)
    # The person sees no token and no Google sub.
    assert set(rows[0]) == {
        "id",
        "email",
        "scopes",
        "connected_at",
        "needs_reconnect",
        "last_error",
    }
    # A reconnect names the account to Google.
    fake.control("as", {"user": "carl"})
    await connect(client, me, account_id=rows[0]["id"])
    assert fake.control("state", {})["authorize"][-1]["login_hint"] == USERS["carl"]
    assert [r["id"] for r in await ok(client, me, "GET", f"{ACCOUNTS}?order=id")] == [
        r["id"] for r in rows
    ]
    # Someone else's account id names nothing.
    r = await client.post(
        f"{GOOGLE}/connect", json={"account_id": rows[0]["id"]}, headers=as_user("g-x")
    )
    assert r.status_code == 404


async def test_nobody_else_sees_an_account(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("g-private")
    await connect(client, me)
    [mine] = await ok(client, me, "GET", ACCOUNTS)
    other = as_user("g-private-other")
    assert await ok(client, other, "GET", ACCOUNTS) == []
    assert (await client.get(ACCOUNTS)).status_code == 401
    assert (await client.get(GOOGLE)).status_code == 401
    r = await client.post(f"{GOOGLE}/accounts/{mine['id']}/disconnect", headers=other)
    assert r.json() == {"disconnected": False, "revoked": False}
    assert len(await ok(client, me, "GET", ACCOUNTS)) == 1
    for fn in ["google_token", "google_connect", "google_set_status"]:
        r = await client.post(
            f"/api/data/core/rpc/{fn}",
            json={"owner": "g-private", "account_id": mine["id"]},
            headers=me,
        )
        assert r.status_code in {401, 403, 404}, (fn, r.text)


async def test_a_carried_over_account_is_adopted_by_email(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    """The calendar migration moves an account without a Google sub."""
    [(account_id,)] = await owner_rows(
        "insert into heycarl_private.google_account (owner_sub, email, scopes)"
        " values (%s, %s, '{}') returning id",
        "g-moved",
        USERS["carl"],
    )
    me = as_user("g-moved")
    await connect(client, me)
    [row] = await ok(client, me, "GET", ACCOUNTS)
    assert row["id"] == account_id
    [(sub,)] = await owner_rows(
        "select google_sub from heycarl_private.google_account where owner_sub = %s",
        "g-moved",
    )
    assert sub == "sub-carl"


async def test_a_lost_token_asks_for_a_reconnect(
    client: httpx.AsyncClient, fake: Fake
) -> None:
    me = as_user("g-lost")
    await connect(client, me)
    [account] = await ok(client, me, "GET", ACCOUNTS)
    await owner_rows(
        "delete from heycarl_private.google_token where account_id = %s", account["id"]
    )
    fake.control("expire_access", {})
    r = await ok(client, me, "POST", "/api/plugins/calendar/sync")
    assert r["ok"] is False and "connect again" in r["error"], r
    [account] = await ok(client, me, "GET", ACCOUNTS)
    assert account["needs_reconnect"] is True
    # The job then skips it, and a connect brings it back.
    r = await ok(client, me, "POST", "/api/plugins/calendar/sync")
    assert r["error"] == "not connected"
    await connect(client, me, account_id=account["id"])
    assert (await ok(client, me, "GET", ACCOUNTS))[0]["needs_reconnect"] is False
