from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from .sessions import Session


@dataclass(frozen=True, slots=True)
class Caller:
    sub: str
    email: str | None
    username: str | None


def caller(request: Request) -> Caller:
    """The signed-in person, from the session AccessMiddleware found.

    No request header names a person: a header is whatever the client sent.
    """
    session: Session | None = getattr(request.state, "session", None)
    if session is None:
        # A route on a public path that asked for a caller anyway.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in")
    return Caller(sub=session.sub, email=session.email, username=session.name)
