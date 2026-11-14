from __future__ import annotations

import importlib
import json
import os
import secrets
import sys
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Final

import anyio
import httpx
import structlog
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import PlainTextResponse, RedirectResponse
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from . import mcpserver, migrations, oauth, oidc, postgrest, push, sessions
from .access import AccessMiddleware, cookie_names, safe_next, set_cookie
from .identity import Caller, caller
from .storefiles import IMMUTABLE, REVALIDATE, StoreFiles, store_file, version
from .tokens import Minter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from .config import Config
    from .plugins import Plugin

log = structlog.get_logger()

CORE_SCOPE: Final = "core"
DATA_METHODS: Final = ["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"]


@dataclass
class Kernel:
    config: Config
    plugins: list[Plugin]
    minter: Minter
    data: httpx.AsyncClient
    pusher: push.Pusher

    def schema_for(self, scope: str) -> str | None:
        if scope == CORE_SCOPE:
            return migrations.API_SCHEMA
        for plugin in self.plugins:
            if plugin.name == scope:
                return plugin.schema
        return None

    async def as_worker(
        self,
        scope: str,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: object = None,
    ) -> httpx.Response:
        """PostgREST as heycarl_worker: for jobs, which have no caller.

        `body` is any JSON-serialisable value.
        """
        schema = self.schema_for(scope)
        if schema is None:
            raise LookupError(f"no data scope {scope!r}")
        headers = postgrest.request_headers(
            {"content-type": "application/json", "accept": "application/json"},
            self.minter.worker(),
            schema,
        )
        response = await self.data.request(
            method,
            "/" + path,
            params=params,
            headers=headers,
            content=None if body is None else json.dumps(body),
        )
        response.raise_for_status()
        return response


# Any: a coroutine's yield and send types, which a job never uses.
Job = Callable[[Kernel], Coroutine[Any, Any, None]]


@dataclass
class PluginContext:
    """What a plugin backend's `register(ctx)` gets."""

    plugin: Plugin
    # Mounted at /api/plugins/<name>, behind the gate.
    router: APIRouter = field(default_factory=APIRouter)
    # Mounted at /api/public/<name>. The gate lets these through with no
    # session, so each route checks its own secret or signature.
    public_router: APIRouter = field(default_factory=APIRouter)
    # Long-running tasks, started after migrations and cancelled at shutdown.
    # One that raises takes the kernel down, and systemd restarts it.
    jobs: list[Job] = field(default_factory=list)

    def job(self, fn: Job) -> Job:
        self.jobs.append(fn)
        return fn


def _kernel(request: Request) -> Kernel:
    kernel: Kernel = request.app.state.kernel
    return kernel


KernelDep = Annotated[Kernel, Depends(_kernel)]
CallerDep = Annotated[Caller, Depends(caller)]


def _register_backends(app: FastAPI, plugins: list[Plugin]) -> list[PluginContext]:
    contexts = []
    for plugin in plugins:
        if plugin.manifest.backend is None:
            continue
        # A plugin's Python lives in <plugin>/python; it may use only what
        # the kernel's own environment provides.
        sys.path.insert(0, str(plugin.root / "python"))
        module = importlib.import_module(plugin.manifest.backend)
        register: Callable[[PluginContext], None] = module.register
        ctx = PluginContext(plugin=plugin)
        register(ctx)
        app.include_router(ctx.router, prefix=f"/api/plugins/{plugin.name}")
        app.include_router(ctx.public_router, prefix=f"/api/public/{plugin.name}")
        contexts.append(ctx)
    return contexts


PURGE_SECONDS: Final = 3600.0


async def _purge_forever(pool: AsyncConnectionPool) -> None:
    # Expiry is checked on every lookup; this only keeps the tables small,
    # and nothing announces that a row expired.
    while True:
        try:
            await sessions.purge(pool)
            await oauth.purge(pool)
        except Exception:
            log.exception("session_purge_failed")
        await anyio.sleep(PURGE_SECONDS)


def _refuse(status_code: int, message: str) -> Response:
    return PlainTextResponse(
        message, status_code=status_code, headers={"cache-control": "no-store"}
    )


class SubscribeKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeRequest(BaseModel):
    """The browser's `PushSubscription.toJSON()`."""

    endpoint: str
    keys: SubscribeKeys


