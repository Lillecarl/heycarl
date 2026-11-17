"""Google Calendar into Hey Carl, read-only (DESIGN.md "Calendar").

Off unless the deploy names a Google client: the page then says so, the
reading tools find an empty cache, and no job runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from .routes import register_routes
from .sync import Syncer
from .tools import register_tools

if TYPE_CHECKING:
    from heycarl.app import PluginContext

log = structlog.get_logger()


def register(ctx: PluginContext) -> None:
    syncer = None if ctx.google is None else Syncer(ctx.google)
    if syncer is not None:
        syncer.google.on_connect(syncer.wake)
        ctx.job(syncer.run_forever)
    else:
        log.info("calendar_not_configured")
    register_routes(ctx.router, syncer)
    register_tools(ctx.mcp, syncer)
