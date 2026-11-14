from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

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


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: int = Field(ge=1, le=1)
    name: str = Field(pattern=NAME_PATTERN)
    title: str
    migrations: str | None = None
    # Python module path with `register(ctx)`.
    backend: str | None = None
    frontend: Frontend | None = None


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
    return plugins


def is_valid_name(name: str) -> bool:
    return re.fullmatch(NAME_PATTERN, name) is not None
