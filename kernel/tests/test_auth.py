"""Sign-in, sessions and the deny-by-default access layer, attacked.

A fake OpenID provider runs on a real port, since the kernel reaches its
provider over HTTP like any other client.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlsplit

import anyio
import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from heycarl import oidc
from heycarl.access import (
    AccessMiddleware,
    access_for,
    inline_script_hashes,
    safe_next,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .conftest import PUBLIC_URL, as_user, needs_database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

pytestmark = [pytest.mark.anyio]
CLIENT_ID = "hey"
CLIENT_SECRET = "client-secret"


def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    # Any: a JWK, a JSON object.
    public: dict[str, Any] = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
    )
    return {**public, "kid": kid, "use": "sig", "alg": "RS256"}


@dataclass
class FakeProvider:
    issuer: str = ""
    key: rsa.RSAPrivateKey = field(default_factory=rsa_key)
    kid: str = "k1"
    sub: str = "alice-sub"
    email: str = "alice@example.com"
    # Claims to change in the next ID token, for the attacks.
    tamper: dict[str, Any] = field(default_factory=dict)
    codes: dict[str, dict[str, str]] = field(default_factory=dict)

    def id_token(self, client_id: str, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": client_id,
            "sub": self.sub,
            "email": self.email,
            "email_verified": True,
            "nonce": nonce,
            "iat": now,
            "exp": now + 300,
            **self.tamper,
        }
        return jwt.encode(
            claims, self.key, algorithm="RS256", headers={"kid": self.kid}
        )

    async def discovery(self, _: Request) -> Response:
        return JSONResponse(
            {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/auth",
                "token_endpoint": f"{self.issuer}/token",
                "jwks_uri": f"{self.issuer}/keys",
            }
        )

    async def keys(self, _: Request) -> Response:
        return JSONResponse({"keys": [jwk(self.key, self.kid)]})

    async def authorize(self, request: Request) -> Response:
        q = request.query_params
        code = secrets.token_urlsafe(16)
        self.codes[code] = {
            "client_id": q["client_id"],
            "redirect_uri": q["redirect_uri"],
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
        }
        target = (
            q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]})
        )
        return RedirectResponse(target, status_code=302)

    async def token(self, request: Request) -> Response:
        # By hand: Starlette's form parser needs python-multipart.
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        basic = base64.b64decode(
            request.headers["authorization"].removeprefix("Basic ")
        )
        if basic.decode() != f"{CLIENT_ID}:{CLIENT_SECRET}":
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        grant = self.codes.pop(str(form["code"]), None)
        verifier = str(form["code_verifier"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        if (
            grant is None
            or grant["redirect_uri"] != form["redirect_uri"]
            or challenge.rstrip(b"=").decode() != grant["challenge"]
        ):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(
            {"id_token": self.id_token(grant["client_id"], grant["nonce"])}
        )

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/.well-known/openid-configuration", self.discovery),
                Route("/keys", self.keys),
                Route("/auth", self.authorize),
                Route("/token", self.token, methods=["POST"]),
            ]
        )


@pytest.fixture
async def provider() -> AsyncIterator[FakeProvider]:
    op = FakeProvider()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    op.issuer = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(op.app(), log_level="warning"))
    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve, [sock])
        # uvicorn sets a flag and offers no event to wait on.
        with anyio.fail_after(10):
            while not server.started:  # noqa: ASYNC110 -- a flag, not an event
                await anyio.sleep(0.05)
        yield op
        server.should_exit = True


@pytest.fixture
def oidc_issuer(provider: FakeProvider) -> str:
    return provider.issuer


def cookies_of(response: httpx.Response) -> dict[str, str]:
    jar = {}
    for line in response.headers.get_list("set-cookie"):
        name, _, rest = line.partition("=")
        jar[name] = rest.split(";", 1)[0]
    return jar


async def sign_in(
    client: httpx.AsyncClient, next_path: str = "/"
) -> tuple[httpx.Response, dict[str, str]]:
    """The whole browser dance. Returns the callback's answer and its cookies."""
    start = await client.get("/auth/login", params={"next": next_path})
    assert start.status_code == 302, start.text
    login_cookies = cookies_of(start)
    async with httpx.AsyncClient() as browser:
        at_provider = await browser.get(start.headers["location"])
    assert at_provider.status_code == 302
    back = urlsplit(at_provider.headers["location"])
    assert f"{back.scheme}://{back.netloc}" == PUBLIC_URL
    answer = await client.get(
        "/auth/callback?" + back.query,
        headers={"cookie": "; ".join(f"{k}={v}" for k, v in login_cookies.items())},
    )
    return answer, cookies_of(answer)


def cookie_header(jar: dict[str, str]) -> dict[str, str]:
    return {"cookie": "; ".join(f"{k}={v}" for k, v in jar.items() if v)}


