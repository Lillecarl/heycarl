from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MANIFEST_NAME: Final = "heycarl-plugin.json"
SCHEMA_PREFIX: Final = "heycarl_plugin_"
# A plugin name becomes part of a schema name and a URL path.
NAME_PATTERN: Final = r"^[a-z][a-z0-9_]{0,30}$"


class Frontend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Served at /plugins/<name>/, relative to the plugin directory.
    directory: str
    # ES module in `directory` that defines the custom elements.
    module: str
    # Custom element name -> navigation title.
    pages: dict[str, str] = {}
    # Table -> custom element that shows one entry of it inside another
    # plugin's page. It gets the entry's key as its `key` property.
    embeds: dict[str, str] = {}


GoogleScope = Annotated[
    str, StringConstraints(pattern=r"^https://www\.googleapis\.com/auth/[a-z0-9._/-]+$")
]


class Google(BaseModel):
    """The plugin reads Google APIs through the kernel's Google accounts."""

    model_config = ConfigDict(extra="forbid")
    scopes: list[GoogleScope] = Field(min_length=1)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: int = Field(ge=1, le=1)
    name: str = Field(pattern=NAME_PATTERN)
    title: str
    # Plugins whose schema this one's migrations use; the kernel migrates
    # them first.
    requires: list[str] = []
    migrations: str | None = None
    # Python module path with `register(ctx)`.
    backend: str | None = None
    frontend: Frontend | None = None
    google: Google | None = None


@dataclass(frozen=True, slots=True)
class Plugin:
    manifest: Manifest
    root: Path

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def schema(self) -> str:
        return SCHEMA_PREFIX + self.manifest.name

    @property
    def migrations_dir(self) -> Path | None:
        if self.manifest.migrations is None:
            return None
        return self.root / self.manifest.migrations


def load_plugin(root: Path) -> Plugin:
    manifest = Manifest.model_validate_json((root / MANIFEST_NAME).read_text())
    return Plugin(manifest=manifest, root=root)


def load_plugins(plugins_file: Path | None) -> list[Plugin]:
    if plugins_file is None:
        return []
    roots = [Path(p) for p in json.loads(plugins_file.read_text())]
    plugins = [load_plugin(root) for root in roots]
    names = [p.name for p in plugins]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise RuntimeError(f"duplicate plugin names: {sorted(duplicates)}")
    return in_requires_order(plugins)


def in_requires_order(plugins: list[Plugin]) -> list[Plugin]:
    """Each plugin after the ones it requires, else in the set's order.
    Migrations run in this order, so a plugin's may use a required schema."""
    by_name = {p.name: p for p in plugins}
    for plugin in plugins:
        missing = [r for r in plugin.manifest.requires if r not in by_name]
        if missing:
            raise RuntimeError(
                f"plugin {plugin.name} requires {missing}, not installed"
            )
    ordered: list[Plugin] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(plugin: Plugin) -> None:
        if plugin.name in done:
            return
        if plugin.name in visiting:
            raise RuntimeError(f"plugins require each other: {sorted(visiting)}")
        visiting.add(plugin.name)
        for name in plugin.manifest.requires:
            visit(by_name[name])
        visiting.discard(plugin.name)
        done.add(plugin.name)
        ordered.append(plugin)

    for plugin in plugins:
        visit(plugin)
    return ordered


def is_valid_name(name: str) -> bool:
    return re.fullmatch(NAME_PATTERN, name) is not None
