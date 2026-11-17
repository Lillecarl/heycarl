"""Google accounts: connect, disconnect, and access tokens for plugins
(DESIGN.md "Google accounts").

Every route needs a session. The OAuth callback is a session route too:
Google sends the browser back with a top-level GET, which carries the
SameSite=Lax cookie, so the callback knows who came back and checks it is
who left.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import anyio
import httpx
import structlog
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import google
from .access import cookie_names, safe_next
from .google import GoogleError, ReconnectNeeded, Unauthorized
from .identity import caller

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Mapping

    from .app import Kernel
    from .plugins import Plugin

log = structlog.get_logger()

PATH: Final = "/api/google"
CALLBACK_PATH: Final = PATH + "/callback"
SCOPE: Final = "core"
PENDING_SECONDS: Final = 600.0
PENDING_LIMIT: Final = 100
# Refresh a little early, so a token does not expire mid-sync.
ACCESS_MARGIN: Final = 60.0


def redirect_uri(public_url: str) -> str:
    return public_url + CALLBACK_PATH


def plugin_scopes(plugins: Iterable[Plugin]) -> dict[str, tuple[str, ...]]:
    return {
        p.name: tuple(p.manifest.google.scopes)
        for p in plugins
        if p.manifest.google is not None
    }


@dataclass(frozen=True, slots=True)
class Account:
    owner: str
    id: int
    email: str


class ApiError(GoogleError):
    """A Google API answered with an error. `reason` is Google's
    ErrorInfo reason or error status when it sent one."""

    def __init__(self, status_code: int, reason: str, path: str) -> None:
        super().__init__(f"Google answered {status_code} {reason} to {path}".strip())
        self.status_code = status_code
        self.reason = reason


def _reason(response: httpx.Response) -> str:
    try:
        answer = response.json()
    except ValueError:
        return ""
    error = answer.get("error") if isinstance(answer, dict) else None
    if not isinstance(error, dict):
        return ""
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            return str(detail["reason"])
    return str(error.get("status") or "")


@dataclass
class _Pending:
    sub: str
    session: str
    verifier: str = field(repr=False)
    next: str
    expires: float


@dataclass
class _Access:
    token: str = field(repr=False)
    expires: float


@dataclass
class GoogleSession:
    """HTTP to Google APIs as one account."""

    accounts: GoogleAccounts
    kernel: Kernel
    account: Account
    http: httpx.AsyncClient

    # Any: a Google API resource, parsed by the plugin.
    async def get(self, url: str, params: Mapping[str, str]) -> dict[str, Any]:
        for attempt in range(2):
            token = await self.accounts.access_token(
                self.kernel, self.http, self.account
            )
            r = await self.http.get(
                url,
                params=params,
                headers={
                    "authorization": f"Bearer {token}",
                    "accept": "application/json",
                },
            )
            if r.status_code == 401:
                # Expired or revoked early: one fresh token, then give up.
                self.accounts.forget(self.account.id)
                if attempt:
                    raise Unauthorized("Google refused the access token")
                continue
            if r.is_error:
                raise ApiError(r.status_code, _reason(r), httpx.URL(url).path)
            answer: dict[str, Any] = r.json()
            return answer
        raise RuntimeError("unreachable")


@dataclass
class GoogleAccounts:
    settings: google.Settings
    # Plugin name -> the scopes it needs.
    plugin_scopes: Mapping[str, tuple[str, ...]]
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _access: dict[int, _Access] = field(default_factory=dict)
    _locks: dict[int, anyio.Lock] = field(default_factory=dict)
    _listeners: list[Callable[[], None]] = field(default_factory=list)

    @property
    def scopes(self) -> tuple[str, ...]:
        wanted = {s for scopes in self.plugin_scopes.values() for s in scopes}
        return (*google.IDENTITY_SCOPES, *sorted(wanted))

    def api(self, name: str, default: str) -> str:
        return self.settings.endpoints.api(name, default)

    def on_connect(self, fn: Callable[[], None]) -> None:
        """`fn` runs after every connect, to wake a plugin's job."""
        self._listeners.append(fn)

    def lock(self, account_id: int) -> anyio.Lock:
        """Serializes a plugin's sync with disconnect, per account."""
        return self._locks.setdefault(account_id, anyio.Lock())

    def forget(self, account_id: int) -> None:
        self._access.pop(account_id, None)

    async def _rpc(self, kernel: Kernel, function: str, body: dict[str, object]) -> Any:
        # Any: the function's JSON answer.
        r = await kernel.as_worker(SCOPE, "POST", f"rpc/{function}", body=body)
        return r.json() if r.content else None

    async def accounts(
        self, kernel: Kernel, scopes: Iterable[str], owner: str | None = None
    ) -> list[Account]:
        """The accounts, everyone's or `owner`'s, that hold `scopes` and
        need no reconnect."""
        rows = await self._rpc(
            kernel, "google_accounts", {"scopes": sorted(scopes), "owner": owner}
        )
        return [
            Account(
                owner=str(r["owner_sub"]),
                id=int(r["account_id"]),
                email=str(r["email"]),
            )
            for r in rows or []
        ]

    async def _refresh_token(self, kernel: Kernel, account: Account) -> str:
        sealed = await self._rpc(
            kernel, "google_token", {"owner": account.owner, "account_id": account.id}
        )
        if sealed is None:
            raise ReconnectNeeded("this Google account has no token; connect again")
        try:
            return google.unseal(
                self.settings.key, account.owner, str(sealed).replace("\n", "")
            )
        except InvalidTag:
            # The token key changed: the stored token is lost for good.
            raise ReconnectNeeded("the token key changed; connect again") from None

    async def access_token(
        self, kernel: Kernel, http: httpx.AsyncClient, account: Account
    ) -> str:
        cached = self._access.get(account.id)
        if cached is not None and cached.expires - ACCESS_MARGIN > time.monotonic():
            return cached.token
        try:
            refresh_token = await self._refresh_token(kernel, account)
            grant = await google.refresh(http, self.settings, refresh_token)
        except ReconnectNeeded as e:
            # No token, a changed key, or Google refused it: only a connect helps.
            await self._rpc(
                kernel,
                "google_set_status",
                {
                    "owner": account.owner,
                    "account_id": account.id,
                    "error": str(e),
                    "needs_reconnect": True,
                },
            )
            raise
        self._remember(account.id, grant)
        if grant.refresh_token and grant.refresh_token != refresh_token:
            await self._rpc(
                kernel,
                "google_put_token",
                {
                    "owner": account.owner,
                    "account_id": account.id,
                    "sealed": google.seal(
                        self.settings.key, account.owner, grant.refresh_token
                    ),
                    "scopes": sorted(grant.scopes) if grant.scopes else None,
                },
            )
        return grant.access_token

    def _remember(self, account_id: int, grant: google.Grant) -> None:
        self._access[account_id] = _Access(
            grant.access_token, time.monotonic() + grant.expires_in
        )

    @asynccontextmanager
    async def session(
        self, kernel: Kernel, account: Account
    ) -> AsyncIterator[GoogleSession]:
        async with httpx.AsyncClient(timeout=google.HTTP_TIMEOUT) as http:
            yield GoogleSession(self, kernel, account, http)

    # The connect flow.

    def _start(self, pending: _Pending) -> str | None:
        now = time.monotonic()
        self._pending = {k: p for k, p in self._pending.items() if p.expires > now}
        if len(self._pending) >= PENDING_LIMIT:
            return None
        state = secrets.token_urlsafe(32)
        self._pending[state] = pending
        return state

    def _finish(self, state: str) -> _Pending | None:
        """Single use: a second callback with the same state finds nothing."""
        found = self._pending.pop(state, None)
        if found is None or found.expires < time.monotonic():
            return None
        return found

    async def disconnect(
        self, kernel: Kernel, owner: str, account_id: int
    ) -> dict[str, bool]:
        """Revoke at Google, then delete the account and, by cascade, every
        plugin's copy, whatever Google answered: the person asked to be
        out. `revoked` says whether Google confirmed."""
        async with self.lock(account_id):
            sealed = await self._rpc(
                kernel, "google_token", {"owner": owner, "account_id": account_id}
            )
            revoked = False
            if sealed is not None:
                try:
                    token = google.unseal(
                        self.settings.key, owner, str(sealed).replace("\n", "")
                    )
                    async with httpx.AsyncClient(timeout=google.HTTP_TIMEOUT) as http:
                        revoked = await google.revoke(http, self.settings, token)
                except Exception as e:
                    log.warning(
                        "google_revoke_failed", owner=owner, error=type(e).__name__
                    )
            self.forget(account_id)
            removed = bool(
                await self._rpc(
                    kernel,
                    "google_disconnect",
                    {"owner": owner, "account_id": account_id},
                )
            )
        log.info(
            "google_disconnected", owner=owner, account=account_id, revoked=revoked
        )
        return {"disconnected": removed, "revoked": revoked}


