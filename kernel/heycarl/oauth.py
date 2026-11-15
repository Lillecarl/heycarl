"""The OAuth 2.1 authorization server that MCP clients sign in through.

The SDK's handlers speak the protocol; this provider keeps their state in
heycarl_private (schema/migrations/0004_oauth.sql). `authorize()` sees no
request and so no session: it parks the request and sends the browser to
the consent page, where a signed-in person approves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from psycopg.types.json import Jsonb

from .sessions import new_token, token_hash

if TYPE_CHECKING:
    from collections.abc import Callable

    from psycopg import AsyncConnection
    from psycopg_pool import AsyncConnectionPool


class Scope(StrEnum):
    READ = "read"
    WRITE = "write"


SCOPES: Final[list[str]] = [Scope.READ, Scope.WRITE]
CONSENT_PATH: Final = "/oauth/consent"
MCP_PATH: Final = "/mcp"
REQUEST_LIFETIME: Final = timedelta(minutes=10)
CODE_LIFETIME: Final = timedelta(minutes=5)
ACCESS_LIFETIME: Final = timedelta(hours=1)
REFRESH_LIFETIME: Final = timedelta(days=30)
# From consent, however often the client refreshes; then the person consents again.
GRANT_LIFETIME: Final = timedelta(days=90)
# A client nobody consented to is dropped after this.
UNUSED_CLIENT_LIFETIME: Final = timedelta(days=1)
# /register is open to anyone. Clients nobody consented to stop at this
# many until the purge drops them: a flood fills no disk.
UNUSED_CLIENT_LIMIT: Final = 50
LOOPBACK_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


class Code(AuthorizationCode):
    grant_id: int


class Refresh(RefreshToken):
    grant_id: int


class Access(AccessToken):
    grant_id: int


class Kind(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def allowed_redirect(uri: str) -> bool:
    """HTTPS anywhere, or plain HTTP to this machine (RFC 8252 native apps).

    Never with user info: https://claude.ai@evil.example goes to evil.example
    but reads as claude.ai.
    """
    parts = urlsplit(uri)
    if parts.fragment or "@" in parts.netloc:
        return False
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS


def resources(public_url: str) -> frozenset[str]:
    """The RFC 8707 resource values that name this server."""
    return frozenset(
        {
            public_url,
            public_url + "/",
            public_url + MCP_PATH,
            public_url + MCP_PATH + "/",
        }
    )


def granted(offered: list[str], chosen: list[str]) -> list[str]:
    """What a person's choice grants: never more than offered, always read."""
    scopes = [s for s in SCOPES if s in offered and s in chosen]
    if Scope.READ not in scopes and scopes:
        scopes.insert(0, Scope.READ)
    return scopes


