"""Synthetic, deterministic tests for the pure state/diff engine."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from policy_tracker.core.contracts import FeedContract, FetchPolicy, RunContext
from policy_tracker.core.engine import (
    FeedOutcome,
    apply_observation,
    document_fingerprint,
    ensure_feed_state,
    rebaseline_feed,
)
from policy_tracker.core.identity import HostRules, appearance_key
from policy_tracker.core.models import (
    BaselineStatus,
    CatalogState,
    Discovery,
    EntityKind,
    EnumerationStatus,
    EventType,
    FeedKind,
    FetchStatus,
    ParseStatus,
    Provenance,
    RecordStatus,
)
from policy_tracker.core.observations import (
    CollectionEvidence,
    FeedObservation,
    ObservedDocument,
    ObservedMatter,
)

SITE = "site"
BASE = "https://example.gov.in/"


# ---------------------------------------------------------------------------
# Helpers


def contract(
    feed_id: str = "feed_a",
    *,
    removal_authority: bool = True,
    coverage_revision: int = 1,
    parser_version: int = 1,
    threshold: float = 0.5,
) -> FeedContract:
    return FeedContract(
        feed_id=feed_id,
        site_id=SITE,
        url=f"{BASE}{feed_id}",
        base_url=BASE,
        kind=FeedKind.AUTHORITATIVE_COLLECTION if removal_authority else FeedKind.ROLLING,
        decision="track",
        removal_authority=removal_authority,
        coverage_revision=coverage_revision,
        parser_version=parser_version,
        host_rules=HostRules(
            allowed_hosts=frozenset({"example.gov.in"}),
            host_aliases={"www.example.gov.in": "example.gov.in"},
        ),
        fetch=FetchPolicy(
            request_rate_per_host_per_second=1.0,
            request_timeout_seconds=30.0,
            max_redirects=5,
            max_document_bytes=100 * 1024 * 1024,
        ),
        row_count_collapse_threshold=threshold,
    )


def ctx(n: int) -> RunContext:
    return RunContext(
        run_id=f"run-{n}",
        started_at=f"2026-01-{n:02d}T02:30:00Z",
        config_hash="cfg",
        code_revision="abc",
    )


def doc(
    name: str,
    *,
    title: str | None = None,
    url: str | None = None,
    native_id: str | None = None,
    section: str | None = None,
    published_raw: str | None = None,
    published_date: str | None = None,
    matter_native_id: str | None = None,
    metadata: dict[str, str] | None = None,
    locator: str = "",
) -> ObservedDocument:
    return ObservedDocument(
        url=url or f"/files/{name}.pdf",
        title=title if title is not None else f"Title {name}",
        provenance=Provenance(page_url=f"{BASE}list", locator=locator or name),
        native_id=native_id,
        matter_native_id=matter_native_id,
        published_date=published_date,
        published_raw=published_raw,
        section=section,
        metadata=metadata or {},
    )


def obs(
    n: int,
    documents: list[ObservedDocument] | tuple[ObservedDocument, ...] = (),
    *,
    feed_id: str = "feed_a",
    matters: tuple[ObservedMatter, ...] = (),
    complete: bool = True,
    fetch_ok: bool = True,
    parse_valid: bool = True,
    block: bool = False,
    mimes: tuple[str, ...] = (),
    parser_version: int = 1,
) -> FeedObservation:
    return FeedObservation(
        feed_id=feed_id,
        site_id=SITE,
        observed_at=f"2026-01-{n:02d}T03:00:00Z",
        parser_version=parser_version,
        evidence=CollectionEvidence(
            fetch_ok=fetch_ok,
            parse_valid=parse_valid,
            enumeration_complete=complete,
            block_page_detected=block,
            unexpected_mime_types=mimes,
        ),
        matters=matters,
        documents=tuple(documents),
    )


def key(name: str) -> str:
    return f"{SITE}:document:url:https://example.gov.in/files/{name}.pdf"


def akey(name: str, feed_id: str = "feed_a") -> str:
    return appearance_key(feed_id, EntityKind.DOCUMENT, key(name))


def types(outcome: FeedOutcome) -> list[EventType]:
    return [event.event_type for event in outcome.events]


def baselined(
    names: tuple[str, ...] = ("a", "b", "c"), c: FeedContract | None = None
) -> CatalogState:
    """State after a silent complete baseline of ``names`` on run 1."""

    c = c or contract()
    outcome = apply_observation(
        CatalogState(), obs(1, [doc(n) for n in names], feed_id=c.feed_id), c, ctx(1)
    )
    assert outcome.events == ()
    return outcome.state


# ---------------------------------------------------------------------------
# Baseline


def test_first_complete_run_is_a_silent_baseline() -> None:
    outcome = apply_observation(CatalogState(), obs(1, [doc("a"), doc("b")]), contract(), ctx(1))
    assert outcome.events == ()
    feed = outcome.state.feeds["feed_a"]
    assert feed.baseline_status is BaselineStatus.COMPLETE
    assert feed.baselined_revision == 1
    assert feed.last_item_count == 2
    assert feed.last_complete_run_id == "run-1"
    assert {d.discovery for d in outcome.state.documents.values()} == {Discovery.BASELINE}
    assert outcome.feed_run.accepted
    assert outcome.feed_run.enumeration_status is EnumerationStatus.COMPLETE
    assert outcome.feed_run.started_at == "2026-01-01T02:30:00Z"
    assert outcome.feed_run.finished_at == "2026-01-01T03:00:00Z"
    document = outcome.state.documents[key("a")]
    assert document.first_seen_at == document.last_seen_at == "2026-01-01T03:00:00Z"
    assert document.published_date is None  # never derived from first_seen


def test_live_new_document_after_baseline() -> None:
    state = baselined()
    outcome = apply_observation(
        state, obs(2, [doc("a"), doc("b"), doc("c"), doc("d")]), contract(), ctx(2)
    )
    assert types(outcome) == [EventType.NEW_DOCUMENT]
    event = outcome.events[0]
    assert event.entity_key == key("d")
    assert event.discovery is Discovery.LIVE
    assert event.run_id == "run-2"
    assert event.occurred_at == "2026-01-02T03:00:00Z"
    assert outcome.state.documents[key("d")].discovery is Discovery.LIVE


def test_new_matter_and_explicit_parent_link() -> None:
    state = baselined()
    matter = ObservedMatter(
        native_id="M1", title="Draft  regulation", url="/m/1", provenance=Provenance(BASE)
    )
    outcome = apply_observation(
        state,
        obs(2, [doc("a"), doc("b"), doc("c"), doc("d", matter_native_id="M1")], matters=(matter,)),
        contract(),
        ctx(2),
    )
    assert sorted(types(outcome)) == sorted([EventType.NEW_MATTER, EventType.NEW_DOCUMENT])
    mkey = f"{SITE}:matter:id:M1"
    assert outcome.state.matters[mkey].title == "Draft regulation"
    assert outcome.state.documents[key("d")].matter_key == mkey


def test_unresolved_matter_reference_stays_none() -> None:
    outcome = apply_observation(
        CatalogState(), obs(1, [doc("a", matter_native_id="NOPE")]), contract(), ctx(1)
    )
    assert outcome.state.documents[key("a")].matter_key is None


def test_matter_key_gained_from_existing_matter_emits_metadata_change() -> None:
    matter = ObservedMatter(native_id="M1", title="M", url=None, provenance=Provenance(BASE))
    state = apply_observation(
        CatalogState(), obs(1, [doc("a")], matters=(matter,)), contract(), ctx(1)
    ).state
    # Matter no longer listed in this observation but already known for the site.
    outcome = apply_observation(
        state, obs(2, [doc("a", matter_native_id="M1")], matters=(matter,)), contract(), ctx(2)
    )
    assert types(outcome) == [EventType.METADATA_CHANGED]
    assert outcome.events[0].details["changed_fields"] == "matter_key"
    assert outcome.state.documents[key("a")].matter_key == f"{SITE}:matter:id:M1"


def test_new_feed_added_later_baselines_silently() -> None:
    state = baselined()
    b = contract("feed_b")
    outcome = apply_observation(state, obs(2, [doc("a"), doc("x")], feed_id="feed_b"), b, ctx(2))
    assert outcome.events == ()
    assert outcome.state.feeds["feed_b"].baseline_status is BaselineStatus.COMPLETE
    assert outcome.state.documents[key("x")].discovery is Discovery.BASELINE


# ---------------------------------------------------------------------------
# Presentation-only changes


def test_ordering_only_change_emits_nothing() -> None:
    state = baselined()
    reordered = [doc("c", locator="r1"), doc("a", locator="r2"), doc("b", locator="r3")]
    outcome = apply_observation(state, obs(2, reordered), contract(), ctx(2))
    assert outcome.events == ()
    assert outcome.state.appearances[akey("c")].provenance.locator == "r1"


def test_whitespace_only_change_emits_nothing() -> None:
    state = apply_observation(
        CatalogState(),
        obs(
            1,
            [
                doc(
                    "a",
                    title="Draft Rules 2026",
                    published_raw="01-01-2026",
                    metadata={"type": "Order"},
                )
            ],
        ),
        contract(),
        ctx(1),
    ).state
    outcome = apply_observation(
        state,
        obs(
            2,
            [
                doc(
                    "a",
                    title="  Draft\n  Rules\t2026 ",
                    published_raw=" 01-01-2026 ",
                    metadata={"type": " Order"},
                )
            ],
        ),
        contract(),
        ctx(2),
    )
    assert outcome.events == ()
    assert outcome.state.documents[key("a")].title == "Draft Rules 2026"


def test_metadata_change_emits_details() -> None:
    state = baselined(("a",))
    outcome = apply_observation(
        state,
        obs(
            2,
            [
                doc(
                    "a",
                    title="New title",
                    published_raw="02/01/2026",
                    published_date="2026-01-02",
                    metadata={"category": "Report"},
                )
            ],
        ),
        contract(),
        ctx(2),
    )
    assert types(outcome) == [EventType.METADATA_CHANGED]
    details = outcome.events[0].details
    assert details["changed_fields"] == ("metadata.category,published_date,published_raw,title")
    assert details["title.old"] == "Title a"
    assert details["title.new"] == "New title"
    assert details["published_date.new"] == "2026-01-02"
    document = outcome.state.documents[key("a")]
    assert document.metadata == {"category": "Report"}
    assert document.metadata_fingerprint == document_fingerprint(
        title="New title",
        published_raw="02/01/2026",
        published_date="2026-01-02",
        metadata={"category": "Report"},
        matter_key=None,
    )
    assert document.metadata_fingerprint != state.documents[key("a")].metadata_fingerprint


def test_section_change_is_status_change_not_removal() -> None:
    state = apply_observation(
        CatalogState(), obs(1, [doc("a", section="current")]), contract(), ctx(1)
    ).state
    outcome = apply_observation(state, obs(2, [doc("a", section="archive")]), contract(), ctx(2))
    assert types(outcome) == [EventType.STATUS_CHANGED]
    assert outcome.events[0].details["section.old"] == "current"
    assert outcome.events[0].details["section.new"] == "archive"
    appearance = outcome.state.appearances[akey("a")]
    assert appearance.present and appearance.section == "archive"
    assert outcome.state.documents[key("a")].status is RecordStatus.ACTIVE


# ---------------------------------------------------------------------------
# Identity and duplicate appearances


def test_url_variants_merge_and_record_original_hrefs() -> None:
    hrefs = [
        "/files/a.pdf",
        "http://www.example.gov.in/files/a.pdf",
        "https://example.gov.in/files/a.pdf#page=2",
    ]
    outcome = apply_observation(
        CatalogState(),
        obs(1, [doc("a", url=h, locator=f"row{i}") for i, h in enumerate(hrefs)]),
        contract(),
        ctx(1),
    )
    assert list(outcome.state.documents) == [key("a")]
    document = outcome.state.documents[key("a")]
    assert document.original_urls == tuple(hrefs)
    assert document.provenance.locator == "row0"
    assert outcome.feed_run.item_count == 1


def test_native_id_takes_precedence_and_titles_never_merge() -> None:
    outcome = apply_observation(
        CatalogState(),
        obs(
            1,
            [
                doc("a", native_id="42"),
                doc("b", native_id="42", locator="dup"),
                doc("c", title="Same"),
                doc("d", title="Same"),
            ],
        ),
        contract(),
        ctx(1),
    )
    keys = set(outcome.state.documents)
    assert keys == {f"{SITE}:document:id:42", key("c"), key("d")}
    assert outcome.state.documents[f"{SITE}:document:id:42"].original_urls == (
        "/files/a.pdf",
        "/files/b.pdf",
    )


def test_duplicate_sightings_within_a_feed_collapse_without_events() -> None:
    state = baselined()
    outcome = apply_observation(
        state,
        obs(
            2, [doc("a"), doc("b"), doc("c"), doc("d", locator="first"), doc("d", locator="second")]
        ),
        contract(),
        ctx(2),
    )
    assert types(outcome) == [EventType.NEW_DOCUMENT]
    appearances = [a for a in outcome.state.appearances.values() if a.entity_key == key("d")]
    assert len(appearances) == 1
    assert appearances[0].provenance.locator == "first"
    assert any("duplicate" in d for d in outcome.feed_run.diagnostics)
    replay = apply_observation(
        outcome.state,
        obs(
            3, [doc("a"), doc("b"), doc("c"), doc("d", locator="second"), doc("d", locator="first")]
        ),
        contract(),
        ctx(3),
    )
    assert replay.events == ()


def test_same_document_on_two_feeds_is_one_document_two_appearances() -> None:
    state = baselined()
    b = contract("feed_b", removal_authority=False)
    state = apply_observation(state, obs(2, [doc("z")], feed_id="feed_b"), b, ctx(2)).state
    outcome = apply_observation(
        state, obs(3, [doc("z"), doc("a", title="Other rendering")], feed_id="feed_b"), b, ctx(3)
    )
    # "a" is already known from feed_a: a new appearance, not a new document,
    # and feed_b's different title does not rewrite feed_a's metadata.
    assert types(outcome) == [EventType.NEW_APPEARANCE]
    assert outcome.events[0].entity_key == key("a")
    assert outcome.events[0].discovery is Discovery.LIVE
    assert outcome.state.documents[key("a")].title == "Title a"
    assert len([a for a in outcome.state.appearances.values() if a.entity_key == key("a")]) == 2
    # Both feeds keep rendering differently: no flip-flopping events.
    again_a = apply_observation(
        outcome.state, obs(4, [doc("a"), doc("b"), doc("c")]), contract(), ctx(4)
    )
    again_b = apply_observation(
        again_a.state,
        obs(4, [doc("z"), doc("a", title="Other rendering")], feed_id="feed_b"),
        b,
        ctx(4),
    )
    assert again_a.events == () and again_b.events == ()


def test_document_first_seen_on_two_feeds_in_one_week() -> None:
    state = baselined()
    b = contract("feed_b", removal_authority=False)
    state = apply_observation(state, obs(2, [doc("z")], feed_id="feed_b"), b, ctx(2)).state
    first = apply_observation(
        state, obs(3, [doc("a"), doc("b"), doc("c"), doc("n")]), contract(), ctx(3)
    )
    second = apply_observation(
        first.state, obs(3, [doc("z"), doc("n")], feed_id="feed_b"), b, ctx(3)
    )
    assert types(first) == [EventType.NEW_DOCUMENT]
    assert types(second) == [EventType.NEW_APPEARANCE]
    assert len(second.state.documents) == 5


# ---------------------------------------------------------------------------
# Backfill


def test_backfill_after_coverage_revision_bump() -> None:
    state = baselined()
    bumped = contract(coverage_revision=2)
    outcome = apply_observation(
        state, obs(2, [doc("a"), doc("b"), doc("c"), doc("old1"), doc("old2")]), bumped, ctx(2)
    )
    assert types(outcome) == [EventType.NEW_DOCUMENT, EventType.NEW_DOCUMENT]
    assert {e.discovery for e in outcome.events} == {Discovery.BACKFILL}
    assert {e.coverage_revision for e in outcome.events} == {2}
    assert outcome.state.documents[key("old1")].discovery is Discovery.BACKFILL
    assert outcome.state.feeds["feed_a"].baselined_revision == 2
    later = apply_observation(
        outcome.state,
        obs(3, [doc("a"), doc("b"), doc("c"), doc("old1"), doc("old2"), doc("n")]),
        bumped,
        ctx(3),
    )
    assert [e.discovery for e in later.events] == [Discovery.LIVE]


def test_partial_backfill_run_keeps_backfill_mode() -> None:
    state = baselined()
    bumped = contract(coverage_revision=2)
    partial = apply_observation(state, obs(2, [doc("old1")], complete=False), bumped, ctx(2))
    assert [e.discovery for e in partial.events] == [Discovery.BACKFILL]
    assert partial.state.feeds["feed_a"].baselined_revision == 1
    complete = apply_observation(
        partial.state,
        obs(3, [doc("a"), doc("b"), doc("c"), doc("old1"), doc("old2")]),
        bumped,
        ctx(3),
    )
    assert [e.discovery for e in complete.events] == [Discovery.BACKFILL]
    assert complete.state.feeds["feed_a"].baselined_revision == 2


# ---------------------------------------------------------------------------
# Removal and reappearance


def test_removal_needs_two_complete_runs_and_reappearance_restores() -> None:
    state = baselined()
    run2 = apply_observation(state, obs(2, [doc("a"), doc("b")]), contract(), ctx(2))
    assert run2.events == ()
    appearance = run2.state.appearances[akey("c")]
    assert appearance.present and appearance.missing_complete_runs == 1

    run3 = apply_observation(run2.state, obs(3, [doc("a"), doc("b")]), contract(), ctx(3))
    assert types(run3) == [EventType.WITHDRAWN]
    assert run3.events[0].details["record_status"] == "withdrawn"
    assert not run3.state.appearances[akey("c")].present
    assert run3.state.documents[key("c")].status is RecordStatus.WITHDRAWN

    run4 = apply_observation(run3.state, obs(4, [doc("a"), doc("b")]), contract(), ctx(4))
    assert run4.events == ()  # already withdrawn; no repeat

    run5 = apply_observation(run4.state, obs(5, [doc("a"), doc("b"), doc("c")]), contract(), ctx(5))
    assert types(run5) == [EventType.REAPPEARED]
    appearance = run5.state.appearances[akey("c")]
    assert appearance.present and appearance.missing_complete_runs == 0
    assert run5.state.documents[key("c")].status is RecordStatus.ACTIVE


def test_seeing_item_again_resets_missing_counter() -> None:
    state = baselined()
    state = apply_observation(state, obs(2, [doc("a"), doc("b")]), contract(), ctx(2)).state
    state = apply_observation(
        state, obs(3, [doc("a"), doc("b"), doc("c")]), contract(), ctx(3)
    ).state
    assert state.appearances[akey("c")].missing_complete_runs == 0
    outcome = apply_observation(state, obs(4, [doc("a"), doc("b")]), contract(), ctx(4))
    assert outcome.events == ()
    assert outcome.state.appearances[akey("c")].present


def test_record_stays_active_while_another_feed_still_lists_it() -> None:
    state = baselined()
    b = contract("feed_b", removal_authority=False)
    state = apply_observation(state, obs(2, [doc("c")], feed_id="feed_b"), b, ctx(2)).state
    state = apply_observation(state, obs(3, [doc("a"), doc("b")]), contract(), ctx(3)).state
    outcome = apply_observation(state, obs(4, [doc("a"), doc("b")]), contract(), ctx(4))
    assert types(outcome) == [EventType.WITHDRAWN]
    assert outcome.events[0].details["record_status"] == "active"
    assert not outcome.state.appearances[akey("c")].present
    assert outcome.state.appearances[akey("c", "feed_b")].present
    assert outcome.state.documents[key("c")].status is RecordStatus.ACTIVE


def test_rolling_feed_never_removes() -> None:
    rolling = contract("feed_r", removal_authority=False, threshold=0.0)
    state = baselined(("a", "b"), rolling)
    for n in range(2, 6):
        outcome = apply_observation(
            state, obs(n, [doc(f"n{n}")], feed_id="feed_r"), rolling, ctx(n)
        )
        assert types(outcome) == [EventType.NEW_DOCUMENT]
        state = outcome.state
    for name in ("a", "b"):
        appearance = state.appearances[akey(name, "feed_r")]
        assert appearance.present and appearance.missing_complete_runs == 0
        assert state.documents[key(name)].status is RecordStatus.ACTIVE


def test_partial_enumeration_does_not_count_toward_removal() -> None:
    state = baselined()
    for n in (2, 3, 4):
        outcome = apply_observation(
            state, obs(n, [doc("a"), doc("e")], complete=False), contract(), ctx(n)
        )
        assert outcome.feed_run.accepted
        assert outcome.feed_run.enumeration_status is EnumerationStatus.PARTIAL
        state = outcome.state
    assert state.documents[key("e")].discovery is Discovery.LIVE  # additions accepted
    for name in ("b", "c"):
        appearance = state.appearances[akey(name)]
        assert appearance.present and appearance.missing_complete_runs == 0
    assert state.feeds["feed_a"].last_complete_run_id == "run-1"
    assert state.feeds["feed_a"].last_item_count == 3


# ---------------------------------------------------------------------------
# Partial baselines


def test_partial_enumeration_does_not_complete_baseline_and_recovers_silently() -> None:
    first = apply_observation(
        CatalogState(), obs(1, [doc("a")], complete=False), contract(), ctx(1)
    )
    assert first.events == ()
    assert first.state.feeds["feed_a"].baseline_status is BaselineStatus.PARTIAL
    assert first.state.feeds["feed_a"].baselined_revision is None

    second = apply_observation(
        first.state, obs(2, [doc("a"), doc("b")], complete=False), contract(), ctx(2)
    )
    assert second.events == ()
    assert second.state.feeds["feed_a"].baseline_status is BaselineStatus.PARTIAL

    third = apply_observation(
        second.state, obs(3, [doc("a"), doc("b"), doc("c")]), contract(), ctx(3)
    )
    assert third.events == ()
    assert third.state.feeds["feed_a"].baseline_status is BaselineStatus.COMPLETE
    assert {d.discovery for d in third.state.documents.values()} == {Discovery.BASELINE}

    live = apply_observation(
        third.state, obs(4, [doc("a"), doc("b"), doc("c"), doc("d")]), contract(), ctx(4)
    )
    assert types(live) == [EventType.NEW_DOCUMENT]


def test_quarantined_first_run_keeps_baseline_not_started() -> None:
    outcome = apply_observation(CatalogState(), obs(1, [doc("a")], block=True), contract(), ctx(1))
    feed = outcome.state.feeds["feed_a"]
    assert feed.baseline_status is BaselineStatus.NOT_STARTED
    assert feed.consecutive_failures == 1
    assert outcome.state.documents == {}


# ---------------------------------------------------------------------------
# Quarantine


@pytest.mark.parametrize(
    ("kwargs", "reason", "fetch", "parse"),
    [
        ({"block": True}, "block_page", FetchStatus.OK, ParseStatus.BLOCKED),
        ({"parse_valid": False}, "parse_invalid", FetchStatus.OK, ParseStatus.INVALID),
        ({"fetch_ok": False}, "fetch_failed", FetchStatus.FAILED, ParseStatus.INVALID),
        ({"mimes": ("text/html",)}, "unexpected_mime_type", FetchStatus.OK, ParseStatus.VALID),
    ],
)
def test_quarantine_preserves_prior_state(
    kwargs: dict[str, object], reason: str, fetch: FetchStatus, parse: ParseStatus
) -> None:
    state = baselined()
    outcome = apply_observation(state, obs(2, [doc("a"), doc("new")], **kwargs), contract(), ctx(2))
    assert not outcome.feed_run.accepted
    assert reason in outcome.feed_run.quarantine_reasons
    assert outcome.feed_run.fetch_status is fetch
    assert outcome.feed_run.parse_status is parse
    assert outcome.events == ()
    _assert_only_failures_changed(state, outcome.state, failures=1)


def test_row_count_collapse_is_quarantined() -> None:
    state = baselined(tuple("abcdefghij"))
    outcome = apply_observation(
        state, obs(2, [doc("a"), doc("b"), doc("c"), doc("d")]), contract(), ctx(2)
    )
    assert outcome.feed_run.quarantine_reasons == ("row_count_collapse",)
    assert outcome.feed_run.item_count == 4
    assert outcome.feed_run.previous_item_count == 10
    _assert_only_failures_changed(state, outcome.state, failures=1)
    # At the threshold the run is accepted.
    ok = apply_observation(state, obs(2, [doc(n) for n in "abcde"]), contract(), ctx(2))
    assert ok.feed_run.accepted


def test_collapse_ignores_partial_enumeration() -> None:
    state = baselined(tuple("abcdefghij"))
    outcome = apply_observation(state, obs(2, [doc("a")], complete=False), contract(), ctx(2))
    assert outcome.feed_run.accepted


def test_unexpected_empty_authoritative_feed_is_quarantined() -> None:
    state = baselined()
    outcome = apply_observation(state, obs(2, []), contract(), ctx(2))
    assert outcome.feed_run.quarantine_reasons == ("unexpected_empty",)
    _assert_only_failures_changed(state, outcome.state, failures=1)


def test_consecutive_failures_accumulate_and_reset() -> None:
    state = baselined()
    state = apply_observation(state, obs(2, [doc("a")], block=True), contract(), ctx(2)).state
    state = apply_observation(state, None, contract(), ctx(3)).state
    assert state.feeds["feed_a"].consecutive_failures == 2
    state = apply_observation(
        state, obs(4, [doc("a"), doc("b"), doc("c")]), contract(), ctx(4)
    ).state
    assert state.feeds["feed_a"].consecutive_failures == 0


def test_observation_none_records_failed_run() -> None:
    state = baselined()
    outcome = apply_observation(state, None, contract(), ctx(2))
    run = outcome.feed_run
    assert not run.accepted
    assert run.quarantine_reasons == ("adapter_failed",)
    assert run.fetch_status is FetchStatus.FAILED
    assert run.enumeration_status is EnumerationStatus.UNKNOWN
    assert run.finished_at == run.started_at == "2026-01-02T02:30:00Z"
    assert outcome.events == ()
    _assert_only_failures_changed(state, outcome.state, failures=1)


def test_stale_observation_is_quarantined() -> None:
    state = apply_observation(CatalogState(), obs(5, [doc("a")]), contract(), ctx(5)).state
    outcome = apply_observation(state, obs(4, [doc("a", title="old")]), contract(), ctx(6))
    assert "stale_observation" in outcome.feed_run.quarantine_reasons


def _assert_only_failures_changed(
    before: CatalogState, after: CatalogState, *, failures: int
) -> None:
    assert after.documents == before.documents
    assert after.matters == before.matters
    assert after.appearances == before.appearances
    old_feed = before.feeds["feed_a"]
    new_feed = after.feeds["feed_a"]
    assert new_feed == replace(
        old_feed, consecutive_failures=old_feed.consecutive_failures + failures
    )


# ---------------------------------------------------------------------------
# Parser migration


def test_parser_migration_applies_metadata_silently() -> None:
    state = baselined()
    v2 = contract(parser_version=2)
    outcome = apply_observation(
        state,
        obs(
            2,
            [doc("a", title="Reparsed A", section="current"), doc("b"), doc("c"), doc("d")],
            parser_version=2,
        ),
        v2,
        ctx(2),
    )
    assert types(outcome) == [EventType.NEW_DOCUMENT]
    assert outcome.events[0].entity_key == key("d")
    assert outcome.state.documents[key("a")].title == "Reparsed A"
    assert outcome.state.feeds["feed_a"].parser_version == 2
    assert any("parser migration" in d for d in outcome.feed_run.diagnostics)
    later = apply_observation(
        outcome.state,
        obs(
            3,
            [doc("a", title="Changed again", section="current"), doc("b"), doc("c"), doc("d")],
            parser_version=2,
        ),
        v2,
        ctx(3),
    )
    assert types(later) == [EventType.METADATA_CHANGED]


def test_partial_parser_migration_keeps_migration_open() -> None:
    state = baselined()
    v2 = contract(parser_version=2)
    partial = apply_observation(
        state, obs(2, [doc("a", title="X")], complete=False, parser_version=2), v2, ctx(2)
    )
    assert partial.events == ()
    assert partial.state.feeds["feed_a"].parser_version == 1
    complete = apply_observation(
        partial.state,
        obs(3, [doc("a", title="X"), doc("b", title="Y"), doc("c")], parser_version=2),
        v2,
        ctx(3),
    )
    assert complete.events == ()
    assert complete.state.feeds["feed_a"].parser_version == 2


# ---------------------------------------------------------------------------
# Determinism and idempotency


def test_replaying_same_snapshot_over_same_base_is_identical() -> None:
    state = baselined()
    snapshot = obs(2, [doc("a", title="Changed"), doc("b"), doc("d")])
    first = apply_observation(state, snapshot, contract(), ctx(2))
    second = apply_observation(state, snapshot, contract(), replace(ctx(2), run_id="retry"))
    assert [e.event_id for e in first.events] == [e.event_id for e in second.events]
    assert first.state == replace(
        second.state,
        feeds={
            **second.state.feeds,
            "feed_a": replace(second.state.feeds["feed_a"], last_complete_run_id="run-2"),
        },
    )
    assert len({e.event_id for e in first.events}) == len(first.events)


def test_reapplying_observation_to_its_own_result_yields_no_events() -> None:
    state = baselined()
    snapshot = obs(2, [doc("a", title="Changed", section="archive"), doc("d")])
    first = apply_observation(state, snapshot, contract(), ctx(2))
    assert first.events
    again = apply_observation(first.state, snapshot, contract(), ctx(3))
    assert again.events == ()
    # The replay is not a second complete enumeration: nothing is withdrawn.
    assert again.state.appearances[akey("b")].missing_complete_runs == 1
    assert again.state.appearances[akey("b")].present
    assert again.state.documents == first.state.documents


def test_events_are_sorted_deterministically() -> None:
    state = baselined()
    names = ["z", "m", "d", "q"]
    forward = apply_observation(
        state, obs(2, [doc("a"), doc("b"), doc("c")] + [doc(n) for n in names]), contract(), ctx(2)
    )
    backward = apply_observation(
        state,
        obs(2, [doc("a"), doc("b"), doc("c")] + [doc(n) for n in reversed(names)]),
        contract(),
        ctx(2),
    )
    assert [e.event_id for e in forward.events] == [e.event_id for e in backward.events]
    assert [e.entity_key for e in forward.events] == sorted(key(n) for n in names)


def test_input_state_is_not_mutated() -> None:
    state = baselined()
    snapshot = copy.deepcopy(state)
    apply_observation(state, obs(2, [doc("a", title="new"), doc("x")]), contract(), ctx(2))
    apply_observation(state, obs(2, [doc("a")]), contract(), ctx(2))
    apply_observation(state, None, contract(), ctx(2))
    apply_observation(state, obs(2, [doc("a")], block=True), contract(), ctx(2))
    rebaseline_feed(state, "feed_a", reason="test")
    ensure_feed_state(state, contract("other"))
    assert state == snapshot


# ---------------------------------------------------------------------------
# Feed-state helpers


def test_rebaseline_feed_makes_next_complete_run_silent() -> None:
    state = baselined(tuple("abcdefghij"))
    with pytest.raises(ValueError):
        rebaseline_feed(state, "feed_a", reason=" ")
    with pytest.raises(KeyError):
        rebaseline_feed(state, "missing", reason="x")
    reset = rebaseline_feed(state, "feed_a", reason="site redesign")
    feed = reset.feeds["feed_a"]
    assert feed.baseline_status is BaselineStatus.NOT_STARTED
    assert feed.baselined_revision is None and feed.last_item_count is None
    # A shrink that would otherwise collapse is accepted and silent.
    outcome = apply_observation(
        reset, obs(2, [doc("a", title="Retitled"), doc("new")]), contract(), ctx(2)
    )
    assert outcome.feed_run.accepted
    assert outcome.events == ()
    assert outcome.state.documents[key("new")].discovery is Discovery.BASELINE
    assert outcome.state.feeds["feed_a"].baseline_status is BaselineStatus.COMPLETE


def test_ensure_feed_state_creates_and_syncs() -> None:
    created = ensure_feed_state(CatalogState(), contract(parser_version=3))
    feed = created.feeds["feed_a"]
    assert feed.baseline_status is BaselineStatus.NOT_STARTED
    assert feed.parser_version == 3 and feed.coverage_revision == 1
    assert ensure_feed_state(created, contract(parser_version=3)) is created
    bumped = ensure_feed_state(created, contract(coverage_revision=4))
    assert bumped.feeds["feed_a"].coverage_revision == 4


def test_mismatched_feed_is_rejected() -> None:
    with pytest.raises(ValueError):
        apply_observation(CatalogState(), obs(1, [doc("a")], feed_id="feed_b"), contract(), ctx(1))
