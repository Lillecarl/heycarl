"""The MCP server and its OAuth authorization server, end to end.

The SDK's own client drives one test over a real uvicorn: the transport
streams events, which an in-process ASGI client cannot carry. The rest
speak OAuth by hand, to attack each step.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from .conftest import PUBLIC_URL, as_user, needs_database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

pytestmark = [pytest.mark.anyio, needs_database]
REDIRECT = "http://localhost:3030/callback"
RESOURCE_METADATA = PUBLIC_URL + "/.well-known/oauth-protected-resource/mcp"


class Plain(httpx.AsyncBaseTransport):
    """Sends https://hey.example.com to the kernel's socket as plain HTTP.

    Caddy ends TLS in production; the Host header still names the site.
    """

    def __init__(self, socket_path: Path) -> None:
        self.inner = httpx.AsyncHTTPTransport(uds=str(socket_path))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.url = request.url.copy_with(scheme="http")
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


@pytest.fixture
async def served(app: FastAPI, postgrest: None, tmp_path: Path) -> AsyncIterator[Plain]:
    path = tmp_path / "kernel.sock"
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(path))
    # The app fixture already ran the lifespan.
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve, [sock])
        # uvicorn sets a flag and offers no event to wait on.
        with anyio.fail_after(10):
            while not server.started:  # noqa: ASYNC110 -- a flag, not an event
                await anyio.sleep(0.05)
        yield Plain(path)
        server.should_exit = True


@pytest.fixture
async def site(app: FastAPI, postgrest: None) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=PUBLIC_URL
    ) as c:
        yield c


class Memory(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def register(site: httpx.AsyncClient, **extra: object) -> dict[str, str]:
    r = await site.post(
        "/register",
        json={
            "client_name": "Test client",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    info: dict[str, str] = r.json()
    return info


async def consent_page(
    site: httpx.AsyncClient, client_id: str, challenge: str, browser: dict[str, str]
) -> str:
    """Sends a browser to /authorize; returns the consent page's request id."""
    r = await site.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "the-state",
            "scope": "read write",
            "resource": PUBLIC_URL + "/mcp",
        },
        headers=browser,
    )
    assert r.status_code == 302, r.text
    location = urlsplit(r.headers["location"])
    assert location.path == "/oauth/consent", location
    [request_id] = parse_qs(location.query)["request"]
    page = await site.get(
        "/oauth/consent", params={"request": request_id}, headers=browser
    )
    assert page.status_code == 200, page.text
    assert "http://localhost:3030" in page.text
    return request_id


async def decide(
    site: httpx.AsyncClient,
    request_id: str,
    browser: dict[str, str],
    decision: str = "approve",
    scopes: tuple[str, ...] = ("read", "write"),
) -> dict[str, list[str]]:
    r = await site.post(
        "/oauth/consent",
        data={"request": request_id, "decision": decision, "scope": list(scopes)},
        headers=browser,
    )
    assert r.status_code == 303, r.text
    location = urlsplit(r.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT
    return parse_qs(location.query)


async def tokens_for(
    site: httpx.AsyncClient,
    sub: str = "alice-sub",
    scopes: tuple[str, ...] = ("read", "write"),
) -> tuple[dict[str, str], httpx.Response]:
    client = await register(site)
    verifier, challenge = pkce()
    browser = as_user(sub)
    request_id = await consent_page(site, client["client_id"], challenge, browser)
    answer = await decide(site, request_id, browser, scopes=scopes)
    assert answer["state"] == ["the-state"]
    r = await site.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": answer["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code_verifier": verifier,
        },
    )
    return client, r


