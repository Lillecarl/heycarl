"""The departures plugin against a fake SL: site search, a stop's filter,
local times with an offset, one fetch per site while it is fresh, a
readable 502, stops nobody else reaches, and a stop bound to a place."""

from __future__ import annotations

import importlib
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

import pytest

from .conftest import as_user, needs_database
from .test_history import call
from .test_mcp import Plain, connected, served, tool

if TYPE_CHECKING:
    from collections.abc import Iterator

    from types import ModuleType

    import httpx
    from fastapi import FastAPI
    from heycarl.config import Config

pytestmark = [pytest.mark.anyio, needs_database]
__all__ = ["served"]

PLUGIN = "/api/plugins/departures"
HOGDALEN = 9162
DOWN = 4242

SITES = [
    {"id": 9001, "name": "T-Centralen", "alias": ["Tcentralen"], "lat": 59.33138, "lon": 18.06043},
    {"id": 9117, "name": "Odenplan", "lat": 59.3429, "lon": 18.0491},
    {"id": HOGDALEN, "name": "Högdalen", "lat": 59.26312, "lon": 18.04117},
    {"id": 9163, "name": "Högdalens centrum", "lat": 59.2633, "lon": 18.0425},
]


def departure(
    line: int, direction: int, destination: str, expected: str, state: str = "EXPECTED", **extra: Any
) -> dict[str, Any]:
    return {
        "destination": destination,
        "direction_code": direction,
        "state": state,
        "display": "x",
        "scheduled": "2026-11-17T11:03:12",
        "expected": expected,
        "stop_point": {"id": 1702, "designation": str(direction)},
        "line": {
            "id": line,
            "designation": str(line),
            "transport_mode": "METRO",
            "group_of_lines": "Tunnelbanans gröna linje",
        },
        "deviations": [],
        "some_new_field": True,
        **extra,
    }


BOARD = {
    "departures": [
        departure(19, 2, "Hagsätra", "2026-11-17T11:06:22"),
        departure(19, 1, "Hässelby strand", "2026-11-17T11:07:41"),
        departure(19, 1, "Vällingby", "2026-11-17T11:12:00"),
        departure(
            19, 1, "Hässelby strand", "2026-11-17T11:18:32", "CANCELLED",
            deviations=[{"importance_level": 7, "consequence": "CANCELLED", "message": "Inställd"}],
        ),
        {
            **departure(144, 1, "Gullmarsplan", "2026-11-17T11:09:00"),
            "line": {"id": 144, "designation": "144", "transport_mode": "BUS"},
        },
    ],
    "stop_deviations": [{"message": "Hissen är ur funktion."}],
}


