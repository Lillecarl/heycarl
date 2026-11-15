"""Google Calendar into Hey Carl, read-only (DESIGN.md "Calendar").

Off unless the deploy names a Google client: the page then says so, the
reading tools find an empty cache, and no job runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from .google import settings_from_env
from .routes import register_routes
from .sync import Syncer
from .tools import register_tools

if TYPE_CHECKING:
    from heycarl.app import PluginContext

log = structlog.get_logger()


def register(ctx: PluginContext) -> None:
    settings = settings_from_env()
    syncer = None if settings is None else Syncer(settings)
    if syncer is not None:
        ctx.job(syncer.run_forever)
    else:
        log.info("calendar_not_configured")
    register_routes(ctx.router, settings, syncer)
    register_tools(ctx.mcp, syncer)
