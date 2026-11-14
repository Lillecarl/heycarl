from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Final

import anyio
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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import migrations, postgrest, push
from .identity import Caller, caller
from .tokens import Minter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

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
            postgrest.client(config.postgrest_socket) as data,
            anyio.create_task_group() as tg,
        ):
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

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

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
                    "module": f"/plugins/{p.name}/{p.manifest.frontend.module}",
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

    for plugin in plugins:
        if plugin.manifest.frontend is not None:
            app.mount(
                f"/plugins/{plugin.name}",
                StaticFiles(
                    directory=plugin.root / plugin.manifest.frontend.directory,
                    # Missing in a source-only plugin, as in the kernel's tests.
                    check_dir=False,
                ),
                name=f"plugin-{plugin.name}",
            )

    static = config.static_dir
    if static is not None:
        index = static / "index.html"
        app.mount("/_app", StaticFiles(directory=static / "_app"), name="shell-assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def shell(path: str) -> FileResponse:
            # Files at the top level (manifest, service worker, icons), else the
            # SPA's index for any client-side route. Never for an API path: a
            # caller expecting JSON must not get a page and a 200.
            if path.startswith(("api/", "plugins/")):
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            candidate = (static / path).resolve()
            if (
                path
                and candidate.is_relative_to(static.resolve())
                and candidate.is_file()
            ):
                return FileResponse(candidate)
            return FileResponse(index)

    return app