def create_app(config: Config, plugins: list[Plugin]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await migrations.migrate(config.database, plugins)
        minter = Minter(config.jwt_secret_file.read_bytes().strip())
        async with (
            AsyncConnectionPool(
                config.database, min_size=1, max_size=4, open=False
            ) as pool,
            httpx.AsyncClient(timeout=10) as http,
            postgrest.client(config.postgrest_socket) as data,
            anyio.create_task_group() as tg,
            server.session_manager.run(),
        ):
            app.state.pool = pool
            app.state.oidc = oidc.Client(
                http,
                config.oidc_issuer,
                config.oidc_client_id,
                config.oidc_client_secret_file.read_text().strip(),
            )
            tg.start_soon(_purge_forever, pool)
            kernel = Kernel(
                config=config,
                plugins=plugins,
                minter=minter,
                data=data,
                pusher=push.Pusher(
                    key=push.load_or_create_key(config.vapid_key_file),
                    subject=config.public_url,
                    data=data,
                    minter=minter,
                ),
            )
            app.state.kernel = kernel
            for ctx in contexts:
                for job in ctx.jobs:
                    tg.start_soon(job, kernel)
            yield
            tg.cancel_scope.cancel()

    app = FastAPI(title="Hey Carl", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(AccessMiddleware, public_url=config.public_url)
    names = cookie_names(config.public_url)
    redirect_uri = config.public_url + "/auth/callback"
    provider = oauth.Provider(lambda: app.state.pool, config.public_url)
    server = mcpserver.build(provider)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/login", include_in_schema=False)
    async def login(request: Request, next: str | None = None) -> Response:
        attempt = await sessions.start_login(request.app.state.pool, safe_next(next))
        client: oidc.Client = request.app.state.oidc
        url = oidc.authorization_url(
            await client.endpoints(),
            client_id=config.oidc_client_id,
            redirect_uri=redirect_uri,
            state=attempt.state,
            nonce=attempt.nonce,
            code_verifier=attempt.code_verifier,
        )
        response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
        lifetime = int(sessions.LOGIN_LIFETIME.total_seconds())
        response.headers.append(
            "set-cookie", set_cookie(names, names.login, attempt.state, lifetime)
        )
        response.headers["cache-control"] = "no-store"
        return response

    @app.get("/auth/callback", include_in_schema=False)
    async def callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> Response:
        if error or not code or not state:
            return _refuse(400, f"The sign-in failed: {error or 'no code'}.")
        # The state in the cookie proves this browser started this login, so
        # nobody can sign a victim in to an attacker's account.
        cookie = request.cookies.get(names.login)
        if cookie is None or not secrets.compare_digest(cookie, state):
            return _refuse(400, "This sign-in was started in another browser.")
        pool = request.app.state.pool
        attempt = await sessions.finish_login(pool, state)
        if attempt is None:
            return _refuse(400, "This sign-in has expired. Start again.")
        client: oidc.Client = request.app.state.oidc
        try:
            identity = await client.identity(
                code=code,
                redirect_uri=redirect_uri,
                code_verifier=attempt.code_verifier,
                nonce=attempt.nonce,
            )
        except oidc.LoginError as e:
            log.warning("login_refused", reason=str(e))
            return _refuse(403, f"The sign-in was refused: {e}.")
        if config.allowed_emails and identity.email not in config.allowed_emails:
            log.warning("login_not_allowed", sub=identity.sub, email=identity.email)
            return _refuse(403, "This account may not use Hey Carl.")
        token = await sessions.create(pool, identity)
        log.info("login", sub=identity.sub)
        response = RedirectResponse(attempt.next, status_code=status.HTTP_303_SEE_OTHER)
        lifetime = int(sessions.SESSION_LIFETIME.total_seconds())
        response.headers.append(
            "set-cookie", set_cookie(names, names.session, token, lifetime)
        )
        response.headers.append("set-cookie", set_cookie(names, names.login, "", 0))
        response.headers["cache-control"] = "no-store"
        return response

    @app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request, _: CallerDep) -> Response:
        token = request.cookies.get(names.session)
        if token:
            await sessions.delete(request.app.state.pool, token)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers.append("set-cookie", set_cookie(names, names.session, "", 0))
        return response

    @app.get("/api/me")
    async def me(who: CallerDep) -> Caller:
        return who

    @app.get("/api/push/key")
    async def push_key(kernel: KernelDep, _: CallerDep) -> dict[str, str]:
        return {"key": push.application_server_key(kernel.pusher.key)}

    @app.post("/api/push/subscribe", status_code=status.HTTP_204_NO_CONTENT)
    async def push_subscribe(
        body: SubscribeRequest, request: Request, kernel: KernelDep, who: CallerDep
    ) -> None:
        headers = postgrest.request_headers(
            {"content-type": "application/json"},
            kernel.minter.user(who.sub),
            migrations.API_SCHEMA,
        )
        # A browser hands out the same endpoint again on re-subscribe.
        r = await kernel.data.delete(
            "/push_subscription",
            params={"endpoint": f"eq.{body.endpoint}"},
            headers=headers,
        )
        r.raise_for_status()
        r = await kernel.data.post(
            "/push_subscription",
            headers=headers,
            content=json.dumps(
                {
                    "endpoint": body.endpoint,
                    "p256dh": body.keys.p256dh,
                    "auth": body.keys.auth,
                    "user_agent": request.headers.get("user-agent", "")[:500],
                }
            ),
        )
        if r.is_error:
            raise HTTPException(r.status_code, r.text)

    @app.post("/api/push/test")
    async def push_test(kernel: KernelDep, who: CallerDep) -> dict[str, int]:
        delivered = await kernel.pusher.send(
            who.sub,
            push.Payload(title="Hey Carl", body="Notifications work.", tag="test"),
        )
        return {"delivered": delivered}

    @app.get("/api/plugins")
    async def list_plugins(kernel: KernelDep, _: CallerDep) -> list[dict[str, object]]:
        # object: the manifest's frontend block, passed through as JSON.
        return [
            {
                "name": p.name,
                "title": p.manifest.title,
                "frontend": None
                if p.manifest.frontend is None
                else {
                    # A new URL per build: a browser that cached the module
                    # under older headers never uses that copy again.
                    "module": f"/plugins/{p.name}/{p.manifest.frontend.module}"
                    f"?v={version(p.root)}",
                    "pages": p.manifest.frontend.pages,
                },
            }
            for p in kernel.plugins
        ]

    @app.api_route("/api/data/{scope}/{path:path}", methods=DATA_METHODS)
    async def data(
        scope: str, path: str, request: Request, kernel: KernelDep, who: CallerDep
    ) -> Response:
        schema = kernel.schema_for(scope)
        if schema is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no data scope {scope!r}")
        upstream = await kernel.data.request(
            request.method,
            "/" + path,
            params=request.query_params.multi_items(),
            headers=postgrest.request_headers(
                dict(request.headers), kernel.minter.user(who.sub), schema
            ),
            content=await request.body(),
        )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=postgrest.response_headers(upstream.headers),
        )

    contexts = _register_backends(app, plugins)
    app.include_router(mcpserver.consent_router(provider))
    mcpserver.mount(app, server)

    for plugin in plugins:
        if plugin.manifest.frontend is not None:
            app.mount(
                f"/plugins/{plugin.name}",
                StoreFiles(
                    cache_control=REVALIDATE,
                    directory=plugin.root / plugin.manifest.frontend.directory,
                    # Missing in a source-only plugin, as in the kernel's tests.
                    check_dir=False,
                ),
                name=f"plugin-{plugin.name}",
            )

    static = config.static_dir
    if static is not None:
        index = static / "index.html"
        app.mount(
            "/_app/immutable",
            StoreFiles(
                cache_control=IMMUTABLE, directory=static / "_app" / "immutable"
            ),
            name="shell-immutable",
        )
        app.mount(
            "/_app",
            StoreFiles(cache_control=REVALIDATE, directory=static / "_app"),
            name="shell-assets",
        )

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def shell(path: str, request: Request) -> Response:
            # Files at the top level (manifest, service worker, icons), else the
            # SPA's index for any client-side route. Never for an API path: a
            # caller expecting JSON must not get a page and a 200.
            if path.startswith(("api/", "plugins/", "auth/", ".well-known/")):
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            candidate = (static / path).resolve()
            target = (
                candidate
                if path
                and candidate.is_relative_to(static.resolve())
                and candidate.is_file()
                else index
            )
            stat_result = await anyio.to_thread.run_sync(os.stat, target)
            return store_file(target, request.headers, REVALIDATE, stat_result)

    return app
