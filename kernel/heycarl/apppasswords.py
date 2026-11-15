"""App passwords at the gate (DESIGN.md "App passwords").

PostgreSQL holds the rules (api/040_app_passwords.sql). The kernel only
parses the Basic header and asks heycarl.dav_authenticate, as heycarl_gate.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from . import migrations, postgrest

if TYPE_CHECKING:
    from .app import Kernel


class Scope(StrEnum):
    READ = "read"
    WRITE = "write"


class Outcome(StrEnum):
    OK = "ok"
    WRONG = "wrong"
    THROTTLED = "throttled"


@dataclass(frozen=True, slots=True)
class Credential:
    id: str
    sub: str
    email: str | None
    scopes: tuple[Scope, ...]


def parse_basic(header: str | None) -> tuple[str, str] | None:
    if header is None:
        return None
    kind, _, encoded = header.partition(" ")
    if kind.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


async def authenticate(
    kernel: Kernel, username: str, secret: str, address: str
) -> tuple[Outcome, Credential | None]:
    r = await kernel.data.post(
        "/rpc/dav_authenticate",
        headers=postgrest.request_headers(
            {"content-type": "application/json"},
            kernel.minter.gate(),
            migrations.API_SCHEMA,
        ),
        content=json.dumps(
            {"username": username, "secret": secret, "address": address}
        ),
    )
    r.raise_for_status()
    # object: PostgREST's JSON reply, read field by field below.
    answer: dict[str, object] = r.json()
    outcome = Outcome(str(answer["status"]))
    if outcome is not Outcome.OK:
        return outcome, None
    email = answer.get("email")
    scopes = answer["scopes"]
    if not isinstance(scopes, list):
        raise TypeError("dav_authenticate returned no scopes")
    return outcome, Credential(
        id=str(answer["id"]),
        sub=str(answer["sub"]),
        email=None if email is None else str(email),
        scopes=tuple(Scope(s) for s in scopes),
    )


async def purge(kernel: Kernel) -> None:
    r = await kernel.data.post(
        "/rpc/app_password_purge",
        headers=postgrest.request_headers(
            {"content-type": "application/json"},
            kernel.minter.worker(),
            migrations.API_SCHEMA,
        ),
        content="{}",
    )
    r.raise_for_status()
