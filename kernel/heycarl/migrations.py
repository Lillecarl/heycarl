from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg
import structlog
from psycopg import sql

from .plugins import SCHEMA_PREFIX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from psycopg.rows import TupleRow

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


@dataclass(frozen=True, slots=True)
class PluginRename:
    """A plugin renamed after databases ran its migrations.

    The migration files carry the new names; the applied ones were recorded
    with the old. Only these two strings changed in any applied file, so
    `backward` rebuilds the recorded text exactly and proves an edit was the
    rename and nothing else.
    """

    old: str
    new: str

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        return (
            (SCHEMA_PREFIX + self.old, SCHEMA_PREFIX + self.new),
            (f"/p/{self.old}/heycarl-{self.old}", f"/p/{self.new}/heycarl-{self.new}"),
        )

    def forward(self, text: str) -> str:
        for old, new in self.pairs:
            text = text.replace(old, new)
        return text

    def backward(self, text: str) -> str:
        for old, new in self.pairs:
            text = text.replace(new, old)
        return text


RENAMES: Final = (PluginRename("crm", "prm"),)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def _rename_plugin(
    cur: psycopg.AsyncCursor[TupleRow],
    rename: PluginRename,
    available: dict[str, Sequence[Migration]],
) -> None:
    """Rename a plugin in place, once: its schema, code and stored text.

    ALTER SCHEMA keeps every oid, so RLS, grants, history and links (all
    keyed by regclass) follow without a step of their own.
    """
    (old_schema, new_schema), (old_url, new_url) = rename.pairs
    await cur.execute(
        "select to_regnamespace(%s) is not null, to_regnamespace(%s) is not null",
        (old_schema, new_schema),
    )
    row = await cur.fetchone()
    if row is None or not row[0]:
        return
    if row[1]:
        raise MigrationError(f"both {old_schema} and {new_schema} exist")
    log.info("plugin_rename", old=rename.old, new=rename.new)
    await cur.execute(
        sql.SQL("alter schema {} rename to {}").format(
            sql.Identifier(old_schema), sql.Identifier(new_schema)
        )
    )
    await cur.execute(
        "update heycarl_private.migration set scope = %s where scope = %s",
        ("plugin:" + rename.new, "plugin:" + rename.old),
    )

    # Function bodies and their SET search_path are text, not oids.
    await cur.execute(
        """
        select pg_get_functiondef(p.oid)
          from pg_proc p join pg_namespace n on n.oid = p.pronamespace
         where n.nspname like 'heycarl%%' and p.prokind in ('f', 'p')
           and (p.prosrc like any (%(patterns)s)
                or array_to_string(p.proconfig, ' ') like any (%(patterns)s))
        """,
        {"patterns": [f"%{old_schema}%", f"%{old_url}%"]},
    )
    for (definition,) in await cur.fetchall():
        await cur.execute(rename.forward(definition).encode())

    await cur.execute(
        "update heycarl_private.tracked set route = replace(route, %s, %s)"
        " where route like %s",
        (old_url, new_url, f"%{old_url}%"),
    )
    await cur.execute(
        "update heycarl_private.version set data = replace(data::text, %s, %s)::jsonb"
        " where data::text like %s",
        (old_url, new_url, f"%{old_url}%"),
    )
    # Stored page links, such as a reminder's url. Version triggers stay off:
    # the rename is not a change the person made.
    await cur.execute(
        """
        select c.table_schema, c.table_name, c.column_name
          from information_schema.columns c
          join information_schema.tables t using (table_schema, table_name)
         where c.table_schema like 'heycarl\\_plugin\\_%'
           and t.table_type = 'BASE TABLE' and c.data_type = 'text'
        """
    )
    for schema, table, column in await cur.fetchall():
        target = sql.Identifier(schema, table)
        await cur.execute(
            sql.SQL("select exists (select from {} where {} like %s)").format(
                target, sql.Identifier(column)
            ),
            (f"%{old_url}%",),
        )
        found = await cur.fetchone()
        if found is None or not found[0]:
            continue
        await cur.execute(sql.SQL("alter table {} disable trigger user").format(target))
        await cur.execute(
            sql.SQL(
                "update {} set {col} = replace({col}, %s, %s) where {col} like %s"
            ).format(target, col=sql.Identifier(column)),
            (old_url, new_url, f"%{old_url}%"),
        )
        await cur.execute(sql.SQL("alter table {} enable trigger user").format(target))

    await cur.execute("select scope, name, checksum from heycarl_private.migration")
    recorded = await cur.fetchall()
    for scope, name, checksum in recorded:
        current = next((m for m in available.get(scope, ()) if m.name == name), None)
        if current is None or current.checksum == checksum:
            continue
        if _sha256(rename.backward(current.text)) == checksum:
            await cur.execute(
                "update heycarl_private.migration set checksum = %s"
                " where scope = %s and name = %s",
                (current.checksum, scope, name),
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
        available: dict[str, Sequence[Migration]] = {
            CORE_SCOPE: read_dir(CORE_SCOPE, base / "migrations")
        }
        for plugin in plugins:
            if plugin.migrations_dir is not None:
                scope = "plugin:" + plugin.name
                available[scope] = read_dir(scope, plugin.migrations_dir)
        for rename in RENAMES:
            await _rename_plugin(cur, rename, available)
        await _run_scope(cur, CORE_SCOPE, available[CORE_SCOPE], None)
        for plugin in plugins:
            scope = "plugin:" + plugin.name
            if scope not in available:
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
            await _run_scope(cur, scope, available[scope], plugin.schema)
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
