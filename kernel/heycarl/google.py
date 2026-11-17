"""Google's OAuth endpoints, over httpx (DESIGN.md "Google accounts").

Every secret goes in a form body or a header, never in a URL: a URL lands
in exception messages and logs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    import httpx

SCOPE_PREFIX: Final = "https://www.googleapis.com/auth/"
# Who the account is: userinfo's sub and email.
IDENTITY_SCOPES: Final = ("openid", "email")
ENV_PREFIX: Final = "HEYCARL_GOOGLE_"
MIN_KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
HTTP_TIMEOUT: Final = 20.0


@dataclass(frozen=True, slots=True)
class Endpoints:
    authorize: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token: str = "https://oauth2.googleapis.com/token"
    revoke: str = "https://oauth2.googleapis.com/revoke"
    userinfo: str = "https://openidconnect.googleapis.com/v1/userinfo"
    # A Google API's base URL by name, replaced for tests; a plugin passes
    # its real one to `api`.
    apis: Mapping[str, str] = field(default_factory=dict)

    def api(self, name: str, default: str) -> str:
        return self.apis.get(name, default)


@dataclass(frozen=True, slots=True)
class Settings:
    client_id: str
    client_secret: str = field(repr=False)
    key: bytes = field(repr=False)
    endpoints: Endpoints = Endpoints()


class ConfigError(RuntimeError):
    pass


OAUTH_ENDPOINTS: Final = frozenset({"authorize", "token", "revoke", "userinfo"})


def settings_from_env(environ: Mapping[str, str] = os.environ) -> Settings | None:
    """None when no client is configured: the feature is off.

    A client id without both files is a broken deploy, not an off switch.
    `HEYCARL_GOOGLE_ENDPOINTS` (JSON) replaces endpoints by name, for tests
    only; a name other than the OAuth ones is a Google API's base URL.
    """
    client_id = environ.get(ENV_PREFIX + "CLIENT_ID", "")
    secret_file = environ.get(ENV_PREFIX + "CLIENT_SECRET_FILE", "")
    key_file = environ.get(ENV_PREFIX + "TOKEN_KEY_FILE", "")
    if not (client_id or secret_file or key_file):
        return None
    if not (client_id and secret_file and key_file):
        raise ConfigError(
            "Google needs a client id, a client secret file and a token key file"
        )
    secret = Path(secret_file).read_text().strip()
    key_material = Path(key_file).read_bytes().strip()
    if not secret:
        raise ConfigError("the Google client secret file is empty")
    if len(key_material) < MIN_KEY_BYTES:
        raise ConfigError(f"the token key file needs at least {MIN_KEY_BYTES} bytes")
    overrides: dict[str, str] = json.loads(
        environ.get(ENV_PREFIX + "ENDPOINTS", "") or "{}"
    )
    return Settings(
        client_id=client_id,
        client_secret=secret,
        key=hashlib.sha256(key_material).digest(),
        endpoints=Endpoints(
            **{k: v for k, v in overrides.items() if k in OAUTH_ENDPOINTS},
            apis={k: v for k, v in overrides.items() if k not in OAUTH_ENDPOINTS},
        ),
    )


def seal(key: bytes, owner: str, token: str) -> str:
    """AES-GCM with the owner as associated data, so a sealed token copied
    to another owner's row does not open."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(nonce, token.encode(), owner.encode())
    return base64.b64encode(nonce + sealed).decode()


def unseal(key: bytes, owner: str, sealed: str) -> str:
    raw = base64.b64decode(sealed)
    return (
        AESGCM(key)
        .decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], owner.encode())
        .decode()
    )


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(
    settings: Settings,
    redirect_uri: str,
    state: str,
    verifier: str,
    scopes: Iterable[str],
    login_hint: str | None = None,
) -> str:
    query = {
        "client_id": settings.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
        # A refresh token, and a new one on every connect.
        "access_type": "offline",
        "prompt": "consent select_account",
        # The new token covers the scopes this account granted before.
        "include_granted_scopes": "true",
    } | ({"login_hint": login_hint} if login_hint else {})
    return settings.endpoints.authorize + "?" + urlencode(query)


class GoogleError(RuntimeError):
    """A failure to show the person. Never carries a token."""


class ReconnectNeeded(GoogleError):
    """The refresh token no longer works: revoked, expired or never valid."""


class Unauthorized(GoogleError):
    pass


@dataclass(frozen=True, slots=True)
class Grant:
    access_token: str = field(repr=False)
    expires_in: int
    refresh_token: str | None = field(repr=False)
    scopes: frozenset[str]


def _grant(answer: Mapping[str, Any]) -> Grant:
    # Any: Google's token response, read field by field.
    return Grant(
        access_token=str(answer["access_token"]),
        expires_in=int(answer.get("expires_in", 3600)),
        refresh_token=None
        if answer.get("refresh_token") is None
        else str(answer["refresh_token"]),
        scopes=frozenset(str(answer.get("scope", "")).split()),
    )


def error_code(response: httpx.Response) -> str:
    try:
        answer = response.json()
    except ValueError:
        return ""
    return str(answer.get("error", "")) if isinstance(answer, dict) else ""


async def exchange_code(
    http: httpx.AsyncClient,
    settings: Settings,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> tuple[Grant, str]:
    """The grant and its refresh token, which a connect must bring."""
    r = await http.post(
        settings.endpoints.token,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
        },
    )
    if r.is_error:
        raise GoogleError(f"Google refused the code ({r.status_code} {error_code(r)})")
    grant = _grant(r.json())
    if not grant.refresh_token:
        raise GoogleError("Google sent no refresh token")
    return grant, grant.refresh_token


async def refresh(
    http: httpx.AsyncClient, settings: Settings, refresh_token: str
) -> Grant:
    r = await http.post(
        settings.endpoints.token,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
        },
    )
    if r.status_code in {400, 401} and error_code(r) in {
        "invalid_grant",
        "unauthorized_client",
    }:
        raise ReconnectNeeded("Google no longer accepts this connection; connect again")
    if r.is_error:
        raise GoogleError(f"Google refused to refresh the token ({r.status_code})")
    return _grant(r.json())


async def revoke(http: httpx.AsyncClient, settings: Settings, token: str) -> bool:
    r = await http.post(settings.endpoints.revoke, data={"token": token})
    # 400 invalid_token: Google had already forgotten it, which is the goal.
    return r.is_success or error_code(r) == "invalid_token"


@dataclass(frozen=True, slots=True)
class Identity:
    sub: str
    email: str


async def identity(
    http: httpx.AsyncClient, settings: Settings, access_token: str
) -> Identity:
    r = await http.get(
        settings.endpoints.userinfo,
        headers={"authorization": f"Bearer {access_token}"},
    )
    if r.is_error:
        raise GoogleError(f"Google did not say who the account is ({r.status_code})")
    answer = r.json()
    sub = str(answer.get("sub") or "") if isinstance(answer, dict) else ""
    if not sub:
        raise GoogleError("Google did not say who the account is")
    return Identity(sub=sub, email=str(answer.get("email") or "").lower())
