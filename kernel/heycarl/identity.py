from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fastapi import HTTPException, Request, status

# Set by the oauth2-proxy gate (`--pass-user-headers`). Trusted because the
# kernel listens on a unix socket only the gate's group can open; a request
# that reached the kernel came through the gate.
USER_HEADER: Final = "x-forwarded-user"
EMAIL_HEADER: Final = "x-forwarded-email"
NAME_HEADER: Final = "x-forwarded-preferred-username"


@dataclass(frozen=True, slots=True)
class Caller:
    sub: str
    email: str | None
    username: str | None


def caller(request: Request) -> Caller:
    sub = request.headers.get(USER_HEADER)
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no identity from the gate")
    return Caller(
        sub=sub,
        email=request.headers.get(EMAIL_HEADER),
        username=request.headers.get(NAME_HEADER),
    )
