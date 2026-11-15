"""Connect, disconnect, choose calendars, sync now. Every route needs a
session: the gate lets nothing under /api/plugins/ through without one.

The OAuth callback is a session route too. Google sends the browser back
with a top-level GET, which carries the SameSite=Lax cookie, so the
callback knows who came back and checks it is who left.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from heycarl.access import cookie_names
from heycarl.identity import caller
from pydantic import BaseModel

from . import google, store
from .google import GoogleError
from .sync import HTTP_TIMEOUT, list_calendars

if TYPE_CHECKING:
    from heycarl.app import Kernel

    from .sync import Syncer

log = structlog.get_logger()

PLUGIN_PATH: Final = "/api/plugins/calendar"
CALLBACK_PATH: Final = PLUGIN_PATH + "/callback"
PAGE: Final = "/p/calendar/heycarl-calendar"
PENDING_SECONDS: Final = 600.0
PENDING_LIMIT: Final = 100


def redirect_uri(public_url: str) -> str:
    return public_url + CALLBACK_PATH


@dataclass(frozen=True, slots=True)
class Pending:
    sub: str
    session: str
    verifier: str = field(repr=False)
    expires: float


@dataclass
class Connects:
    """Connects in progress, by state. In memory: one kernel process, and a
    connect lost to a restart is started again."""

    _pending: dict[str, Pending] = field(default_factory=dict)

    def start(self, sub: str, session: str) -> tuple[str, str] | None:
        now = time.monotonic()
        self._pending = {k: p for k, p in self._pending.items() if p.expires > now}
        if len(self._pending) >= PENDING_LIMIT:
            return None
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        self._pending[state] = Pending(sub, session, verifier, now + PENDING_SECONDS)
        return state, verifier

    def finish(self, state: str) -> Pending | None:
        """Single use: a second callback with the same state finds nothing."""
        found = self._pending.pop(state, None)
        if found is None or found.expires < time.monotonic():
            return None
        return found


def session_id(request: Request, public_url: str) -> str:
    token = request.cookies.get(cookie_names(public_url).session, "")
    return hashlib.sha256(token.encode()).hexdigest()


def _kernel(request: Request) -> Kernel:
    kernel: Kernel = request.app.state.kernel
    return kernel


def _refuse(code: int, message: str) -> Response:
    return PlainTextResponse(
        message, status_code=code, headers={"cache-control": "no-store"}
    )


class Selection(BaseModel):
    selected: bool


def register_routes(
    router: APIRouter, settings: google.Settings | None, syncer: Syncer | None
) -> None:
    connects = Connects()

    @router.get("/status")
    async def status_(request: Request) -> dict[str, object]:
        caller(request)
        kernel = _kernel(request)
        return {
            "configured": settings is not None,
            "redirect_uri": redirect_uri(kernel.config.public_url),
        }

    def configured() -> tuple[google.Settings, Syncer]:
        if settings is None or syncer is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Google Calendar is not configured"
            )
        return settings, syncer

    @router.post("/connect")
    async def connect(request: Request) -> dict[str, str]:
        who = caller(request)
        conf, _ = configured()
        public_url = _kernel(request).config.public_url
        started = connects.start(who.sub, session_id(request, public_url))
        if started is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "too many connects; try soon"
            )
        state, verifier = started
        return {
            "url": google.authorization_url(
                conf, redirect_uri(public_url), state, verifier
            )
        }

    @router.get(CALLBACK_PATH.removeprefix(PLUGIN_PATH), include_in_schema=False)
    async def callback(
        request: Request,
        state: str | None = None,
        code: str | None = None,
        error: str | None = None,
    ) -> Response:
        who = caller(request)
        conf, sync = configured()
        kernel = _kernel(request)
        public_url = kernel.config.public_url
        pending = connects.finish(state) if state else None
        if pending is None:
            return _refuse(
                400, "This connect has expired or was never started. Start again."
            )
        # The state names who started; the cookie names who came back.
        if not (
            secrets.compare_digest(pending.sub, who.sub)
            and secrets.compare_digest(pending.session, session_id(request, public_url))
        ):
            log.warning("calendar_connect_mismatch", sub=who.sub)
            return _refuse(403, "This connect was started in another session.")
        if error or not code:
            return _refuse(400, f"Google did not connect: {error or 'no code'}.")
        async with sync.lock(who.sub), httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
            try:
                grant, refresh_token = await google.exchange_code(
                    http, conf, code, pending.verifier, redirect_uri(public_url)
                )

                async def get(path: str, params: dict[str, str]) -> dict[str, object]:
                    return await google.api_get(
                        http, conf, grant.access_token, path, params
                    )

                calendars = await list_calendars(get)
            except (GoogleError, httpx.HTTPError) as e:
                message = (
                    str(e) if isinstance(e, GoogleError) else "cannot reach Google"
                )
                log.warning("calendar_connect_failed", sub=who.sub, error=message)
                return _refuse(502, f"Connecting failed: {message}.")
            primary = next((c.google_id for c in calendars if c.is_primary), "")
            await store.connect_account(
                kernel,
                who.sub,
                primary,
                google.seal(conf.key, who.sub, refresh_token),
                grant.scope,
            )
            await store.put_calendars(kernel, who.sub, calendars)
            sync.remember(who.sub, grant)
        log.info("calendar_connected", sub=who.sub, calendars=len(calendars))
        sync.wake()
        return RedirectResponse(
            PAGE,
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"cache-control": "no-store"},
        )

    @router.post("/disconnect")
    async def disconnect(request: Request) -> dict[str, bool]:
        who = caller(request)
        conf, sync = configured()
        return await disconnect_owner(_kernel(request), conf, sync, who.sub)

    @router.post("/sync")
    async def sync_now(request: Request) -> dict[str, object]:
        who = caller(request)
        _, sync = configured()
        outcome = await sync.sync_now(_kernel(request), who.sub)
        if outcome is None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "synced a moment ago"
            )
        return {"ok": outcome.ok, "error": outcome.error, "events": outcome.events}

    @router.post("/calendars/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def choose(calendar_id: int, body: Selection, request: Request) -> None:
        who = caller(request)
        _, sync = configured()
        async with sync.lock(who.sub):
            found = await store.select_calendar(
                _kernel(request), who.sub, calendar_id, body.selected
            )
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such calendar")
        if body.selected:
            sync.wake()


async def disconnect_owner(
    kernel: Kernel, settings: google.Settings, syncer: Syncer, owner: str
) -> dict[str, bool]:
    """Revoke at Google, then delete the token and the cache, whatever Google
    answered: the person asked to be out. `revoked` says whether Google
    confirmed; if not, the grant stays listed in their Google account."""
    async with syncer.lock(owner):
        sealed = await store.refresh_token(kernel, owner)
        revoked = False
        if sealed is not None:
            try:
                token = google.unseal(settings.key, owner, sealed)
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
                    revoked = await google.revoke(http, settings, token)
            except Exception as e:
                log.warning(
                    "calendar_revoke_failed", owner=owner, error=type(e).__name__
                )
        syncer.forget(owner)
        removed = await store.disconnect(kernel, owner)
    log.info("calendar_disconnected", owner=owner, revoked=revoked)
    return {"disconnected": removed, "revoked": revoked}
