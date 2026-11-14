"""Demo data for the dev environment, through the API as the `demo` user.

Signs in through dex like a browser (heycarl-login), then writes with that
session. Dates are relative to today, so "coming up" and "time to get in
touch" always have something to show. Runs once: a second run adds a
second set.
"""

import json
import os
import subprocess
import urllib.request
from datetime import UTC, date, datetime, timedelta

BASE = "http://127.0.0.1:8080/api/data"
PUBLIC_URL = os.environ["HEYCARL_PUBLIC_URL"]
COOKIE = subprocess.run(
    ["heycarl-login", "demo@example.com"], check=True, capture_output=True, text=True
).stdout.strip()


def call(path, body=None, method=None):
    request = urllib.request.Request(
        f"{BASE}/{path}",
        data=None if body is None else json.dumps(body).encode(),
        method=method or ("GET" if body is None else "POST"),
        headers={
            "content-type": "application/json",
            "prefer": "return=representation",
            "cookie": COOKIE,
            "origin": PUBLIC_URL,
        },
    )
    with urllib.request.urlopen(request) as response:
        text = response.read()
    return json.loads(text) if text else None


def ago(days):
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


today = date.today()


def soon(days):
    return today + timedelta(days=days)


PEOPLE = [
    # first, last, nickname, company, job, favorite, stay_in_touch_days
    ("Anna", "Lindqvist", "Annie", "", "", True, 14),
    ("Björn", "Lindqvist", "", "Volvo Cars", "Engineer", False, None),
    ("Cecilia", "Ek", "", "Klarna", "Product manager", True, 30),
    ("David", "Okafor", "Dave", "Spotify", "Backend developer", False, 60),
    ("Elin", "Berg", "", "", "", False, None),
    ("Farid", "Haddad", "", "Region Stockholm", "Nurse", False, 21),
    ("Greta", "Lindqvist", "Mormor", "", "", True, 7),
    ("Hugo", "Nyström", "", "", "", False, None),
]

ids = {}
for first, last, nick, company, job, favorite, touch in PEOPLE:
    [row] = call(
        "crm/contact",
        {
            "first_name": first,
            "last_name": last,
            "nickname": nick,
            "company": company,
            "job_title": job,
            "favorite": favorite,
            "stay_in_touch_days": touch,
        },
    )
    ids[first] = row["id"]

call(
    "crm/contact?first_name=eq.Anna",
    {"description": "Met at the climbing gym in 2019. Vegetarian, loves board games."},
    "PATCH",
)

fields = [
    ("Anna", "phone", "mobile", "+46 70 123 45 67"),
    ("Anna", "email", "", "anna@example.com"),
    ("Anna", "address", "home", "Götgatan 12, Stockholm"),
    ("Björn", "phone", "work", "+46 31 555 12 34"),
    ("Cecilia", "email", "work", "cecilia@example.org"),
    ("Cecilia", "website", "", "https://example.org/cecilia"),
    ("David", "social", "mastodon", "https://mastodon.social/@dave"),
    ("Greta", "phone", "landline", "+46 8 765 43 21"),
]
for who, kind, label, value in fields:
    call(
        "crm/contact_field",
        {"contact_id": ids[who], "kind": kind, "label": label, "value": value},
    )

dates = [
    ("Anna", "birthday", "", soon(3), 1991),
    ("Greta", "birthday", "", soon(12), 1941),
    ("Björn", "birthday", "", soon(40), None),
    ("Anna", "anniversary", "with Björn", soon(25), 2016),
    ("Cecilia", "birthday", "", soon(-20), 1988),
    ("Hugo", "memorial", "", soon(0), 2020),
]
for who, kind, label, on, year in dates:
    call(
        "crm/important_date",
        {
            "contact_id": ids[who],
            "kind": kind,
            "label": label,
            "month": on.month,
            "day": on.day,
            "year": year,
        },
    )

relationships = [
    ("Anna", "Björn", "spouse"),
    ("Anna", "Greta", "grandparent"),
    ("Cecilia", "David", "colleague"),
    ("Anna", "Cecilia", "best friend"),
]
for a, b, kind in relationships:
    call("crm/relationship", {"contact_id": ids[a], "other_id": ids[b], "kind": kind})

for name, people in [
    ("family", ["Anna", "Björn", "Greta"]),
    ("work", ["Cecilia", "David"]),
    ("climbing", ["Anna", "Elin"]),
]:
    [tag] = call("crm/tag", {"name": name})
    for who in people:
        call("crm/contact_tag", {"contact_id": ids[who], "tag_id": tag["id"]})

activities = [
    (["Anna", "Björn"], "meal", "Dinner at their place", 4),
    (["Cecilia"], "call", "Talked about her new job", 45),
    (["Greta"], "visit", "Coffee and cinnamon buns", 10),
    (["David", "Cecilia"], "meeting", "After-work at Mosebacke", 90),
    (["Farid"], "message", "Congratulated him on the exam", 30),
]
for people, kind, title, days in activities:
    call(
        "crm/rpc/log_activity",
        {
            "contact_ids": [ids[p] for p in people],
            "title": title,
            "kind": kind,
            "happened_at": ago(days),
        },
    )

call(
    "crm/note",
    {
        "contact_id": ids["Anna"],
        "body": "Wants to try the new bouldering place in Hammarby.",
    },
)
call(
    "crm/note",
    {"contact_id": ids["Greta"], "body": "Hip surgery went well. Call on Sundays."},
)
call(
    "crm/gift",
    {"contact_id": ids["Anna"], "title": "Wingspan expansion", "state": "idea"},
)
call("crm/gift", {"contact_id": ids["Greta"], "title": "Photo book", "state": "bought"})

call("todos/todo", {"title": "Book a table for Anna's birthday"})
call("todos/todo", {"title": "Renew passport"})

print(f"seeded {len(ids)} contacts, {len(call('reminders/reminder'))} reminders")
