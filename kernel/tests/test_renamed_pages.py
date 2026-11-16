"""A page URL from before a plugin rename still opens the page."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from heycarl.app import renamed_path
from heycarl.migrations import RENAMES

from .conftest import as_user, needs_database

if TYPE_CHECKING:
    import httpx


def test_renamed_path_keeps_the_page() -> None:
    [crm] = RENAMES
    assert renamed_path(crm, "heycarl-crm") == "/p/prm/heycarl-prm"
    assert renamed_path(crm, "other") == "/p/prm/other"


@pytest.mark.anyio
@needs_database
async def test_the_old_page_url_redirects(client: httpx.AsyncClient) -> None:
    answer = await client.get(
        "/p/crm/heycarl-crm?x=1", headers=as_user("rename-redirect")
    )
    assert answer.status_code == 308
    assert answer.headers["location"] == "/p/prm/heycarl-prm?x=1"
