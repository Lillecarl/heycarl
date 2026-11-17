"""Choose calendars and sync now. Every route needs a session: the gate
lets nothing under /api/plugins/ through without one. Connecting and
disconnecting a Google account are the kernel's (/api/google)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from heycarl.identity import caller

from . import store

if TYPE_CHECKING:
    from heycarl.app import Kernel

    from .sync import Syncer


def _kernel(request: Request) -> Kernel:
    kernel: Kernel = request.app.state.kernel
    return kernel


class Selection(BaseModel):
    selected: bool


def register_routes(router: APIRouter, syncer: Syncer | None) -> None:
    def configured() -> Syncer:
        if syncer is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Google is not configured")
        return syncer

    @router.get("/status")
    async def status_(request: Request) -> dict[str, object]:
        caller(request)
        return {"configured": syncer is not None}

    @router.post("/sync")
    async def sync_now(request: Request) -> dict[str, object]:
        who = caller(request)
        outcome = await configured().sync_now(_kernel(request), who.sub)
        if outcome is None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "synced a moment ago"
            )
        return {"ok": outcome.ok, "error": outcome.error, "events": outcome.events}

    @router.post("/calendars/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def choose(calendar_id: int, body: Selection, request: Request) -> None:
        who = caller(request)
        sync = configured()
        kernel = _kernel(request)
        account_id = await store.calendar_account(kernel, who.sub, calendar_id)
        if account_id is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such calendar")
        async with sync.google.lock(account_id):
            found = await store.select_calendar(
                kernel, who.sub, calendar_id, body.selected
            )
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such calendar")
        if body.selected:
            sync.wake()
