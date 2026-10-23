"""Block-page detection: known block fixtures are flagged, normal fixtures are not."""

from __future__ import annotations

from pathlib import Path

import pytest

from policy_tracker.adapters.base import FetchResponse
from policy_tracker.adapters.blocks import (
    BLOCK_REASONS,
    detect_block_page,
    detect_block_response,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BLOCK_FIXTURES = {
    "ministry_of_coal/captcha_block.html": "captcha",
    "ministry_of_power/browser_block.html": "browser_block",
}
NORMAL_FIXTURES = sorted(
    str(path.relative_to(FIXTURES))
    for path in FIXTURES.glob("*/*")
    if path.suffix in {".html", ".json", ".xml"}
    and str(path.relative_to(FIXTURES)) not in BLOCK_FIXTURES
)


@pytest.mark.parametrize(("name", "reason"), sorted(BLOCK_FIXTURES.items()))
def test_block_fixtures_are_detected(name: str, reason: str) -> None:
    body = (FIXTURES / name).read_bytes()
    assert detect_block_page(body, "text/html; charset=utf-8") == reason
    assert detect_block_page(body, None) == reason
    assert reason in BLOCK_REASONS


def test_block_page_served_with_json_type_is_detected() -> None:
    body = (FIXTURES / "ministry_of_power/browser_block.html").read_bytes()
    assert detect_block_page(body, "application/json") == "browser_block"


def test_every_normal_fixture_passes() -> None:
    assert len(NORMAL_FIXTURES) > 60
    flagged = {
        name: detect_block_page((FIXTURES / name).read_bytes(), None) for name in NORMAL_FIXTURES
    }
    assert {name: reason for name, reason in flagged.items() if reason} == {}


NORMAL_PAGE = (
    "<!doctype html><html><head><title>What's New | Ministry of Coal</title>"
    '<script src="/bnith__X9a8b7c6d5e4f3/challenge.js"></script></head><body>'
    "<table><tr><td>Validation request for coal block auction</td></tr></table>"
    '<form action="/feedback"><img src="/captcha/image.png">'
    '<input name="captcha_response"></form>'
    "<p>Access Denied areas are listed in the safety manual.</p>"
    + "<p>row</p>" * 50
    + "</body></html>"
)


@pytest.mark.parametrize(
    "body",
    [
        NORMAL_PAGE.encode(),
        b"",
        b'{"posts": [], "title": "Access Denied"}',
        b"%PDF-1.7\n<title>Access Denied</title>",
        "<html><head><title>पहुँच</title></head><body>सामान्य पृष्ठ</body></html>".encode(),
        # A large page titled "Access Denied" is real content, not a block page.
        b"<html><head><title>Access Denied</title></head><body>"
        + b"<p>long content</p>" * 2_000
        + b"</body></html>",
    ],
)
def test_normal_bodies_are_not_flagged(body: bytes) -> None:
    assert detect_block_page(body, "text/html") is None


def test_binary_content_type_is_never_flagged() -> None:
    body = (FIXTURES / "ministry_of_coal/captcha_block.html").read_bytes()
    assert detect_block_page(body, "application/pdf") is None


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            (
                b"<html><head><title> Validation\n request </title></head><body>"
                b"<img src='/captcha.gif'></body></html>"
            ),
            "captcha",
        ),
        (
            (
                b"\xef\xbb\xbf  <HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY><H1>Access "
                b"Denied</H1>You don't have permission to access this server.</BODY></HTML>"
            ),
            "browser_block",
        ),
        (
            (
                b"<html><head><title>Request Rejected</title></head><body>The requested URL was "
                b"rejected. Please consult with your administrator.<br><br>Your support ID is: 123"
                b"</body></html>"
            ),
            "waf_rejected",
        ),
        (
            (
                b"<html><head><title>Just a moment...</title></head><body>"
                b"<script src='/cdn-cgi/challenge-platform/x.js'></script></body></html>"
            ),
            "challenge",
        ),
    ],
)
def test_other_block_shapes(body: bytes, reason: str) -> None:
    assert detect_block_page(body, "text/html") == reason


def test_detect_block_response_reads_content_type_case_insensitively() -> None:
    body = (FIXTURES / "ministry_of_coal/captcha_block.html").read_bytes()
    response = FetchResponse(
        url="https://www.coal.nic.in/whats-new",
        status_code=200,
        headers={"content-TYPE": "text/html"},
        body=body,
    )
    assert detect_block_response(response) == "captcha"
    failed = FetchResponse(url=response.url, status_code=0, headers={}, body=b"", error="x")
    assert detect_block_response(failed) is None
