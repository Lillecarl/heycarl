"""The worker functions in migrations/0001_calendar.sql, one call each."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from heycarl.app import Kernel

    from .google import Calendar, Event

SCOPE: Final = "calendar"


async def _rpc(kernel: Kernel, function: str, body: dict[str, object]) -> object:
    r = await kernel.as_worker(SCOPE, "POST", f"rpc/{function}", body=body)
    return r.json() if r.content else None


async def connect_account(
    kernel: Kernel, owner: str, google_account: str, sealed: str, scope: str
) -> None:
    await _rpc(
        kernel,
        "connect_account",
        {
            "owner": owner,
            "google_account": google_account,
            "sealed": sealed,
            "scope": scope,
        },
    )


async def refresh_token(kernel: Kernel, owner: str) -> str | None:
    sealed = await _rpc(kernel, "refresh_token", {"owner": owner})
    return None if sealed is None else str(sealed).replace("\n", "")


async def owners(kernel: Kernel) -> list[str]:
    rows = await _rpc(kernel, "owners", {})
    return [str(r) for r in rows] if isinstance(rows, list) else []


async def set_status(
    kernel: Kernel,
    owner: str,
    error: str | None = None,
    *,
    needs_reconnect: bool = False,
) -> None:
    await _rpc(
        kernel,
        "set_status",
        {"owner": owner, "error": error, "needs_reconnect": needs_reconnect},
    )


async def disconnect(kernel: Kernel, owner: str) -> bool:
    return bool(await _rpc(kernel, "disconnect", {"owner": owner}))


async def put_calendars(
    kernel: Kernel, owner: str, calendars: Sequence[Calendar]
) -> None:
    await _rpc(
        kernel,
        "put_calendars",
        {"owner": owner, "calendars": [c.row() for c in calendars]},
    )


async def select_calendar(
    kernel: Kernel, owner: str, calendar_id: int, selected: bool
) -> bool:
    return bool(
        await _rpc(
            kernel,
            "select_calendar",
            {"owner": owner, "calendar_id": calendar_id, "selected": selected},
        )
    )


@dataclass(frozen=True, slots=True)
class SyncState:
    calendar_id: int
    google_id: str
    sync_token: str | None
    window_start: datetime | None
    window_end: datetime | None
    full_sync_at: datetime | None


def _when(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


async def sync_state(kernel: Kernel, owner: str) -> list[SyncState]:
    rows = await _rpc(kernel, "sync_state", {"owner": owner})
    if not isinstance(rows, list):
        return []
    return [
        SyncState(
            calendar_id=int(r["calendar_id"]),
            google_id=str(r["google_id"]),
            sync_token=r["sync_token"],
            window_start=_when(r["window_start"]),
            window_end=_when(r["window_end"]),
            full_sync_at=_when(r["full_sync_at"]),
        )
        for r in rows
    ]


async def apply_events(
    kernel: Kernel,
    owner: str,
    calendar_id: int,
    *,
    full_sync: bool,
    events: Sequence[Event],
    removed: Sequence[str],
    sync_token: str | None,
    window: tuple[datetime, datetime],
) -> int:
    kept = await _rpc(
        kernel,
        "apply_events",
        {
            "owner": owner,
            "calendar_id": calendar_id,
            "full_sync": full_sync,
            "events": [e.row() for e in events],
            "removed": list(removed),
            "sync_token": sync_token,
            "window_start": window[0].isoformat(),
            "window_end": window[1].isoformat(),
        },
    )
    return int(str(kept))
