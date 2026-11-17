"""Sync now. Every route needs a session: the gate lets nothing under
/api/plugins/ through without one. Links and copies are PostgREST writes as
the person (/api/data/google_contacts); accounts are the kernel's
(/api/google)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, status

from heycarl.identity import caller

if TYPE_CHECKING:
    from heycarl.app import Kernel

    from .sync import Syncer


def _kernel(request: Request) -> Kernel:
    kernel: Kernel = request.app.state.kernel
    return kernel


def register_routes(router: APIRouter, syncer: Syncer | None) -> None:
    @router.get("/status")
    async def status_(request: Request) -> dict[str, object]:
        caller(request)
        return {"configured": syncer is not None}

    @router.post("/sync")
    async def sync_now(request: Request) -> dict[str, object]:
        who = caller(request)
        if syncer is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Google is not configured")
        outcome = await syncer.sync_now(_kernel(request), who.sub)
        if outcome is None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "synced a moment ago"
            )
        return {"ok": outcome.ok, "error": outcome.error, "contacts": outcome.contacts}
