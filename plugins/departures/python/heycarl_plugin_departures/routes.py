"""Site search and live departures for the page. Both answer public SL data
and read no rows: the page reads its own stops through /api/data, as the
person, and asks here with a stop's filter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Path, Query, status
from heycarl.identity import Caller, caller

from .sl import MAX_LINES, MAX_SITE_ID, Board, Filter, Site, SLError, Transport, select

if TYPE_CHECKING:
    from fastapi import APIRouter

    from .sl import SL

CallerDep = Annotated[Caller, Depends(caller)]


def _unavailable(e: SLError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"No departures: {e}.")


def register_routes(router: APIRouter, sl: SL) -> None:
    @router.get("/sites")
    async def sites(
        _: CallerDep, q: Annotated[str, Query(min_length=1, max_length=100)]
    ) -> dict[str, list[Site]]:
        try:
            found = (await sl.sites()).search(q)
        except SLError as e:
            raise _unavailable(e) from e
        return {"sites": found}

    @router.get("/sites/{site_id}/departures")
    async def departures(
        _: CallerDep,
        site_id: Annotated[int, Path(ge=1, le=MAX_SITE_ID)],
        transport: Transport | None = None,
        line: Annotated[list[int], Query(max_length=MAX_LINES)] = [],  # noqa: B006 -- FastAPI copies it
        direction: Annotated[int | None, Query(ge=1, le=2)] = None,
    ) -> Board:
        try:
            board = await sl.board(site_id)
        except SLError as e:
            raise _unavailable(e) from e
        return select(
            board, Filter(transport=transport, lines=line, direction=direction)
        )
