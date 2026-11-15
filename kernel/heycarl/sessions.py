"""Browser sessions and logins in progress, in heycarl_private.

The browser holds a random token; the database holds its SHA-256. Both
tables are the kernel's alone (see migrations/0003_session.sql).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from .oidc import Identity

SESSION_LIFETIME: Final = timedelta(days=30)
LOGIN_LIFETIME: Final = timedelta(minutes=10)
TOKEN_BYTES: Final = 32


def admitted(email: str | None, allowed: frozenset[str]) -> bool:
    """Whether allowedEmails lets `email` in. Empty lets in everyone."""
    return not allowed or email in allowed


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Session:
    sub: str
    email: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class Login:
    state: str
    nonce: str
    code_verifier: str
    next: str


async def start_login(pool: AsyncConnectionPool, next_path: str) -> Login:
    login = Login(
        state=new_token(),
        nonce=new_token(),
        code_verifier=new_token(),
        next=next_path,
    )
    async with pool.connection() as conn:
        await conn.execute(
            "insert into heycarl_private.login"
            " (state_hash, nonce, code_verifier, next, expires_at)"
            " values (%s, %s, %s, %s, now() + %s)",
            (
                token_hash(login.state),
                login.nonce,
                login.code_verifier,
                login.next,
                LOGIN_LIFETIME,
            ),
        )
    return login


async def finish_login(pool: AsyncConnectionPool, state: str) -> Login | None:
    """The login for `state`, used up: a second callback with it finds nothing."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "delete from heycarl_private.login where state_hash = %s"
            " returning nonce, code_verifier, next, expires_at > now()",
            (token_hash(state),),
        )
        row = await cur.fetchone()
    if row is None or not row[3]:
        return None
    return Login(state=state, nonce=row[0], code_verifier=row[1], next=row[2])


async def create(pool: AsyncConnectionPool, identity: Identity) -> str:
    token = new_token()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into heycarl_private.session"
            " (token_hash, sub, email, name, expires_at)"
            " values (%s, %s, %s, %s, now() + %s)",
            (
                token_hash(token),
                identity.sub,
                identity.email,
                identity.name,
                SESSION_LIFETIME,
            ),
        )
    return token


async def lookup(pool: AsyncConnectionPool, token: str) -> Session | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select sub, email, name from heycarl_private.session"
            " where token_hash = %s and expires_at > now()",
            (token_hash(token),),
        )
        row = await cur.fetchone()
    return None if row is None else Session(sub=row[0], email=row[1], name=row[2])


async def delete(pool: AsyncConnectionPool, token: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "delete from heycarl_private.session where token_hash = %s",
            (token_hash(token),),
        )


async def purge(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "delete from heycarl_private.session where expires_at < now()"
        )
        await conn.execute("delete from heycarl_private.login where expires_at < now()")
