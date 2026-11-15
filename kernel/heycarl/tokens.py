from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import jwt

ALGORITHM: Final = "HS256"
# Long enough for one PostgREST request, short enough that a leaked one is
# worthless by the time anyone reads a log.
LIFETIME_SECONDS: Final = 60


class Role(StrEnum):
    USER = "heycarl_user"
    WORKER = "heycarl_worker"


class Via(StrEnum):
    """The path a request took, recorded in history as its actor.

    Only APP may purge history, so only a browser session's request may
    carry it.
    """

    APP = "app"


def via_mcp(client_id: str) -> str:
    return "mcp:" + client_id


@dataclass(frozen=True, slots=True)
class Minter:
    """Signs the tokens PostgREST trusts. Only the kernel holds the key."""

    secret: bytes

    def mint(
        self,
        role: Role,
        sub: str | None,
        via: str | None = None,
        now: float | None = None,
    ) -> str:
        issued = int(time.time() if now is None else now)
        claims: dict[str, str | int] = {
            "role": role,
            "iat": issued,
            "exp": issued + LIFETIME_SECONDS,
        }
        if sub is not None:
            claims["sub"] = sub
        if via is not None:
            claims["via"] = via
        return jwt.encode(claims, self.secret, algorithm=ALGORITHM)

    def user(self, sub: str, via: str) -> str:
        return self.mint(Role.USER, sub, via)

    def worker(self) -> str:
        return self.mint(Role.WORKER, None)
