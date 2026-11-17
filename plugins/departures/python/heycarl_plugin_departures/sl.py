"""SL's Transport API: sites and departures, public and without a key
(DESIGN.md "Departures").

Every URL is built from an integer site id, so no caller can make the
kernel fetch anything else. Answers are cached for a short time, and
concurrent askers share one fetch.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import anyio
import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

log = structlog.get_logger()

BASE_URL: Final = "https://transport.integration.sl.se/v1"
# SL sends local times without an offset.
ZONE: Final = ZoneInfo("Europe/Stockholm")
DEPARTURES_SECONDS: Final = 20.0
SITES_SECONDS: Final = 24 * 3600.0
FORECAST_MINUTES: Final = 60
CACHE_LIMIT: Final = 200
SEARCH_LIMIT: Final = 20
HTTP_TIMEOUT: Final = httpx.Timeout(10.0)
MAX_SITE_ID: Final = 99_999_999
MAX_LINES: Final = 20
MAX_SHOWN: Final = 30
CANCELLED: Final = "CANCELLED"


class Transport(StrEnum):
    BUS = "BUS"
    METRO = "METRO"
    TRAIN = "TRAIN"
    TRAM = "TRAM"
    SHIP = "SHIP"
    FERRY = "FERRY"
    TAXI = "TAXI"


class SLError(Exception):
    """SL did not answer, or answered something unreadable."""


# SL's shapes, as far as they are read. Unknown fields are ignored: SL adds
# fields without notice.


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _RawSite(_Loose):
    id: int
    name: str
    alias: list[str] = []
    lat: float | None = None
    lon: float | None = None


class _RawLine(_Loose):
    id: int
    designation: str = ""
    transport_mode: str = ""
    group_of_lines: str | None = None


class _RawDeviation(_Loose):
    message: str = ""


class _RawStopPoint(_Loose):
    designation: str | None = None


class _RawDeparture(_Loose):
    destination: str = ""
    direction_code: int
    state: str = ""
    scheduled: datetime
    expected: datetime | None = None
    line: _RawLine
    stop_point: _RawStopPoint | None = None
    deviations: list[_RawDeviation] = []


class _RawBoard(_Loose):
    departures: list[_RawDeparture] = []
    stop_deviations: list[_RawDeviation] = []


_SITES: Final = TypeAdapter(list[_RawSite])


# What the kernel answers.


class Site(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: int
    name: str
    latitude: float | None
    longitude: float | None


class Departure(BaseModel):
    model_config = ConfigDict(frozen=True)
    line: int
    designation: str
    transport: str
    group: str | None
    direction: int
    destination: str
    # SL's state: EXPECTED, ATSTOP, CANCELLED, and others.
    state: str
    scheduled: datetime
    expected: datetime
    platform: str | None
    notices: list[str]


class Board(BaseModel):
    model_config = ConfigDict(frozen=True)
    site_id: int
    fetched_at: datetime
    departures: list[Departure]
    notices: list[str]


class Filter(BaseModel):
    """A stop's choice. Empty or null is everything."""

    model_config = ConfigDict(frozen=True)
    transport: Transport | None = None
    lines: list[int] = Field(default=[], max_length=MAX_LINES)
    direction: int | None = Field(default=None, ge=1, le=2)


def local(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=ZONE)


def _messages(deviations: Iterable[_RawDeviation]) -> list[str]:
    return list(dict.fromkeys(m for d in deviations if (m := d.message.strip())))


def parse_board(site_id: int, body: bytes, fetched_at: datetime) -> Board:
    try:
        raw = _RawBoard.model_validate_json(body)
    except ValidationError as e:
        raise SLError("SL sent departures in an unknown shape") from e
    departures = [
        Departure(
            line=d.line.id,
            designation=d.line.designation or str(d.line.id),
            transport=d.line.transport_mode,
            group=d.line.group_of_lines,
            direction=d.direction_code,
            destination=d.destination,
            state=d.state,
            scheduled=local(d.scheduled),
            expected=local(d.expected or d.scheduled),
            platform=d.stop_point.designation if d.stop_point else None,
            notices=_messages(d.deviations),
        )
        for d in raw.departures
    ]
    return Board(
        site_id=site_id,
        fetched_at=fetched_at,
        # SL orders by its own rules; "the next one" means the soonest.
        departures=sorted(departures, key=lambda d: d.expected),
        notices=_messages(raw.stop_deviations),
    )


def soonest(board: Board, limit: int) -> Board:
    """The first `limit` departures, plus the next one of each line and
    direction they leave out, in time order. A cancelled departure is not
    a line's next one."""
    shown = board.departures[:limit]
    covered = {(d.line, d.direction) for d in shown if d.state != CANCELLED}
    extra: set[int] = set()
    for i, d in enumerate(board.departures[limit:], start=limit):
        if d.state != CANCELLED and (key := (d.line, d.direction)) not in covered:
            covered.add(key)
            extra.add(i)
    return board.model_copy(
        update={
            "departures": [
                d for i, d in enumerate(board.departures) if i < limit or i in extra
            ]
        }
    )


