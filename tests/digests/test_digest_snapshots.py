"""Snapshot and behaviour tests for build_digest and the renderers (Step 6).

Snapshots live in ``tests/digests/snapshots``. Regenerate them deliberately with
``UPDATE_SNAPSHOTS=1 python -m pytest tests/digests`` and review the diff.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import os
import re
from pathlib import Path

import pytest

from policy_tracker.core.identity import appearance_key
from policy_tracker.core.models import (
    Appearance,
    BaselineStatus,
    CandidateState,
    CatalogState,
    Discovery,
    Document,
    DocumentVersion,
    EntityKind,
    EnumerationStatus,
    Event,
    EventType,
    FeedRun,
    FeedState,
    FetchStatus,
    ParseStatus,
)
from policy_tracker.documents.types import ExtractedText, ExtractionStatus
from policy_tracker.reporting import deadlines as dl
from policy_tracker.reporting.digest import BACKFILL_LABEL, build_digest, section_of
from policy_tracker.reporting.render import (
    _mime_size,
    neutralize,
    render_csv,
    render_email,
    render_json,
    render_markdown,
    render_open_deadlines_csv,
)
from policy_tracker.reporting.types import Category, Deadline, Digest, DigestItem

SNAPSHOTS = Path(__file__).parent / "snapshots"
RUN_ID = "20261026T023000Z-abcdef12"
GENERATED_AT = "2026-10-26T02:30:00Z"
PREVIOUS_RUN_AT = "2026-10-12T02:30:00Z"  # 14 days earlier: gap warning
NOW = "2026-10-26T02:00:00Z"
EARLIER = "2026-09-01T02:00:00Z"
ARTIFACTS = "https://github.com/example/tracker/blob/tracker-data"

SITES = {"cerc": "https://cercind.gov.in/", "mop": "https://powermin.gov.in/",
         "coal": "https://coal.nic.in/"}  # fmt: skip
LIVE_FEEDS = (
    "cerc_draft_regulations",
    "cerc_recent_rops",
    "cerc_public_notices",
    "mop_documents",
    "coal_monthly_statistics",
    "coal_whats_new",
)


def _site(feed_id: str) -> str:
    prefix = feed_id.split("_", 1)[0]
    return {"cerc": "cerc", "mop": "ministry_of_power", "coal": "ministry_of_coal"}[prefix]


def doc(
    key: str,
    feed_id: str,
    title: str,
    *,
    url: str | None = None,
    published: str | None = "12.10.2026",
    discovery: Discovery = Discovery.LIVE,
    first_seen: str = NOW,
    metadata: dict[str, str] | None = None,
) -> Document:
    base = SITES[feed_id.split("_", 1)[0]]
    return Document(
        key=key,
        site_id=_site(feed_id),
        native_id=None,
        canonical_url=url if url is not None else f"{base}{key}.pdf",
        original_urls=(url or f"{base}{key}.pdf",),
        title=title,
        matter_key=None,
        published_date=(f"{published[6:]}-{published[3:5]}-{published[:2]}" if published else None),
        published_raw=published,
        metadata=metadata or {},
        metadata_fingerprint="fp",
        first_seen_at=first_seen,
        last_seen_at=NOW,
        discovery=discovery,
    )


def appearance(feed_id: str, key: str, first_seen: str = NOW) -> Appearance:
    return Appearance(
        key=appearance_key(feed_id, EntityKind.DOCUMENT, key),
        feed_id=feed_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_key=key,
        first_seen_at=first_seen,
        last_seen_at=NOW,
        present=True,
        missing_complete_runs=0,
    )


def event(
    key: str,
    feed_id: str,
    event_type: EventType,
    *,
    discovery: Discovery = Discovery.LIVE,
    details: dict[str, str] | None = None,
) -> Event:
    return Event(
        event_id=f"ev-{key}-{event_type.value}-{feed_id}",
        event_type=event_type,
        run_id=RUN_ID,
        feed_id=feed_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_key=key,
        occurred_at=NOW,
        discovery=discovery,
        coverage_revision=1,
        details=details or {},
    )


def feed_state(
    feed_id: str,
    status: BaselineStatus = BaselineStatus.COMPLETE,
    *,
    revision: int = 1,
    baselined: int | None = 1,
    count: int | None = 10,
    failures: int = 0,
) -> FeedState:
    return FeedState(
        feed_id=feed_id,
        baseline_status=status,
        coverage_revision=revision,
        baselined_revision=baselined,
        parser_version=1,
        last_item_count=count,
        consecutive_failures=failures,
    )


def feed_run(
    feed_id: str,
    *,
    accepted: bool = True,
    status: EnumerationStatus = EnumerationStatus.COMPLETE,
    count: int = 10,
    reasons: tuple[str, ...] = (),
) -> FeedRun:
    return FeedRun(
        run_id=RUN_ID,
        feed_id=feed_id,
        started_at=NOW,
        finished_at=NOW,
        fetch_status=FetchStatus.OK if accepted else FetchStatus.FAILED,
        parse_status=ParseStatus.VALID,
        enumeration_status=status,
        accepted=accepted,
        item_count=count,
        previous_item_count=count,
        coverage_revision=1,
        parser_version=1,
        config_hash="cfg",
        code_revision="rev",
        quarantine_reasons=reasons,
    )


def synthetic() -> tuple[CandidateState, CatalogState, dict[str, ExtractedText], dict]:
    docs = [
        doc("d1", "cerc_draft_regulations",
            "Draft CERC (Connectivity) Regulations, 2026 <script>alert(1)</script>"),
        doc("d2", "mop_documents",
            "=HYPERLINK(\"http://evil\") Guidelines on Resource Adequacy", first_seen=EARLIER),
        doc("d3", "cerc_recent_rops", "Record of Proceedings in Petition No. 12/MP/2026"),
        doc("d4", "cerc_recent_rops", "Order in Suo Motu Petition No. 3/SM/2026 on market coupling"),
        doc("d5", "coal_monthly_statistics", "Provisional Coal Statistics September 2026"),
        doc("d6", "cerc_public_notices", "Notice [click](javascript:alert(1)) *bold* | pipe",
            url="javascript:alert(1)", discovery=Discovery.BACKFILL, published="03.04.2019"),
        doc("d7", "cerc_draft_regulations",
            "Extension of last date for comments on Draft Tariff Regulations till 30.11.2026",
            first_seen=EARLIER),
        doc("d8", "cerc_draft_regulations", "Draft Amendment to Sharing Regulations",
            first_seen=EARLIER),
        doc("d9", "cerc_study_reports", "Study on ancillary services: comments by 20.11.2026",
            discovery=Discovery.BASELINE),
        doc("d10", "mop_documents", "Minister visits thermal plant", published=None),
    ]  # fmt: skip
    feeds_of = {
        "d1": ["cerc_draft_regulations", "cerc_public_notices"],
        "d2": ["mop_documents"],
        "d3": ["cerc_recent_rops"],
        "d4": ["cerc_recent_rops"],
        "d5": ["coal_monthly_statistics"],
        "d6": ["cerc_public_notices"],
        "d7": ["cerc_draft_regulations"],
        "d8": ["cerc_draft_regulations"],
        "d9": ["cerc_study_reports"],
        "d10": ["mop_documents"],
    }
    appearances = [appearance(feed, key) for key, feeds in feeds_of.items() for feed in feeds]
    feeds = [*LIVE_FEEDS, "cerc_study_reports"]
    previous_feeds = {f: feed_state(f) for f in LIVE_FEEDS}
    previous_feeds["cerc_study_reports"] = feed_state(
        "cerc_study_reports", BaselineStatus.PARTIAL, baselined=None, count=None
    )
    previous_feeds["cerc_public_notices"] = feed_state("cerc_public_notices", revision=2)
    current_feeds = dict(previous_feeds)
    current_feeds["cerc_study_reports"] = feed_state("cerc_study_reports", count=42)
    current_feeds["cerc_public_notices"] = feed_state(
        "cerc_public_notices", revision=2, baselined=2
    )
    current_feeds["coal_whats_new"] = feed_state("coal_whats_new", failures=2)
    state = CatalogState(
        documents={d.key: d for d in docs},
        appearances={a.key: a for a in appearances},
        versions={
            "d10": (
                DocumentVersion(document_key="d10", observed_at=NOW, extraction_status="scanned"),
            )
        },
        feeds=current_feeds,
    )
    previous = CatalogState(feeds=previous_feeds)
    events = (
        event("d1", "cerc_draft_regulations", EventType.NEW_DOCUMENT),
        event("d1", "cerc_public_notices", EventType.NEW_APPEARANCE),
        event("d2", "mop_documents", EventType.METADATA_CHANGED,
              details={"changed_fields": "title", "title.old": "Guidelines", "title.new": "x"}),
        event("d3", "cerc_recent_rops", EventType.NEW_DOCUMENT),
        event("d4", "cerc_recent_rops", EventType.NEW_DOCUMENT),
        event("d5", "coal_monthly_statistics", EventType.NEW_DOCUMENT),
        event("d6", "cerc_public_notices", EventType.NEW_DOCUMENT, discovery=Discovery.BACKFILL),
        event("d7", "cerc_draft_regulations", EventType.METADATA_CHANGED,
              details={"changed_fields": "title"}),
        event("d9", "cerc_study_reports", EventType.NEW_DOCUMENT, discovery=Discovery.BASELINE),
        event("d10", "mop_documents", EventType.NEW_DOCUMENT),
    )  # fmt: skip
    runs = tuple(
        feed_run(f)
        if f != "coal_whats_new"
        else feed_run(
            f, accepted=False, status=EnumerationStatus.UNKNOWN, count=0, reasons=("fetch_failed",)
        )
        for f in sorted(feeds)
    )
    candidate = CandidateState(
        run_id=RUN_ID, base_state_hash="base", state=state, events=events, feed_runs=runs
    )
    texts = {
        "d1": ExtractedText(
            "d1",
            ExtractionStatus.OK,
            text=(
                "The Commission has notified the draft regulations. Comments and suggestions "
                "are invited from stakeholders. Comments may be submitted on or before "
                "15.11.2026 by 5:00 PM."
            ),
            language="en",
        ),
        "d5": ExtractedText("d5", ExtractionStatus.HINDI_ONLY, language="hi"),
        "d8": ExtractedText(
            "d8",
            ExtractionStatus.OK,
            text="Stakeholders may send comments on or before 10th December, 2026.",
        ),
        "d10": ExtractedText("d10", ExtractionStatus.SCANNED, warnings=("ocr_unavailable",)),
    }
    previous_deadlines = {
        "d7": (
            Deadline(
                raw="01.11.2026",
                date="2026-11-01",
                time=None,
                sort_key="2026-11-01T23:59:00+05:30",
                evidence="Comments by 01.11.2026",
                method="title_regex",
            ),
        )
    }
    return candidate, previous, texts, previous_deadlines


def synthetic_digest() -> tuple[Digest, dict[str, tuple[Deadline, ...]]]:
    candidate, previous, texts, previous_deadlines = synthetic()
    digest, index = build_digest(
        candidate,
        previous,
        texts,
        generated_at=GENERATED_AT,
        previous_run_at=PREVIOUS_RUN_AT,
        previous_deadlines=previous_deadlines,
    )
    return digest, dict(index)


def heartbeat_digest() -> Digest:
    feeds = {f: feed_state(f) for f in ("cerc_draft_regulations", "mop_documents")}
    state = CatalogState(feeds=feeds)
    candidate = CandidateState(
        run_id=RUN_ID,
        base_state_hash="base",
        state=state,
        events=(),
        feed_runs=tuple(feed_run(f) for f in sorted(feeds)),
    )
    digest, _ = build_digest(
        candidate,
        state,
        {},
        generated_at=GENERATED_AT,
        previous_run_at="2026-10-19T02:30:00Z",
        previous_deadlines={},
    )
    return digest


def check_snapshot(name: str, content: str | bytes) -> None:
    data = content.encode("utf-8") if isinstance(content, str) else content
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_bytes(data)
    assert path.exists(), f"missing snapshot {name}; run with UPDATE_SNAPSHOTS=1"
    assert data == path.read_bytes(), f"snapshot {name} differs; review and regenerate"


# ---------------------------------------------------------------------- snapshots


def test_markdown_snapshot() -> None:
    digest, _ = synthetic_digest()
    rendered = render_markdown(digest)
    assert rendered.path == f"digests/2026/10/{RUN_ID}.md"
    check_snapshot("synthetic.md", rendered.content)


def test_json_snapshot() -> None:
    digest, _ = synthetic_digest()
    rendered = render_json(digest)
    assert rendered.path == f"digests/2026/10/{RUN_ID}.json"
    json.loads(rendered.content)
    check_snapshot("synthetic.json", rendered.content)


def test_csv_snapshots() -> None:
    digest, _ = synthetic_digest()
    events_csv = render_csv(digest)
    assert events_csv.path == f"digests/2026/10/{RUN_ID}.csv"
    check_snapshot("synthetic.csv", events_csv.content)
    open_csv = render_open_deadlines_csv(digest)
    assert open_csv.path == "open_deadlines.csv"
    check_snapshot("synthetic_open_deadlines.csv", open_csv.content)


def test_email_snapshots() -> None:
    digest, _ = synthetic_digest()
    email = render_email(digest, artifact_base_url=ARTIFACTS)
    check_snapshot("synthetic_email.txt", f"Subject: {email.subject}\n\n{email.text_body}")
    check_snapshot("synthetic_email.html", email.html_body)
    assert [a.path for a in email.attachments] == [
        f"digests/2026/10/{RUN_ID}.csv",
        "open_deadlines.csv",
    ]
    assert 0 < email.size_bytes < 5 * 1024 * 1024


def test_heartbeat_snapshots() -> None:
    digest = heartbeat_digest()
    assert digest.items == () and not digest.partial and digest.gap_warning is None
    email = render_email(digest, artifact_base_url=None)
    assert "heartbeat" in email.subject
    check_snapshot("heartbeat_email.txt", f"Subject: {email.subject}\n\n{email.text_body}")
    check_snapshot("heartbeat.md", render_markdown(digest).content)
    assert "This heartbeat confirms that the tracker ran." in email.html_body


# ---------------------------------------------------------------------- behaviour


def _by_key(digest: Digest) -> dict[str, DigestItem]:
    return {item.entity_key: item for item in digest.items}


def test_ordering_and_categories() -> None:
    digest, _ = synthetic_digest()
    keys = [item.entity_key for item in digest.items]
    assert keys == ["d1", "d7", "d8", "d2", "d4", "d5", "d10", "d6", "d3"]
    categories = [item.category for item in digest.items]
    assert categories == sorted(categories)
    items = _by_key(digest)
    assert items["d1"].category is Category.ACTION_REQUIRED
    assert items["d3"].category is Category.ROUTINE and section_of(items["d3"]) == "routine"
    assert items["d4"].category is Category.POLICY_AND_DIRECTION
    assert any(r.startswith("promoted:") for r in items["d4"].reasons)
    assert section_of(items["d10"]) == "other"
    assert items["d5"].category is Category.RESEARCH_AND_REPORTING


def test_baseline_events_hidden_and_dedupe() -> None:
    digest, _ = synthetic_digest()
    items = _by_key(digest)
    assert "d9" not in items  # baseline discovery is never shown
    assert items["d1"].event_type is EventType.NEW_DOCUMENT
    assert items["d1"].details["other_events"] == "new_appearance"
    assert len([i for i in digest.items if i.entity_key == "d1"]) == 1


def test_backfill_label() -> None:
    digest, _ = synthetic_digest()
    item = _by_key(digest)["d6"]
    assert item.discovery is Discovery.BACKFILL
    assert item.details["discovery_label"] == BACKFILL_LABEL
    md = render_markdown(digest).content.decode()
    assert "Backfill: newly covered history (not a new publication)" in md


def test_deadline_changes_and_time_not_stated() -> None:
    digest, index = synthetic_digest()
    items = _by_key(digest)
    assert items["d7"].deadline_changed
    assert items["d7"].details["previous_deadlines"] == "1 Nov 2026 (time not stated)"
    assert items["d8"].deadline_changed and items["d8"].event_type is EventType.DEADLINE_CHANGED
    assert not items["d1"].deadline_changed  # discovered with a new document, not a change
    assert items["d1"].deadlines[0].time == "17:00"
    assert "d9" in index and not any(i.entity_key == "d9" for i in digest.items)
    text = render_email(digest).text_body
    assert (
        "Deadline changed: 30 Nov 2026 (time not stated) (previously: 1 Nov 2026 "
        "(time not stated))" in text
    )
    assert "Deadline: 15 Nov 2026, 17:00 IST" in text
    open_keys = [i.entity_key for i in digest.open_deadlines]
    assert open_keys == ["d1", "d9", "d7", "d8"]


def test_no_deadline_changes_on_first_index() -> None:
    candidate, previous, texts, _ = synthetic()
    digest, _ = build_digest(
        candidate, previous, texts, generated_at=GENERATED_AT, previous_run_at=None
    )
    assert not any(item.deadline_changed for item in digest.items)
    assert any("Deadline index initialized" in note for note in digest.notes)
    assert digest.gap_warning is None


def test_rerun_with_returned_index_reports_no_deadline_change() -> None:
    candidate, previous, texts, _ = synthetic()
    _, index = synthetic_digest()
    again, _ = build_digest(
        candidate,
        previous,
        texts,
        generated_at=GENERATED_AT,
        previous_run_at=PREVIOUS_RUN_AT,
        previous_deadlines=index,
    )
    assert not any(item.deadline_changed for item in again.items)
    assert dl.deadline_changes(index, index) == set()


def test_text_deadlines_carry_over_without_new_text() -> None:
    candidate, previous, _, _ = synthetic()
    _, index = synthetic_digest()
    _, carried = build_digest(
        candidate,
        previous,
        {},
        generated_at=GENERATED_AT,
        previous_run_at=PREVIOUS_RUN_AT,
        previous_deadlines=index,
    )
    assert carried["d8"] == index["d8"]


def test_health_partial_gap_and_notes() -> None:
    digest, _ = synthetic_digest()
    assert digest.partial
    assert digest.gap_warning and "14 days" in digest.gap_warning
    failed = [h for h in digest.health if not h.accepted]
    assert [(h.feed_id, h.site_id, h.consecutive_failures) for h in failed] == [
        ("coal_whats_new", "ministry_of_coal", 2)
    ]
    assert any(n.startswith("Coverage initialized for cerc_study_reports") for n in digest.notes)
    assert any("cerc_public_notices expanded" in n for n in digest.notes)
    assert any("D-008" in n for n in digest.notes)
    assert digest.period_label == "Week of 26 Oct 2026"


def test_warnings_hindi_only_and_scanned() -> None:
    digest, _ = synthetic_digest()
    items = _by_key(digest)
    assert items["d5"].warnings == ("hindi_only",)
    assert items["d5"].title == "Provisional Coal Statistics September 2026"  # D-005
    assert items["d10"].warnings == ("scanned", "ocr_unavailable")
    text = render_email(digest).text_body
    assert "Document is Hindi-only; English listing details shown" in text


def test_html_and_markdown_escaping() -> None:
    digest, _ = synthetic_digest()
    email = render_email(digest)
    assert "<script>" not in email.html_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in email.html_body
    assert "javascript:" not in email.html_body.replace("[click](javascript:alert(1))", "")
    assert 'href="javascript' not in email.html_body
    md = render_markdown(digest).content.decode()
    assert "\\<script\\>" in md
    assert "(javascript:alert(1))" in md  # only as escaped text, never as a link target
    assert "\\[click\\](javascript" in md
    assert re.search(r"(?<!\\)\]\(javascript", md) is None
    assert "\\*bold\\*" in md and "\\| pipe" in md


def test_csv_formula_neutralization() -> None:
    digest, _ = synthetic_digest()
    rows = list(csv.DictReader(io.StringIO(render_csv(digest).content.decode())))
    titles = {row["entity_key"]: row["title"] for row in rows}
    assert titles["d2"].startswith("'=HYPERLINK")
    for value in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"):
        assert neutralize(value) == "'" + value
    assert neutralize("plain") == "plain"


def test_size_overflow_drops_attachments_and_links() -> None:
    digest, _ = synthetic_digest()
    filler = [
        dataclasses.replace(
            digest.items[-1],
            event_id=f"ev{n}",
            entity_key=f"r{n}",
            title=f"Record of Proceedings in Petition No. {n}/MP/2026 " + "x" * 200,
        )
        for n in range(3000)
    ]
    huge = dataclasses.replace(digest, items=digest.items + tuple(filler))
    limit = 400_000
    email = render_email(huge, max_bytes=limit, artifact_base_url=ARTIFACTS)
    assert email.attachments == ()
    assert email.size_bytes < limit
    assert f"{ARTIFACTS}/digests/2026/10/{RUN_ID}.csv" in email.text_body
    assert "not attached because the message would exceed" in email.text_body
    assert "more routine record(s) in the CSV and archive" in email.text_body
    assert email.size_bytes == _mime_size(email.subject, email.text_body, email.html_body, ())


def test_default_cap_attaches_when_small() -> None:
    digest, _ = synthetic_digest()
    email = render_email(digest, attach_csv=False)
    assert email.attachments == ()
    assert "not attached" not in email.text_body


@pytest.mark.parametrize("renderer", [render_markdown, render_json, render_csv])
def test_renderers_are_deterministic(renderer) -> None:  # type: ignore[no-untyped-def]
    first, _ = synthetic_digest()
    second, _ = synthetic_digest()
    assert renderer(first).content == renderer(second).content


def _single(
    title: str,
    text: str,
    *,
    appearances: tuple[Appearance, ...] | None = None,
    events: tuple[Event, ...] | None = None,
    authoritative_feeds: frozenset[str] | None = None,
) -> Digest:
    feeds = {f: feed_state(f) for f in ("mop_documents", "mop_whats_new")}
    document = doc("r1", "mop_documents", title)
    state = CatalogState(
        documents={"r1": document},
        appearances={a.key: a for a in (appearances or (appearance("mop_documents", "r1"),))},
        feeds=feeds,
    )
    candidate = CandidateState(
        run_id=RUN_ID,
        base_state_hash="base",
        state=state,
        events=(event("r1", "mop_documents", EventType.NEW_DOCUMENT),)
        if events is None
        else events,
        feed_runs=tuple(feed_run(f) for f in sorted(feeds)),
    )
    digest, _ = build_digest(
        candidate,
        CatalogState(feeds=feeds),
        {"r1": ExtractedText("r1", ExtractionStatus.OK, text=text)},
        generated_at=GENERATED_AT,
        previous_run_at=None,
        authoritative_feeds=authoritative_feeds,
    )
    return digest


def test_only_open_deadlines_promote_to_action_required() -> None:
    title = "Annual report on the performance of distribution utilities"
    future = _single(title, "Comments may be sent by 10.12.2026.")
    (item,) = future.items
    assert item.deadlines and "deadline:found" in item.reasons
    assert item.category is Category.ACTION_REQUIRED
    past = _single(title, "Comments may be sent by 10.09.2026.")
    (item,) = past.items
    assert item.deadlines  # the expired deadline is still recorded
    assert "deadline:found" not in item.reasons
    assert item.category is not Category.ACTION_REQUIRED


def test_owning_feed_ranks_authoritative_above_rolling() -> None:
    appearances = (
        appearance("mop_whats_new", "r1", first_seen=EARLIER),
        appearance("mop_documents", "r1"),
    )
    text = "Comments may be sent by 10.12.2026."
    legacy = _single("Annual report", text, appearances=appearances, events=())
    assert [i.feed_id for i in legacy.open_deadlines] == ["mop_whats_new"]
    ranked = _single(
        "Annual report",
        text,
        appearances=appearances,
        events=(),
        authoritative_feeds=frozenset({"mop_documents"}),
    )
    assert [i.feed_id for i in ranked.open_deadlines] == ["mop_documents"]