@needs_database
async def test_sign_in_makes_a_session(client: httpx.AsyncClient) -> None:
    answer, jar = await sign_in(client, "/p/prm/heycarl-prm")
    assert answer.status_code == 303, answer.text
    assert answer.headers["location"] == "/p/prm/heycarl-prm"
    line = next(
        c
        for c in answer.headers.get_list("set-cookie")
        if c.startswith("__Host-heycarl=")
    )
    for attribute in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
        assert attribute in line, line
    me = await client.get("/api/me", headers=cookie_header(jar))
    assert me.json()["sub"] == "alice-sub", me.text


@needs_database
async def test_a_callback_needs_the_browser_that_started_it(
    client: httpx.AsyncClient,
) -> None:
    start = await client.get("/auth/login")
    async with httpx.AsyncClient() as browser:
        at_provider = await browser.get(start.headers["location"])
    query = urlsplit(at_provider.headers["location"]).query
    # The victim's browser, sent the attacker's callback link: no login cookie.
    answer = await client.get("/auth/callback?" + query)
    assert answer.status_code == 400
    assert "__Host-heycarl=" not in answer.headers.get("set-cookie", "")


@needs_database
async def test_a_callback_works_once(client: httpx.AsyncClient) -> None:
    start = await client.get("/auth/login")
    login = cookie_header(cookies_of(start))
    async with httpx.AsyncClient() as browser:
        at_provider = await browser.get(start.headers["location"])
    query = urlsplit(at_provider.headers["location"]).query
    assert (
        await client.get("/auth/callback?" + query, headers=login)
    ).status_code == 303
    assert (
        await client.get("/auth/callback?" + query, headers=login)
    ).status_code == 400


@needs_database
async def test_a_token_for_another_login_is_refused(
    client: httpx.AsyncClient, provider: FakeProvider
) -> None:
    provider.tamper = {"nonce": "someone-elses"}
    answer, jar = await sign_in(client)
    assert answer.status_code == 403
    assert "__Host-heycarl" not in jar


@needs_database
async def test_next_never_leaves_the_site(client: httpx.AsyncClient) -> None:
    for evil in ("//evil.example", "https://evil.example/", "/\\evil.example"):
        answer, _ = await sign_in(client, evil)
        assert answer.headers["location"] == "/", evil


