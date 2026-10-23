"""Drills 12 and 14: schema/parser migrations on real data, and the ten-day gap flag."""

from __future__ import annotations

import json

import pytest
from acceptance_harness import (
    COAL,
    FakeSender,
    Harness,
    events_of,
    fixture_page,
    week,
)

from policy_tracker.adapters import ministry_of_coal
from policy_tracker.core import migrations
from policy_tracker.core.models import SCHEMA_VERSION, EventType
from policy_tracker.operations.store import CATALOG_PARTS

QUARTERLY = "coal_quarterly_reports"
QUARTERLY_URL = COAL + "major-statistics/quarterly-booklet"


def _v0_to_v1(document: dict) -> dict:
    """Synthetic v0: feed states called the counter ``failures`` and had no parser version."""

    if document.get("kind") == "catalog_state":
        for feed in document["feeds"].values():
            feed["consecutive_failures"] = feed.pop("failures")
            feed.setdefault("parser_version", 1)
    return document


def _downgrade_catalog_to_v0(harness: Harness) -> dict[str, int]:
    """Rewrite the promoted catalog as a v0 writer would have; return failure counters."""

    counters: dict[str, int] = {}
    for part in CATALOG_PARTS:
        path = harness.data_dir / "catalog" / f"{part}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["schema_version"] = 0
        document["state_hash"] = "v0-writers-hashed-differently"
        if part == "feeds":
            for feed_id, feed in document["records"].items():
                counters[feed_id] = feed["consecutive_failures"]
                feed["failures"] = feed.pop("consecutive_failures")
                del feed["parser_version"]
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return counters


def test_drill12_v0_catalog_migrates_on_load_and_is_written_back_current(
    core: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = core
    harness.baseline()
    before = harness.state()
    counters = _downgrade_catalog_to_v0(harness)
    assert any(counters.values())  # unreachable feeds carry a non-zero counter

    with pytest.raises(Exception, match="no migration registered from schema_version 0"):
        harness.state()  # without the migration step, v0 data is refused, never guessed
    monkeypatch.setattr(migrations, "MIGRATIONS", {0: _v0_to_v1})
    migrated = harness.state()
    assert migrated == before

    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    assert report.exit_code == 0 and report.delivery == "delivered"
    assert report.events == ()  # a schema migration is never a source change
    assert "heartbeat" in sender.calls[0][1].subject
    after = harness.state()
    for feed_id, failures in counters.items():
        expected = 0 if feed_id in harness.world.feeds else failures + 1
        assert after.feeds[feed_id].consecutive_failures == expected, feed_id
    for part in CATALOG_PARTS:
        document = json.loads((harness.data_dir / "catalog" / f"{part}.json").read_text())
        assert document["schema_version"] == SCHEMA_VERSION
    monkeypatch.setattr(migrations, "MIGRATIONS", {})
    assert harness.store.state_hash() == report.state_hash  # current, hash-verified again


def test_drill12_parser_version_bump_applies_metadata_silently(
    core: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = core
    harness.baseline()
    monkeypatch.setattr(ministry_of_coal, "PARSER_VERSION", 2)  # a deployed parser change
    harness.world.set_page(
        QUARTERLY,
        QUARTERLY_URL,
        fixture_page("ministry_of_coal", "direct_attachment_table_new_row.html"),
    )
    harness.world.edit_page(
        QUARTERLY,
        QUARTERLY_URL,
        lambda html: html.replace(
            "<td>Current example publication</td>",
            "<td>Current example publication (title as the new parser reads it)</td>",
        ),
    )
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    assert report.exit_code == 0
    assert not events_of(report.events, EventType.METADATA_CHANGED)
    assert not events_of(report.events, EventType.STATUS_CHANGED)
    (new,) = events_of(report.events, EventType.NEW_DOCUMENT)  # real additions still alert
    assert new.entity_key.endswith("new-example.pdf")
    state = harness.state()
    titles = {d.title for d in state.documents.values() if d.site_id == "ministry_of_coal"}
    assert "Current example publication (title as the new parser reads it)" in titles
    (feed_run,) = [r for r in report.feed_runs if r.feed_id == QUARTERLY]
    assert any("parser migration 1 -> 2" in d for d in feed_run.diagnostics)
    coal_live = [f for f in harness.world.feeds if f.startswith("coal_")]
    assert {state.feeds[f].parser_version for f in coal_live} == {2}
    assert {state.feeds[f].parser_version for f in harness.world.feeds if f not in coal_live} == {1}

    # The next run under the new parser is ordinary again: a real title edit alerts.
    harness.world.edit_page(
        QUARTERLY, QUARTERLY_URL, lambda html: html.replace("01 Apr 2021", "02 Apr 2021")
    )
    later = harness.run(when=week(2), sender=sender)
    (changed,) = events_of(later.events, EventType.METADATA_CHANGED)
    assert "published_raw" in changed.details["changed_fields"]


def test_drill14_gap_over_ten_days_is_flagged_in_the_next_digest(core: Harness) -> None:
    harness = core
    harness.baseline()
    sender = FakeSender()

    def gap(report) -> str | None:
        return harness.digest(report)["gap_warning"]

    weekly = harness.run(when=week(1), sender=sender)
    assert gap(weekly) is None and "GAP WARNING" not in sender.calls[-1][1].text_body

    # Week 2: every feed fails (outage). Nothing is promoted or emailed.
    harness.world.failing.update({feed_id: "connection_reset" for feed_id in harness.world.feeds})
    outage = harness.run(when=week(2), sender=sender)
    assert outage.exit_code == 1 and not outage.promoted and len(sender.calls) == 1
    harness.world.failing.clear()

    # Week 3: the first successful run since week 1 flags the 14-day gap.
    recovered = harness.run(when=week(3), sender=sender)
    warning = gap(recovered)
    assert warning is not None and "14 days ago" in warning
    email = sender.calls[-1][1]
    assert "GAP WARNING" in email.text_body and "GAP WARNING" in email.html_body
    md = next(p for p in recovered.artifacts if p.endswith(".md"))
    assert "GAP WARNING" in (harness.data_dir / md).read_text()

    assert gap(harness.run(when=week(4), sender=sender)) is None  # back on schedule

    # A pending delivery spanning the gap: the retried digest is promoted late, but the
    # gap is still measured from what the previous digest covered.
    stuck = harness.run(when=week(5), sender=FakeSender(["timeout"]))
    assert stuck.delivery == "pending"
    late = harness.run(when=week(7), sender=sender)
    assert late.exit_code == 0
    assert gap(late) is not None and "14 days ago" in gap(late)