def select(board: Board, wanted: Filter) -> Board:
    """The departures `wanted` names; the stop's own notices stay."""
    lines = set(wanted.lines)
    return board.model_copy(
        update={
            "departures": [
                d
                for d in board.departures
                if (wanted.transport is None or d.transport == wanted.transport)
                and (not lines or d.line in lines)
                and (wanted.direction is None or d.direction == wanted.direction)
            ]
        }
    )


def _fold(text: str) -> str:
    return re.sub(r"[\W_]", "", text.casefold())


@dataclass(frozen=True, slots=True)
class Sites:
    """SL's site list, folded once for search."""

    sites: dict[int, Site]
    names: dict[int, tuple[str, ...]]

    @classmethod
    def parse(cls, body: bytes) -> Sites:
        try:
            raw = _SITES.validate_json(body)
        except ValidationError as e:
            raise SLError("SL sent sites in an unknown shape") from e
        return cls(
            sites={
                s.id: Site(id=s.id, name=s.name, latitude=s.lat, longitude=s.lon)
                for s in raw
            },
            names={s.id: tuple(_fold(n) for n in (s.name, *s.alias)) for s in raw},
        )

    def search(self, query: str) -> list[Site]:
        """Sites whose name or alias holds `query`, ignoring case, spaces
        and dashes ("tcentralen" finds T-Centralen). An exact name comes
        first, then names that start with it."""
        q = _fold(query)
        if not q:
            return []
        ranked: list[tuple[int, int, str, int]] = []
        for site_id, names in self.names.items():
            if q in names:
                rank = 0
            elif any(n.startswith(q) for n in names):
                rank = 1
            elif any(q in n for n in names):
                rank = 2
            else:
                continue
            name = self.sites[site_id].name
            ranked.append((rank, len(name), name, site_id))
        ranked.sort()
        return [self.sites[r[3]] for r in ranked[:SEARCH_LIMIT]]


@dataclass(slots=True)
class _Entry[T]:
    value: T
    expires: float


@dataclass
class SL:
    """One per kernel. `transport` replaces the network, for tests."""

    base_url: str = BASE_URL
    transport: httpx.AsyncBaseTransport | None = None
    clock: Callable[[], float] = time.monotonic
    _boards: dict[int, _Entry[Board]] = field(default_factory=dict)
    _sites: _Entry[Sites] | None = None
    # None is the site list.
    _locks: dict[int | None, anyio.Lock] = field(default_factory=dict)

    async def _get(self, path: str, params: dict[str, str]) -> bytes:
        async with httpx.AsyncClient(
            base_url=self.base_url, transport=self.transport, timeout=HTTP_TIMEOUT
        ) as http:
            try:
                r = await http.get(path, params=params)
            except httpx.HTTPError as e:
                log.warning("sl_unreachable", path=path, error=type(e).__name__)
                raise SLError("cannot reach SL") from e
        if r.status_code != httpx.codes.OK:
            log.warning("sl_refused", path=path, status=r.status_code)
            raise SLError(f"SL answered {r.status_code}")
        return r.content

    def _fresh[T](self, entry: _Entry[T] | None) -> T | None:
        return (
            entry.value if entry is not None and entry.expires > self.clock() else None
        )

    async def _once[T](
        self,
        key: int | None,
        current: Callable[[], _Entry[T] | None],
        fetch: Callable[[], Awaitable[T]],
    ) -> T:
        """The cached value, or one fetch shared by every caller waiting on `key`."""
        if (found := self._fresh(current())) is not None:
            return found
        async with self._locks.setdefault(key, anyio.Lock()):
            if (found := self._fresh(current())) is not None:
                return found
            return await fetch()

    def _keep(self, site_id: int, board: Board) -> None:
        now = self.clock()
        if len(self._boards) >= CACHE_LIMIT:
            self._boards = {k: e for k, e in self._boards.items() if e.expires > now}
            self._locks = {
                k: lock
                for k, lock in self._locks.items()
                if k is None or k in self._boards or lock.statistics().locked
            }
        if len(self._boards) < CACHE_LIMIT:
            self._boards[site_id] = _Entry(board, now + DEPARTURES_SECONDS)

    async def board(self, site_id: int) -> Board:
        if not 1 <= site_id <= MAX_SITE_ID:
            raise SLError("no such site")

        async def fetch() -> Board:
            body = await self._get(
                f"/sites/{site_id}/departures", {"forecast": str(FORECAST_MINUTES)}
            )
            board = parse_board(site_id, body, datetime.now(ZONE))
            self._keep(site_id, board)
            return board

        return await self._once(site_id, lambda: self._boards.get(site_id), fetch)

    async def sites(self) -> Sites:
        async def fetch() -> Sites:
            sites = Sites.parse(await self._get("/sites", {"expand": "false"}))
            self._sites = _Entry(sites, self.clock() + SITES_SECONDS)
            return sites

        return await self._once(None, lambda: self._sites, fetch)
