"""Drills 4-6: malformed 200s, partial pagination, recovered baselines (PROJECT_PLAN Step 8)."""

from __future__ import annotations

import pytest
from acceptance_harness import (
    CERC,
    COAL,
    COAL_NA,
    COAL_PI,
    COAL_TESTS,
    FakeSender,
    Harness,
    ReplayEntry,
    docs_url,
    events_of,
    feed_appearances,
    fixture_page,
    http_status,
    page,
    week,
    with_new_draft_file,
)

from policy_tracker.adapters import ministry_of_power as mop
from policy_tracker.core.models import BaselineStatus, Discovery, EventType
from policy_tracker.operations import cli, runner

QUARTERLY = "coal_quarterly_reports"
QUARTERLY_URL = COAL + "major-statistics/quarterly-booklet"


@pytest.fixture
def harness(core: Harness) -> Harness:
    return core


def health_row(digest: dict, feed_id: str) -> dict:
    (row,) = [h for h in digest["health"] if h["feed_id"] == feed_id]
    return row


@pytest.mark.parametrize(
    ("feed_id", "url", "fixture_site", "fixture", "reason"),
    [
        ("mop_whats_new", mop.WHATS_NEW_URL, "ministry_of_power", "browser_block.html", "block"),
        (QUARTERLY, QUARTERLY_URL, "ministry_of_coal", "captcha_block.html", "captcha"),
        (
            "mop_documents",
            docs_url("acts-and-policy", 1),
            "ministry_of_power",
            "browser_block.html",
            "block mid-pagination",
        ),
    ],
)
def test_drill4_malformed_200_quarantines_one_feed_and_others_advance(
    harness: Harness, feed_id: str, url: str, fixture_site: str, fixture: str, reason: str
) -> None:
    base_report = harness.baseline()
    base = harness.state()
    harness.world.set_page(feed_id, url, fixture_page(fixture_site, fixture))  # HTTP 200
    # A real change elsewhere in the same run must still be reported.
    other = "coal_termination_orders" if feed_id != QUARTERLY else "mop_whats_new"
    if other == "mop_whats_new":
        harness.world.set_page(
            other,
            mop.WHATS_NEW_URL,
            fixture_page("ministry_of_power", "adapter_whats_new_added.json"),
        )
    else:
        harness.world.set_page(
            other,
            COAL_NA + "termination-order",
            fixture_page("ministry_of_coal", "termination_orders_moved.html"),
        )
        harness.world.set_page(
            other,
            COAL_NA + "archive-termination-order",
            fixture_page("ministry_of_coal", "termination_archive_moved.html"),
        )
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    assert report.exit_code == 0 and report.delivery == "delivered"

    (feed_run,) = [r for r in report.feed_runs if r.feed_id == feed_id]
    assert not feed_run.accepted and "block_page" in feed_run.quarantine_reasons, reason
    state = harness.state()
    # Prior state retained for the blocked feed: same appearances, still live, one failure.
    assert feed_appearances(state, feed_id) == feed_appearances(base, feed_id)
    feed = state.feeds[feed_id]
    assert feed.baseline_status is BaselineStatus.COMPLETE
    assert feed.last_complete_run_id == base_report.run_id
    assert feed.last_item_count == base.feeds[feed_id].last_item_count
    assert feed.consecutive_failures == 1
    assert not [e for e in report.events if e.feed_id == feed_id]

    # Other feeds advanced, and the change on another feed is reported.
    assert state.feeds[other].last_complete_run_id == report.run_id
    assert [e for e in report.live_events if e.feed_id == other]
    digest = harness.digest(report)
    assert digest["partial"] is True
    row = health_row(digest, feed_id)
    assert row["accepted"] is False and "block_page" in row["quarantine_reasons"]
    assert row["consecutive_failures"] == 1 and row["baseline_status"] == "complete"
    assert health_row(digest, other)["accepted"] is True
    email = sender.calls[0][1]
    assert "PARTIAL DIGEST" in email.text_body
    assert f"{feed_id} (" in email.text_body and "block_page" in email.text_body

    health = harness.store.load_run(report.run_id)
    assert any(r["feed_id"] == feed_id and not r["accepted"] for r in health["feed_runs"])