async def test_the_sdk_client_signs_in_and_calls_a_tool(served: Plain) -> None:
    browser = as_user("alice-sub")
    answer: dict[str, list[str]] = {}

    async with httpx.AsyncClient(transport=served, base_url=PUBLIC_URL) as site:

        async def redirect_handler(url: str) -> None:
            r = await site.get(url, headers=browser)
            assert r.status_code == 302, r.text
            request_id = parse_qs(urlsplit(r.headers["location"]).query)["request"][0]
            answer.update(await decide(site, request_id, browser))

        async def callback_handler() -> tuple[str, str | None]:
            return answer["code"][0], answer["state"][0]

        auth = OAuthClientProvider(
            server_url=PUBLIC_URL + "/mcp",
            client_metadata=OAuthClientMetadata(
                client_name="SDK test client",
                redirect_uris=[REDIRECT],  # pyrefly: ignore[bad-argument-type]
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            ),
            storage=Memory(),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        async with (
            httpx.AsyncClient(transport=served, auth=auth, timeout=10) as http,
            streamable_http_client(PUBLIC_URL + "/mcp", http_client=http) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            init = await session.initialize()
            assert init.serverInfo.name == "Hey Carl"
            tools = await session.list_tools()
            assert "whoami" in [t.name for t in tools.tools]
            result = await session.call_tool("whoami", {})
            assert not result.isError, result
            assert result.structuredContent is not None
            assert result.structuredContent["sub"] == "alice-sub"
            assert sorted(result.structuredContent["scopes"]) == ["read", "write"]


async def test_mcp_without_a_token_points_at_the_metadata(
    site: httpx.AsyncClient,
) -> None:
    r = await site.post("/mcp", json={})
    assert r.status_code == 401
    assert f'resource_metadata="{RESOURCE_METADATA}"' in r.headers["www-authenticate"]
    resource = (await site.get(RESOURCE_METADATA)).json()
    assert resource["resource"] == PUBLIC_URL + "/mcp"
    [issuer] = resource["authorization_servers"]
    server = (await site.get("/.well-known/oauth-authorization-server")).json()
    assert server["issuer"] == issuer
    assert server["registration_endpoint"] == PUBLIC_URL + "/register"
    assert server["scopes_supported"] == ["read", "write"]


async def test_authorize_needs_a_session(site: httpx.AsyncClient) -> None:
    r = await site.get("/authorize", params={"client_id": "x"})
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=%2Fauthorize%3F")
    r = await site.get("/oauth/consent", params={"request": "x"})
    assert r.status_code == 302


@pytest.mark.parametrize(
    "uri",
    ["http://evil.example/cb", "javascript:alert(1)", "https://ok.example/cb#frag"],
)
async def test_registration_refuses_a_dangerous_redirect(
    site: httpx.AsyncClient, uri: str
) -> None:
    r = await site.post(
        "/register",
        json={
            "redirect_uris": [uri],
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert r.status_code == 400, r.text


async def test_a_grant_carries_the_scopes_the_person_chose(
    site: httpx.AsyncClient,
) -> None:
    _, r = await tokens_for(site, scopes=("read",))
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "read"
    # Write alone still grants read: a token never lacks it.
    _, r = await tokens_for(site, scopes=("write",))
    assert r.json()["scope"] == "read write"


async def test_denial_sends_access_denied(site: httpx.AsyncClient) -> None:
    client = await register(site)
    browser = as_user("alice-sub")
    request_id = await consent_page(site, client["client_id"], pkce()[1], browser)
    answer = await decide(site, request_id, browser, decision="deny")
    assert answer["error"] == ["access_denied"]
    assert "code" not in answer
    # Answered once: a second answer finds nothing.
    r = await site.post(
        "/oauth/consent",
        data={"request": request_id, "decision": "approve", "scope": "read"},
        headers=browser,
    )
    assert r.status_code == 404


async def test_consent_from_another_origin_is_refused(site: httpx.AsyncClient) -> None:
    client = await register(site)
    browser = as_user("alice-sub")
    request_id = await consent_page(site, client["client_id"], pkce()[1], browser)
    r = await site.post(
        "/oauth/consent",
        data={"request": request_id, "decision": "approve", "scope": "read"},
        headers={**browser, "origin": "https://evil.example"},
    )
    assert r.status_code == 403


async def test_a_code_works_once(site: httpx.AsyncClient) -> None:
    client = await register(site)
    verifier, challenge = pkce()
    browser = as_user("alice-sub")
    answer = await decide(
        site, await consent_page(site, client["client_id"], challenge, browser), browser
    )
    form = {
        "grant_type": "authorization_code",
        "code": answer["code"][0],
        "redirect_uri": REDIRECT,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
    }
    wrong = await site.post("/token", data={**form, "code_verifier": pkce()[0]})
    assert wrong.status_code == 400
    assert (
        await site.post("/token", data={**form, "code_verifier": verifier})
    ).status_code == 200
    again = await site.post("/token", data={**form, "code_verifier": verifier})
    assert again.status_code == 400
    assert again.json()["error"] == "invalid_grant"


async def test_refresh_rotates_and_revoke_ends_the_grant(
    site: httpx.AsyncClient,
) -> None:
    client, r = await tokens_for(site)
    first = r.json()
    refresh = {
        "grant_type": "refresh_token",
        "refresh_token": first["refresh_token"],
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
    }
    r = await site.post("/token", data=refresh)
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["access_token"] != first["access_token"]
    # The old refresh token is used up.
    assert (await site.post("/token", data=refresh)).status_code == 400

    r = await site.post(
        "/revoke",
        data={
            "token": second["access_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert r.status_code == 200, r.text
    again = {**refresh, "refresh_token": second["refresh_token"]}
    assert (await site.post("/token", data=again)).status_code == 400


async def test_a_token_is_bound_to_its_client(site: httpx.AsyncClient) -> None:
    _, r = await tokens_for(site)
    other = await register(site)
    stolen = await site.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": r.json()["refresh_token"],
            "client_id": other["client_id"],
            "client_secret": other["client_secret"],
        },
    )
    assert stolen.status_code == 400
