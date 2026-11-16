"""Error bodies in PostgREST's shape, `{code, message, details, hint}`.

/api/data passes PostgREST's errors through, so the kernel's own /api errors
use the same shape: a client parses one shape for every /api answer. These
handlers act on /api/ paths only; elsewhere (sign-in pages, consent, the MCP
mount) FastAPI's defaults stand. The gate in access.py uses `body` on every
path it refuses, DAV included. Exception: a browser navigation that lands
under /api (the calendar's OAuth callback) answers plain text for a person.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI, Request, Response

API_PREFIX = "/api/"


@dataclass(frozen=True, slots=True)
class Problem:
    code: str
    message: str
    # object: whatever explains the failure, such as validation errors.
    details: object = None
    hint: str | None = None


def problem(
    status_code: int,
    message: str | None = None,
    *,
    details: object = None,
    hint: str | None = None,
) -> Problem:
    """`code` is `HC` plus the status, as PostgREST's raised errors are `PT`
    plus the status."""
    return Problem(
        code=f"HC{status_code}",
        message=message or HTTPStatus(status_code).phrase,
        details=details,
        hint=hint,
    )


def body(status_code: int, message: str | None = None) -> bytes:
    return json.dumps(asdict(problem(status_code, message))).encode()


def response(
    status_code: int,
    message: str | None = None,
    *,
    details: object = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    found = problem(status_code, message, details=details)
    return JSONResponse(
        jsonable_encoder(asdict(found)),
        status_code=status_code,
        headers={"cache-control": "no-store", **(headers or {})},
    )


async def _http_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, HTTPException):
        raise exc
    if not request.url.path.startswith(API_PREFIX):
        return await http_exception_handler(request, exc)
    message, details = (
        (exc.detail, None) if isinstance(exc.detail, str) else (None, exc.detail)
    )
    return response(exc.status_code, message, details=details, headers=exc.headers)


async def _invalid_request(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RequestValidationError):
        raise exc
    if not request.url.path.startswith(API_PREFIX):
        return await request_validation_exception_handler(request, exc)
    return response(422, "The request is not valid.", details=exc.errors())


async def _crashed(request: Request, exc: Exception) -> Response:
    # Starlette logs and re-raises after this answer, as without a handler.
    if not request.url.path.startswith(API_PREFIX):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return response(500, "Hey Carl had a problem. Try again.")


def install(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(Exception, _crashed)