class FakeSL(BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        self.requests.append(path)
        if path == "/v1/sites":
            self._send(200, SITES)
        elif path == f"/v1/sites/{HOGDALEN}/departures":
            self._send(200, BOARD)
        elif path == f"/v1/sites/{DOWN}/departures":
            self._send(500, {"message": "down"})
        else:
            self._send(404, {"message": "no such site"})

    def _send(self, code: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def fake_sl(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    FakeSL.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSL)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HEYCARL_SL_URL", f"http://127.0.0.1:{server.server_address[1]}/v1")
    yield FakeSL.requests
    server.shutdown()
    server.server_close()


# The app reads HEYCARL_SL_URL when it registers the plugin.
@pytest.fixture
def config(config: Config, fake_sl: list[str]) -> Config:
    return config


async def get(client: httpx.AsyncClient, me: dict[str, str], path: str) -> Any:
    r = await client.get(PLUGIN + path, headers=me)
    assert r.is_success, f"{path}: {r.status_code} {r.text}"
    return r.json()


async def test_search_and_filter(client: httpx.AsyncClient, fake_sl: list[str]) -> None:
    me = as_user("sl-search")
    assert (await client.get(f"{PLUGIN}/sites?q=hog")).status_code == 401
    found = (await get(client, me, "/sites?q=tcentralen"))["sites"]
    assert [s["name"] for s in found] == ["T-Centralen"]
    found = (await get(client, me, "/sites?q=Högdalen"))["sites"]
    assert [s["id"] for s in found] == [HOGDALEN, 9163], found
    assert found[0] == {"id": HOGDALEN, "name": "Högdalen", "latitude": 59.26312, "longitude": 18.04117}

    board = await get(client, me, f"/sites/{HOGDALEN}/departures?transport=METRO&line=19&direction=1")
    assert [(d["destination"], d["state"]) for d in board["departures"]] == [
        ("Hässelby strand", "EXPECTED"),
        ("Vällingby", "EXPECTED"),
        ("Hässelby strand", "CANCELLED"),
    ]
    first = board["departures"][0]
    # SL's local time, sent on with Stockholm's offset (CET in November).
    assert first["expected"] == "2026-11-17T11:07:41+01:00"
    assert first["scheduled"] == "2026-11-17T11:03:12+01:00"
    assert (first["line"], first["designation"], first["platform"]) == (19, "19", "1")
    assert board["departures"][2]["notices"] == ["Inställd"]
    assert board["notices"] == ["Hissen är ur funktion."]

    everything = await get(client, me, f"/sites/{HOGDALEN}/departures")
    # In time order, though SL sent the bus last.
    assert [(d["line"], d["destination"]) for d in everything["departures"]] == [
        (19, "Hagsätra"),
        (19, "Hässelby strand"),
        (144, "Gullmarsplan"),
        (19, "Vällingby"),
        (19, "Hässelby strand"),
    ]
    # A limit keeps the next departure of every line and direction.
    first = await get(client, me, f"/sites/{HOGDALEN}/departures?limit=1")
    assert [(d["line"], d["direction"]) for d in first["departures"]] == [(19, 2), (19, 1), (144, 1)]
    north = await get(client, me, f"/sites/{HOGDALEN}/departures?limit=1&direction=1")
    assert [d["line"] for d in north["departures"]] == [19, 144]
    buses = await get(client, me, f"/sites/{HOGDALEN}/departures?transport=BUS")
    assert [d["line"] for d in buses["departures"]] == [144]
    # One fetch of the site list and one of the board, while both are fresh.
    assert fake_sl.count(f"/v1/sites/{HOGDALEN}/departures") == 1
    assert fake_sl.count("/v1/sites") == 1


async def test_refusals(client: httpx.AsyncClient, fake_sl: list[str]) -> None:
    me = as_user("sl-refusals")
    for path in (
        "/sites/0/departures",
        "/sites/x/departures",
        f"/sites/{HOGDALEN}/departures?direction=3",
        f"/sites/{HOGDALEN}/departures?transport=ROCKET",
        f"/sites/{HOGDALEN}/departures?limit=0",
        f"/sites/{HOGDALEN}/departures?limit=31",
        "/sites?q=",
    ):
        r = await client.get(PLUGIN + path, headers=me)
        assert r.status_code == 422, (path, r.text)
    assert fake_sl == []
    r = await client.get(f"{PLUGIN}/sites/{DOWN}/departures", headers=me)
    assert r.status_code == 502, r.text
    assert r.json()["code"] == "HC502"
    assert "SL answered 500" in r.json()["message"]


async def test_stops_are_private_and_checked(client: httpx.AsyncClient) -> None:
    owner = as_user("stop-owner")
    [stop] = await call(
        client, owner, "POST", "departures/stop",
        {"name": "Högdalen → city", "site_id": HOGDALEN, "site_name": "Högdalen",
         "transport": "METRO", "lines": [19], "direction": 1},
    )
    assert (stop["lines"], stop["direction"]) == ([19], 1)
    for bad in (
        {"direction": 3},
        {"transport": "ROCKET"},
        {"lines": [19, None]},
        {"name": " padded"},
        {"site_id": 0},
    ):
        r = await client.post(
            "/api/data/departures/stop",
            json={"name": "x", "site_id": HOGDALEN, "site_name": "Högdalen", **bad},
            headers=owner,
        )
        assert r.status_code == 400, (bad, r.text)

    me = as_user("stop-other")
    assert await call(client, me, "GET", "departures/stop") == []
    r = await client.patch(
        f"/api/data/departures/stop?id=eq.{stop['id']}",
        json={"name": "Mine"},
        headers={**me, "prefer": "return=representation"},
    )
    assert r.json() == [], r.text
    r = await client.post(
        "/api/data/departures/stop",
        json={"name": "x", "site_id": HOGDALEN, "site_name": "x", "owner_sub": "stop-owner"},
        headers=me,
    )
    assert r.status_code in (401, 403), r.text


async def test_a_stop_is_here_and_embeds(client: httpx.AsyncClient) -> None:
    me = as_user("stop-here")
    [stop] = await call(
        client, me, "POST", "departures/stop",
        {"name": "Högdalen → city", "site_id": HOGDALEN, "site_name": "Högdalen", "direction": 1},
    )
    [place] = await call(
        client, me, "POST", "places/place",
        {"name": "Högdalen", "latitude": 59.26312, "longitude": 18.04117, "radius": 300},
    )
    await call(
        client, me, "POST", "core/rpc/link",
        {"a_scope": "places", "a_table": "place", "a_key": {"id": place["id"]},
         "b_scope": "departures", "b_table": "stop", "b_key": {"id": stop["id"]}},
    )
    [found] = await call(
        client, me, "POST", "places/rpc/here", {"lat": 59.2632, "lon": 18.0412}
    )
    assert found["entry"] == {
        "scope": "departures",
        "table": "stop",
        "key": {"id": stop["id"]},
        "label": "Högdalen → city",
        "url": f"/p/departures/heycarl-departures#/stop/{stop['id']}",
    }, found

    r = await client.get("/api/plugins", headers=me)
    [departures] = [p for p in r.json() if p["name"] == "departures"]
    assert departures["frontend"]["embeds"] == {"stop": "heycarl-departures-stop"}
    [places] = [p for p in r.json() if p["name"] == "places"]
    assert places["frontend"]["embeds"] == {}


# The plugin's module is importable once the app has loaded the plugins.
@pytest.fixture
def sl(app: FastAPI) -> ModuleType:
    return importlib.import_module("heycarl_plugin_departures.sl")


def shown_by(sl: ModuleType, limit: int, *departures: tuple[int, int, int, str]) -> list[tuple[int, int, int]]:
    start = datetime(2026, 11, 17, 11, 0, tzinfo=sl.ZONE)
    board = sl.Board(
        site_id=1,
        fetched_at=start,
        notices=[],
        departures=[
            sl.Departure(
                line=line, designation=str(line), transport="METRO", group=None,
                direction=direction, destination="x", state=state,
                scheduled=start + timedelta(minutes=minute),
                expected=start + timedelta(minutes=minute),
                platform=None, notices=[],
            )
            for line, direction, minute, state in departures
        ],
    )
    return [(d.line, d.direction, d.expected.minute) for d in sl.soonest(board, limit).departures]


async def test_every_line_and_direction_gets_its_next_departure(sl: ModuleType) -> None:
    every_line_shown = [(17, 1, 1, "EXPECTED"), (18, 1, 2, "EXPECTED"), (17, 1, 4, "EXPECTED")]
    assert shown_by(sl, 2, *every_line_shown) == [(17, 1, 1), (18, 1, 2)]
    assert shown_by(
        sl, 2,
        (17, 1, 1, "EXPECTED"), (17, 1, 3, "EXPECTED"), (17, 2, 5, "EXPECTED"),
        (17, 1, 6, "EXPECTED"), (19, 1, 9, "EXPECTED"), (19, 1, 12, "EXPECTED"),
    ) == [(17, 1, 1), (17, 1, 3), (17, 2, 5), (19, 1, 9)]
    # A cancelled departure is not a line's next one, and a line with
    # nothing that leaves adds nothing.
    assert shown_by(
        sl, 1,
        (17, 1, 1, "CANCELLED"), (18, 1, 2, "EXPECTED"), (17, 1, 4, "EXPECTED"), (19, 1, 5, "CANCELLED"),
    ) == [(17, 1, 1), (18, 1, 2), (17, 1, 4)]


async def test_mcp_tools(served: Plain, fake_sl: list[str]) -> None:
    async with connected(served, sub="stop-mcp") as session:
        [site] = (await tool(session, "departures_sites", query="odenplan"))
        assert site["id"] == 9117
        made = await tool(
            session, "departures_add_stop",
            name="Högdalen → city", site_id=HOGDALEN, transport="METRO", lines=[19], direction=1,
        )
        assert made["site_name"] == "Högdalen"
        [board] = await tool(session, "departures_next", stop_id=made["id"], limit=2)
        assert board["stop"]["id"] == made["id"]
        assert [d["destination"] for d in board["departures"]] == ["Hässelby strand", "Vällingby"]
        assert all(d["minutes"] == 0 for d in board["departures"])
        changed = await tool(
            session, "departures_update_stop", stop_id=made["id"], both_directions=True, lines=[]
        )
        assert (changed["direction"], changed["lines"], changed["transport"]) == (None, [], "METRO")
        [stop] = await tool(session, "departures_stops")
        assert stop["id"] == made["id"]
