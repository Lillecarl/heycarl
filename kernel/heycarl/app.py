from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Final

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

from . import migrations, postgrest
from .identity import Caller, caller
from .tokens import Minter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    import httpx

    from .config import Config
    from .plugins import Plugin

log = structlog.get_logger()

CORE_SCOPE: Final = "core"
DATA_METHODS: Final = ["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"]


@dataclass
class PluginContext:
    """What a plugin backend's `register(ctx)` gets."""

    plugin: Plugin
    # Mounted at /api/plugins/<name>, behind the gate.
    router: APIRouter = field(default_factory=APIRouter)
    # Mounted at /api/public/<name>. The gate lets these through with no
    # session, so each route checks its own secret or signature.
    public_router: APIRouter = field(default_factory=APIRouter)


@dataclass
class Kernel:
    config: Config
    plugins: list[Plugin]
    minter: Minter
    data: httpx.AsyncClient

    def schema_for(self, scope: str) -> str | None:
        if scope == CORE_SCOPE:
            return migrations.API_SCHEMA
        for plugin in self.plugins:
            if plugin.name == scope:
                return plugin.schema
        return None


def _kernel(request: Request) -> Kernel:
    kernel: Kernel = request.app.state.kernel
    return kernel


KernelDep = Annotated[Kernel, Depends(_kernel)]
CallerDep = Annotated[Caller, Depends(caller)]


def _register_backends(app: FastAPI, plugins: list[Plugin]) -> None:
    for plugin in plugins:
        if plugin.manifest.backend is None:
            continue
        module = importlib.import_module(plugin.manifest.backend)
        register: Callable[[PluginContext], None] = module.register
        ctx = PluginContext(plugin=plugin)
        register(ctx)
        app.include_router(ctx.router, prefix=f"/api/plugins/{plugin.name}")
        app.include_router(ctx.public_router, prefix=f"/api/public/{plugin.name}")


def create_app(config: Config, plugins: list[Plugin]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await migrations.migrate(config.database, plugins)
        async with postgrest.client(config.postgrest_socket) as data:
            app.state.kernel = Kernel(
                config=config,
                plugins=plugins,
                minter=Minter(config.jwt_secret_file.read_bytes().strip()),
                data=data,
            )
            yield

    app = FastAPI(title="Hey Carl", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/me")
    async def me(who: CallerDep) -> Caller:
        return who

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

    _register_backends(app, plugins)

    for plugin in plugins:
        if plugin.manifest.frontend is not None:
            app.mount(
                f"/plugins/{plugin.name}",
                StaticFiles(directory=plugin.root / plugin.manifest.frontend.directory),
                name=f"plugin-{plugin.name}",
            )

    static = config.static_dir
    if static is not None:
        index = static / "index.html"
        app.mount("/_app", StaticFiles(directory=static / "_app"), name="shell-assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def shell(path: str) -> FileResponse:
            # Files at the top level (manifest, service worker, icons), else the
            # SPA's index for any client-side route.
            candidate = (static / path).resolve()
            if (
                path
                and candidate.is_relative_to(static.resolve())
                and candidate.is_file()
            ):
                return FileResponse(candidate)
            return FileResponse(index)

    return app
