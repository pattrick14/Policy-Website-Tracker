"""File-signature checks: compare the bytes a server sent with the type it claimed.

A claim comes from the URL extension first, then the ``Content-Type`` header.
A body whose magic bytes do not satisfy the claim (for example an HTML block
page served for a ``.pdf`` link) is rejected before hashing is trusted.
"""

from __future__ import annotations

from urllib.parse import urlsplit

PDF = "pdf"
ZIP = "zip"  # OOXML (xlsx, docx, pptx) and plain zip archives
OLE = "ole"  # legacy Office compound files (xls, doc, ppt)
HTML = "html"
UNKNOWN = "unknown"

_EXTENSION_CLAIMS = {
    ".pdf": PDF,
    ".xlsx": ZIP,
    ".docx": ZIP,
    ".pptx": ZIP,
    ".zip": ZIP,
    ".xls": OLE,
    ".doc": OLE,
    ".ppt": OLE,
    ".htm": HTML,
    ".html": HTML,
}

_CONTENT_TYPE_CLAIMS = {
    "application/pdf": PDF,
    "application/x-pdf": PDF,
    "application/zip": ZIP,
    "application/vnd.ms-excel": OLE,
    "application/msword": OLE,
    "application/vnd.ms-powerpoint": OLE,
    "text/html": HTML,
    "application/xhtml+xml": HTML,
}

# Legacy-Office links are often actually OOXML files; accept either container.
_ACCEPTS = {PDF: {PDF}, ZIP: {ZIP}, OLE: {OLE, ZIP}, HTML: {HTML}}

_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")


def detect(body: bytes) -> str:
    """Return the type implied by the leading bytes of ``body``."""

    head = body[:1024]
    if b"%PDF-" in head:  # the PDF header may follow a little leading junk
        return PDF
    if body.startswith(b"PK\x03\x04"):
        return ZIP
    if body.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return OLE
    lowered = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if any(marker in lowered for marker in _HTML_MARKERS):
        return HTML
    return UNKNOWN


def media_type(content_type: str | None) -> str | None:
    """Return the lower-cased media type without parameters."""

    if not content_type:
        return None
    value = content_type.split(";", 1)[0].strip().lower()
    return value or None


def claimed_type(url: str, content_type: str | None) -> str | None:
    """Return the type the link or server claims, or ``None`` when nothing is claimed."""

    path = urlsplit(url).path.lower()
    for extension, claim in _EXTENSION_CLAIMS.items():
        if path.endswith(extension):
            return claim
    media = media_type(content_type)
    if media is None:
        return None
    if media.startswith("application/vnd.openxmlformats-officedocument."):
        return ZIP
    return _CONTENT_TYPE_CLAIMS.get(media)


def satisfies(claimed: str | None, detected: str) -> bool:
    """True when the detected signature is acceptable for the claim."""

    if claimed is None:
        return True
    return detected in _ACCEPTS.get(claimed, {claimed})
