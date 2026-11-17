"""The worker functions in migrations/, one call each."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from heycarl.app import Kernel
    from heycarl.googleaccounts import Account

    from .google import Calendar, Event

SCOPE: Final = "calendar"


async def _rpc(kernel: Kernel, function: str, body: dict[str, object]) -> object:
    r = await kernel.as_worker(SCOPE, "POST", f"rpc/{function}", body=body)
    return r.json() if r.content else None


async def set_status(
    kernel: Kernel, account: Account, error: str | None = None
) -> None:
    await _rpc(
        kernel,
        "set_status",
        {"owner": account.owner, "account_id": account.id, "error": error},
    )


async def put_calendars(
    kernel: Kernel, account: Account, calendars: Sequence[Calendar]
) -> None:
    await _rpc(
        kernel,
        "put_calendars",
        {
            "owner": account.owner,
            "account_id": account.id,
            "calendars": [c.row() for c in calendars],
        },
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


async def calendar_account(kernel: Kernel, owner: str, calendar_id: int) -> int | None:
    """The Google account a calendar of `owner`'s belongs to."""
    found = await _rpc(
        kernel, "calendar_account", {"owner": owner, "calendar_id": calendar_id}
    )
    return None if found is None else int(str(found))


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


async def sync_state(kernel: Kernel, account: Account) -> list[SyncState]:
    rows = await _rpc(
        kernel, "sync_state", {"owner": account.owner, "account_id": account.id}
    )
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
