"""SL's Transport API: sites and departures, public and without a key
(DESIGN.md "Departures").

Every URL is built from an integer site id, so no caller can make the
kernel fetch anything else. Answers are cached, concurrent askers share
one fetch, and after a refusal the kernel asks SL nothing for a while and
answers from the last board it has.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
DEPARTURES_SECONDS: Final = 60.0
SITES_SECONDS: Final = 24 * 3600.0
# How long an old answer stands in while SL does not answer. Past this, a
# board has mostly left.
STALE_BOARD_SECONDS: Final = 15 * 60.0
STALE_SITES_SECONDS: Final = 7 * 24 * 3600.0
# SL publishes no rate limit and, measured 2026-11-17, sends no Retry-After
# with its 429. The pause doubles from the first to the last.
BACKOFF_SECONDS: Final = 60.0
MAX_BACKOFF_SECONDS: Final = 15 * 60.0
# A departure this long past its expected time has left.
LEFT_SECONDS: Final = 60.0
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


def still_to_leave(board: Board, now: datetime) -> Board:
    """`board` without the departures that left before `now`."""
    gone = now - timedelta(seconds=LEFT_SECONDS)
    return board.model_copy(
        update={"departures": [d for d in board.departures if d.expected >= gone]}
    )


def retry_after(response: httpx.Response) -> float:
    """SL's Retry-After in seconds, when it sends one as a number."""
    try:
        return max(0.0, float(response.headers.get("retry-after", "")))
    except ValueError:
        return 0.0


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
    # On `SL.clock`.
    fetched: float


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
    # One pause for all of SL: its limit is per client, not per site.
    _quiet_until: float = 0.0
    _backoff: float = 0.0

    def _pause(self, asked: float) -> None:
        self._backoff = min(
            max(2 * self._backoff, BACKOFF_SECONDS), MAX_BACKOFF_SECONDS
        )
        self._quiet_until = self.clock() + max(asked, self._backoff)
        log.warning("sl_pause", seconds=round(self._quiet_until - self.clock()))

    async def _get(self, path: str, params: dict[str, str]) -> bytes:
        if self.clock() < self._quiet_until:
            raise SLError("SL asked Hey Carl to wait. It tries again shortly")
        async with httpx.AsyncClient(
            base_url=self.base_url, transport=self.transport, timeout=HTTP_TIMEOUT
        ) as http:
            try:
                r = await http.get(path, params=params)
            except httpx.HTTPError as e:
                log.warning("sl_unreachable", path=path, error=type(e).__name__)
                self._pause(0.0)
                raise SLError("cannot reach SL") from e
        if r.status_code != httpx.codes.OK:
            log.warning("sl_refused", path=path, status=r.status_code)
            # A 404 is about one site; these are about SL, or about us.
            if (
                r.status_code == httpx.codes.TOO_MANY_REQUESTS
                or r.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
            ):
                self._pause(retry_after(r))
            raise SLError(f"SL answered {r.status_code}")
        self._backoff = 0.0
        return r.content

    def _age[T](self, entry: _Entry[T]) -> float:
        return self.clock() - entry.fetched

    async def _once[T](
        self,
        key: int | None,
        current: Callable[[], _Entry[T] | None],
        fetch: Callable[[], Awaitable[T]],
        fresh: float,
        stale: float,
    ) -> T:
        """The cached value while it is younger than `fresh`, else one fetch
        shared by every caller waiting on `key`. If the fetch fails, the
        cached value while it is younger than `stale`."""
        if (entry := current()) is not None and self._age(entry) < fresh:
            return entry.value
        async with self._locks.setdefault(key, anyio.Lock()):
            if (entry := current()) is not None and self._age(entry) < fresh:
                return entry.value
            try:
                return await fetch()
            except SLError:
                if entry is not None and self._age(entry) < stale:
                    return entry.value
                raise

    def _keep(self, site_id: int, board: Board) -> None:
        now = self.clock()
        if len(self._boards) >= CACHE_LIMIT:
            self._boards = {
                k: e
                for k, e in self._boards.items()
                if now - e.fetched < STALE_BOARD_SECONDS
            }
            self._locks = {
                k: lock
                for k, lock in self._locks.items()
                if k is None or k in self._boards or lock.statistics().locked
            }
        if len(self._boards) < CACHE_LIMIT:
            self._boards[site_id] = _Entry(board, now)

    async def board(self, site_id: int) -> Board:
        """The site's board, up to `DEPARTURES_SECONDS` old. While SL does
        not answer, up to `STALE_BOARD_SECONDS` old, as its `fetched_at`
        says, and without the departures that have left."""
        if not 1 <= site_id <= MAX_SITE_ID:
            raise SLError("no such site")

        async def fetch() -> Board:
            body = await self._get(
                f"/sites/{site_id}/departures", {"forecast": str(FORECAST_MINUTES)}
            )
            board = parse_board(site_id, body, datetime.now(ZONE))
            self._keep(site_id, board)
            return board

        board = await self._once(
            site_id,
            lambda: self._boards.get(site_id),
            fetch,
            DEPARTURES_SECONDS,
            STALE_BOARD_SECONDS,
        )
        now = datetime.now(ZONE)
        if now - board.fetched_at < timedelta(seconds=DEPARTURES_SECONDS):
            return board
        return still_to_leave(board, now)

    async def sites(self) -> Sites:
        async def fetch() -> Sites:
            sites = Sites.parse(await self._get("/sites", {"expand": "false"}))
            self._sites = _Entry(sites, self.clock())
            return sites

        return await self._once(
            None, lambda: self._sites, fetch, SITES_SECONDS, STALE_SITES_SECONDS
        )
