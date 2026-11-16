"""Renaming a plugin in a database that ran it: crm became prm."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psycopg
import pytest
from heycarl import migrations, plugins
from heycarl.migrations import RENAMES, PluginRename
from psycopg import sql

from .conftest import needs_database

if TYPE_CHECKING:
    from collections.abc import Iterator

CRM: Final = RENAMES[0]
DATABASE: Final = "heycarl_rename"
USER: Final = "rename-anna"

# What hetztop recorded before the rename: sha256 of each file as it was.
# A rename step that rebuilds a different text fails here, not in production.
DEPLOYED: Final = {
    (
        "plugin:crm",
        "0001_crm.sql",
    ): "1626ea4d877d5fa1f111e7d79fffc27b31042eb5f8fa0846a5ed0e9a14237091",
    (
        "plugin:crm",
        "0002_touch_due_zone.sql",
    ): "cd6f9e88d1d32a9a73690b58efa87188b115a6d31be5c20a126a0ff84530f308",
    (
        "plugin:crm",
        "0003_history.sql",
    ): "2f0587483eb236f600364c5f745a7315b47e8c37cc7d0a488ed3fbdc85c7dab3",
    (
        "plugin:crm",
        "0004_entry_route.sql",
    ): "feb05a00b57f0d4946445af8deb00750b36ab05a6a8c23bcf38c287fea0954fd",
    (
        "plugin:calendar",
        "0001_calendar.sql",
    ): "dba25e5c041b954a98059bfddae79fa02d4ab20a70cbb3a03cd9388c8073d591",
    (
        "core",
        "0008_link.sql",
    ): "5f0baaafac3ed77fa030e4f5d4b888aa62c1b0faa5b7f821a6775aeb2df3797c",
}


def _conninfo(user: str, database: str = DATABASE) -> str:
    return f"host={os.environ['PGHOST']} dbname={database} user={user}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _by_name(plugin_dirs: list[str]) -> dict[str, Path]:
    return {plugins.load_plugin(Path(d)).name: Path(d) for d in plugin_dirs}


def _migration_text(plugin_dirs: list[str], scope: str, name: str) -> str:
    if scope == migrations.CORE_SCOPE:
        return (migrations.schema_dir() / "migrations" / name).read_text()
    plugin = scope.removeprefix("plugin:")
    root = _by_name(plugin_dirs)[CRM.new if plugin == CRM.old else plugin]
    return (root / "migrations" / name).read_text()


@pytest.mark.parametrize(("scope", "name"), sorted(DEPLOYED))
def test_backward_rebuilds_the_deployed_text(
    plugin_dirs: list[str], scope: str, name: str
) -> None:
    text = _migration_text(plugin_dirs, scope, name)
    assert _sha256(CRM.backward(text)) == DEPLOYED[scope, name]


def test_forward_and_backward_are_inverse() -> None:
    rename = PluginRename("crm", "prm")
    text = "set search_path = heycarl_plugin_crm; url '/p/crm/heycarl-crm#/contact/1'"
    assert rename.backward(rename.forward(text)) == text
    assert "crm" not in rename.forward(text)


def _crm_era(plugin_dirs: list[str], into: Path) -> list[str]:
    """The plugin directories as they were before the rename."""
    out = []
    for name, root in _by_name(plugin_dirs).items():
        if name not in (CRM.new, "calendar"):
            out.append(str(root))
            continue
        old = into / (CRM.old if name == CRM.new else name)
        shutil.copytree(root, old)
        old.chmod(0o755)
        for path in old.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        for path in (old / "migrations").glob("*.sql"):
            path.write_text(CRM.backward(path.read_text()))
        manifest = json.loads((old / plugins.MANIFEST_NAME).read_text())
        if name == CRM.new:
            manifest["name"] = CRM.old
        (old / plugins.MANIFEST_NAME).write_text(json.dumps(manifest))
        out.append(str(old))
    return out


@pytest.fixture
def database() -> Iterator[str]:
    admin = _conninfo("postgres", "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        name = sql.Identifier(DATABASE)
        conn.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        conn.execute(sql.SQL("create database {} owner heycarl_owner").format(name))
        conn.execute(sql.SQL("revoke all on database {} from public").format(name))
        conn.execute(
            sql.SQL("grant connect on database {} to heycarl_authenticator").format(
                name
            )
        )
    with psycopg.connect(_conninfo("postgres"), autocommit=True) as conn:
        conn.execute("revoke all on schema public from public")
        conn.execute("grant usage on schema public to heycarl_owner")
    yield _conninfo("heycarl_owner")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("drop database {} with (force)").format(sql.Identifier(DATABASE))
        )


def _as_person(conn: psycopg.Connection) -> None:
    conn.execute("set role heycarl_user")
    conn.execute(
        "select set_config('request.jwt.claims', %s, false)",
        (json.dumps({"sub": USER, "role": "heycarl_user", "via": "app"}),),
    )


@pytest.mark.anyio
@needs_database
async def test_a_crm_era_database_upgrades(
    plugin_dirs: list[str], tmp_path: Path, database: str
) -> None:
    old_dirs = _crm_era(plugin_dirs, tmp_path)
    await migrations.migrate(database, [plugins.load_plugin(Path(d)) for d in old_dirs])
    with psycopg.connect(database, autocommit=True) as conn:
        # The core file this build carries has the new example URL.
        conn.execute(
            "update heycarl_private.migration set checksum = %s"
            " where scope = 'core' and name = '0008_link.sql'",
            (DEPLOYED["core", "0008_link.sql"],),
        )

    with psycopg.connect(_conninfo("heycarl_authenticator"), autocommit=True) as conn:
        _as_person(conn)
        [(contact,)] = conn.execute(
            "insert into heycarl_plugin_crm.contact (first_name) values ('Anna') returning id"
        ).fetchall()
        url = f"/p/crm/heycarl-crm#/contact/{contact}"
        [(reminder,)] = conn.execute(
            "insert into heycarl_plugin_reminders.reminder (title, due_at, url)"
            " values ('Call Anna', now() + interval '1 day', %s) returning id",
            (url,),
        ).fetchall()
        conn.execute(
            "select heycarl.link('crm', 'contact', %s, 'reminders', 'reminder', %s)",
            (json.dumps({"id": contact}), json.dumps({"id": reminder})),
        )
        conn.execute("delete from heycarl_plugin_crm.contact where id = %s", (contact,))

    with psycopg.connect(database) as conn:
        [(versions_before,)] = conn.execute(
            "select count(*) from heycarl_private.version"
        ).fetchall()

    new = [plugins.load_plugin(Path(d)) for d in plugin_dirs]
    await migrations.migrate(database, new)
    # Once only: a second start finds nothing to rename and nothing edited.
    await migrations.migrate(database, new)

    with psycopg.connect(database) as conn:
        [(old_schema, new_schema)] = conn.execute(
            "select to_regnamespace('heycarl_plugin_crm') is not null,"
            " to_regnamespace('heycarl_plugin_prm') is not null"
        ).fetchall()
        assert (old_schema, new_schema) == (False, True)
        scopes = {
            s for (s,) in conn.execute("select scope from heycarl_private.migration")
        }
        assert "plugin:prm" in scopes and "plugin:crm" not in scopes
        stale = conn.execute(
            "select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname like 'heycarl%%'"
            " and (p.prosrc like any (%(p)s) or array_to_string(p.proconfig, ' ') like any (%(p)s))",
            {"p": ["%heycarl_plugin_crm%", "%/p/crm/%"]},
        ).fetchall()
        assert stale == []
        [(route,)] = conn.execute(
            "select route from heycarl_private.tracked"
            " where rel = 'heycarl_plugin_prm.contact'::regclass"
        ).fetchall()
        assert route == "/p/prm/heycarl-prm#/contact/{id}"
        [(stored,)] = conn.execute(
            "select url from heycarl_plugin_reminders.reminder where id = %s",
            (reminder,),
        ).fetchall()
        assert stored == f"/p/prm/heycarl-prm#/contact/{contact}"
        [(versions_after,)] = conn.execute(
            "select count(*) from heycarl_private.version"
        ).fetchall()
        assert versions_after == versions_before
        [(old_urls,)] = conn.execute(
            "select count(*) from heycarl_private.version where data::text like '%/p/crm/%'"
        ).fetchall()
        assert old_urls == 0

    with psycopg.connect(_conninfo("heycarl_authenticator"), autocommit=True) as conn:
        _as_person(conn)
        [(entry, items)] = conn.execute(
            "select entry, items from heycarl.trash()"
        ).fetchall()
        assert items[0]["scope"] == CRM.new
        conn.execute("select heycarl.restore(%s)", (entry,))
        [(name,)] = conn.execute(
            "select first_name from heycarl_plugin_prm.contact where id = %s",
            (contact,),
        ).fetchall()
        assert name == "Anna"
        [(linked,)] = conn.execute(
            "select jsonb_agg(l) from heycarl.links('reminders', 'reminder', %s) l",
            (json.dumps({"id": reminder}),),
        ).fetchall()
        assert [e["entry"]["scope"] for e in linked] == [CRM.new]
        assert linked[0]["entry"]["url"] == f"/p/prm/heycarl-prm#/contact/{contact}"
