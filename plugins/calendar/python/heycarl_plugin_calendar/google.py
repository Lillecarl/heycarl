"""The Calendar API's resources, parsed. The kernel's Google accounts
(heycarl.googleaccounts) hold the grant and make the requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from collections.abc import Mapping

SCOPE: Final = "https://www.googleapis.com/auth/calendar.readonly"
API_NAME: Final = "calendar"
API_URL: Final = "https://www.googleapis.com/calendar/v3"
PAGE_SIZE: Final = 250
# A page loop that never ends is a bug at Google or here; stop it.
MAX_PAGES: Final = 200
TITLE_CHARS: Final = 1000
DESCRIPTION_CHARS: Final = 20000
LOCATION_CHARS: Final = 1000
LINK_CHARS: Final = 2000
MAX_ATTENDEES: Final = 200


def calendar_path(google_id: str) -> str:
    return f"/calendars/{quote(google_id, safe='')}/events"


@dataclass(frozen=True, slots=True)
class Calendar:
    google_id: str
    name: str
    color: str
    time_zone: str
    is_primary: bool

    def row(self) -> dict[str, object]:
        return {
            "google_id": self.google_id,
            "name": self.name,
            "color": self.color,
            "time_zone": self.time_zone,
            "is_primary": self.is_primary,
        }


def parse_calendar(item: Mapping[str, Any]) -> Calendar:
    return Calendar(
        google_id=str(item["id"]),
        name=str(item.get("summaryOverride") or item.get("summary") or item["id"]),
        color=str(item.get("backgroundColor", "")),
        time_zone=str(item.get("timeZone") or "UTC"),
        is_primary=bool(item.get("primary", False)),
    )


@dataclass(frozen=True, slots=True)
class Event:
    google_id: str
    recurring_event_id: str | None
    status: str
    title: str
    description: str
    location: str
    starts_at: datetime | None
    ends_at: datetime | None
    start_date: date | None
    end_date: date | None
    attendees: tuple[str, ...]
    organizer: str
    html_link: str
    updated_at: str | None

    def row(self) -> dict[str, object]:
        return {
            "google_id": self.google_id,
            "recurring_event_id": self.recurring_event_id,
            "status": self.status,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "starts_at": None if self.starts_at is None else self.starts_at.isoformat(),
            "ends_at": None if self.ends_at is None else self.ends_at.isoformat(),
            "start_date": None
            if self.start_date is None
            else self.start_date.isoformat(),
            "end_date": None if self.end_date is None else self.end_date.isoformat(),
            "attendees": list(self.attendees),
            "organizer": self.organizer,
            "html_link": self.html_link,
            "updated_at": self.updated_at,
        }

    def overlaps(self, start: datetime, end: datetime) -> bool:
        """Whether it touches [start, end). An all-day event counts from its
        date in UTC; the database places it in the calendar's zone, and the
        window has more than a day to spare at both ends."""
        if self.start_date is not None and self.end_date is not None:
            return self.end_date >= start.date() and self.start_date <= end.date()
        if self.starts_at is None or self.ends_at is None:
            return False
        return self.ends_at > start and self.starts_at < end


def _moment(value: Mapping[str, Any] | None) -> tuple[datetime | None, date | None]:
    if not value:
        return None, None
    if "dateTime" in value:
        moment = datetime.fromisoformat(str(value["dateTime"]))
        # Google may leave out the offset when the item names its zone.
        if moment.tzinfo is None:
            try:
                zone: tzinfo = ZoneInfo(str(value.get("timeZone") or "UTC"))
            except ZoneInfoNotFoundError, ValueError:
                zone = UTC
            moment = moment.replace(tzinfo=zone)
        return moment, None
    if "date" in value:
        return None, date.fromisoformat(str(value["date"]))
    return None, None


def _text(value: object, limit: int) -> str:
    return "" if value is None else str(value)[:limit]


def parse_event(item: Mapping[str, Any]) -> Event | None:
    """None for a cancelled event, or one with no usable time: the caller
    removes those. A cancelled item may carry nothing but id and status."""
    if item.get("status") == "cancelled":
        return None
    try:
        starts_at, start_date = _moment(item.get("start"))
        ends_at, end_date = _moment(item.get("end"))
    except ValueError:
        return None
    if start_date is not None and end_date is None:
        end_date = start_date
    if starts_at is not None and ends_at is None:
        ends_at = starts_at
    if (starts_at is None) == (start_date is None):
        return None
    attendees = tuple(
        str(a["email"]).lower()
        for a in (item.get("attendees") or [])[:MAX_ATTENDEES]
        if isinstance(a, dict) and a.get("email")
    )
    organizer = item.get("organizer") or {}
    return Event(
        google_id=str(item["id"]),
        recurring_event_id=None
        if item.get("recurringEventId") is None
        else str(item["recurringEventId"]),
        status=_text(item.get("status", "confirmed"), 20),
        title=_text(item.get("summary"), TITLE_CHARS),
        description=_text(item.get("description"), DESCRIPTION_CHARS),
        location=_text(item.get("location"), LOCATION_CHARS),
        starts_at=starts_at,
        ends_at=ends_at,
        start_date=start_date,
        end_date=end_date,
        attendees=attendees,
        organizer=_text(
            organizer.get("email", "") if isinstance(organizer, dict) else "", 500
        ).lower(),
        html_link=_text(item.get("htmlLink"), LINK_CHARS),
        updated_at=None if item.get("updated") is None else str(item["updated"]),
    )
