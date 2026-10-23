"""Drills 7-8: old-parent attachments and rolling vs authoritative disappearance."""

from __future__ import annotations

import json

import pytest
from acceptance_harness import (
    CERC,
    COAL,
    FakeSender,
    Harness,
    docs_url,
    events_of,
    feed_appearances,
    fixture_page,
    week,
    with_new_draft_file,
)

from policy_tracker.adapters import ministry_of_power as mop
from policy_tracker.core.models import Discovery, EventType, RecordStatus

QUARTERLY = "coal_quarterly_reports"
QUARTERLY_URL = COAL + "major-statistics/quarterly-booklet"
NEW_ROW_KEY = (
    "ministry_of_coal:document:id:https://www.coal.nic.in/sites/default/files/2026-10/"
    "new-example.pdf"
)


@pytest.fixture
def harness(core: Harness) -> Harness:
    return core


def _cerc_new_draft_file(harness: Harness) -> None:
    harness.world.edit_page("cerc_draft_regulations", CERC + "Draft_reg.html", with_new_draft_file)


def _mop_new_attachment(harness: Harness) -> None:
    harness.world.set_page(
        "mop_documents",
        docs_url("acts-and-policy", 1),
        fixture_page("ministry_of_power", "adapter_acts_page1_new_attachment.json"),
    )


OLD_PARENT_CASES = {
    "cerc_draft_regulation": (
        _cerc_new_draft_file,
        "cerc_draft_regulations",
        "cerc:document:url:https://cercind.gov.in/2026/draft_reg/example-reasons.pdf",
        "cerc:matter:id:draft_reg:119",
    ),
    "mop_parent_post": (
        _mop_new_attachment,
        "mop_documents",
        "ministry_of_power:document:id:6013",
        "ministry_of_power:matter:id:7002",
    ),
}


@pytest.mark.parametrize("case", sorted(OLD_PARENT_CASES))
def test_drill7_old_parent_gaining_a_file_is_a_new_document_of_the_existing_matter(
    harness: Harness, case: str
) -> None:
    stage, feed_id, document_key, matter_key = OLD_PARENT_CASES[case]
    harness.baseline()
    base = harness.state()
    assert matter_key in base.matters and document_key not in base.documents
    old_children = {k for k, d in base.documents.items() if d.matter_key == matter_key}
    assert old_children  # the parent already had files at baseline

    stage(harness)
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    assert report.exit_code == 0 and report.delivery == "delivered"
    (new,) = events_of(report.events, EventType.NEW_DOCUMENT)
    assert (new.entity_key, new.feed_id, new.discovery) == (document_key, feed_id, Discovery.LIVE)
    assert new.details["matter_key"] == matter_key
    assert not events_of(report.events, EventType.NEW_MATTER)
    state = harness.state()
    assert state.documents[document_key].matter_key == matter_key
    assert state.matters[matter_key].first_seen_at == base.matters[matter_key].first_seen_at
    assert state.matters[matter_key].discovery is Discovery.BASELINE
    assert old_children <= {k for k, d in state.documents.items() if d.matter_key == matter_key}
    items = {i["entity_key"]: i for i in harness.digest(report)["items"]}
    assert items[document_key]["event_type"] == "new_document"
    assert state.documents[document_key].title in sender.calls[0][1].text_body


def _drop_cerc_home_item(html: str) -> str:
    gone = '<li><a href="/2022/whatsnew/example-persistent-form.docx">'
    start = html.index(gone)
    return html[:start] + html[html.index("</li>", start) + 5 :]


def _drop_mop_whats_new_post(body: str) -> str:
    data = json.loads(body)
    data["posts"] = [post for post in data["posts"] if post["ID"] != 9001]
    data["total_items"] = len(data["posts"])
    return json.dumps(data)


def test_drill8_rolling_items_disappearing_for_three_complete_runs_are_never_withdrawn(
    harness: Harness,
) -> None:
    harness.baseline()
    base = harness.state()
    for url in ("index-en.html", "viewall.html"):
        harness.world.edit_page("cerc_home", CERC + url, _drop_cerc_home_item)
    harness.world.edit_page("mop_whats_new", mop.WHATS_NEW_URL, _drop_mop_whats_new_post)
    rolling = ("cerc_home", "mop_whats_new")
    reports = [harness.run(when=week(n), no_email=True) for n in (1, 2, 3)]
    for report in reports:
        for feed_run in report.feed_runs:
            if feed_run.feed_id in rolling:
                assert feed_run.accepted and feed_run.enumeration_status.value == "complete"
        assert not events_of(report.events, EventType.WITHDRAWN)
        assert not [e for e in report.events if e.feed_id in rolling]
    state = harness.state()
    for feed_id in rolling:
        for key, appearance in feed_appearances(base, feed_id).items():
            record = state.documents.get(key) or state.matters[key]
            assert record.status is RecordStatus.ACTIVE, key
            assert state.appearances[appearance.key].missing_complete_runs == 0
    gone = [k for k in feed_appearances(base, "cerc_home") if k.endswith(".docx")]
    assert gone and state.documents[gone[0]].status is RecordStatus.ACTIVE


def test_drill8_authoritative_item_withdrawn_exactly_once_then_reappears(
    harness: Harness,
) -> None:
    with_row = fixture_page("ministry_of_coal", "direct_attachment_table_new_row.html")
    without = fixture_page("ministry_of_coal", "direct_attachment_table.html")
    captcha = fixture_page("ministry_of_coal", "captcha_block.html")
    harness.world.set_page(QUARTERLY, QUARTERLY_URL, with_row)
    harness.baseline()
    sender = FakeSender()

    def run(n: int, entry, days: int = 0):
        harness.world.set_page(QUARTERLY, QUARTERLY_URL, entry)
        report = harness.run(when=week(n, days=days), sender=sender)
        assert report.exit_code == 0 and report.delivery == "delivered"
        return report

    def appearance():
        return feed_appearances(harness.state(), QUARTERLY)[NEW_ROW_KEY]

    first_missing = run(1, without)
    assert not first_missing.events and appearance().missing_complete_runs == 1
    blocked = run(1, captcha, days=3)  # a quarantined run in between counts for nothing
    assert not blocked.events and appearance().missing_complete_runs == 1
    second_missing = run(2, without)
    (withdrawn,) = second_missing.events
    assert (withdrawn.event_type, withdrawn.entity_key) == (EventType.WITHDRAWN, NEW_ROW_KEY)
    assert withdrawn.details["record_status"] == "withdrawn"
    assert harness.state().documents[NEW_ROW_KEY].status is RecordStatus.WITHDRAWN
    (item,) = harness.digest(second_missing)["items"]
    assert item["event_type"] == "withdrawn"
    assert "Removed from source" in sender.calls[-1][1].text_body

    third_missing = run(3, without)
    assert third_missing.events == ()
    back = run(4, with_row)
    (reappeared,) = back.events
    assert (reappeared.event_type, reappeared.entity_key) == (EventType.REAPPEARED, NEW_ROW_KEY)
    assert harness.state().documents[NEW_ROW_KEY].status is RecordStatus.ACTIVE
    assert appearance().present and appearance().missing_complete_runs == 0
    assert harness.digest(back)["items"][0]["event_type"] == "reappeared"

    every = [e for e in harness.all_events() if e.entity_key == NEW_ROW_KEY]
    assert [e.event_type for e in every] == [EventType.WITHDRAWN, EventType.REAPPEARED]
    assert run(5, with_row).events == ()
