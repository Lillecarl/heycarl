"""The WebDAV root that CalDAV and CardDAV clients discover.

The gate has already checked the app password (access.py, Access.DAV).
Address books and calendars come later (issues #2 and #3); this answers
only what a client asks to find its principal.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Final

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as parse_xml
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

if TYPE_CHECKING:
    from .apppasswords import Credential

DAV_NS: Final = "DAV:"
ROOT: Final = "/dav/"
MAX_BODY: Final = 64 * 1024
# What the root holds now. A client asks for more; those get a 404 propstat.
DAV_HEADER: Final = "1, 3"
ALLOW: Final = "OPTIONS, PROPFIND"

router = APIRouter(include_in_schema=False)


def credential_of(request: Request) -> Credential:
    found: Credential | None = getattr(request.state, "app_password", None)
    if found is None:
        # Only a route outside /dav/ could get here, and none uses this.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "app password needed")
    return found


def _tag(name: str) -> str:
    return f"{{{DAV_NS}}}{name}"


def requested(body: bytes) -> list[str] | None:
    """The property tags a PROPFIND asks for; None means all of them."""
    if not body.strip():
        return None
    try:
        root = parse_xml(body, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad XML") from e
    if root.tag != _tag("propfind"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a propfind")
    prop = root.find(_tag("prop"))
    if prop is None:
        return None
    return [child.tag for child in prop]


def multistatus(href: str, wanted: list[str] | None, name: str) -> bytes:
    known: dict[str, ET.Element] = {}
    resourcetype = ET.Element(_tag("resourcetype"))
    ET.SubElement(resourcetype, _tag("collection"))
    ET.SubElement(resourcetype, _tag("principal"))
    known[resourcetype.tag] = resourcetype
    principal = ET.Element(_tag("current-user-principal"))
    ET.SubElement(principal, _tag("href")).text = ROOT
    known[principal.tag] = principal
    displayname = ET.Element(_tag("displayname"))
    displayname.text = name
    known[displayname.tag] = displayname

    root = ET.Element(_tag("multistatus"))
    response = ET.SubElement(root, _tag("response"))
    ET.SubElement(response, _tag("href")).text = href
    tags = list(known) if wanted is None else wanted
    found = [t for t in tags if t in known]
    missing = [t for t in tags if t not in known]
    for group, code in ((found, "200 OK"), (missing, "404 Not Found")):
        if not group:
            continue
        propstat = ET.SubElement(response, _tag("propstat"))
        props = ET.SubElement(propstat, _tag("prop"))
        for t in group:
            props.append(known[t] if t in known else ET.Element(t))
        ET.SubElement(propstat, _tag("status")).text = f"HTTP/1.1 {code}"
    ET.register_namespace("d", DAV_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


@router.api_route("/.well-known/caldav", methods=["GET", "PROPFIND"])
@router.api_route("/.well-known/carddav", methods=["GET", "PROPFIND"])
async def well_known() -> Response:
    # RFC 6764: a client starts here and follows the redirect.
    return RedirectResponse(ROOT, status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.api_route("/dav", methods=["OPTIONS"])
@router.api_route(ROOT, methods=["OPTIONS"])
async def options(request: Request) -> Response:
    credential_of(request)
    return Response(
        status_code=status.HTTP_200_OK, headers={"dav": DAV_HEADER, "allow": ALLOW}
    )


@router.api_route("/dav", methods=["PROPFIND"])
@router.api_route(ROOT, methods=["PROPFIND"])
async def propfind(request: Request) -> Response:
    credential_of(request)
    # Depth infinity walks everything; RFC 4918 lets a server refuse it.
    if request.headers.get("depth", "infinity") not in {"0", "1"}:
        return Response(
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:error xmlns:d="DAV:"><d:propfind-finite-depth/></d:error>',
            status_code=status.HTTP_403_FORBIDDEN,
            media_type="application/xml; charset=utf-8",
        )
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "body too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "body too large"
            )
    content = multistatus(ROOT, requested(bytes(body)), "Hey Carl")
    return Response(
        content,
        status_code=207,
        media_type="application/xml; charset=utf-8",
        headers={"dav": DAV_HEADER},
    )
