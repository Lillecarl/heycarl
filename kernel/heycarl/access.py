"""Who may reach which path. Every request passes here first.

Deny by default: a path is public or bearer-authenticated only when this
file says so, and every other path needs a browser session. A route added
anywhere else is therefore private until someone lists it here.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, urlsplit

from . import apppasswords, errors, sessions

if TYPE_CHECKING:
    from collections.abc import Iterable

    from psycopg_pool import AsyncConnectionPool
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class Access(enum.Enum):
    PUBLIC = "public"
    # The route checks an OAuth bearer token itself (MCP).
    BEARER = "bearer"
    # An app password over HTTP Basic, for CalDAV and CardDAV clients.
    DAV = "dav"
    SESSION = "session"


# Android's install service fetches the manifest and icons without cookies.
# A browser fetches the service worker's script without following a
# redirect, so a login redirect there means no worker and no push.
# /api/public/ is for webhooks, which check their own secret. MCP clients
# discover, register and trade codes for tokens with no session; the code
# itself comes from /authorize, which needs one.
PUBLIC_EXACT: Final = frozenset(
    {
        "/healthz",
        "/manifest.webmanifest",
        "/service-worker.js",
        "/auth/login",
        "/auth/callback",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/mcp",
        "/token",
        "/register",
        "/revoke",
    }
)
PUBLIC_PREFIXES: Final = ("/icons/", "/api/public/")
BEARER_EXACT: Final = frozenset({"/mcp"})
DAV_EXACT: Final = frozenset({"/dav", "/.well-known/caldav", "/.well-known/carddav"})
DAV_PREFIX: Final = "/dav/"
SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
DAV_READ_METHODS: Final = SAFE_METHODS | {"PROPFIND", "REPORT"}
DAV_REALM: Final = b'Basic realm="Hey Carl", charset="UTF-8"'
MAX_NEXT: Final = 2048


def access_for(path: str) -> Access | None:
    """None for a path no route may see: one with a `.` or `..` segment."""
    if any(segment in {".", ".."} for segment in path.split("/")):
        return None
    if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES):
        return Access.PUBLIC
    if path in BEARER_EXACT:
        return Access.BEARER
    if path in DAV_EXACT or path.startswith(DAV_PREFIX):
        return Access.DAV
    return Access.SESSION


def safe_next(value: str | None) -> str:
    """A path on this site to go back to after login, never another site."""
    if (
        not value
        or len(value) > MAX_NEXT
        or not value.startswith("/")
        or value.startswith(("//", "/\\"))
    ):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    return value


def same_origin(origin: str | None, public_url: str) -> bool:
    return origin is not None and origin.rstrip("/") == public_url


@dataclass(frozen=True, slots=True)
class CookieNames:
    session: str
    login: str
    secure: bool


def cookie_names(public_url: str) -> CookieNames:
    # __Host- cookies must be Secure, with Path=/ and no Domain: no other
    # host, subdomain or path can set or shadow them. Plain HTTP (the dev
    # environment) cannot hold Secure cookies, so it gets plain names.
    if public_url.startswith("https://"):
        return CookieNames("__Host-heycarl", "__Host-heycarl-login", secure=True)
    return CookieNames("heycarl", "heycarl-login", secure=False)


def set_cookie(names: CookieNames, name: str, value: str, max_age: int) -> str:
    attributes = [
        f"{name}={value}",
        "Path=/",
        f"Max-Age={max_age}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if names.secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def read_cookie(headers: list[tuple[bytes, bytes]], name: str) -> str | None:
    for key, value in headers:
        if key == b"cookie":
            jar = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except Exception:
                continue
            if name in jar:
                return jar[name].value
    return None


INLINE_SCRIPT: Final = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
# geolocation: the places page, this site only. The browser still asks the
# person first.
PERMISSIONS_POLICY: Final = (
    "camera=(), microphone=(), geolocation=(self), payment=(), usb=(),"
    " serial=(), bluetooth=(), display-capture=()"
)


def inline_script_hashes(html: str) -> list[str]:
    """CSP sources for the inline scripts in `html`: SvelteKit's bootstrap."""
    return [
        "'sha256-"
        + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        + "'"
        for body in INLINE_SCRIPT.findall(html)
    ]


def content_security_policy(
    script_hashes: Iterable[str] = (), form_targets: Iterable[str] = ()
) -> str:
    """No script but this site's files and the hashed ones.

    Styles allow inline: Svelte writes style attributes, and CSS alone does
    not run code. `form_targets` adds origins a form may reach, because
    Chrome checks form-action on the redirect after a POST too.
    """
    return "; ".join(
        [
            "default-src 'self'",
            " ".join(["script-src 'self'", *script_hashes]),
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "object-src 'none'",
            "base-uri 'none'",
            " ".join(["form-action 'self'", *form_targets]),
            "frame-ancestors 'none'",
        ]
    )


