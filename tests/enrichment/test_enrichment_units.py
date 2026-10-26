"""Unit tests for signature checks, language detection, and extraction helpers."""

from __future__ import annotations

import pytest

from policy_tracker.documents import signatures
from policy_tracker.documents.extract import normalize_text, run_bounded
from policy_tracker.documents.language import detect_language, script_counts


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"%PDF-1.7\n...", signatures.PDF),
        (b"\r\n  junk %PDF-1.4", signatures.PDF),
        (b"PK\x03\x04rest", signatures.ZIP),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", signatures.OLE),
        (b"\xef\xbb\xbf  <!DOCTYPE html><html>", signatures.HTML),
        (b"<HTML><body>blocked</body></HTML>", signatures.HTML),
        (b"plain bytes", signatures.UNKNOWN),
    ],
)
def test_detect(body: bytes, expected: str) -> None:
    assert signatures.detect(body) == expected


def test_claims_prefer_extension_then_content_type() -> None:
    assert signatures.claimed_type("https://x.gov.in/a/B.PDF", "text/html") == signatures.PDF
    assert signatures.claimed_type("https://x.gov.in/a.xlsx", None) == signatures.ZIP
    assert signatures.claimed_type("https://x.gov.in/get?id=1", "application/pdf; q=1") == "pdf"
    ooxml = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert signatures.claimed_type("https://x.gov.in/get", ooxml) == signatures.ZIP
    assert signatures.claimed_type("https://x.gov.in/get", "application/octet-stream") is None
    assert signatures.claimed_type("https://x.gov.in/get", None) is None


def test_satisfies() -> None:
    assert signatures.satisfies(signatures.PDF, signatures.PDF)
    assert not signatures.satisfies(signatures.PDF, signatures.HTML)
    assert signatures.satisfies(signatures.OLE, signatures.ZIP)  # .xls that is really xlsx
    assert not signatures.satisfies(signatures.ZIP, signatures.PDF)
    assert signatures.satisfies(None, signatures.UNKNOWN)


def test_language_detection() -> None:
    assert detect_language("Draft Electricity Rules, 2026") == "en"
    assert detect_language("विद्युत नियम मसौदा 2026") == "hi"
    assert detect_language("विद्युत नियम मसौदा / Draft Electricity Rules") == "bilingual"
    assert detect_language("12345 ... ---") is None
    # A few English acronyms inside Hindi text keep it Hindi-only.
    assert detect_language("केंद्रीय विद्युत विनियामक आयोग द्वारा जारी आदेश CERC") == "hi"
    assert script_counts("aé क") == (1, 2)


def test_normalize_text() -> None:
    assert normalize_text("  a\n\tb  c ") == "a b c"


def _boom(data: bytes, max_pages: int) -> tuple[list[str], int]:
    raise RuntimeError("bad pdf")


def test_run_bounded_reports_child_errors() -> None:
    assert run_bounded(_boom, b"x", 1, 30.0) == ("error", "RuntimeError")
