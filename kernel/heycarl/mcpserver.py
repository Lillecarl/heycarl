"""The MCP server at /mcp, and the consent page that ends its OAuth flow.

The SDK builds a Starlette app with the transport, the bearer check and
the authorization server's endpoints. Its routes join the kernel's router
one by one, so the shell's catch-all never shadows them; each one hands
the request to the SDK app, which matches the path again.
"""

from __future__ import annotations

import json
from html import escape
from typing import TYPE_CHECKING, Any, Final, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ClientCapabilities, ElicitationCapability, ToolAnnotations
from pydantic import BaseModel
from starlette.routing import Route

from . import oauth, postgrest
from .identity import caller
from .oauth import CONSENT_PATH, MCP_PATH, SCOPES, Provider, Scope

if TYPE_CHECKING:
    from fastapi import FastAPI
    from mcp.server.auth.provider import AccessToken, AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull
    from starlette.applications import Starlette

    from .app import Kernel

INSTRUCTIONS: Final = (
    "Hey Carl is one person's application framework: contacts, reminders"
    " and todos. Every tool acts as the person who approved this connection."
)
READ_METHODS: Final = frozenset({"GET", "HEAD"})
# Any: JSON values, whatever the table holds. A tool returns it inside an
# object: FastMCP sends each item of a bare list as its own text block, and
# a client that reads the text gets one object, or nothing for no rows.
Json = dict[str, Any] | list[Any] | str | int | float | bool | None
SCOPE_TEXT: Final = {
    Scope.READ: "Read your data",
    Scope.WRITE: "Change and delete your data",
}


class Whoami(BaseModel):
    sub: str
    client_id: str
    scopes: list[str]


def token_of(ctx: Context) -> AccessToken:
    """The token of the request that called the tool.

    Not `get_access_token()`: a stateful session's task keeps the context of
    the request that opened it, so that would be the first token, not this one.
    """
    request = ctx.request_context.request
    user = None if request is None else request.scope.get("user")
    if not isinstance(user, AuthenticatedUser):
        raise ToolError("no access token")
    return user.access_token


def require(ctx: Context, scope: Scope) -> str:
    """The caller's sub, when its token carries `scope`."""
    token = token_of(ctx)
    if scope not in token.scopes or token.subject is None:
        raise ToolError(f"this connection lacks the {scope} scope")
    return token.subject


def kernel_of(ctx: Context) -> Kernel:
    request = ctx.request_context.request
    if request is None:
        raise ToolError("no request")
    kernel: Kernel = request.app.state.kernel
    return kernel


# Any: PostgREST's JSON, whatever the table holds.
async def call(
    ctx: Context,
    scope: str,
    method: str,
    path: str,
    *,
    params: dict[str, str] | list[tuple[str, str]] | None = None,
    body: object = None,
) -> Any:
    """PostgREST as the person who approved this connection, so RLS applies.

    GET and HEAD need `read`; anything else needs `write`. `scope` is
    "core" or a plugin's name.
    """
    method = method.upper()
    sub = require(ctx, Scope.READ if method in READ_METHODS else Scope.WRITE)
    kernel = kernel_of(ctx)
    schema = kernel.schema_for(scope)
    if schema is None:
        raise ToolError(f"no data scope {scope!r}; see the data_scopes tool")
    response = await kernel.data.request(
        method,
        "/" + path.lstrip("/"),
        params=None if params is None else httpx.QueryParams(params),
        headers=postgrest.request_headers(
            {
                "content-type": "application/json",
                "accept": "application/json",
                "prefer": "return=representation",
            },
            kernel.minter.user(sub),
            schema,
        ),
        content=None if body is None else json.dumps(body),
    )
    if response.is_error:
        raise ToolError(f"{response.status_code}: {response.text[:2000]}")
    return response.json() if response.content else None


class Confirmation(BaseModel):
    confirm: bool


