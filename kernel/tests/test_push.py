"""Web push and reminders against a local push service over TLS."""

from __future__ import annotations

import base64
import importlib
import os
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import anyio
import pytest
import trustme
from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .conftest import as_user, needs_database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx
    from heycarl.app import Kernel

pytestmark = [pytest.mark.anyio, needs_database]


@dataclass
class PushService:
    """Accepts pushes at /push/<name>, and answers 410 at /push/gone."""

    base: str
    received: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    arrived: anyio.Event = field(default_factory=anyio.Event)

    async def handle(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        await request.read()
        self.received.append((name, dict(request.headers)))
        self.arrived.set()
        return web.Response(status=410 if name == "gone" else 201)


@pytest.fixture
async def push_service(kernel: Kernel) -> AsyncIterator[PushService]:
    ca = trustme.CA()
    server_tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert("127.0.0.1").configure_cert(server_tls)
    client_tls = ssl.create_default_context()
    ca.configure_trust(client_tls)
    kernel.pusher.tls = client_tls

    runner = web.AppRunner(web.Application())
    service = PushService(base="")
    runner.app.router.add_post("/push/{name}", service.handle)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_tls)
    await site.start()
    host, port = runner.addresses[0][:2]
    service.base = f"https://{host}:{port}/push"
    try:
        yield service
    finally:
        await runner.cleanup()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def browser_keys() -> dict[str, str]:
    """What a browser puts in `keys`: its ECDH public key and auth secret."""
    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    return {"p256dh": _b64(point), "auth": _b64(os.urandom(16))}


async def subscribe(client: httpx.AsyncClient, user: str, endpoint: str) -> None:
    r = await client.post(
        "/api/push/subscribe",
        json={"endpoint": endpoint, "keys": browser_keys()},
        headers=as_user(user),
    )
    assert r.status_code == 204, r.text


async def test_key_is_a_p256_point(client: httpx.AsyncClient) -> None:
    key = (await client.get("/api/push/key", headers=as_user("alice"))).json()["key"]
    raw = base64.urlsafe_b64decode(key + "=")
    assert len(raw) == 65
    assert raw[0] == 4


async def test_test_push_reaches_the_device(
    client: httpx.AsyncClient, push_service: PushService
) -> None:
    await subscribe(client, "push-test", f"{push_service.base}/phone")
    # Subscribing the same endpoint twice keeps one row.
    await subscribe(client, "push-test", f"{push_service.base}/phone")

    r = await client.post("/api/push/test", headers=as_user("push-test"))
    assert r.json() == {"delivered": 1}, r.text
    name, headers = push_service.received[-1]
    assert name == "phone"
    assert headers["Authorization"].startswith("vapid t=")
    assert headers["Content-Encoding"] == "aes128gcm"


async def test_gone_subscription_is_dropped(
    client: httpx.AsyncClient, push_service: PushService
) -> None:
    await subscribe(client, "push-gone", f"{push_service.base}/gone")
    r = await client.post("/api/push/test", headers=as_user("push-gone"))
    assert r.json() == {"delivered": 0}
    rows = await client.get(
        "/api/data/core/push_subscription", headers=as_user("push-gone")
    )
    assert rows.json() == []


async def test_due_reminder_is_pushed(
    client: httpx.AsyncClient, kernel: Kernel, push_service: PushService
) -> None:
    user = "reminded"
    await subscribe(client, user, f"{push_service.base}/reminder-phone")
    due = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    r = await client.post(
        "/api/data/reminders/reminder",
        json={"title": "call mum", "due_at": due},
        headers={**as_user(user), "prefer": "return=representation"},
    )
    assert r.status_code == 201, r.text
    reminder_id = r.json()[0]["id"]

    # The plugin's module is importable only once the kernel has loaded it.
    reminders = importlib.import_module("heycarl_plugin_reminders")
    await reminders.deliver_due(kernel)

    with anyio.fail_after(10):
        while not any(name == "reminder-phone" for name, _ in push_service.received):
            await push_service.arrived.wait()
            push_service.arrived = anyio.Event()

    rows = await client.get(
        f"/api/data/reminders/reminder?id=eq.{reminder_id}", headers=as_user(user)
    )
    assert rows.json()[0]["state"] == "sent", rows.text


async def test_moving_a_sent_reminder_rearms_it(
    client: httpx.AsyncClient, kernel: Kernel
) -> None:
    user = "rearm"
    due = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    r = await client.post(
        "/api/data/reminders/reminder",
        json={"title": "no device", "due_at": due},
        headers={**as_user(user), "prefer": "return=representation"},
    )
    reminder_id = r.json()[0]["id"]
    reminders = importlib.import_module("heycarl_plugin_reminders")
    await reminders.deliver_due(kernel)

    row = (
        await client.get(
            f"/api/data/reminders/reminder?id=eq.{reminder_id}", headers=as_user(user)
        )
    ).json()[0]
    # No device: back to pending, one attempt spent, error recorded.
    assert (row["state"], row["attempts"]) == ("pending", 1), row
    assert row["last_error"]

    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await client.patch(
        f"/api/data/reminders/reminder?id=eq.{reminder_id}",
        json={"due_at": later},
        headers=as_user(user),
    )
    row = (
        await client.get(
            f"/api/data/reminders/reminder?id=eq.{reminder_id}", headers=as_user(user)
        )
    ).json()[0]
    assert (row["state"], row["attempts"], row["last_error"]) == ("pending", 0, None), (
        row
    )