def security_headers(public_url: str, csp: str) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"same-origin"),
        (b"content-security-policy", csp.encode()),
        (b"permissions-policy", PERMISSIONS_POLICY.encode()),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]
    if public_url.startswith("https://"):
        headers.append((b"strict-transport-security", b"max-age=31536000"))
    return headers


def header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope["headers"]:
        if key == name:
            return value.decode("latin-1")
    return None


def client_address(scope: Scope) -> str:
    """Caddy appends the peer it saw, so the last entry is the one to trust."""
    forwarded = header(scope, b"x-forwarded-for")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()[:64]
    client = scope.get("client")
    return client[0] if client else "unknown"


class AccessMiddleware:
    """Pure ASGI: it never buffers a body, so MCP's event streams pass."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        public_url: str,
        script_hashes: Iterable[str] = (),
        allowed_emails: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        self.public_url = public_url
        self.allowed_emails = allowed_emails
        self.names = cookie_names(public_url)
        self.extra = security_headers(
            public_url, content_security_policy(script_hashes)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # No route takes a WebSocket, and this gate checks none: a plugin
        # router's websocket route would otherwise open with no session.
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [
                    *message.get("headers", []),
                    *(h for h in self.extra if h[0] not in present),
                ]
            await send(message)

        path: str = scope["path"]
        access = access_for(path)
        if access is None:
            await self._reply(send_with_headers, 400, "bad path")
            return
        if access is Access.DAV:
            await self._dav(scope, receive, send_with_headers)
            return
        if access is not Access.SESSION:
            await self.app(scope, receive, send_with_headers)
            return

        token = read_cookie(scope["headers"], self.names.session)
        # The kernel opens the pool at startup, after the middleware exists.
        pool: AsyncConnectionPool = scope["app"].state.pool
        session = await sessions.lookup(pool, token) if token else None
        # allowedEmails may have lost this address since the sign-in.
        if (
            token
            and session is not None
            and not sessions.admitted(session.email, self.allowed_emails)
        ):
            await sessions.delete(pool, token)
            session = None
        if session is None:
            if path.startswith("/api/") or scope["method"] not in SAFE_METHODS:
                await self._reply(send_with_headers, 401, "sign in")
            else:
                target = path + (
                    "?" + scope["query_string"].decode()
                    if scope["query_string"]
                    else ""
                )
                location = "/auth/login?next=" + quote(target, safe="")
                await self._reply(send_with_headers, 302, None, location=location)
            return
        # A browser sends Origin on every cross-origin request and on every
        # same-origin one that is not a GET or HEAD. SameSite=Lax already
        # keeps the cookie off cross-site POSTs; this holds without it.
        if scope["method"] not in SAFE_METHODS and not same_origin(
            header(scope, b"origin"), self.public_url
        ):
            await self._reply(send_with_headers, 403, "cross-origin request")
            return
        scope.setdefault("state", {})["session"] = session
        await self.app(scope, receive, send_with_headers)

    async def _dav(self, scope: Scope, receive: Receive, send: Send) -> None:
        """An app password, never a cookie (DESIGN.md "App passwords")."""
        origin = header(scope, b"origin")
        if (origin is not None and not same_origin(origin, self.public_url)) or header(
            scope, b"sec-fetch-site"
        ) == "cross-site":
            await self._reply(send, 403, "cross-origin request")
            return
        credentials = apppasswords.parse_basic(header(scope, b"authorization"))
        if credentials is None:
            await self._reply(send, 401, "app password needed", dav=True)
            return
        username, secret = credentials
        outcome, credential = await apppasswords.authenticate(
            scope["app"].state.kernel, username, secret, client_address(scope)
        )
        if outcome is apppasswords.Outcome.THROTTLED:
            await self._reply(send, 429, "too many failed attempts", retry_after=900)
            return
        # allowedEmails lives in the kernel's config, as for sessions.
        if credential is None or not sessions.admitted(
            credential.email, self.allowed_emails
        ):
            await self._reply(send, 401, "wrong app password", dav=True)
            return
        if (
            scope["method"] not in DAV_READ_METHODS
            and apppasswords.Scope.WRITE not in credential.scopes
        ):
            await self._reply(send, 403, "this app password is read-only")
            return
        scope.setdefault("state", {})["app_password"] = credential
        await self.app(scope, receive, send)

    @staticmethod
    async def _reply(
        send: Send,
        status: int,
        message: str | None,
        location: str | None = None,
        *,
        dav: bool = False,
        retry_after: int | None = None,
    ) -> None:
        content = b"" if message is None else errors.body(status, message)
        headers = [
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-store"),
        ]
        if location is not None:
            headers.append((b"location", location.encode()))
        if dav:
            headers.append((b"www-authenticate", DAV_REALM))
        if retry_after is not None:
            headers.append((b"retry-after", str(retry_after).encode()))
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": content})
