"""Per-person preferences: the picked theme."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .conftest import as_user, needs_database

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.anyio, needs_database]
API = "/api/data/core/preference"
SET = "/api/data/core/rpc/preference_set_theme"


async def theme_of(client: httpx.AsyncClient, session: dict[str, str]) -> str | None:
    r = await client.get(API, headers=session)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) <= 1
    return rows[0]["theme"] if rows else None


async def test_a_theme_round_trips(client: httpx.AsyncClient) -> None:
    olga = as_user("pref-olga", "olga@example.com")
    assert await theme_of(client, olga) is None
    for slug in ("gruvbox-dark-hard", "nord", None):
        r = await client.post(SET, headers=olga, json={"theme": slug})
        assert r.status_code in (200, 204), r.text
        assert await theme_of(client, olga) == slug


async def test_one_person_cannot_see_or_change_anothers(
    client: httpx.AsyncClient,
) -> None:
    pia = as_user("pref-pia", "pia@example.com")
    mallory = as_user("pref-mallory", "mallory@example.com")
    await client.post(SET, headers=pia, json={"theme": "nord"})
    assert await theme_of(client, mallory) is None
    await client.post(SET, headers=mallory, json={"theme": "dracula"})
    assert await theme_of(client, pia) == "nord"
    assert await theme_of(client, mallory) == "dracula"
    r = await client.patch(
        API, headers={**mallory, "prefer": "return=representation"}, json={"theme": "x"}
    )
    assert r.status_code >= 400 or r.json() == []
    assert await theme_of(client, pia) == "nord"


async def test_it_needs_a_session_and_a_clean_slug(client: httpx.AsyncClient) -> None:
    assert (await client.get(API)).status_code == 401
    body = {"theme": "nord"}
    assert (await client.post(SET, json=body)).status_code == 401
    quinn = as_user("pref-quinn", "quinn@example.com")
    foreign = {**quinn, "origin": "https://evil.example"}
    assert (await client.post(SET, headers=foreign, json=body)).status_code == 403
    for bad in ("Nord", "-x", "a b", "x" * 101, "<script>"):
        r = await client.post(SET, headers=quinn, json={"theme": bad})
        assert r.status_code == 400, (bad, r.text)
    assert await theme_of(client, quinn) is None