async def confirm(ctx: Context, message: str) -> None:
    """Asks the person through the client; refuses when the client cannot ask.

    An AI must never answer this for the person, so a client without
    elicitation gets an error, not a skipped question.
    """
    if not ctx.session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    ):
        raise ToolError(
            "this change needs your confirmation, and this client cannot ask"
            " for it; make it in the Hey Carl app instead"
        )
    answer = await ctx.elicit(message, schema=Confirmation)
    if not (isinstance(answer, AcceptedElicitation) and answer.data.confirm):
        raise ToolError("not confirmed; nothing changed")


def condensed_schema(openapi: dict[str, Any]) -> dict[str, Any]:
    """Tables with their columns, and the functions, from PostgREST's OpenAPI.

    Any: the OpenAPI document, passed through from PostgREST.
    """
    tables = {
        name: {
            column: " ".join(
                str(spec.get(key)) for key in ("format", "description") if spec.get(key)
            )
            for column, spec in definition.get("properties", {}).items()
        }
        for name, definition in openapi.get("definitions", {}).items()
    }
    functions = sorted(
        path.removeprefix("/rpc/")
        for path in openapi.get("paths", {})
        if path.startswith("/rpc/")
    )
    return {"tables": tables, "functions": functions}


def build(provider: Provider) -> FastMCP:
    public_url = provider.public_url
    server = FastMCP(
        name="Hey Carl",
        instructions=INSTRUCTIONS,
        website_url=public_url,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url + MCP_PATH,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
            ),
            revocation_options=RevocationOptions(enabled=True),
            # The SDK also publishes these as the scopes a client may ask
            # for; a read-only client would then never be offered write.
            # Each tool checks its scope itself instead.
            required_scopes=None,
        ),
        # The default trusts localhost only. A request through Caddy keeps
        # its Host header.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[urlsplit(public_url).netloc],
            allowed_origins=[public_url],
        ),
        # Elicitation and progress need the session.
        stateless_http=False,
    )

    @server.tool()
    async def whoami(ctx: Context) -> Whoami:
        """Who this connection acts as, and what it may do."""
        token = token_of(ctx)
        require(ctx, Scope.READ)
        return Whoami(
            sub=token.subject or "",
            client_id=token.client_id,
            scopes=token.scopes,
        )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def data_scopes(ctx: Context) -> dict[str, str]:
        """The data scopes, each a PostgreSQL schema served by PostgREST.

        Pass a scope to data_schema, data_read and data_write.
        """
        require(ctx, Scope.READ)
        kernel = kernel_of(ctx)
        return {
            "core": "Hey Carl itself: push subscriptions",
            **{p.name: p.manifest.title for p in kernel.plugins},
        }

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def data_schema(ctx: Context, scope: str) -> dict[str, Any]:
        """Tables, their columns and the functions (rpc/...) of one scope."""
        openapi = await call(ctx, scope, "GET", "/")
        return condensed_schema(openapi)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def data_read(
        ctx: Context, scope: str, path: str, query: dict[str, str] | None = None
    ) -> dict[str, Json]:
        """Read through PostgREST: GET <path>?<query> in one scope.

        `path` is a table, a view or rpc/<function>. `query` takes PostgREST
        syntax, for example {"select": "id,first_name,contact_field(*)",
        "last_name": "eq.Lindqvist", "order": "first_name", "limit": "20"}.
        Rows of other people never show: row-level security applies.
        """
        return {"result": await call(ctx, scope, "GET", path, params=query)}

    @server.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def data_write(
        ctx: Context,
        scope: str,
        method: Literal["POST", "PATCH", "DELETE"],
        path: str,
        query: dict[str, str] | None = None,
        body: Json = None,
    ) -> dict[str, Json]:
        """Write through PostgREST: POST (insert or call rpc), PATCH or DELETE.

        PATCH and DELETE need a filter in `query`, such as {"id": "eq.7"}.
        A DELETE asks the person to confirm first. Returns the rows written.
        """
        if method in {"PATCH", "DELETE"} and not query:
            raise ToolError(f"{method} needs a filter in query")
        if method == "DELETE":
            filters = ", ".join(f"{k}={v}" for k, v in (query or {}).items())
            await confirm(ctx, f"Delete from {scope}/{path} where {filters}?")
        return {"result": await call(ctx, scope, method, path, params=query, body=body)}

    return server


