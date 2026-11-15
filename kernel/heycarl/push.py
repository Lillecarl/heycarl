from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

import aiohttp
import structlog
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pydantic import BaseModel
from pywebpush import WebPushException, webpush_async

if TYPE_CHECKING:
    import ssl
    from pathlib import Path

    import httpx

    from .tokens import Minter

log = structlog.get_logger()

# A push service keeps an undelivered message this long, for a phone that is
# off or offline.
TTL_SECONDS: Final = 6 * 3600
# The push service answers these for a subscription that will never work again.
GONE: Final = frozenset({404, 410})
# The browsers' push services: Chrome and Android, Firefox, Safari, Edge.
# A subscription elsewhere would make the kernel POST, with each reminder's
# text, to whatever URL the row names.
PUSH_SERVICE_HOSTS: Final = frozenset(
    {
        "fcm.googleapis.com",
        "push.services.mozilla.com",
        "web.push.apple.com",
        "notify.windows.com",
    }
)


def is_push_service(endpoint: str, hosts: frozenset[str]) -> bool:
    """`endpoint` is https on one of `hosts` or a subdomain of one."""
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    return (
        parts.scheme == "https"
        and parts.username is None
        and any(host == h or host.endswith("." + h) for h in hosts)
    )


class Payload(BaseModel):
    """What the service worker shows (frontend/src/service-worker.ts)."""

    title: str
    body: str | None = None
    url: str | None = None
    tag: str | None = None


class Subscription(BaseModel):
    id: int
    owner_sub: str
    endpoint: str
    p256dh: str
    auth: str
    failures: int


def load_or_create_key(path: Path) -> Vapid02:
    if path.exists():
        return Vapid02.from_file(str(path))
    key = Vapid02()
    key.generate_keys()
    path.parent.mkdir(parents=True, exist_ok=True)
    key.save_key(str(path))
    path.chmod(0o600)
    return key


def application_server_key(key: Vapid02) -> str:
    """The public key as `pushManager.subscribe` takes it: base64url, raw point."""
    raw = key.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class Pusher:
    key: Vapid02
    # VAPID `sub`: how a push service reaches the sender, a mailto: or https URL.
    subject: str
    data: httpx.AsyncClient
    minter: Minter
    # TLS for the push services. Tests swap in a context that trusts their
    # own endpoint's certificate.
    tls: ssl.SSLContext | bool = True
    # Tests add their own endpoint's host.
    hosts: frozenset[str] = PUSH_SERVICE_HOSTS

    async def _subscriptions(self, owner: str) -> list[Subscription]:
        r = await self.data.get(
            "/push_subscription",
            params={"owner_sub": f"eq.{owner}"},
            headers=self._headers(),
        )
        r.raise_for_status()
        return [Subscription.model_validate(row) for row in r.json()]

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.minter.worker()}",
            "accept-profile": "heycarl",
            "content-profile": "heycarl",
            "content-type": "application/json",
        }

    async def _update(self, sub: Subscription, fields: dict[str, int | str]) -> None:
        r = await self.data.patch(
            "/push_subscription",
            params={"id": f"eq.{sub.id}"},
            headers=self._headers(),
            content=json.dumps(fields),
        )
        r.raise_for_status()

    async def send(self, owner: str, payload: Payload) -> int:
        """Push to every device of `owner`. Returns how many accepted it."""
        delivered = 0
        body = payload.model_dump_json(exclude_none=True)
        connector = aiohttp.TCPConnector(ssl=self.tls)
        async with aiohttp.ClientSession(connector=connector) as session:
            for sub in await self._subscriptions(owner):
                if not is_push_service(sub.endpoint, self.hosts):
                    log.warning("push_refused_endpoint", subscription=sub.id)
                    continue
                if await self._send_one(session, sub, body):
                    delivered += 1
        return delivered

    async def _send_one(
        self, session: aiohttp.ClientSession, sub: Subscription, body: str
    ) -> bool:
        info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            await webpush_async(
                info,
                data=body,
                vapid_private_key=self.key,
                vapid_claims={"sub": self.subject},
                ttl=TTL_SECONDS,
                aiohttp_session=session,
            )
        except WebPushException as e:
            status = e.response.status if e.response is not None else None
            log.warning("push_failed", subscription=sub.id, status=status, error=str(e))
            if status in GONE:
                r = await self.data.delete(
                    "/push_subscription",
                    params={"id": f"eq.{sub.id}"},
                    headers=self._headers(),
                )
                r.raise_for_status()
            else:
                await self._update(sub, {"failures": sub.failures + 1})
            return False
        except aiohttp.ClientError as e:
            log.warning("push_unreachable", subscription=sub.id, error=str(e))
            return False
        now = datetime.now(UTC).isoformat()
        await self._update(sub, {"last_success_at": now, "failures": 0})
        return True
