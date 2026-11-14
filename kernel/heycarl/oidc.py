"""Sign-in with an OpenID Connect provider: authorization code flow with PKCE.

State binds the callback to the browser that started the login (a cookie),
the nonce binds the ID token to that login, and PKCE binds the code to it.
The ID token comes straight from the token endpoint over TLS, and its
signature is still checked: nothing about it is taken on trust.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote, urlencode

import anyio
import jwt
import structlog

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

SCOPES: Final = "openid email profile"
# Asymmetric only: an HS256 token would be checked against a public key,
# and "none" against nothing.
ALGORITHMS: Final = ["RS256", "ES256"]
# Seconds the provider's clock may be off from ours.
LEEWAY: Final = 60
# The least time between two JWKS fetches, so a flood of tokens with made-up
# key ids cannot make the kernel hammer the provider.
JWKS_REFRESH_SECONDS: Final = 60.0


class LoginError(Exception):
    """A login that must not create a session. The message is safe to show."""


@dataclass(frozen=True, slots=True)
class Endpoints:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True, slots=True)
class Identity:
    sub: str
    email: str | None
    name: str | None


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(
    endpoints: Endpoints,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_verifier: str,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in endpoints.authorization_endpoint else "?"
    return f"{endpoints.authorization_endpoint}{separator}{query}"


def parse_discovery(document: dict[str, Any], issuer: str) -> Endpoints:
    # Any: a JSON document from the provider, checked field by field here.
    if document.get("issuer") != issuer:
        raise LoginError("the provider names another issuer")
    try:
        return Endpoints(
            issuer=issuer,
            authorization_endpoint=str(document["authorization_endpoint"]),
            token_endpoint=str(document["token_endpoint"]),
            jwks_uri=str(document["jwks_uri"]),
        )
    except KeyError as e:
        raise LoginError(f"the provider's discovery lacks {e}") from e


class UnknownKeyError(LoginError):
    pass


def validate_id_token(
    token: str,
    keys: dict[str, jwt.PyJWK],
    *,
    issuer: str,
    client_id: str,
    nonce: str,
) -> Identity:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise LoginError("the ID token is malformed") from e
    key = keys.get(str(header.get("kid", "")))
    if key is None:
        raise UnknownKeyError("the ID token is signed with an unknown key")
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=ALGORITHMS,
            audience=client_id,
            issuer=issuer,
            leeway=LEEWAY,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise LoginError(f"the ID token is not valid: {e}") from e
    if claims.get("nonce") != nonce:
        raise LoginError("the ID token belongs to another login")
    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if len(audiences) > 1 and claims.get("azp") != client_id:
        raise LoginError("the ID token was issued to another client")
    sub = claims["sub"]
    if not isinstance(sub, str) or not sub:
        raise LoginError("the ID token has no subject")
    email = claims.get("email")
    name = claims.get("name") or claims.get("preferred_username")
    return Identity(
        sub=sub,
        email=email.lower() if isinstance(email, str) else None,
        name=name if isinstance(name, str) else None,
    )


class Client:
    def __init__(
        self, http: httpx.AsyncClient, issuer: str, client_id: str, client_secret: str
    ) -> None:
        self.http = http
        self.issuer = issuer
        self.client_id = client_id
        self.client_secret = client_secret
        self._endpoints: Endpoints | None = None
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_fetched_at = float("-inf")
        self._lock = anyio.Lock()

    async def endpoints(self) -> Endpoints:
        if self._endpoints is None:
            url = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
            r = await self.http.get(url)
            r.raise_for_status()
            self._endpoints = parse_discovery(r.json(), self.issuer)
        return self._endpoints

    async def _fetch_keys(self) -> dict[str, jwt.PyJWK]:
        async with self._lock:
            now = anyio.current_time()
            if now - self._keys_fetched_at >= JWKS_REFRESH_SECONDS or not self._keys:
                r = await self.http.get((await self.endpoints()).jwks_uri)
                r.raise_for_status()
                keys = {}
                for jwk in r.json().get("keys", []):
                    try:
                        keys[str(jwk.get("kid", ""))] = jwt.PyJWK(jwk)
                    except jwt.PyJWTError:
                        # An encryption key or an algorithm PyJWT lacks.
                        continue
                self._keys = keys
                self._keys_fetched_at = now
            return self._keys

    async def identity(
        self, *, code: str, redirect_uri: str, code_verifier: str, nonce: str
    ) -> Identity:
        endpoints = await self.endpoints()
        r = await self.http.post(
            endpoints.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            # RFC 6749 2.3.1: form-encode both halves before Basic.
            auth=(quote(self.client_id, safe=""), quote(self.client_secret, safe="")),
        )
        if r.is_error:
            log.warning("oidc_token_error", status=r.status_code, body=r.text[:500])
            raise LoginError("the provider refused the code")
        id_token = r.json().get("id_token")
        if not isinstance(id_token, str):
            raise LoginError("the provider sent no ID token")
        check = {"issuer": self.issuer, "client_id": self.client_id, "nonce": nonce}
        keys = self._keys or await self._fetch_keys()
        try:
            return validate_id_token(id_token, keys, **check)
        except UnknownKeyError:
            # The provider rotated its keys since the last fetch.
            return validate_id_token(id_token, await self._fetch_keys(), **check)
