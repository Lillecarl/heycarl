"""Static files from the Nix store, with the store path as their ETag.

Every file in the store has mtime 1970. Starlette's ETag is mtime and size,
so two builds of a file with the same size share one, and a revalidation
answers 304 with the old file. And a Last-Modified of 1970 lets a browser
keep a file for years without asking. The resolved store path changes
whenever the content does, so it is the ETag, and there is no Last-Modified.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Final

from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles

if TYPE_CHECKING:
    import os

    from starlette.types import Scope

# For files whose name carries a content hash.
IMMUTABLE: Final = "public, max-age=31536000, immutable"
# Keep, but ask before each use; the ETag makes the answer a 304.
REVALIDATE: Final = "no-cache"


def version(path: Path) -> str:
    """Changes whenever the store path under `path` does."""
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:32]


def etag(path: Path) -> str:
    return f'"{version(path)}"'


def matches(tag: str, if_none_match: str) -> bool:
    tags = {t.strip().removeprefix("W/") for t in if_none_match.split(",")}
    return tag in tags or "*" in tags


def store_file(
    path: Path,
    request_headers: Headers,
    cache_control: str,
    stat_result: os.stat_result,
    status_code: int = 200,
) -> Response:
    tag = etag(path)
    response = FileResponse(
        path,
        status_code=status_code,
        stat_result=stat_result,
        headers={"etag": tag, "cache-control": cache_control},
    )
    del response.headers["last-modified"]
    if matches(tag, request_headers.get("if-none-match", "")):
        return NotModifiedResponse(response.headers)
    return response


class StoreFiles(StaticFiles):
    def __init__(
        self, *, cache_control: str, directory: Path, check_dir: bool = True
    ) -> None:
        super().__init__(directory=directory, check_dir=check_dir)
        self.cache_control = cache_control

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        return store_file(
            Path(full_path),
            Headers(scope=scope),
            self.cache_control,
            stat_result,
            status_code,
        )