class Provider:
    def __init__(
        self, pool: Callable[[], AsyncConnectionPool], public_url: str
    ) -> None:
        # A callable: the kernel opens the pool in its lifespan, after the
        # routes that hold this provider exist.
        self._pool = pool
        self.public_url = public_url
        self.unused_client_limit = UNUSED_CLIENT_LIMIT

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._pool().connection() as conn:
            cur = await conn.execute(
                "select info from heycarl_private.oauth_client where client_id = %s",
                (client_id,),
            )
            row = await cur.fetchone()
        return (
            None if row is None else OAuthClientInformationFull.model_validate(row[0])
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not allowed_redirect(str(uri)):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "a redirect URI must be https, or http to a loopback address",
                )
        async with self._pool().connection() as conn:
            cur = await conn.execute(
                "insert into heycarl_private.oauth_client (client_id, info)"
                " select %s, %s where (select count(*)"
                "  from heycarl_private.oauth_client c where not exists"
                "  (select from heycarl_private.oauth_grant g"
                "   where g.client_id = c.client_id)) < %s",
                (
                    client_info.client_id,
                    Jsonb(client_info.model_dump(mode="json")),
                    self.unused_client_limit,
                ),
            )
        if cur.rowcount == 0:
            raise RegistrationError(
                "invalid_client_metadata",
                "too many apps wait for approval; try again tomorrow",
            )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource is not None and params.resource not in resources(
            self.public_url
        ):
            raise AuthorizeError("invalid_request", "unknown resource")
        request_id = new_token()
        async with self._pool().connection() as conn:
            await conn.execute(
                "insert into heycarl_private.oauth_request"
                " (id_hash, client_id, params, expires_at)"
                " values (%s, %s, %s, now() + %s)",
                (
                    token_hash(request_id),
                    client.client_id,
                    Jsonb(params.model_dump(mode="json")),
                    REQUEST_LIFETIME,
                ),
            )
        return self.public_url + CONSENT_PATH + "?" + urlencode({"request": request_id})

    async def pending(
        self, request_id: str
    ) -> tuple[OAuthClientInformationFull, AuthorizationParams] | None:
        async with self._pool().connection() as conn:
            cur = await conn.execute(
                "select c.info, r.params from heycarl_private.oauth_request r"
                " join heycarl_private.oauth_client c using (client_id)"
                " where r.id_hash = %s and r.expires_at > now()",
                (token_hash(request_id),),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return (
            OAuthClientInformationFull.model_validate(row[0]),
            AuthorizationParams.model_validate(row[1]),
        )

    async def decide(
        self, request_id: str, sub: str, chosen: list[str] | None
    ) -> str | None:
        """Where to send the browser: a code on approval, an error on denial.

        `chosen` None is a denial. None back means no such request, or it
        expired. The request is used up either way.
        """
        async with self._pool().connection() as conn:
            cur = await conn.execute(
                "delete from heycarl_private.oauth_request where id_hash = %s"
                " returning client_id, params, expires_at > now()",
                (token_hash(request_id),),
            )
            row = await cur.fetchone()
            if row is None or not row[2]:
                return None
            client_id: str = row[0]
            params = AuthorizationParams.model_validate(row[1])
            redirect = str(params.redirect_uri)
            scopes = granted(params.scopes or SCOPES, chosen or [])
            if not scopes:
                return construct_redirect_uri(
                    redirect, error="access_denied", state=params.state
                )
            cur = await conn.execute(
                "insert into heycarl_private.oauth_grant"
                " (client_id, sub, scopes, resource, expires_at)"
                " values (%s, %s, %s, %s, now() + %s) returning id",
                (client_id, sub, scopes, params.resource, GRANT_LIFETIME),
            )
            grant = await cur.fetchone()
            if grant is None:
                raise RuntimeError("insert returned no grant")
            code = new_token()
            await conn.execute(
                "insert into heycarl_private.oauth_code"
                " (code_hash, grant_id, code_challenge, redirect_uri,"
                "  redirect_uri_explicit, expires_at)"
                " values (%s, %s, %s, %s, %s, now() + %s)",
                (
                    token_hash(code),
                    grant[0],
                    params.code_challenge,
                    redirect,
                    params.redirect_uri_provided_explicitly,
                    CODE_LIFETIME,
                ),
            )
        return construct_redirect_uri(redirect, code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Code | None:
        async with self._pool().connection() as conn:
            cur = await conn.execute(
                "select g.id, g.client_id, g.sub, g.scopes, g.resource,"
                " c.code_challenge, c.redirect_uri, c.redirect_uri_explicit,"
                " extract(epoch from c.expires_at)::float8"
                " from heycarl_private.oauth_code c"
                " join heycarl_private.oauth_grant g on g.id = c.grant_id"
                " where c.code_hash = %s and g.client_id = %s"
                " and g.expires_at > now()",
                (token_hash(authorization_code), client.client_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Code(
            code=authorization_code,
            grant_id=row[0],
            client_id=row[1],
            subject=row[2],
            scopes=row[3],
            resource=row[4],
            code_challenge=row[5],
            redirect_uri=row[6],
            redirect_uri_provided_explicitly=row[7],
            expires_at=row[8],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: Code
    ) -> OAuthToken:
        async with self._pool().connection() as conn:
            # Two requests with one code: only one deletes the row.
            cur = await conn.execute(
                "delete from heycarl_private.oauth_code where code_hash = %s"
                " returning grant_id",
                (token_hash(authorization_code.code),),
            )
            if await cur.fetchone() is None:
                raise TokenError("invalid_grant", "authorization code already used")
            return await _issue(
                conn, authorization_code.grant_id, authorization_code.scopes
            )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Refresh | None:
        found = await self._load(refresh_token, Kind.REFRESH)
        if found is None:
            await self._end_if_spent(refresh_token)
            return None
        if found.client_id != client.client_id:
            return None
        return Refresh(
            token=found.token,
            grant_id=found.grant_id,
            client_id=found.client_id,
            scopes=found.scopes,
            expires_at=found.expires_at,
            subject=found.subject,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: Refresh,
        scopes: list[str],
    ) -> OAuthToken:
        async with self._pool().connection() as conn:
            cur = await conn.execute(
                "delete from heycarl_private.oauth_token"
                " where token_hash = %s and kind = %s returning expires_at",
                (token_hash(refresh_token.token), Kind.REFRESH),
            )
            row = await cur.fetchone()
            if row is None:
                raise TokenError("invalid_grant", "refresh token already used")
            await conn.execute(
                "insert into heycarl_private.oauth_spent"
                " (token_hash, grant_id, expires_at) values (%s, %s, %s)",
                (token_hash(refresh_token.token), refresh_token.grant_id, row[0]),
            )
            # Rotation: the grant's older access tokens end here too.
            await conn.execute(
                "delete from heycarl_private.oauth_token where grant_id = %s",
                (refresh_token.grant_id,),
            )
            return await _issue(conn, refresh_token.grant_id, scopes)

    async def load_access_token(self, token: str) -> Access | None:
        return await self._load(token, Kind.ACCESS)

    async def revoke_token(self, token: Access | Refresh) -> None:
        async with self._pool().connection() as conn:
            await conn.execute(
                "delete from heycarl_private.oauth_grant where id = %s",
                (token.grant_id,),
            )

    async def _end_if_spent(self, refresh_token: str) -> None:
        """A rotated refresh token came back: a thief or the client holds a
        copy, and the server cannot tell which. Both lose the grant."""
        async with self._pool().connection() as conn:
            await conn.execute(
                "delete from heycarl_private.oauth_grant where id in"
                " (select grant_id from heycarl_private.oauth_spent"
                "  where token_hash = %s)",
                (token_hash(refresh_token),),
            )

    async def _load(self, token: str, kind: Kind) -> Access | None:
        async with self._pool().connection() as conn:
            cur = await conn.execute(
                "select g.id, g.client_id, g.sub, t.scopes, g.resource,"
                " extract(epoch from t.expires_at)::bigint"
                " from heycarl_private.oauth_token t"
                " join heycarl_private.oauth_grant g on g.id = t.grant_id"
                " where t.token_hash = %s and t.kind = %s and t.expires_at > now()"
                " and g.expires_at > now()",
                (token_hash(token), kind),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Access(
            token=token,
            grant_id=row[0],
            client_id=row[1],
            subject=row[2],
            scopes=row[3],
            resource=row[4],
            expires_at=row[5],
        )


async def _issue(conn: AsyncConnection, grant_id: int, scopes: list[str]) -> OAuthToken:
    access, refresh = new_token(), new_token()
    for token, kind, lifetime in (
        (access, Kind.ACCESS, ACCESS_LIFETIME),
        (refresh, Kind.REFRESH, REFRESH_LIFETIME),
    ):
        await conn.execute(
            "insert into heycarl_private.oauth_token"
            " (token_hash, grant_id, kind, scopes, expires_at)"
            " select %s, id, %s, %s, least(now() + %s, expires_at)"
            " from heycarl_private.oauth_grant where id = %s",
            (token_hash(token), kind, scopes, lifetime, grant_id),
        )
    return OAuthToken(
        access_token=access,
        token_type="Bearer",
        expires_in=int(ACCESS_LIFETIME.total_seconds()),
        scope=" ".join(scopes),
        refresh_token=refresh,
    )


@dataclass(frozen=True, slots=True)
class Grant:
    id: int
    # Chosen by whoever registered the client; never proof of who it is.
    client_name: str | None
    redirect_hosts: list[str]
    scopes: list[str]
    created_at: datetime


async def grants(pool: AsyncConnectionPool, sub: str) -> list[Grant]:
    """The live connections `sub` approved, newest first."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select g.id, c.info, g.scopes, g.created_at"
            " from heycarl_private.oauth_grant g"
            " join heycarl_private.oauth_client c using (client_id)"
            " where g.sub = %s and g.expires_at > now()"
            " and exists (select from heycarl_private.oauth_token t"
            "   where t.grant_id = g.id and t.expires_at > now())"
            " order by g.created_at desc",
            (sub,),
        )
        rows = await cur.fetchall()
    result = []
    for grant_id, info, scopes, created_at in rows:
        client = OAuthClientInformationFull.model_validate(info)
        hosts = sorted({urlsplit(str(u)).netloc for u in client.redirect_uris or []})
        result.append(Grant(grant_id, client.client_name, hosts, scopes, created_at))
    return result


async def revoke_grant(pool: AsyncConnectionPool, sub: str, grant_id: int) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "delete from heycarl_private.oauth_grant where id = %s and sub = %s",
            (grant_id, sub),
        )
    return cur.rowcount > 0


async def purge(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        for table in (
            "oauth_request",
            "oauth_code",
            "oauth_token",
            "oauth_spent",
            "oauth_grant",
        ):
            await conn.execute(
                f"delete from heycarl_private.{table} where expires_at < now()"
            )
        await conn.execute(
            "delete from heycarl_private.oauth_grant g"
            " where not exists (select from heycarl_private.oauth_code c where c.grant_id = g.id)"
            " and not exists (select from heycarl_private.oauth_token t where t.grant_id = g.id)"
        )
        await conn.execute(
            "delete from heycarl_private.oauth_client c"
            " where c.created_at < now() - %s"
            " and not exists (select from heycarl_private.oauth_grant g where g.client_id = c.client_id)"
            " and not exists (select from heycarl_private.oauth_request r where r.client_id = c.client_id)",
            (UNUSED_CLIENT_LIFETIME,),
        )