def _session_id(request: Request, public_url: str) -> str:
    token = request.cookies.get(cookie_names(public_url).session, "")
    return hashlib.sha256(token.encode()).hexdigest()


def _kernel(request: Request) -> Kernel:
    kernel: Kernel = request.app.state.kernel
    return kernel


def _refuse(code: int, message: str) -> Response:
    return PlainTextResponse(
        message, status_code=code, headers={"cache-control": "no-store"}
    )


class ConnectRequest(BaseModel):
    # Where the browser goes after the callback: a path on this site.
    next: str = Field(default="/", max_length=2000)
    # Connect this account again, or grant it more scopes.
    account_id: int | None = None


def router(service: GoogleAccounts | None) -> APIRouter:
    routes = APIRouter()

    def configured() -> GoogleAccounts:
        if service is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Google is not configured")
        return service

    @routes.get("")
    async def status_(request: Request) -> dict[str, object]:
        caller(request)
        return {
            "configured": service is not None,
            "redirect_uri": redirect_uri(_kernel(request).config.public_url),
            "plugins": {} if service is None else dict(service.plugin_scopes),
        }

    @routes.post("/connect")
    async def connect(body: ConnectRequest, request: Request) -> dict[str, str]:
        who = caller(request)
        conf = configured()
        kernel = _kernel(request)
        public_url = kernel.config.public_url
        hint = None
        if body.account_id is not None:
            rows = await conf._rpc(
                kernel,
                "google_account_of",
                {"owner": who.sub, "account_id": body.account_id},
            )
            if not rows:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "no such Google account")
            hint = str(rows[0]["email"]) or None
        verifier = secrets.token_urlsafe(48)
        state = conf._start(
            _Pending(
                sub=who.sub,
                session=_session_id(request, public_url),
                verifier=verifier,
                next=safe_next(body.next),
                expires=time.monotonic() + PENDING_SECONDS,
            )
        )
        if state is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "too many connects; try soon"
            )
        return {
            "url": google.authorization_url(
                conf.settings,
                redirect_uri(public_url),
                state,
                verifier,
                conf.scopes,
                login_hint=hint,
            )
        }

    @routes.get(CALLBACK_PATH.removeprefix(PATH), include_in_schema=False)
    async def callback(
        request: Request,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> Response:
        who = caller(request)
        conf = configured()
        kernel = _kernel(request)
        public_url = kernel.config.public_url
        pending = conf._finish(state) if state else None
        if pending is None:
            return _refuse(
                400, "This connect has expired or was never started. Start again."
            )
        # The state names who started; the cookie names who came back.
        if not (
            secrets.compare_digest(pending.sub, who.sub)
            and secrets.compare_digest(
                pending.session, _session_id(request, public_url)
            )
        ):
            log.warning("google_connect_mismatch", sub=who.sub)
            return _refuse(403, "This connect was started in another session.")
        if error or not code:
            return _refuse(400, f"Google did not connect: {error or 'no code'}.")
        try:
            async with httpx.AsyncClient(timeout=google.HTTP_TIMEOUT) as http:
                grant, refresh_token = await google.exchange_code(
                    http,
                    conf.settings,
                    code,
                    pending.verifier,
                    redirect_uri(public_url),
                )
                found = await google.identity(http, conf.settings, grant.access_token)
        except (GoogleError, httpx.HTTPError) as e:
            message = str(e) if isinstance(e, GoogleError) else "cannot reach Google"
            log.warning("google_connect_failed", sub=who.sub, error=message)
            return _refuse(502, f"Connecting failed: {message}.")
        account_id = int(
            await conf._rpc(
                kernel,
                "google_connect",
                {
                    "owner": who.sub,
                    "google_sub": found.sub,
                    "email": found.email,
                    "scopes": sorted(grant.scopes),
                    "sealed": google.seal(conf.settings.key, who.sub, refresh_token),
                },
            )
        )
        conf._remember(account_id, grant)
        log.info(
            "google_connected",
            sub=who.sub,
            account=account_id,
            scopes=len(grant.scopes),
        )
        for wake in conf._listeners:
            wake()
        return RedirectResponse(
            pending.next,
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"cache-control": "no-store"},
        )

    @routes.post("/accounts/{account_id}/disconnect")
    async def disconnect(account_id: int, request: Request) -> dict[str, bool]:
        who = caller(request)
        return await configured().disconnect(_kernel(request), who.sub, account_id)

    return routes
