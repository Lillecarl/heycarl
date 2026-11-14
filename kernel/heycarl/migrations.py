from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg
import structlog
from psycopg import sql

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .plugins import Plugin

log = structlog.get_logger()

CORE_SCOPE: Final = "core"
API_SCHEMA: Final = "heycarl"
# pg_advisory_xact_lock key: two kernels starting at once migrate one at a time.
LOCK_KEY: Final = 0x4E7CA21
LOCK_TIMEOUT: Final = "30s"


@dataclass(frozen=True, slots=True)
class Migration:
    scope: str
    name: str
    text: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


class MigrationError(RuntimeError):
    pass


def schema_dir() -> Path:
    return Path(str(resources.files("heycarl") / "schema"))


def read_dir(scope: str, directory: Path) -> list[Migration]:
    return [
        Migration(scope=scope, name=path.name, text=path.read_text())
        for path in sorted(directory.glob("*.sql"))
    ]


def pending(
    available: Sequence[Migration], applied: dict[str, str], scope: str
) -> list[Migration]:
    """The migrations still to run, after checking history matches the files.

    `applied` maps name to checksum for this scope.
    """
    names = {m.name for m in available}
    unknown = sorted(set(applied) - names)
    if unknown:
        raise MigrationError(
            f"{scope}: database has migrations this build lacks: {unknown}"
        )
    for m in available:
        if m.name in applied and applied[m.name] != m.checksum:
            raise MigrationError(f"{scope}: applied migration {m.name} was edited")
    return [m for m in available if m.name not in applied]


BOOTSTRAP: Final = """
create schema if not exists heycarl_private;
create table if not exists heycarl_private.migration (
  scope text not null,
  name text not null,
  checksum text not null,
  applied_at timestamptz not null default now(),
  primary key (scope, name)
);
"""


async def _applied(
    cur: psycopg.AsyncCursor[tuple[str, str]], scope: str
) -> dict[str, str]:
    await cur.execute(
        "select name, checksum from heycarl_private.migration where scope = %s",
        (scope,),
    )
    return dict(await cur.fetchall())


async def _run_scope(
    cur: psycopg.AsyncCursor[tuple[str, str]],
    scope: str,
    available: Sequence[Migration],
    search_path: str | None,
) -> None:
    todo = pending(available, await _applied(cur, scope), scope)
    for m in todo:
        log.info("migration_apply", scope=scope, name=m.name)
        if search_path is not None:
            await cur.execute(
                sql.SQL("set local search_path = {}, public").format(
                    sql.Identifier(search_path)
                )
            )
        await cur.execute(m.text.encode())
        await cur.execute("reset search_path")
        await cur.execute(
            "insert into heycarl_private.migration (scope, name, checksum) values (%s, %s, %s)",
            (m.scope, m.name, m.checksum),
        )


async def migrate(conninfo: str, plugins: Sequence[Plugin]) -> None:
    """Bring the database to this build, in one transaction.

    Core migrations, then each plugin's in its own schema, then the API
    views from scratch. Ends with NOTIFY pgrst so PostgREST reloads its
    schema cache; a rollback drops the NOTIFY too.
    """
    base = schema_dir()
    async with (
        await psycopg.AsyncConnection.connect(conninfo) as conn,
        conn.transaction(),
        conn.cursor() as cur,
    ):
        await cur.execute(
            sql.SQL("set local lock_timeout = {}").format(sql.Literal(LOCK_TIMEOUT))
        )
        await cur.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        await cur.execute(BOOTSTRAP.encode())
        await _run_scope(
            cur, CORE_SCOPE, read_dir(CORE_SCOPE, base / "migrations"), None
        )
        for plugin in plugins:
            directory = plugin.migrations_dir
            if directory is None:
                continue
            await cur.execute(
                sql.SQL("create schema if not exists {}").format(
                    sql.Identifier(plugin.schema)
                )
            )
            # PostgREST's schema cache needs it before any role switch.
            await cur.execute(
                sql.SQL("grant usage on schema {} to heycarl_authenticator").format(
                    sql.Identifier(plugin.schema)
                )
            )
            scope = "plugin:" + plugin.name
            await _run_scope(cur, scope, read_dir(scope, directory), plugin.schema)
        await cur.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(API_SCHEMA)
            )
        )
        await cur.execute(
            sql.SQL("create schema {}").format(sql.Identifier(API_SCHEMA))
        )
        for view in read_dir("api", base / "api"):
            await cur.execute(view.text.encode())
        await cur.execute("notify pgrst, 'reload schema'")
    log.info("migrations_done", plugins=[p.name for p in plugins])
