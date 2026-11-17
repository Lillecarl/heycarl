"""Google contacts into the cache, one Google account at a time.

The first sync, and one a day after it, lists every contact and asks for a
sync token. Between those, the token brings only changes, deletions as a
bare person with metadata.deleted. Google ends a token 7 days after its
full sync, so the daily full sync also keeps it alive. An expired token
answers 410 (EXPIRED_SYNC_TOKEN), and the sync starts over in full.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import anyio
import httpx
import structlog

from heycarl.google import GoogleError
from heycarl.googleaccounts import ApiError

from . import people

if TYPE_CHECKING:
    from heycarl.app import Kernel
    from heycarl.googleaccounts import Account, GoogleAccounts, GoogleSession

log = structlog.get_logger()

SCOPE: Final = "google_contacts"
SYNC_SECONDS: Final = 600.0
FULL_SYNC_EVERY: Final = timedelta(days=1)
EXPIRED: Final = "EXPIRED_SYNC_TOKEN"
MANUAL_SYNC_SECONDS = 10.0


def expired(e: ApiError) -> bool:
    return e.status_code == 410 or e.reason == EXPIRED


@dataclass(frozen=True, slots=True)
class Outcome:
    ok: bool
    error: str | None = None
    contacts: int = 0


def combine(outcomes: list[Outcome]) -> Outcome:
    errors = [o.error for o in outcomes if o.error is not None]
    return Outcome(
        ok=all(o.ok for o in outcomes),
        error="; ".join(errors) or None,
        contacts=sum(o.contacts for o in outcomes),
    )


async def _rpc(kernel: Kernel, function: str, body: dict[str, object]) -> Any:
    # Any: the function's JSON answer.
    r = await kernel.as_worker(SCOPE, "POST", f"rpc/{function}", body=body)
    return r.json() if r.content else None


async def pages(
    session: GoogleSession, url: str, params: dict[str, str]
) -> tuple[list[dict[str, Any]], str | None]:
    """Every connection, and the sync token, which only the last page carries."""
    items: list[dict[str, Any]] = []
    page: str | None = None
    for _ in range(people.MAX_PAGES):
        answer = await session.get(
            url,
            {
                **params,
                "personFields": people.PERSON_FIELDS,
                "pageSize": str(people.PAGE_SIZE),
            }
            | ({"pageToken": page} if page else {}),
        )
        found = answer.get("connections")
        if isinstance(found, list):
            items.extend(i for i in found if isinstance(i, dict))
        next_page = answer.get("nextPageToken")
        if not next_page:
            token = answer.get("nextSyncToken")
            return items, None if token is None else str(token)
        page = str(next_page)
    raise GoogleError("Google sent too many pages")


@dataclass
class Syncer:
    google: GoogleAccounts
    _wake: anyio.Event = field(default_factory=anyio.Event)
    _manual: dict[str, float] = field(default_factory=dict)

    def wake(self) -> None:
        self._wake.set()

    async def run_forever(self, kernel: Kernel) -> None:
        # PostgREST may still be starting with the kernel; a connect wakes
        # this early.
        while True:
            with anyio.move_on_after(SYNC_SECONDS):
                await self._wake.wait()
            self._wake = anyio.Event()
            try:
                accounts = await self.google.accounts(kernel, [people.SCOPE])
            except Exception:
                log.exception("contacts_accounts_failed")
                continue
            for account in accounts:
                await self.sync_account(kernel, account)

    async def sync_now(self, kernel: Kernel, owner: str) -> Outcome | None:
        """A sync of every account the person asked for; None when they
        asked a moment ago. Each one costs Google quota."""
        now = time.monotonic()
        if now - self._manual.get(owner, -MANUAL_SYNC_SECONDS) < MANUAL_SYNC_SECONDS:
            return None
        self._manual[owner] = now
        accounts = await self.google.accounts(kernel, [people.SCOPE], owner)
        if not accounts:
            return Outcome(ok=False, error="not connected")
        return combine([await self.sync_account(kernel, a) for a in accounts])

    async def sync_account(self, kernel: Kernel, account: Account) -> Outcome:
        """Never raises: a failure is the account's last_error."""
        async with self.google.lock(account.id):
            try:
                outcome = await self._sync(kernel, account)
            except GoogleError as e:
                outcome = Outcome(ok=False, error=str(e))
            except httpx.HTTPStatusError as e:
                # Only the kernel's own PostgREST calls raise for status.
                log.exception(
                    "contacts_store_failed",
                    owner=account.owner,
                    body=e.response.text[:500],
                )
                outcome = Outcome(ok=False, error="the sync failed; see the kernel log")
            except httpx.HTTPError as e:
                # Its message may hold a URL; the type is enough.
                outcome = Outcome(
                    ok=False, error=f"cannot reach Google ({type(e).__name__})"
                )
            except Exception:
                log.exception("contacts_sync_failed", owner=account.owner)
                outcome = Outcome(ok=False, error="the sync failed; see the kernel log")
            if outcome.error is not None:
                log.warning(
                    "contacts_sync_error",
                    owner=account.owner,
                    account=account.id,
                    error=outcome.error,
                )
            try:
                await _rpc(
                    kernel,
                    "set_status",
                    {
                        "owner": account.owner,
                        "account_id": account.id,
                        "error": outcome.error,
                    },
                )
            except Exception:
                log.exception("contacts_status_failed", owner=account.owner)
            return outcome

    async def _sync(self, kernel: Kernel, account: Account) -> Outcome:
        url = self.google.api(people.API_NAME, people.API_URL) + people.CONNECTIONS
        rows = await _rpc(
            kernel, "sync_token", {"owner": account.owner, "account_id": account.id}
        )
        state = rows[0] if rows else {}
        token = state.get("sync_token")
        full_at = state.get("full_sync_at")
        full = (
            token is None
            or full_at is None
            or datetime.now(UTC) - datetime.fromisoformat(str(full_at))
            >= FULL_SYNC_EVERY
        )
        async with self.google.session(kernel, account) as session:
            items: list[dict[str, Any]] = []
            next_token: str | None = None
            if not full:
                try:
                    items, next_token = await pages(
                        session,
                        url,
                        {"syncToken": str(token), "requestSyncToken": "true"},
                    )
                except ApiError as e:
                    if not expired(e):
                        raise
                    log.info("contacts_sync_token_expired", owner=account.owner)
                    full = True
            if full:
                items, next_token = await pages(
                    session, url, {"requestSyncToken": "true"}
                )
        changes = people.split(items)
        kept = await _rpc(
            kernel,
            "apply_people",
            {
                "owner": account.owner,
                "account_id": account.id,
                "full_sync": full,
                "people": [p.row for p in changes.people],
                "removed": changes.removed,
                "renamed": [{"from": a, "to": b} for a, b in changes.renamed],
                "sync_token": next_token,
            },
        )
        return Outcome(ok=True, contacts=int(kept))