PARTIAL_CASES = {
    # A pager page answers 404 mid-enumeration (authoritative, paged).
    "coal_404_page": (
        "coal_national_coal_index",
        COAL_NA + "national-coal-index?page=1",
        http_status(404),
    ),
    # A pager page's connection is reset (authoritative JSON API).
    "mop_reset_page": (
        "mop_documents",
        docs_url("reports", 2),
        ReplayEntry(error="connection_reset"),
    ),
    # An archive pager loses its Last link: the run is accepted but only partially
    # enumerated, so the rows on the unreached archive page are simply not seen.
    "coal_truncated_archive": (
        "coal_minutes_of_meetings",
        COAL_PI + "minutes-metings/archive-minutes-metings",
        page(
            COAL_TESTS.fixture_bytes("minutes_archive.html").replace(
                b'<li class="pager__item pager__item--last"><a href="?page=1" '
                b'title="Go to last page">Last &raquo;</a></li>',
                b"",
            )
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(PARTIAL_CASES))
def test_drill5_partial_pagination_never_removes_and_marks_feed_incomplete(
    harness: Harness, case: str
) -> None:
    feed_id, url, entry = PARTIAL_CASES[case]
    base_report = harness.baseline()
    base = harness.state()
    harness.world.set_page(feed_id, url, entry)
    reports = [harness.run(when=week(n), no_email=True) for n in (1, 2, 3)]
    for report in reports:
        (feed_run,) = [r for r in report.feed_runs if r.feed_id == feed_id]
        assert feed_run.enumeration_status.value != "complete", case
        assert not events_of(report.events, EventType.WITHDRAWN)
        assert not [e for e in report.events if e.feed_id == feed_id]
        digest = harness.digest(report)
        assert digest["partial"] is True
        assert health_row(digest, feed_id)["enumeration_status"] != "complete"
    state = harness.state()
    for key, appearance in feed_appearances(state, feed_id).items():
        assert appearance.present and appearance.missing_complete_runs == 0, key
    assert set(feed_appearances(state, feed_id)) == set(feed_appearances(base, feed_id))
    if case == "coal_truncated_archive":  # accepted partial runs refresh what they saw
        assert any(r.accepted for r in reports[0].feed_runs if r.feed_id == feed_id)
    feed = state.feeds[feed_id]
    assert feed.last_complete_run_id == base_report.run_id  # no complete run since
    assert feed.last_item_count == base.feeds[feed_id].last_item_count
    assert all(d.status.value == "active" for d in state.documents.values())


def test_drill6_feed_failing_during_baseline_baselines_silently_on_recovery(
    harness: Harness,
) -> None:
    harness.world.failing[QUARTERLY] = "connection_reset"
    first = harness.baseline()
    assert QUARTERLY in first.failed_feeds
    assert harness.state().feeds[QUARTERLY].baseline_status is BaselineStatus.NOT_STARTED

    # Weekly runs while it is still down: nothing about it is alerted.
    sender = FakeSender()
    harness.run(when=week(1), sender=sender)
    assert harness.state().feeds[QUARTERLY].consecutive_failures == 2

    # It recovers, and its listing already holds a row published in the meantime:
    # that row is history for this feed's new baseline, not a live alert.
    del harness.world.failing[QUARTERLY]
    harness.world.set_page(
        QUARTERLY,
        QUARTERLY_URL,
        fixture_page("ministry_of_coal", "direct_attachment_table_new_row.html"),
    )
    recovered = harness.run(when=week(2), sender=sender)
    assert recovered.exit_code == 0 and recovered.delivery == "delivered"
    assert not [e for e in recovered.events if e.feed_id == QUARTERLY]
    state = harness.state()
    feed = state.feeds[QUARTERLY]
    assert feed.baseline_status is BaselineStatus.COMPLETE and feed.consecutive_failures == 0
    assert feed.last_item_count == 3
    keys = feed_appearances(state, QUARTERLY)
    assert all(state.documents[k].discovery is Discovery.BASELINE for k in keys)
    digest = harness.digest(recovered)
    assert digest["items"] == []
    assert any(f"Coverage initialized for {QUARTERLY}" in note for note in digest["notes"])
    email = sender.calls[-1][1]
    assert "heartbeat" in email.subject and f"Coverage initialized for {QUARTERLY}" in (
        email.text_body
    )

    # From now on the feed is live: a later addition is a LIVE new document.
    harness.world.set_page(
        QUARTERLY, QUARTERLY_URL, fixture_page("ministry_of_coal", "direct_attachment_table.html")
    )
    harness.world.edit_page(
        QUARTERLY,
        QUARTERLY_URL,
        lambda html: html.replace(
            "<tr><td>2</td>",
            '<tr><td>1</td><td>Another example publication</td><td><a href="/sites/default/'
            'files/2026-11/another-example.pdf">Downloads</a> File Size: 1 MB File Type: PDF'
            "</td><td>10/11/2026</td></tr>\n<tr><td>2</td>",
            1,
        ),
    )
    later = harness.run(when=week(3), sender=sender)
    new = events_of(later.events, EventType.NEW_DOCUMENT, QUARTERLY)
    assert [e.discovery for e in new] == [Discovery.LIVE], later.events


def test_drill6_operator_rebaseline_after_redesign_is_silent(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """``policy-tracker rebaseline-feed``: the redesigned feed re-baselines without alerts."""

    harness.baseline()
    monkeypatch.setattr(runner, "utc_now", harness.clock)
    harness.clock.at(week(0, days=3))
    feed_id = "cerc_draft_regulations"
    code = cli.main(
        ["rebaseline-feed", feed_id, "--reason", "site redesign", "--config",
         str(harness.config), "--data-dir", str(harness.data_dir)]
    )  # fmt: skip
    assert code == 0 and f"rebaselined {feed_id}" in capsys.readouterr().out
    assert harness.state().feeds[feed_id].baseline_status is BaselineStatus.NOT_STARTED
    harness.world.edit_page(feed_id, CERC + "Draft_reg.html", with_new_draft_file)
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    assert not [e for e in report.events if e.feed_id == feed_id]
    assert harness.state().feeds[feed_id].baseline_status is BaselineStatus.COMPLETE
    assert any(f"Coverage initialized for {feed_id}" in n for n in harness.digest(report)["notes"])
    health = (harness.data_dir / "health" / "feeds.json").read_text()
    assert "site redesign" in health
