"""Deterministic tests for Step 6 document enrichment with a fake fetcher.

PDFs are generated in memory; no source documents are stored in the repository.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from policy_tracker.adapters.base import FetchResponse
from policy_tracker.adapters.replay import ReplayFetcher
from policy_tracker.core.contracts import FeedContract, FetchPolicy, RunContext
from policy_tracker.core.identity import HostRules, appearance_key, stable_hash
from policy_tracker.core.models import (
    Appearance,
    BaselineStatus,
    CatalogState,
    Discovery,
    Document,
    DocumentVersion,
    EntityKind,
    Event,
    EventType,
    FeedKind,
    FeedState,
)
from policy_tracker.documents.enrich import (
    CONDITIONAL_304,
    CONDITIONAL_UNCHANGED,
    FULL_DOWNLOAD,
    Resolution,
    content_replaced_id,
    enrich,
    owning_feed,
    select_documents,
    wordpress_attachment,
)
from policy_tracker.documents.extract import Extractor, normalize_text
from policy_tracker.documents.types import EnrichmentLimits, ExtractionStatus

HOST = "example.gov.in"
BASE = f"https://{HOST}/"
FEED = "feed_a"
RUN = RunContext(
    run_id="run-1", started_at="2026-01-05T02:30:00Z", config_hash="c", code_revision="r"
)
RUN2 = RunContext(
    run_id="run-2", started_at="2026-01-12T02:30:00Z", config_hash="c", code_revision="r"
)
RUN3 = RunContext(
    run_id="run-3", started_at="2026-01-19T02:30:00Z", config_hash="c", code_revision="r"
)


# ---------------------------------------------------------------------------
# Synthetic inputs


def make_pdf(pages: list[str | None]) -> bytes:
    """Build a minimal valid PDF; ``None`` pages have no text layer."""

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids: list[str] = []
    number = 4
    for text in pages:
        page, content = number, number + 1
        number += 2
        kids.append(f"{page} 0 R")
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects[page] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>"
        ).encode()
        objects[content] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for index in range(1, number):
        offsets[index] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (index, objects[index])
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % number
    for index in range(1, number):
        out += b"%010d 00000 n \n" % offsets[index]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (number, xref)
    return bytes(out)


TEXT_PDF = make_pdf(["Draft Electricity Amendment Rules 2026"])
OTHER_PDF = make_pdf(["Revised Draft Electricity Amendment Rules 2026"])
BLANK_PDF = make_pdf([None, None])


def contract(feed_id: str = FEED) -> FeedContract:
    return FeedContract(
        feed_id=feed_id,
        site_id="site",
        url=f"{BASE}{feed_id}",
        base_url=BASE,
        kind=FeedKind.AUTHORITATIVE_COLLECTION,
        decision="track",
        removal_authority=True,
        coverage_revision=3,
        parser_version=1,
        host_rules=HostRules(allowed_hosts=frozenset({HOST}), host_aliases={HOST: HOST}),
        fetch=FetchPolicy(
            request_rate_per_host_per_second=1.0,
            request_timeout_seconds=30.0,
            max_redirects=5,
            max_document_bytes=100 * 1024 * 1024,
        ),
    )


CONTRACTS = {FEED: contract()}


def doc_key(name: str) -> str:
    return f"site:document:url:{BASE}{name}"


def make_state(
    *names: str,
    versions: Mapping[str, tuple[DocumentVersion, ...]] | None = None,
    baseline: BaselineStatus = BaselineStatus.COMPLETE,
) -> CatalogState:
    documents: dict[str, Document] = {}
    appearances: dict[str, Appearance] = {}
    for name in names:
        key = doc_key(name)
        documents[key] = Document(
            key=key,
            site_id="site",
            native_id=None,
            canonical_url=f"{BASE}{name}",
            original_urls=(f"/{name}",),
            title=f"Title {name}",
            matter_key=None,
            published_date=None,
            published_raw=None,
            metadata={},
            metadata_fingerprint="fp",
            first_seen_at="2026-01-01T00:00:00Z",
            last_seen_at="2026-01-01T00:00:00Z",
            discovery=Discovery.BASELINE,
        )
        akey = appearance_key(FEED, EntityKind.DOCUMENT, key)
        appearances[akey] = Appearance(
            key=akey,
            feed_id=FEED,
            entity_kind=EntityKind.DOCUMENT,
            entity_key=key,
            first_seen_at="2026-01-01T00:00:00Z",
            last_seen_at="2026-01-01T00:00:00Z",
            present=True,
            missing_complete_runs=0,
        )
    feeds = {
        FEED: FeedState(
            feed_id=FEED,
            baseline_status=baseline,
            coverage_revision=3,
            baselined_revision=3 if baseline is BaselineStatus.COMPLETE else None,
            parser_version=1,
        )
    }
    return CatalogState(
        documents=documents, appearances=appearances, versions=dict(versions or {}), feeds=feeds
    )


def ok(url: str, body: bytes, **headers: str) -> FetchResponse:
    return FetchResponse(url=url, status_code=200, headers=headers, body=body)


@dataclass
class FakeFetcher:
    responses: dict[str, list[FetchResponse]]
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResponse:
        self.calls.append((url, dict(headers or {})))
        queue = self.responses.get(url)
        if not queue:
            return FetchResponse(url=url, status_code=0, headers={}, body=b"", error="no_fixture")
        return queue.pop(0) if len(queue) > 1 else queue[0]


def run_enrich(state: CatalogState, fetcher: FakeFetcher, **kwargs: object):
    kwargs.setdefault("run", RUN)
    kwargs.setdefault("extractor", NO_OCR)
    return enrich(state, (), CONTRACTS, lambda _contract: fetcher, **kwargs)  # type: ignore[arg-type]


def _no_tesseract(name: str) -> str | None:
    return "/usr/bin/pdftoppm" if name == "pdftoppm" else None


NO_OCR = Extractor(which=_no_tesseract)


def slow_extractor(data: bytes, max_pages: int) -> tuple[list[str], int]:
    time.sleep(30)
    return ["never"], 1


# ---------------------------------------------------------------------------
# First acquisition


def test_first_acquisition_hashes_extracts_and_records_version() -> None:
    state = make_state("a.pdf")
    key = doc_key("a.pdf")
    fetcher = FakeFetcher(
        {
            f"{BASE}a.pdf": [
                ok(
                    f"{BASE}a.pdf",
                    TEXT_PDF,
                    ETag='"v1"',
                    **{"Last-Modified": "Mon, 05 Jan 2026 00:00:00 GMT"},
                    **{"Content-Type": "application/pdf"},
                )
            ]
        }
    )
    result = run_enrich(state, fetcher)

    assert fetcher.calls == [(f"{BASE}a.pdf", {})]  # unconditional first GET
    (version,) = result.state.versions[key]
    text = result.texts[key]
    assert text.status is ExtractionStatus.OK
    assert "Draft Electricity Amendment Rules 2026" in text.text
    assert text.language == "en"
    assert text.pages == 1
    assert version == DocumentVersion(
        document_key=key,
        observed_at=RUN.started_at,
        etag='"v1"',
        last_modified="Mon, 05 Jan 2026 00:00:00 GMT",
        content_length=len(TEXT_PDF),
        content_type="application/pdf",
        byte_sha256=hashlib.sha256(TEXT_PDF).hexdigest(),
        text_fingerprint=stable_hash(normalize_text(text.text)),
        extraction_status="ok",
        verification_method=FULL_DOWNLOAD,
    )
    assert result.events == ()
    assert result.state.documents == state.documents


def test_signature_mismatch_records_no_version() -> None:
    state = make_state("a.pdf")
    block_page = b"<!DOCTYPE html><html><body>Access denied</body></html>"
    fetcher = FakeFetcher({f"{BASE}a.pdf": [ok(f"{BASE}a.pdf", block_page)]})
    result = run_enrich(state, fetcher)
    key = doc_key("a.pdf")
    assert key not in result.state.versions
    assert result.texts[key].status is ExtractionStatus.FAILED
    assert result.texts[key].warnings == ("signature_mismatch:claimed=pdf:detected=html",)
    assert result.diagnostics == (f"{key}: signature_mismatch:claimed=pdf:detected=html",)
    assert key in result.state.documents  # never dropped


def test_html_document_text() -> None:
    page = (
        b"<html><head><style>p{}</style><script>var x=1;</script></head>"
        b"<body><h1>Public Notice</h1><p>Comments   invited by 30 March 2026.</p></body></html>"
    )
    state = make_state("notice.html")
    fetcher = FakeFetcher(
        {f"{BASE}notice.html": [ok(f"{BASE}notice.html", page, **{"Content-Type": "text/html"})]}
    )
    result = run_enrich(state, fetcher)
    text = result.texts[doc_key("notice.html")]
    assert text.status is ExtractionStatus.OK
    assert text.text == "Public Notice\nComments invited by 30 March 2026."
    assert text.language == "en"
    assert result.state.versions[doc_key("notice.html")][0].content_type == "text/html"


def test_xlsx_is_hashed_but_unsupported() -> None:
    body = b"PK\x03\x04" + b"\x00" * 64
    state = make_state("data.xlsx")
    fetcher = FakeFetcher({f"{BASE}data.xlsx": [ok(f"{BASE}data.xlsx", body)]})
    result = run_enrich(state, fetcher)
    key = doc_key("data.xlsx")
    (version,) = result.state.versions[key]
    assert version.byte_sha256 == hashlib.sha256(body).hexdigest()
    assert version.extraction_status == "unsupported"
    assert version.text_fingerprint is None
    assert result.texts[key].status is ExtractionStatus.UNSUPPORTED


def test_hindi_only_and_bilingual_html() -> None:
    hindi = "<html><body><p>विद्युत मंत्रालय द्वारा जारी अधिसूचना</p></body></html>".encode()
    bilingual = (
        "<html><body><p>विद्युत मंत्रालय अधिसूचना</p><p>Ministry of Power notification</p></body></html>"
    ).encode()
    state = make_state("hi.html", "bi.html")
    fetcher = FakeFetcher(
        {
            f"{BASE}hi.html": [ok(f"{BASE}hi.html", hindi)],
            f"{BASE}bi.html": [ok(f"{BASE}bi.html", bilingual)],
        }
    )
    result = run_enrich(state, fetcher)
    hindi_text = result.texts[doc_key("hi.html")]
    assert hindi_text.status is ExtractionStatus.HINDI_ONLY
    assert hindi_text.language == "hi"
    assert "अधिसूचना" in hindi_text.text  # text kept
    assert result.state.versions[doc_key("hi.html")][0].extraction_status == "hindi_only"
    bi_text = result.texts[doc_key("bi.html")]
    assert bi_text.status is ExtractionStatus.OK
    assert bi_text.language == "bilingual"
    assert list(result.texts) == sorted(result.texts)


def test_scanned_pdf_without_ocr_tools() -> None:
    state = make_state("scan.pdf")
    fetcher = FakeFetcher({f"{BASE}scan.pdf": [ok(f"{BASE}scan.pdf", BLANK_PDF)]})
    result = run_enrich(state, fetcher)
    key = doc_key("scan.pdf")
    text = result.texts[key]
    assert text.status is ExtractionStatus.SCANNED
    assert text.warnings == ("no_text_layer", "ocr_unavailable")
    assert text.pages == 2
    (version,) = result.state.versions[key]
    assert version.extraction_status == "scanned"
    assert version.text_fingerprint is None
    assert version.byte_sha256 == hashlib.sha256(BLANK_PDF).hexdigest()


def test_ocr_runs_when_tools_exist_and_cleans_up() -> None:
    seen_dirs: list[Path] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if command[1] == "--list-langs":
            return subprocess.CompletedProcess(command, 0, b"List of languages:\neng\nosd\n", b"")
        if command[0].endswith("pdftoppm"):
            prefix = Path(command[-1])
            seen_dirs.append(prefix.parent)
            for number in (1, 2):
                prefix.with_name(f"page-{number}.png").write_bytes(b"png")
            return subprocess.CompletedProcess(command, 0, b"", b"")
        assert command[-2:] == ["-l", "eng"]
        page = Path(command[1]).stem
        return subprocess.CompletedProcess(command, 0, f"Scanned order {page}".encode(), b"")

    extractor = Extractor(which=lambda name: f"/bin/{name}", run=fake_run)
    state = make_state("scan.pdf")
    fetcher = FakeFetcher({f"{BASE}scan.pdf": [ok(f"{BASE}scan.pdf", BLANK_PDF)]})
    limits = EnrichmentLimits(ocr_max_pages=1)
    result = run_enrich(state, fetcher, extractor=extractor, limits=limits)
    text = result.texts[doc_key("scan.pdf")]
    assert text.status is ExtractionStatus.OCR
    assert text.text == "Scanned order page-1\nScanned order page-2"
    assert "ocr_page_cap:1/2" in text.warnings
    assert seen_dirs and not seen_dirs[0].exists()  # temp dir removed


def test_page_cap_marks_partial() -> None:
    pdf = make_pdf(["Page one text", "Page two text", "Page three text"])
    state = make_state("long.pdf")
    fetcher = FakeFetcher({f"{BASE}long.pdf": [ok(f"{BASE}long.pdf", pdf)]})
    result = run_enrich(state, fetcher, limits=EnrichmentLimits(max_pages=2))
    text = result.texts[doc_key("long.pdf")]
    assert text.status is ExtractionStatus.PARTIAL
    assert "Page two text" in text.text and "Page three" not in text.text
    assert text.pages == 3
    assert text.warnings == ("page_cap:2/3",)


def test_extraction_timeout_kills_child_and_keeps_hash() -> None:
    state = make_state("slow.pdf")
    fetcher = FakeFetcher({f"{BASE}slow.pdf": [ok(f"{BASE}slow.pdf", TEXT_PDF)]})
    extractor = Extractor(pdf_text=slow_extractor, which=_no_tesseract)
    started = time.monotonic()
    result = run_enrich(
        state,
        fetcher,
        extractor=extractor,
        limits=EnrichmentLimits(document_time_budget_seconds=1.0),
    )
    assert time.monotonic() - started < 15
    key = doc_key("slow.pdf")
    assert result.texts[key].status is ExtractionStatus.FAILED
    assert result.texts[key].warnings == ("pdf_time_budget",)
    (version,) = result.state.versions[key]
    assert version.extraction_status == "failed"
    assert version.byte_sha256 == hashlib.sha256(TEXT_PDF).hexdigest()


def test_corrupt_pdf_is_failed_not_dropped() -> None:
    state = make_state("bad.pdf")
    fetcher = FakeFetcher({f"{BASE}bad.pdf": [ok(f"{BASE}bad.pdf", b"%PDF-1.4\ngarbage")]})
    result = run_enrich(state, fetcher)
    key = doc_key("bad.pdf")
    assert result.texts[key].status is ExtractionStatus.FAILED
    assert result.texts[key].warnings[0].startswith("pdf_error:")
    assert result.state.versions[key][0].extraction_status == "failed"


# ---------------------------------------------------------------------------
# Revalidation


def first_version(name: str, body: bytes, *, etag: str | None = '"v1"') -> DocumentVersion:
    return DocumentVersion(
        document_key=doc_key(name),
        observed_at="2026-01-01T02:30:00Z",
        etag=etag,
        last_modified="Thu, 01 Jan 2026 00:00:00 GMT",
        content_length=len(body),
        content_type="application/pdf",
        byte_sha256=hashlib.sha256(body).hexdigest(),
        text_fingerprint="tfp",
        extraction_status="ok",
        verification_method=FULL_DOWNLOAD,
    )


def versioned_state(**kwargs: object) -> CatalogState:
    key = doc_key("a.pdf")
    return make_state("a.pdf", versions={key: (first_version("a.pdf", TEXT_PDF),)}, **kwargs)


def touched(name: str = "a.pdf") -> tuple[Event, ...]:
    return (
        Event(
            event_id="e1",
            event_type=EventType.METADATA_CHANGED,
            run_id="run-2",
            feed_id=FEED,
            entity_kind=EntityKind.DOCUMENT,
            entity_key=doc_key(name),
            occurred_at=RUN2.started_at,
            discovery=Discovery.LIVE,
            coverage_revision=3,
        ),
    )


def revalidate(state: CatalogState, fetcher: FakeFetcher, run: RunContext = RUN2):
    return enrich(state, touched(), CONTRACTS, lambda _c: fetcher, run=run, extractor=NO_OCR)


def test_not_modified_appends_bounded_confirmation() -> None:
    state = versioned_state()
    key = doc_key("a.pdf")
    not_modified = FetchResponse(url=f"{BASE}a.pdf", status_code=304, headers={}, body=b"")
    fetcher = FakeFetcher({f"{BASE}a.pdf": [not_modified]})
    result = revalidate(state, fetcher)
    assert fetcher.calls == [
        (
            f"{BASE}a.pdf",
            {"If-None-Match": '"v1"', "If-Modified-Since": "Thu, 01 Jan 2026 00:00:00 GMT"},
        )
    ]
    history = result.state.versions[key]
    assert [v.verification_method for v in history] == [FULL_DOWNLOAD, CONDITIONAL_304]
    assert history[1].byte_sha256 == history[0].byte_sha256
    assert history[1].observed_at == RUN2.started_at
    assert result.texts[key].status is ExtractionStatus.NOT_FETCHED
    assert result.events == ()

    again = revalidate(result.state, fetcher, RUN3)
    history = again.state.versions[key]
    assert [v.verification_method for v in history] == [FULL_DOWNLOAD, CONDITIONAL_304]
    assert history[1].observed_at == RUN3.started_at


def test_changed_validators_same_bytes_records_version_without_event() -> None:
    state = versioned_state()
    key = doc_key("a.pdf")
    fetcher = FakeFetcher({f"{BASE}a.pdf": [ok(f"{BASE}a.pdf", TEXT_PDF, ETag='"v2"')]})
    result = revalidate(state, fetcher)
    history = result.state.versions[key]
    assert [v.etag for v in history] == ['"v1"', '"v2"']
    assert history[1].verification_method == FULL_DOWNLOAD
    assert history[1].byte_sha256 == history[0].byte_sha256
    assert result.events == ()


def test_identical_200_is_a_bounded_confirmation() -> None:
    key = doc_key("a.pdf")
    previous = first_version("a.pdf", TEXT_PDF, etag=None)
    previous = DocumentVersion(**{**previous.__dict__, "last_modified": None})
    state = make_state("a.pdf", versions={key: (previous,)})
    response = ok(f"{BASE}a.pdf", TEXT_PDF, **{"Content-Type": "application/pdf"})
    fetcher = FakeFetcher({f"{BASE}a.pdf": [response]})
    result = revalidate(state, fetcher)
    assert fetcher.calls[0][1] == {}  # no validators to send
    methods = [v.verification_method for v in result.state.versions[key]]
    assert methods == [FULL_DOWNLOAD, CONDITIONAL_UNCHANGED]
    again = revalidate(result.state, fetcher, RUN3)
    methods = [v.verification_method for v in again.state.versions[key]]
    assert methods == [FULL_DOWNLOAD, CONDITIONAL_UNCHANGED]


def test_different_bytes_emit_deterministic_content_replaced() -> None:
    state = versioned_state()
    key = doc_key("a.pdf")
    fetcher = FakeFetcher({f"{BASE}a.pdf": [ok(f"{BASE}a.pdf", OTHER_PDF, ETag='"v2"')]})
    result = revalidate(state, fetcher)
    old_hash = hashlib.sha256(TEXT_PDF).hexdigest()
    new_hash = hashlib.sha256(OTHER_PDF).hexdigest()
    (event,) = result.events
    assert event.event_type is EventType.CONTENT_REPLACED
    assert event.event_id == content_replaced_id(key, old_hash, new_hash)
    assert event.event_id == stable_hash("content_replaced", key, old_hash, new_hash)
    assert event.discovery is Discovery.LIVE
    assert event.feed_id == FEED
    assert event.run_id == "run-2"
    assert event.coverage_revision == 3
    assert event.details["old_sha256"] == old_hash
    assert event.details["new_sha256"] == new_hash
    assert event.details["old_etag"] == '"v1"'
    assert event.details["new_etag"] == '"v2"'
    assert result.state.versions[key][-1].byte_sha256 == new_hash
    assert "Revised" in result.texts[key].text

    rerun = revalidate(state, fetcher)
    assert rerun.events == result.events
    assert rerun.state == result.state


def test_replacement_during_baseline_is_silent() -> None:
    state = versioned_state(baseline=BaselineStatus.PARTIAL)
    fetcher = FakeFetcher({f"{BASE}a.pdf": [ok(f"{BASE}a.pdf", OTHER_PDF, ETag='"v2"')]})
    result = revalidate(state, fetcher)
    assert result.events == ()
    assert len(result.state.versions[doc_key("a.pdf")]) == 2
    assert result.diagnostics == (
        f"{doc_key('a.pdf')}: content_replaced_silent:baseline_incomplete",
    )


@pytest.mark.parametrize(
    "response",
    [
        FetchResponse(url=f"{BASE}a.pdf", status_code=0, headers={}, body=b"", error="timeout"),
        FetchResponse(url=f"{BASE}a.pdf", status_code=500, headers={}, body=b"oops"),
        FetchResponse(url=f"{BASE}a.pdf", status_code=405, headers={}, body=b""),
    ],
)
def test_failed_fetch_keeps_state(response: FetchResponse) -> None:
    state = versioned_state()
    fetcher = FakeFetcher({f"{BASE}a.pdf": [response]})
    result = revalidate(state, fetcher)
    assert result.state == state
    assert result.events == ()
    key = doc_key("a.pdf")
    assert result.texts[key].status is ExtractionStatus.NOT_FETCHED
    assert result.diagnostics[0].startswith(f"{key}: fetch_failed:")


def test_unexpected_304_without_validators_is_a_failure() -> None:
    state = make_state("a.pdf")
    response = FetchResponse(url=f"{BASE}a.pdf", status_code=304, headers={}, body=b"")
    result = run_enrich(state, FakeFetcher({f"{BASE}a.pdf": [response]}))
    assert result.state == state
    assert result.diagnostics == (f"{doc_key('a.pdf')}: fetch_failed:http_304",)


def test_refusals_record_nothing() -> None:
    state = make_state("a.pdf", "b.pdf")
    redirected = ok("https://evil.example.com/a.pdf", TEXT_PDF)
    huge = ok(f"{BASE}b.pdf", TEXT_PDF)
    fetcher = FakeFetcher({f"{BASE}a.pdf": [redirected], f"{BASE}b.pdf": [huge]})
    result = run_enrich(state, fetcher, limits=EnrichmentLimits(max_bytes=10))
    assert result.state == state
    assert result.diagnostics == (
        f"{doc_key('a.pdf')}: redirect_host_not_allowed",
        f"{doc_key('b.pdf')}: too_large:{len(TEXT_PDF)}",
    )


def test_unexpected_exception_is_contained() -> None:
    state = make_state("a.pdf", "b.html")
    fetcher = FakeFetcher(
        {
            f"{BASE}a.pdf": [ok(f"{BASE}a.pdf", TEXT_PDF)],
            f"{BASE}b.html": [ok(f"{BASE}b.html", b"<html><body>Notice</body></html>")],
        }
    )

    class Exploding(Extractor):
        def extract(self, document_key, body, detected, limits):  # type: ignore[override]
            if detected == "pdf":
                raise RuntimeError("boom")
            return super().extract(document_key, body, detected, limits)

    result = run_enrich(state, fetcher, extractor=Exploding(which=_no_tesseract))
    assert result.diagnostics == (f"{doc_key('a.pdf')}: enrichment_error:RuntimeError",)
    assert doc_key("a.pdf") not in result.state.versions
    assert result.texts[doc_key("b.html")].status is ExtractionStatus.OK


# ---------------------------------------------------------------------------
# Selection, batching, purity


def test_selection_order_and_modes() -> None:
    key_a = doc_key("a.pdf")
    state = make_state(
        "a.pdf", "b.pdf", "c.pdf", versions={key_a: (first_version("a.pdf", TEXT_PDF),)}
    )
    events = touched("c.pdf")
    assert select_documents(state, events, "changed") == [doc_key("c.pdf"), doc_key("b.pdf")]
    assert select_documents(state, events, "all") == [
        doc_key("c.pdf"),
        doc_key("b.pdf"),
        key_a,
    ]
    with pytest.raises(ValueError):
        enrich(state, events, CONTRACTS, lambda _c: FakeFetcher({}), run=RUN, select="some")


def test_max_documents_batches_the_baseline() -> None:
    state = make_state("a.pdf", "b.pdf", "c.pdf")
    responses = {f"{BASE}{n}": [ok(f"{BASE}{n}", TEXT_PDF)] for n in ("a.pdf", "b.pdf", "c.pdf")}
    fetcher = FakeFetcher(responses)
    first = run_enrich(state, fetcher, max_documents=2)
    assert sorted(first.state.versions) == [doc_key("a.pdf"), doc_key("b.pdf")]
    assert first.diagnostics == ("deferred 1 document(s) beyond max_documents",)

    second = run_enrich(first.state, fetcher, run=RUN2, max_documents=2)
    assert sorted(second.state.versions) == [doc_key(n) for n in ("a.pdf", "b.pdf", "c.pdf")]
    assert [call[0] for call in fetcher.calls] == [
        f"{BASE}a.pdf",
        f"{BASE}b.pdf",
        f"{BASE}c.pdf",
    ]
    assert second.diagnostics == ()


def test_no_contract_or_disallowed_host_is_skipped() -> None:
    state = make_state("a.pdf")
    result = enrich(state, (), {}, lambda _c: FakeFetcher({}), run=RUN)
    assert result.state == state
    assert result.diagnostics == (f"{doc_key('a.pdf')}: no_contract",)


def test_input_state_is_not_mutated() -> None:
    state = versioned_state()
    snapshot = copy.deepcopy(state)
    fetcher = FakeFetcher({f"{BASE}a.pdf": [ok(f"{BASE}a.pdf", OTHER_PDF, ETag='"v2"')]})
    result = revalidate(state, fetcher)
    assert state == snapshot
    assert result.state is not state
    assert len(state.versions[doc_key("a.pdf")]) == 1


@pytest.mark.skipif(shutil.which("tesseract") is not None, reason="tesseract is installed")
def test_default_extractor_degrades_without_tesseract() -> None:
    state = make_state("scan.pdf")
    fetcher = FakeFetcher({f"{BASE}scan.pdf": [ok(f"{BASE}scan.pdf", BLANK_PDF)]})
    result = run_enrich(state, fetcher, extractor=Extractor())
    text = result.texts[doc_key("scan.pdf")]
    assert text.status is ExtractionStatus.SCANNED
    assert "ocr_unavailable" in text.warnings


# ---------------------------------------------------------------------------
# Ministry of Power attachment records

MOP_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ministry_of_power"
MOP_RECORD = "https://www.powermin.gov.in/cms/wp-json/post-page/post?id=6001"
MOP_FILE = "https://powermin.gov.in/static/uploads/example-policy.pdf"


def mop_contract() -> FeedContract:
    base = contract("mop_documents")
    return replace(
        base,
        site_id="ministry_of_power",
        base_url="https://www.powermin.gov.in/",
        host_rules=HostRules(
            allowed_hosts=frozenset({"www.powermin.gov.in", "powermin.gov.in"}),
            host_aliases={},
        ),
    )


def mop_state(versions: Mapping[str, tuple[DocumentVersion, ...]] | None = None) -> CatalogState:
    base = make_state("x.pdf")
    (document,) = base.documents.values()
    (appearance,) = base.appearances.values()
    key = "ministry_of_power:document:id:6001"
    document = replace(document, key=key, site_id="ministry_of_power", canonical_url=MOP_RECORD)
    appearance = replace(appearance, key="a", feed_id="mop_documents", entity_key=key)
    feed = replace(base.feeds[FEED], feed_id="mop_documents")
    return CatalogState(
        documents={key: document},
        appearances={"a": appearance},
        versions=dict(versions or {}),
        feeds={"mop_documents": feed},
    )


def mop_enrich(
    state: CatalogState, fetcher: ReplayFetcher, run: RunContext = RUN, select: str = "changed"
):
    contracts = {"mop_documents": mop_contract()}
    return enrich(
        state, (), contracts, lambda _c: fetcher, run=run, extractor=NO_OCR, select=select
    )


def test_mop_attachment_record_resolves_to_file() -> None:
    fetcher = ReplayFetcher(fixture_root=MOP_FIXTURES)
    fetcher.add(MOP_RECORD, fixture="attachment_detail.json")
    fetcher.add(MOP_FILE, body=TEXT_PDF, headers={"ETag": '"f1"'}, content_type="application/pdf")
    state = mop_state()
    key = "ministry_of_power:document:id:6001"
    result = mop_enrich(state, fetcher)
    assert fetcher.requested_urls == [MOP_RECORD, MOP_FILE]
    (version,) = result.state.versions[key]
    assert version.byte_sha256 == hashlib.sha256(TEXT_PDF).hexdigest()
    assert version.etag == '"f1"'
    assert version.content_type == "application/pdf"
    text = result.texts[key]
    assert text.status is ExtractionStatus.OK
    assert text.warnings[0] == f"resolved:{MOP_FILE}"
    assert result.diagnostics == (f"{key}: resolved:{MOP_FILE}",)

    # Revalidation: the record is re-read, the file request is conditional.
    again = mop_enrich(result.state, fetcher, RUN2, select="all")
    assert fetcher.requests[-1].url == MOP_FILE
    assert fetcher.requests[-1].headers.get("If-None-Match") == '"f1"'
    assert [v.verification_method for v in again.state.versions[key]] == [
        FULL_DOWNLOAD,
        CONDITIONAL_304,
    ]


def test_mop_prefers_english_and_counts_other_language_files() -> None:
    record = json.loads((MOP_FIXTURES / "attachment_detail.json").read_text())
    acf = record["posts"]["acf_data"]
    acf["pdf_type"] = "Separate"
    hindi = dict(acf.pop("pdf_both"), url="https://powermin.gov.in/static/uploads/hi.pdf")
    english = dict(hindi, url="https://powermin.gov.in/static/uploads/en.pdf")
    acf["pdf_hindi"] = hindi
    acf["pdf_english"] = english
    fetcher = ReplayFetcher()
    fetcher.add(MOP_RECORD, body=json.dumps(record), content_type="application/json")
    fetcher.add(english["url"], body=TEXT_PDF)
    result = mop_enrich(mop_state(), fetcher)
    key = "ministry_of_power:document:id:6001"
    assert fetcher.requested_urls == [MOP_RECORD, english["url"]]
    assert result.texts[key].warnings[:2] == (
        f"resolved:{english['url']}",
        "other_language_files:1",
    )


def test_mop_record_without_files_records_nothing() -> None:
    fetcher = ReplayFetcher()
    fetcher.add(
        MOP_RECORD, body='{"posts": {"ID": 1, "acf_data": {}}}', content_type="application/json"
    )
    state = mop_state()
    result = mop_enrich(state, fetcher)
    key = "ministry_of_power:document:id:6001"
    assert result.state == state
    assert result.diagnostics == (f"{key}: resolve_failed:no_file_objects",)
    assert result.texts[key].status is ExtractionStatus.NOT_FETCHED


def test_wordpress_attachment_resolver_order() -> None:
    def response(acf: dict[str, object]) -> FetchResponse:
        body = json.dumps({"posts": {"acf_data": acf}}).encode()
        return FetchResponse(url=MOP_RECORD, status_code=200, headers={}, body=body)

    item = {"mime_type": "application/pdf"}
    both = {"pdf_both": {**item, "url": "https://p/b.pdf"}, "pdf_x": {**item, "url": "https://p/x"}}
    assert wordpress_attachment(response(both)) == Resolution(
        "https://p/b.pdf", ("other_language_files:1",)
    )
    only_hindi = {"pdf_hindi": {**item, "url": "https://p/h.pdf"}}
    assert wordpress_attachment(response(only_hindi)) == Resolution("https://p/h.pdf")
    bad = FetchResponse(url=MOP_RECORD, status_code=200, headers={}, body=b"{not json")
    assert wordpress_attachment(bad) == "invalid_json"


def test_content_replaced_needs_feed_live_before_the_run() -> None:
    # The feed completed its baseline in this run (COMPLETE in the post-run
    # state) but was not live before it: the replacement stays silent.
    state = versioned_state()
    fetcher = FakeFetcher({f"{BASE}a.pdf": [ok(f"{BASE}a.pdf", OTHER_PDF, ETag='"v2"')]})
    result = enrich(
        state,
        touched(),
        CONTRACTS,
        lambda _c: fetcher,
        run=RUN2,
        extractor=NO_OCR,
        live_feed_ids=frozenset(),
    )
    assert result.events == ()
    assert len(result.state.versions[doc_key("a.pdf")]) == 2
    assert result.diagnostics == (
        f"{doc_key('a.pdf')}: content_replaced_silent:baseline_incomplete",
    )
    live = enrich(
        state,
        touched(),
        CONTRACTS,
        lambda _c: fetcher,
        run=RUN2,
        extractor=NO_OCR,
        live_feed_ids=frozenset({FEED}),
    )
    assert [e.event_type for e in live.events] == [EventType.CONTENT_REPLACED]
    # live_feed_ids overrides the post-run status in both directions.
    partial = versioned_state(baseline=BaselineStatus.PARTIAL)
    forced = enrich(
        partial,
        touched(),
        CONTRACTS,
        lambda _c: fetcher,
        run=RUN2,
        extractor=NO_OCR,
        live_feed_ids=frozenset({FEED}),
    )
    assert [e.event_type for e in forced.events] == [EventType.CONTENT_REPLACED]


def test_owning_feed_prefers_authoritative_over_rolling() -> None:
    key = doc_key("a.pdf")
    rolling = replace(contract("feed_r"), kind=FeedKind.ROLLING, removal_authority=False)
    contracts = {FEED: contract(), "feed_r": rolling}

    def seen(feed_id: str, first_seen: str, present: bool = True) -> Appearance:
        return Appearance(
            key=appearance_key(feed_id, EntityKind.DOCUMENT, key),
            feed_id=feed_id,
            entity_kind=EntityKind.DOCUMENT,
            entity_key=key,
            first_seen_at=first_seen,
            last_seen_at=first_seen,
            present=present,
            missing_complete_runs=0,
        )

    early_rolling = seen("feed_r", "2026-01-01T00:00:00Z")
    assert owning_feed([early_rolling, seen(FEED, "2026-01-08T00:00:00Z")], contracts) == FEED
    withdrawn = seen(FEED, "2026-01-08T00:00:00Z", present=False)
    assert owning_feed([early_rolling, withdrawn], contracts) == "feed_r"
    assert owning_feed([early_rolling], {FEED: contract()}) is None
