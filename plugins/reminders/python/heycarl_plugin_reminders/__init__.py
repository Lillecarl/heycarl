"""Delivers due reminders by web push.

Runs as heycarl_worker inside the kernel. A pass claims due rows through
`claim_due`, pushes each to its owner's devices and records the outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import anyio
import structlog
from heycarl.push import Payload
from pydantic import BaseModel

if TYPE_CHECKING:
    from heycarl.app import Kernel, PluginContext

log = structlog.get_logger()

SCOPE: Final = "reminders"
PAGE: Final = "/p/reminders/heycarl-reminders"
MAX_ATTEMPTS: Final = 5
# The longest a pass waits. A reminder created or moved earlier than the one
# the loop sleeps towards waits at most this long; nothing in the database
# announces that a time has come, so the loop has to wake on a clock.
MAX_SLEEP_SECONDS: Final = 30.0


class Reminder(BaseModel):
    id: int
    owner_sub: str
    title: str
    body: str
    url: str | None
    attempts: int


def register(ctx: PluginContext) -> None:
    ctx.job(deliver_forever)


def sleep_seconds(next_due: datetime | None, now: datetime) -> float:
    if next_due is None:
        return MAX_SLEEP_SECONDS
    return min(MAX_SLEEP_SECONDS, max(0.0, (next_due - now).total_seconds()))


async def deliver_forever(kernel: Kernel) -> None:
    while True:
        try:
            await deliver_due(kernel)
            r = await kernel.as_worker(SCOPE, "POST", "rpc/next_due", body={})
            raw = r.json()
            next_due = datetime.fromisoformat(raw) if raw else None
        except Exception:
            # A database or PostgREST outage must not end delivery for good.
            log.exception("reminders_pass_failed")
            next_due = None
        await anyio.sleep(sleep_seconds(next_due, datetime.now(UTC)))


async def deliver_due(kernel: Kernel) -> int:
    """One pass. Returns how many reminders reached a device."""
    r = await kernel.as_worker(SCOPE, "POST", "rpc/claim_due", body={})
    sent = 0
    for reminder in (Reminder.model_validate(row) for row in r.json()):
        payload = Payload(
            title=reminder.title,
            body=reminder.body or None,
            url=reminder.url or PAGE,
            tag=f"reminder-{reminder.id}",
        )
        delivered = await kernel.pusher.send(reminder.owner_sub, payload)
        where = {"id": f"eq.{reminder.id}"}
        if delivered:
            sent += 1
            await kernel.as_worker(
                SCOPE,
                "PATCH",
                "reminder",
                params=where,
                body={"state": "sent", "sent_at": datetime.now(UTC).isoformat()},
            )
        else:
            given_up = reminder.attempts >= MAX_ATTEMPTS
            await kernel.as_worker(
                SCOPE,
                "PATCH",
                "reminder",
                params=where,
                body={
                    "state": "failed" if given_up else "pending",
                    "last_error": "no device accepted the notification",
                },
            )
        log.info("reminder_delivery", id=reminder.id, devices=delivered)
    return sent
