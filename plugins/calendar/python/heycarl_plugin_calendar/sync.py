"""Google Calendar into the cache, one owner at a time.

A calendar's first sync, and one a day after it, fetches the whole window
with singleEvents, so recurring events arrive as instances. Between those,
the sync token brings only changes; it cannot carry timeMin, so changes
outside the window are dropped here. The daily full sync moves the window
on: an instance that enters it at the far end is no change to Google.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import anyio
import httpx
import structlog
from cryptography.exceptions import InvalidTag

from . import google, store
from .google import Gone, GoogleError, ReconnectNeeded, Unauthorized

if TYPE_CHECKING:
    from heycarl.app import Kernel

    from .google import Calendar, Event, Settings

log = structlog.get_logger()

SYNC_SECONDS: Final = 600.0
WINDOW_BEFORE: Final = timedelta(days=30)
WINDOW_AFTER: Final = timedelta(days=365)
FULL_SYNC_EVERY: Final = timedelta(days=1)
# Refresh a little early, so a token does not expire mid-sync.
ACCESS_MARGIN: Final = 60.0
HTTP_TIMEOUT: Final = 20.0
MANUAL_SYNC_SECONDS = 10.0


def window(now: datetime) -> tuple[datetime, datetime]:
    return now - WINDOW_BEFORE, now + WINDOW_AFTER


def needs_full(state: store.SyncState, now: datetime) -> bool:
    return (
        state.sync_token is None
        or state.full_sync_at is None
        or now - state.full_sync_at >= FULL_SYNC_EVERY
    )


def split(
    items: list[dict[str, object]], start: datetime, end: datetime
) -> tuple[list[Event], list[str]]:
    """The events to keep, and the ids to remove: cancelled, unreadable or
    outside the window."""
    keep: list[Event] = []
    removed: list[str] = []
    for item in items:
        event = google.parse_event(item)
        if event is not None and event.overlaps(start, end):
            keep.append(event)
        elif "id" in item:
            removed.append(str(item["id"]))
    return keep, removed


@dataclass(frozen=True, slots=True)
class Outcome:
    ok: bool
    error: str | None = None
    needs_reconnect: bool = False
    events: int = 0


@dataclass
class Access:
    token: str = field(repr=False)
    expires: float


@dataclass
class Syncer:
    settings: Settings
    _locks: dict[str, anyio.Lock] = field(default_factory=dict)
    _access: dict[str, Access] = field(default_factory=dict)
    _wake: anyio.Event = field(default_factory=anyio.Event)
    _manual: dict[str, float] = field(default_factory=dict)

    def lock(self, owner: str) -> anyio.Lock:
        return self._locks.setdefault(owner, anyio.Lock())

    def wake(self) -> None:
        self._wake.set()

    def remember(self, owner: str, grant: google.Grant) -> None:
        self._access[owner] = Access(
            grant.access_token, time.monotonic() + grant.expires_in
        )

    def forget(self, owner: str) -> None:
        self._access.pop(owner, None)

    async def run_forever(self, kernel: Kernel) -> None:
        # PostgREST may still be starting with the kernel; a connect wakes
        # this early.
        while True:
            with anyio.move_on_after(SYNC_SECONDS):
                await self._wake.wait()
            self._wake = anyio.Event()
            try:
                owners = await store.owners(kernel)
            except Exception:
                log.exception("calendar_owners_failed")
                continue
            for owner in owners:
                await self.sync_owner(kernel, owner)

    async def _refresh_token(self, kernel: Kernel, owner: str) -> str | None:
        sealed = await store.refresh_token(kernel, owner)
        if sealed is None:
            return None
        try:
            return google.unseal(self.settings.key, owner, sealed)
        except InvalidTag:
            # The token key changed: the stored token is lost for good.
            raise ReconnectNeeded("the token key changed; connect again") from None

    async def _access_token(
        self, kernel: Kernel, http: httpx.AsyncClient, owner: str, refresh_token: str
    ) -> str:
        cached = self._access.get(owner)
        if cached is not None and cached.expires - ACCESS_MARGIN > time.monotonic():
            return cached.token
        grant = await google.refresh(http, self.settings, refresh_token)
        self.remember(owner, grant)
        if grant.refresh_token and grant.refresh_token != refresh_token:
            # An empty account keeps the one stored.
            await store.connect_account(
                kernel,
                owner,
                "",
                google.seal(self.settings.key, owner, grant.refresh_token),
                grant.scope or google.SCOPE,
            )
        return grant.access_token

    async def sync_now(self, kernel: Kernel, owner: str) -> Outcome | None:
        """A sync the person asked for; None when they asked a moment ago.
        Each one costs Google quota, and an MCP client may loop."""
        now = time.monotonic()
        if now - self._manual.get(owner, -MANUAL_SYNC_SECONDS) < MANUAL_SYNC_SECONDS:
            return None
        self._manual[owner] = now
        return await self.sync_owner(kernel, owner)

    async def sync_owner(self, kernel: Kernel, owner: str) -> Outcome:
        """Never raises: a failure is the account's last_error."""
        async with self.lock(owner):
            try:
                outcome = await self._sync(kernel, owner)
            except ReconnectNeeded as e:
                self.forget(owner)
                outcome = Outcome(ok=False, error=str(e), needs_reconnect=True)
            except GoogleError as e:
                outcome = Outcome(ok=False, error=str(e))
            except httpx.HTTPStatusError as e:
                # Only the kernel's own PostgREST calls raise for status.
                log.exception(
                    "calendar_store_failed", owner=owner, body=e.response.text[:500]
                )
                outcome = Outcome(ok=False, error="the sync failed; see the kernel log")
            except httpx.HTTPError as e:
                # Its message may hold a URL; the type is enough.
                outcome = Outcome(
                    ok=False, error=f"cannot reach Google ({type(e).__name__})"
                )
            except Exception:
                log.exception("calendar_sync_failed", owner=owner)
                outcome = Outcome(ok=False, error="the sync failed; see the kernel log")
            if outcome.error is not None:
                log.warning("calendar_sync_error", owner=owner, error=outcome.error)
            try:
                await store.set_status(
                    kernel,
                    owner,
                    outcome.error,
                    needs_reconnect=outcome.needs_reconnect,
                )
            except Exception:
                log.exception("calendar_status_failed", owner=owner)
            return outcome

    async def _sync(self, kernel: Kernel, owner: str) -> Outcome:
        refresh_token = await self._refresh_token(kernel, owner)
        if refresh_token is None:
            return Outcome(ok=False, error="not connected")
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:

            async def get(path: str, params: dict[str, str]) -> dict[str, object]:
                for attempt in range(2):
                    token = await self._access_token(kernel, http, owner, refresh_token)
                    try:
                        return await google.api_get(
                            http, self.settings, token, path, params
                        )
                    except Unauthorized:
                        # Expired or revoked early: one fresh token, then give up.
                        self.forget(owner)
                        if attempt:
                            raise
                raise RuntimeError("unreachable")

            calendars = await list_calendars(get)
            await store.put_calendars(kernel, owner, calendars)
            total = 0
            now = datetime.now(UTC)
            for state in await store.sync_state(kernel, owner):
                total += await self._sync_calendar(kernel, owner, state, get, now)
        return Outcome(ok=True, events=total)

    async def _sync_calendar(
        self,
        kernel: Kernel,
        owner: str,
        state: store.SyncState,
        get: Getter,
        now: datetime,
    ) -> int:
        path = google.calendar_path(state.google_id)
        full = needs_full(state, now)
        start, end = (
            (state.window_start, state.window_end)
            if not full
            and state.window_start is not None
            and state.window_end is not None
            else window(now)
        )
        items: list[dict[str, object]] = []
        token: str | None = None
        if not full and state.sync_token is not None:
            try:
                items, token = await pages(
                    get, path, {"singleEvents": "true", "syncToken": state.sync_token}
                )
            except Gone:
                log.info(
                    "calendar_sync_token_gone", owner=owner, calendar=state.calendar_id
                )
                full = True
                start, end = window(now)
        if full:
            items, token = await pages(
                get,
                path,
                {
                    "singleEvents": "true",
                    "timeMin": start.isoformat(),
                    "timeMax": end.isoformat(),
                },
            )
        events, removed = split(items, start, end)
        return await store.apply_events(
            kernel,
            owner,
            state.calendar_id,
            full_sync=full,
            events=events,
            removed=removed,
            sync_token=token,
            window=(start, end),
        )


Getter = Callable[[str, dict[str, str]], Awaitable[dict[str, object]]]


async def pages(
    get: Getter, path: str, params: dict[str, str]
) -> tuple[list[dict[str, object]], str | None]:
    """Every item, and the sync token, which only the last page carries."""
    items: list[dict[str, object]] = []
    page: str | None = None
    for _ in range(google.MAX_PAGES):
        answer = await get(
            path,
            {**params, "maxResults": str(google.PAGE_SIZE)}
            | ({"pageToken": page} if page else {}),
        )
        found = answer.get("items")
        if isinstance(found, list):
            items.extend(i for i in found if isinstance(i, dict))
        next_page = answer.get("nextPageToken")
        if not next_page:
            token = answer.get("nextSyncToken")
            return items, None if token is None else str(token)
        page = str(next_page)
    raise GoogleError("Google sent too many pages")


async def list_calendars(get: Getter) -> list[Calendar]:
    items, _ = await pages(get, "/users/me/calendarList", {})
    return [google.parse_calendar(i) for i in items if "id" in i]
