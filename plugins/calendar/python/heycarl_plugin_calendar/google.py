"""Google's OAuth endpoints and the Calendar API, read-only, over httpx.

Every secret goes in a form body or a header, never in a URL: a URL lands
in exception messages and logs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

log = structlog.get_logger()

SCOPE: Final = "https://www.googleapis.com/auth/calendar.readonly"
ENV_PREFIX: Final = "HEYCARL_GOOGLE_"
PAGE_SIZE: Final = 250
# A page loop that never ends is a bug at Google or here; stop it.
MAX_PAGES: Final = 200
MIN_KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
TITLE_CHARS: Final = 1000
DESCRIPTION_CHARS: Final = 20000
LOCATION_CHARS: Final = 1000
LINK_CHARS: Final = 2000
MAX_ATTENDEES: Final = 200


@dataclass(frozen=True, slots=True)
class Endpoints:
    authorize: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token: str = "https://oauth2.googleapis.com/token"
    revoke: str = "https://oauth2.googleapis.com/revoke"
    calendar: str = "https://www.googleapis.com/calendar/v3"


@dataclass(frozen=True, slots=True)
class Settings:
    client_id: str
    client_secret: str = field(repr=False)
    key: bytes = field(repr=False)
    endpoints: Endpoints = Endpoints()


class ConfigError(RuntimeError):
    pass


def settings_from_env(environ: Mapping[str, str] = os.environ) -> Settings | None:
    """None when no client is configured: the feature is off.

    A client id without both files is a broken deploy, not an off switch.
    `HEYCARL_GOOGLE_ENDPOINTS` (JSON) replaces endpoints, for tests only.
    """
    client_id = environ.get(ENV_PREFIX + "CLIENT_ID", "")
    secret_file = environ.get(ENV_PREFIX + "CLIENT_SECRET_FILE", "")
    key_file = environ.get(ENV_PREFIX + "TOKEN_KEY_FILE", "")
    if not (client_id or secret_file or key_file):
        return None
    if not (client_id and secret_file and key_file):
        raise ConfigError(
            "Google Calendar needs a client id, a client secret file and a token key file"
        )
    secret = Path(secret_file).read_text().strip()
    key_material = Path(key_file).read_bytes().strip()
    if not secret:
        raise ConfigError("the Google client secret file is empty")
    if len(key_material) < MIN_KEY_BYTES:
        raise ConfigError(f"the token key file needs at least {MIN_KEY_BYTES} bytes")
    overrides = json.loads(environ.get(ENV_PREFIX + "ENDPOINTS", "") or "{}")
    return Settings(
        client_id=client_id,
        client_secret=secret,
        key=hashlib.sha256(key_material).digest(),
        endpoints=Endpoints(**overrides),
    )


def seal(key: bytes, owner: str, token: str) -> str:
    """AES-GCM with the owner as associated data, so a sealed token copied
    to another owner's row does not open."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, token.encode(), owner.encode())
    return base64.b64encode(nonce + sealed).decode()


def unseal(key: bytes, owner: str, sealed: str) -> str:
    raw = base64.b64decode(sealed)
    return (
        AESGCM(key)
        .decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], owner.encode())
        .decode()
    )


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(
    settings: Settings, redirect_uri: str, state: str, verifier: str
) -> str:
    query = {
        "client_id": settings.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
        # A refresh token, and a new one on every connect.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
    }
    return settings.endpoints.authorize + "?" + urlencode(query)


class GoogleError(RuntimeError):
    """A failure to show the person. Never carries a token."""


class ReconnectNeeded(GoogleError):
    """The refresh token no longer works: revoked, expired or never valid."""


class Unauthorized(GoogleError):
    pass


class Gone(GoogleError):
    """The sync token expired: wipe and sync in full."""


@dataclass(frozen=True, slots=True)
class Grant:
    access_token: str = field(repr=False)
    expires_in: int
    refresh_token: str | None = field(repr=False)
    scope: str


def _grant(answer: Mapping[str, Any]) -> Grant:
    # Any: Google's token response, read field by field.
    return Grant(
        access_token=str(answer["access_token"]),
        expires_in=int(answer.get("expires_in", 3600)),
        refresh_token=None
        if answer.get("refresh_token") is None
        else str(answer["refresh_token"]),
        scope=str(answer.get("scope", "")),
    )


def _error_code(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", ""))
    except ValueError:
        return ""


async def exchange_code(
    http: httpx.AsyncClient,
    settings: Settings,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> tuple[Grant, str]:
    """The grant and its refresh token, which a connect must bring."""
    r = await http.post(
        settings.endpoints.token,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
        },
    )
    if r.is_error:
        raise GoogleError(f"Google refused the code ({r.status_code} {_error_code(r)})")
    grant = _grant(r.json())
    if SCOPE not in grant.scope.split():
        raise GoogleError("Google did not grant read access to the calendar")
    if not grant.refresh_token:
        raise GoogleError("Google sent no refresh token")
    return grant, grant.refresh_token


async def refresh(
    http: httpx.AsyncClient, settings: Settings, refresh_token: str
) -> Grant:
    r = await http.post(
        settings.endpoints.token,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
        },
    )
    if r.status_code in {400, 401} and _error_code(r) in {
        "invalid_grant",
        "unauthorized_client",
    }:
        raise ReconnectNeeded("Google no longer accepts this connection; connect again")
    if r.is_error:
        raise GoogleError(f"Google refused to refresh the token ({r.status_code})")
    return _grant(r.json())


async def revoke(http: httpx.AsyncClient, settings: Settings, token: str) -> bool:
    r = await http.post(settings.endpoints.revoke, data={"token": token})
    # 400 invalid_token: Google had already forgotten it, which is the goal.
    return r.is_success or _error_code(r) == "invalid_token"


async def api_get(
    http: httpx.AsyncClient,
    settings: Settings,
    access_token: str,
    path: str,
    params: Mapping[str, str],
) -> dict[str, Any]:
    # Any: a Calendar API resource, parsed by the callers.
    r = await http.get(
        settings.endpoints.calendar + path,
        params=params,
        headers={
            "authorization": f"Bearer {access_token}",
            "accept": "application/json",
        },
    )
    if r.status_code == 401:
        raise Unauthorized("Google refused the access token")
    if r.status_code == 410:
        raise Gone("the sync token expired")
    if r.is_error:
        raise GoogleError(f"Google answered {r.status_code} to {path.split('?')[0]}")
    answer: dict[str, Any] = r.json()
    return answer


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
