"""The People API's Person, parsed into a cache row. The kernel's Google
accounts (heycarl.googleaccounts) hold the grant and make the requests.

Photos are left out on purpose: the page would load them from Google, and
each view would tell Google that someone looked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

SCOPE: Final = "https://www.googleapis.com/auth/contacts.readonly"
API_NAME: Final = "people"
API_URL: Final = "https://people.googleapis.com/v1"
CONNECTIONS: Final = "/people/me/connections"
# The same string on every call: a sync token and a page token are each
# valid only with the parameters of the call that issued them.
PERSON_FIELDS: Final = (
    "names,nicknames,emailAddresses,phoneNumbers,addresses,birthdays,"
    "organizations,urls,biographies,metadata"
)
PAGE_SIZE: Final = 1000
# A page loop that never ends is a bug at Google or here; stop it.
MAX_PAGES: Final = 100
NAME_CHARS: Final = 200
TEXT_CHARS: Final = 20000
VALUE_CHARS: Final = 500
LABEL_CHARS: Final = 50
MAX_VALUES: Final = 50
# The contact book's range for a year.
MIN_YEAR: Final = 1800
MAX_YEAR: Final = 2200
LEAP_YEAR: Final = 2000


def _text(value: object, limit: int) -> str:
    return "" if value is None else str(value).strip()[:limit]


def _primary(items: object) -> Mapping[str, Any]:
    """The item Google marks primary, else the first; {} for none."""
    if not isinstance(items, list):
        return {}
    found = [i for i in items if isinstance(i, dict)]
    for item in found:
        if (item.get("metadata") or {}).get("primary"):
            return item
    return found[0] if found else {}


def _values(items: object, key: str) -> list[dict[str, str]]:
    """[{value, label}], without empty values and duplicates."""
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        # An address's lines become one line, as the contact book keeps it.
        value = " ".join(_text(item.get(key), VALUE_CHARS).split())[:VALUE_CHARS]
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        label = _text(item.get("formattedType") or item.get("type"), LABEL_CHARS)
        out.append({"value": value, "label": label})
        if len(out) == MAX_VALUES:
            break
    return out


def _birthday(items: object) -> tuple[int | None, int | None, int | None]:
    """(year, month, day) of a real date, the year None when unknown. Google
    sends 0 for a part it does not know; the contact book refuses those and
    a 29 February in a year that has none."""
    found = _primary(items).get("date")
    if not isinstance(found, dict):
        return None, None, None
    year, month, day = found.get("year"), found.get("month"), found.get("day")
    if not (isinstance(month, int) and isinstance(day, int)):
        return None, None, None
    known = year if isinstance(year, int) and MIN_YEAR <= year <= MAX_YEAR else None
    try:
        # A leap year stands in for an unknown one, so 29 February holds.
        date(LEAP_YEAR if known is None else known, month, day)
    except ValueError:
        return None, None, None
    return known, month, day


@dataclass(frozen=True, slots=True)
class Person:
    resource_name: str
    row: dict[str, object]


def parse_person(item: Mapping[str, Any]) -> Person:
    name = _primary(item.get("names"))
    organization = _primary(item.get("organizations"))
    year, month, day = _birthday(item.get("birthdays"))
    resource_name = str(item["resourceName"])
    return Person(
        resource_name=resource_name,
        row={
            "resource_name": resource_name,
            "display_name": _text(name.get("displayName"), NAME_CHARS),
            "given_name": _text(name.get("givenName"), NAME_CHARS),
            "family_name": _text(name.get("familyName"), NAME_CHARS),
            "nickname": _text(_primary(item.get("nicknames")).get("value"), NAME_CHARS),
            "company": _text(organization.get("name"), NAME_CHARS),
            "job_title": _text(organization.get("title"), NAME_CHARS),
            "biography": _text(
                _primary(item.get("biographies")).get("value"), TEXT_CHARS
            ),
            "birthday_year": year,
            "birthday_month": month,
            "birthday_day": day,
            "emails": _values(item.get("emailAddresses"), "value"),
            "phones": _values(item.get("phoneNumbers"), "value"),
            "addresses": _values(item.get("addresses"), "formattedValue"),
            "urls": _values(item.get("urls"), "value"),
        },
    )


@dataclass(frozen=True, slots=True)
class Changes:
    people: list[Person]
    removed: list[str]
    # (old resource name, new one)
    renamed: list[tuple[str, str]]


def split(items: list[dict[str, Any]]) -> Changes:
    """A sync's items: contacts to keep, resource names deleted at Google,
    and renames."""
    people: list[Person] = []
    removed: list[str] = []
    renamed: list[tuple[str, str]] = []
    for item in items:
        if not item.get("resourceName"):
            continue
        metadata = item.get("metadata") or {}
        if metadata.get("deleted"):
            removed.append(str(item["resourceName"]))
            continue
        person = parse_person(item)
        people.append(person)
        renamed.extend(
            (str(old), person.resource_name)
            for old in metadata.get("previousResourceNames") or []
            if old
        )
    return Changes(people, removed, renamed)