@needs_database
async def test_a_rotated_key_is_fetched(
    client: httpx.AsyncClient, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(oidc, "JWKS_REFRESH_SECONDS", 0.0)
    assert (await sign_in(client))[0].status_code == 303
    provider.key, provider.kid = rsa_key(), "k2"
    assert (await sign_in(client))[0].status_code == 303


@needs_database
async def test_logout_ends_the_session(client: httpx.AsyncClient) -> None:
    _, jar = await sign_in(client)
    headers = {**cookie_header(jar), "origin": PUBLIC_URL}
    assert (await client.post("/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/me", headers=headers)).status_code == 401


@needs_database
async def test_deny_by_default(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/me")).status_code == 401
    page = await client.get("/p/prm/heycarl-prm?x=1")
    assert page.status_code == 302
    assert (
        page.headers["location"] == "/auth/login?next=%2Fp%2Fprm%2Fheycarl-prm%3Fx%3D1"
    )
    assert (await client.get("/healthz")).status_code == 200
    # A header naming a person is just a header.
    forged = {"x-forwarded-user": "alice-sub", "x-forwarded-email": "alice@example.com"}
    assert (await client.get("/api/me", headers=forged)).status_code == 401
    # A public prefix does not reach past itself. httpx removes a literal
    # `..` before sending, so that case is checked on the rule itself.
    assert access_for("/icons/../api/me") is None
    assert access_for("/api/public/./x") is None
    assert (await client.get("/api/public/%2e%2e/me")).status_code in {400, 404}


@needs_database
async def test_writes_need_this_origin(client: httpx.AsyncClient) -> None:
    signed_in = as_user("csrf")
    body = {"title": "x"}
    no_origin = {"cookie": signed_in["cookie"]}
    evil = {**no_origin, "origin": "https://evil.example"}
    for headers in (no_origin, evil):
        r = await client.post("/api/data/todos/todo", json=body, headers=headers)
        assert r.status_code == 403, r.text
    r = await client.post("/api/data/todos/todo", json=body, headers=signed_in)
    assert r.status_code == 201, r.text


@needs_database
async def test_security_headers(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers["content-security-policy"]
    assert "script-src 'self';" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "camera=()" in r.headers["permissions-policy"]
    assert r.headers["strict-transport-security"].startswith("max-age=")


def test_inline_script_hashes() -> None:
    html = (
        '<script src="/a.js"></script><script type="module">boot()</script>'
        "<SCRIPT>\nx\n</SCRIPT>"
    )
    assert inline_script_hashes(html) == [
        "'sha256-" + _b64sha("boot()") + "'",
        "'sha256-" + _b64sha("\nx\n") + "'",
    ]


def _b64sha(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


@needs_database
@pytest.mark.parametrize("allowed_emails", [frozenset({"carl@example.com"})])
async def test_allowed_emails(
    client: httpx.AsyncClient, provider: FakeProvider
) -> None:
    assert (await sign_in(client))[0].status_code == 403
    provider.email = "Carl@Example.com"
    assert (await sign_in(client))[0].status_code == 303
    provider.tamper = {"email_verified": False}
    assert (await sign_in(client))[0].status_code == 403


@needs_database
@pytest.mark.parametrize("allowed_emails", [frozenset({"carl@example.com"})])
async def test_a_session_ends_when_its_address_leaves_the_list(
    client: httpx.AsyncClient,
) -> None:
    staying = as_user("carl-sub", "carl@example.com")
    assert (await client.get("/api/me", headers=staying)).status_code == 200
    gone = as_user("former-sub", "former@example.com")
    assert (await client.get("/api/me", headers=gone)).status_code == 401
    assert (await client.get("/api/me", headers=as_user("none-sub"))).status_code == 401


# The ID token checks, one at a time.

KEY = rsa_key()
KEYS = {"k1": jwt.PyJWK(jwk(KEY, "k1"))}
ISSUER = "https://idp.example"


def token(
    headers: dict[str, str] | None = None,
    key: object = KEY,
    alg: str = "RS256",
    **changes: Any,
) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "s",
        "nonce": "n",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(changes)
    return jwt.encode(claims, key, algorithm=alg, headers=headers or {"kid": "k1"})  # type: ignore[arg-type]


def validate(id_token: str) -> oidc.Identity:
    return oidc.validate_id_token(
        id_token, KEYS, issuer=ISSUER, client_id=CLIENT_ID, nonce="n"
    )


def unsigned() -> str:
    def part(value: dict[str, Any]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
        )

    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "s",
        "nonce": "n",
        "iat": now,
        "exp": now + 300,
    }
    return f"{part({'alg': 'none', 'kid': 'k1'})}.{part(claims)}."


def test_a_good_token_passes() -> None:
    assert validate(token(email="A@B.C", email_verified=True)) == oidc.Identity(
        sub="s", email="a@b.c", name=None
    )


@pytest.mark.parametrize("verified", [False, "true", None])
def test_an_unverified_email_counts_as_none(verified: object) -> None:
    assert validate(token(email="a@b.c", email_verified=verified)).email is None


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: token(iss="https://other.example"), id="issuer"),
        pytest.param(lambda: token(aud="another-client"), id="audience"),
        pytest.param(lambda: token(nonce="other"), id="nonce"),
        pytest.param(lambda: token(exp=int(time.time()) - 3600), id="expired"),
        pytest.param(lambda: token(aud=[CLIENT_ID, "x"], azp="x"), id="azp"),
        pytest.param(lambda: token(key=rsa_key()), id="other-key-same-kid"),
        pytest.param(lambda: token(headers={"kid": "nope"}), id="unknown-kid"),
        pytest.param(lambda: token(key="secret", alg="HS256"), id="hs256"),
        pytest.param(unsigned, id="alg-none"),
        pytest.param(lambda: "not.a.jwt", id="garbage"),
    ],
)
def test_a_bad_token_fails(bad: Any) -> None:
    with pytest.raises(oidc.LoginError):
        validate(bad())


def test_discovery_must_name_our_issuer() -> None:
    with pytest.raises(oidc.LoginError):
        oidc.parse_discovery(
            {
                "issuer": "https://evil.example",
                "authorization_endpoint": "x",
                "token_endpoint": "x",
                "jwks_uri": "x",
            },
            ISSUER,
        )


def test_safe_next() -> None:
    assert safe_next("/p/x?y=1") == "/p/x?y=1"
    for evil in (
        None,
        "",
        "//e.x",
        "/\\e.x",
        "https://e.x",
        "e.x",
        "javascript:alert(1)",
        "/" + "x" * 5000,
    ):
        assert safe_next(evil) == "/"


async def test_the_gate_refuses_every_websocket() -> None:
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["path"])

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    gate = AccessMiddleware(inner, public_url=PUBLIC_URL)
    for path in ("/api/me", "/healthz", "/mcp"):
        await gate({"type": "websocket", "path": path, "headers": []}, receive, send)
    assert reached == []
    assert sent == [{"type": "websocket.close", "code": 1008}] * 3


@needs_database
async def test_pending_logins_are_capped(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    app.state.pending_login_limit = 0
    refused = await client.get("/auth/login")
    assert refused.status_code == 503
    assert "set-cookie" not in refused.headers
    app.state.pending_login_limit = 10_000
    assert (await client.get("/auth/login")).status_code == 302


def test_pkce_matches_rfc_7636() -> None:
    # Appendix B.
    assert (
        oidc.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )
