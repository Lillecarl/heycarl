from __future__ import annotations

import os
from typing import TYPE_CHECKING

from heycarl.storefiles import REVALIDATE, etag, store_file
from starlette.datastructures import Headers

if TYPE_CHECKING:
    from pathlib import Path


def builds(tmp_path: Path) -> tuple[Path, Path]:
    """Two builds of one file: same size, same mtime, other content."""
    old, new = (
        tmp_path / "aaa-plugin" / "index.js",
        tmp_path / "bbb-plugin" / "index.js",
    )
    for path, text in ((old, "old"), (new, "new")):
        path.parent.mkdir()
        path.write_text(text)
        os.utime(path, (1, 1))
    return old, new


def test_another_build_has_another_etag(tmp_path: Path) -> None:
    old, new = builds(tmp_path)
    assert etag(old) != etag(new)


def test_a_link_has_its_targets_etag(tmp_path: Path) -> None:
    old, _ = builds(tmp_path)
    link = tmp_path / "current"
    link.symlink_to(old.parent)
    assert etag(link / "index.js") == etag(old)


def test_revalidation(tmp_path: Path) -> None:
    old, new = builds(tmp_path)
    first = store_file(old, Headers(), REVALIDATE, old.stat())
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"
    assert "last-modified" not in first.headers

    asked = Headers({"if-none-match": first.headers["etag"]})
    assert store_file(old, asked, REVALIDATE, old.stat()).status_code == 304
    assert store_file(new, asked, REVALIDATE, new.stat()).status_code == 200
