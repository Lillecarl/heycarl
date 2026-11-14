from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio
import uvicorn

from . import config, migrations, plugins
from .app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heycarl")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the kernel")
    serve.add_argument(
        "--socket", type=Path, required=True, help="unix socket to listen on"
    )
    sub.add_parser("migrate", help="bring the database to this build and exit")
    sub.add_parser("roles-sql", help="print the path of the superuser roles script")
    args = parser.parse_args(argv)

    if args.command == "roles-sql":
        print(migrations.schema_dir() / "roles.sql")
        return 0

    cfg = config.from_env()
    loaded = plugins.load_plugins(cfg.plugins_file)
    if args.command == "migrate":
        anyio.run(migrations.migrate, cfg.database, loaded)
        return 0

    uvicorn.run(
        create_app(cfg, loaded),
        uds=str(args.socket),
        proxy_headers=False,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
