"""Content-based block-page detection (D-017, OPERATIONS "Per-site fetching behavior").

Some sites answer throttled or filtered clients with HTTP 200 and a small HTML
page instead of the requested content. Adapters must call
``detect_block_page`` (or ``detect_block_response``) on every listing response
before parsing, and report a hit as ``block_page_detected`` evidence, never as
an empty list.

Signatures are deliberately narrow: each needs a marker specific to a known
block page, and title-only signatures apply only to small documents, so normal
pages that merely contain a CAPTCHA-protected feedback form or the Coal
anti-bot ``<script src="/bnith__...">`` tag are not flagged.
"""

from __future__ import annotations

import re

from policy_tracker.adapters.base import FetchResponse

CAPTCHA = "captcha"
BROWSER_BLOCK = "browser_block"
WAF_REJECTED = "waf_rejected"
CHALLENGE = "challenge"
BLOCK_REASONS = frozenset({CAPTCHA, BROWSER_BLOCK, WAF_REJECTED, CHALLENGE})

_SCAN_BYTES = 64 * 1024  # block pages are about 1 KB; never scan a whole large page
_SMALL_PAGE_BYTES = 16 * 1024  # title-only signatures apply below this size
_TITLE = re.compile(rb"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_SPACE = re.compile(r"\s+")

# ministry_of_coal/captcha_block.html: coal.nic.in "Validation request" form.
_CAPTCHA_FORM_MARKERS = ('action="/captcha_resp"', 'name="captcha_resp_txt"')
_CAPTCHA_TITLES = frozenset({"validation request"})
_CAPTCHA_TEXT = "user validation required to continue"
# ministry_of_power/browser_block.html and the common CDN "Access Denied" page.
_BROWSER_BLOCK_TEXT = "the requested browser client was blocked"
_ACCESS_DENIED_TITLES = frozenset({"access denied", "403 forbidden", "forbidden"})
# F5 BIG-IP ASM rejection page, widely used on Indian government hosts.
_WAF_TEXT = "the requested url was rejected. please consult with your administrator"
# Interstitial JavaScript/CAPTCHA challenges from CDN bot managers.
_CHALLENGE_MARKERS = ("cf-chl-", "challenge-platform", "_incapsula_resource")
_CHALLENGE_TITLES = frozenset({"just a moment...", "attention required! | cloudflare"})

_BINARY_TYPES = ("application/pdf", "application/zip", "image/", "audio/", "video/")


def _looks_like_markup(head: bytes) -> bool:
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return stripped.startswith(b"<")


def detect_block_page(body: bytes, content_type: str | None) -> str | None:
    """Return a block reason from ``BLOCK_REASONS`` if ``body`` is a block page, else None.

    Only markup bodies are inspected; ``content_type`` is advisory, so an HTML
    block page served as ``application/json`` (an API URL) is still caught,
    while a body declared as PDF, image, or archive is never flagged.
    """

    head = body[:_SCAN_BYTES]
    media = (content_type or "").split(";", 1)[0].strip().lower()
    if not _looks_like_markup(head):
        return None  # JSON, PDF, plain text, or binary: not a block page
    if media.startswith(_BINARY_TYPES):
        return None  # a PDF/image declared binary is never parsed as HTML anyway

    text = head.decode("utf-8", errors="replace").lower()
    match = _TITLE.search(head)
    title = ""
    if match:
        title = _SPACE.sub(" ", match.group(1).decode("utf-8", "replace")).strip().lower()
    small = len(body) <= _SMALL_PAGE_BYTES

    if any(marker in text for marker in _CAPTCHA_FORM_MARKERS):
        return CAPTCHA
    if small and title in _CAPTCHA_TITLES and ("captcha" in text or _CAPTCHA_TEXT in text):
        return CAPTCHA
    if _BROWSER_BLOCK_TEXT in text:
        return BROWSER_BLOCK
    if small and title in _ACCESS_DENIED_TITLES:
        return BROWSER_BLOCK
    if small and _WAF_TEXT in _SPACE.sub(" ", text):
        return WAF_REJECTED
    if small and (title in _CHALLENGE_TITLES or any(m in text for m in _CHALLENGE_MARKERS)):
        return CHALLENGE
    return None


def detect_block_response(response: FetchResponse) -> str | None:
    """``detect_block_page`` applied to a successful ``FetchResponse``."""

    if response.error is not None or not response.body:
        return None
    content_type = None
    for key, value in response.headers.items():
        if key.lower() == "content-type":
            content_type = value
            break
    return detect_block_page(response.body, content_type)
