"""Moves each important date's reminder on to its next occurrence.

Edits to a date, a contact's name or the time zone resync at once, through
triggers. Only the passing of a date has no trigger, so a job resyncs
everything on a clock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import anyio
import structlog

from .tools import register_tools

if TYPE_CHECKING:
    from heycarl.app import Kernel, PluginContext

log = structlog.get_logger()

SCOPE: Final = "crm"
# A reminder fires on its day; the next one only has to exist before next year.
SYNC_SECONDS: Final = 3600.0


def register(ctx: PluginContext) -> None:
    ctx.job(sync_forever)
    register_tools(ctx.mcp)


async def sync_forever(kernel: Kernel) -> None:
    while True:
        try:
            moved = await sync(kernel)
            if moved:
                log.info("crm_reminders_moved", count=moved)
        except Exception:
            # A database or PostgREST outage must not end the job for good.
            log.exception("crm_sync_failed")
        await anyio.sleep(SYNC_SECONDS)


async def sync(kernel: Kernel) -> int:
    """One pass. Returns how many dates got a new reminder."""
    r = await kernel.as_worker(SCOPE, "POST", "rpc/sync_reminders", body={})
    return int(r.json())