def mount(app: FastAPI, server: FastMCP) -> Starlette:
    """Returns the SDK app. A tool's request belongs to it, not to `app`, so
    the kernel sets `state.kernel` on both."""
    sdk_app = server.streamable_http_app()
    for route in sdk_app.routes:
        if isinstance(route, Route):
            app.router.add_route(route.path, sdk_app, include_in_schema=False)
    return sdk_app


def _page(body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Connect to Hey Carl</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:2rem auto;"
        "padding:0 1rem;color:#1d1d1f;background:#fff}"
        "@media (prefers-color-scheme:dark){body{color:#eee;background:#161616}}"
        ".host{font:600 1.2rem ui-monospace,monospace;word-break:break-all}"
        "label{display:block;margin:.5rem 0}"
        "button{font:inherit;padding:.6rem 1.2rem;margin:1rem .5rem 0 0}"
        "</style></head><body>" + body + "</body></html>",
        status_code=status_code,
        headers={"cache-control": "no-store"},
    )


def consent_html(
    request_id: str, client: OAuthClientInformationFull, params: AuthorizationParams
) -> str:
    redirect = urlsplit(str(params.redirect_uri))
    offered = params.scopes or SCOPES
    # Anyone may register a client under any name, so the page leads with
    # the redirect host, which the client cannot fake.
    name = escape(client.client_name or "An unnamed app")
    boxes = "".join(
        f"<label><input type=checkbox name=scope value={escape(s)} checked"
        f"{' disabled' if s == Scope.READ else ''}> {escape(SCOPE_TEXT[Scope(s)])}"
        "</label>"
        for s in SCOPES
        if s in offered
    )
    return (
        "<h1>Connect an app</h1>"
        f"<p>An app that calls itself <strong>{name}</strong> asks to use Hey Carl"
        " as you. Approve it only if you started this, and only if you know"
        " this address:</p>"
        f"<p class=host>{escape(redirect.scheme)}://{escape(redirect.netloc)}</p>"
        f"<form method=post action={CONSENT_PATH}>"
        f"<input type=hidden name=request value={escape(request_id, quote=True)}>"
        f"<input type=hidden name=scope value={Scope.READ}>"
        f"{boxes}"
        "<button name=decision value=approve>Approve</button>"
        "<button name=decision value=deny>Deny</button>"
        "</form>"
    )


def consent_router(provider: Provider) -> APIRouter:
    router = APIRouter(include_in_schema=False)
    gone = "This request has expired or was already answered. Start again from the app."

    @router.get(CONSENT_PATH)
    async def consent(request: Request) -> HTMLResponse:
        caller(request)
        request_id = request.query_params.get("request", "")
        found = await provider.pending(request_id) if request_id else None
        if found is None:
            return _page(f"<p>{gone}</p>", status.HTTP_404_NOT_FOUND)
        return _page(consent_html(request_id, *found))

    @router.post(CONSENT_PATH)
    async def decide(request: Request) -> Response:
        who = caller(request)
        form = await request.form()
        request_id = form.get("request")
        if not isinstance(request_id, str):
            return PlainTextResponse(gone, status.HTTP_400_BAD_REQUEST)
        chosen = (
            [s for s in form.getlist("scope") if isinstance(s, str)]
            if form.get("decision") == "approve"
            else None
        )
        target = await provider.decide(request_id, who.sub, chosen)
        if target is None:
            return PlainTextResponse(gone, status.HTTP_404_NOT_FOUND)
        return RedirectResponse(
            target,
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"cache-control": "no-store"},
        )

    @router.get("/api/oauth/grants")
    async def list_grants(request: Request) -> list[oauth.Grant]:
        return await oauth.grants(request.app.state.pool, caller(request).sub)

    @router.delete(
        "/api/oauth/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    async def revoke(grant_id: int, request: Request) -> None:
        pool = request.app.state.pool
        if not await oauth.revoke_grant(pool, caller(request).sub, grant_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such connection")

    return router
