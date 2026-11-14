from __future__ import annotations

from typing import TYPE_CHECKING, Final

import httpx

if TYPE_CHECKING:
    from pathlib import Path

# Request headers PostgREST acts on. Everything else stays behind; in
# particular the caller's cookies and the gate's identity headers.
FORWARD_REQUEST: Final = frozenset(
    {
        "accept",
        "content-type",
        "prefer",
        "range",
        "range-unit",
        "if-match",
        "if-none-match",
    }
)
FORWARD_RESPONSE: Final = frozenset(
    {"content-type", "content-range", "location", "preference-applied", "etag"}
)


def client(socket: Path) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=str(socket))
    return httpx.AsyncClient(transport=transport, base_url="http://postgrest")


def request_headers(
    incoming: httpx.Headers | dict[str, str], token: str, schema: str
) -> dict[str, str]:
    headers = {
        k.lower(): v for k, v in incoming.items() if k.lower() in FORWARD_REQUEST
    }
    headers["authorization"] = f"Bearer {token}"
    # PostgREST picks the schema from these; the kernel sets them, not the
    # caller, so a caller cannot reach a schema by naming it.
    headers["accept-profile"] = schema
    headers["content-profile"] = schema
    return headers


def response_headers(outgoing: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in outgoing.items() if k.lower() in FORWARD_RESPONSE}
