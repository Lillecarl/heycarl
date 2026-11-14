from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ENV_PREFIX: Final = "HEYCARL_"


@dataclass(frozen=True, slots=True)
class Config:
    # libpq connection string for heycarl_owner, which runs migrations.
    database: str
    # PostgREST's unix socket.
    postgrest_socket: Path
    # The key PostgREST verifies the kernel's tokens with (HS256).
    jwt_secret_file: Path
    # JSON list of plugin directories, built by Nix.
    plugins_file: Path | None
    # The built shell: index.html and its assets.
    static_dir: Path | None
    # The VAPID private key (PEM), made on first start.
    vapid_key_file: Path
    # https://hey.example.com: VAPID's contact for the push services.
    public_url: str


def _path(name: str) -> Path | None:
    value = os.environ.get(ENV_PREFIX + name)
    return Path(value) if value else None


def _required(name: str) -> str:
    value = os.environ.get(ENV_PREFIX + name)
    if not value:
        raise RuntimeError(f"{ENV_PREFIX}{name} is not set")
    return value


def from_env() -> Config:
    return Config(
        database=_required("DATABASE"),
        postgrest_socket=Path(_required("POSTGREST_SOCKET")),
        jwt_secret_file=Path(_required("JWT_SECRET_FILE")),
        plugins_file=_path("PLUGINS_FILE"),
        static_dir=_path("STATIC_DIR"),
        vapid_key_file=Path(_required("VAPID_KEY_FILE")),
        public_url=_required("PUBLIC_URL"),
    )
