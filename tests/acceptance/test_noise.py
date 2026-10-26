"""Acceptance criterion: no alerts for ordering, whitespace, navigation, mirrors, or repeats.

Each perturbation below changes the bytes a source serves without changing what
it publishes. A week of such noise must be a heartbeat.
"""

from __future__ import annotations

import dataclasses
import json
import re

import pytest
from acceptance_harness import (
    CERC,
    COAL,
    COAL_NA,
    FakeSender,
    Harness,
    ReplayEntry,
    week,
)

from policy_tracker.adapters import ministry_of_power as mop

QUARTERLY_URL = COAL + "major-statistics/quarterly-booklet"


@pytest.fixture
def harness(core: Harness) -> Harness:
    return core


def _spaced_text_nodes(html: str) -> str:
    """Pad and double the spaces of every text node (markup and attributes untouched)."""

    return re.sub(r">([^<]+)<", lambda m: ">  " + m.group(1).replace(" ", "  ") + " \n<", html)


def _swap_quarterly_rows(html: str) -> str:
    rows = re.findall(r"<tr><td>\d</td>.*?</tr>", html)
    assert len(rows) == 2
    swapped = html.replace(rows[0], "\0").replace(rows[1], rows[0]).replace("\0", rows[1])
    return (
        swapped.replace("<td>2</td>", "<td>X</td>")
        .replace("<td>1</td>", "<td>2</td>")
        .replace("<td>X</td>", "<td>1</td>")
    )


def _anti_bot_token(html: str, token: str) -> str:
    return html.replace("</body>", f'<script src="/bnith__{token}"></script></body>')


def _duplicate_first_row(html: str) -> str:
    row = re.search(r"<tr><td>1</td>.*?</tr>", html)
    assert row is not None
    return html.replace(row.group(0), row.group(0) * 2, 1)


def _reorder_mop_posts(body: str) -> str:
    data = json.loads(body)
    data["posts"] = list(reversed(data["posts"]))
    for index, post in enumerate(data["posts"]):
        post["menu_order"] = 1000 + index  # ordering metadata changes too
    return json.dumps(data, indent=4)


def test_cosmetic_source_changes_produce_a_heartbeat(harness: Harness) -> None:
    harness.baseline()
    world = harness.world
    world.edit_page("cerc_public_notices", CERC + "public-notice.html", _spaced_text_nodes)
    world.edit_page("cerc_draft_regulations", CERC + "Draft_reg.html", _spaced_text_nodes)
    world.edit_page("coal_quarterly_reports", QUARTERLY_URL, _swap_quarterly_rows)
    world.edit_page("mop_whats_new", mop.WHATS_NEW_URL, _reorder_mop_posts)
    # Anti-bot script tokens change on every Coal request (D-017).
    index = COAL_NA + "national-coal-index"
    world.edit_page("coal_national_coal_index", index, lambda h: _anti_bot_token(h, "9f8e7d"))
    world.edit_page(
        "coal_termination_orders", COAL_NA + "termination-order", lambda h: _anti_bot_token(h, "1")
    )
    # CERC answered from the www fallback host (D-020) for every page this week.
    for url, entry in list(world.feeds["cerc_home"].items()):
        assert isinstance(entry, ReplayEntry)
        mirrored = url.replace("https://cercind.gov.in/", "https://www.cercind.gov.in/")
        world.set_page(
            "cerc_home",
            url,
            dataclasses.replace(entry, final_url=mirrored, served_by_host="www.cercind.gov.in"),
        )

    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    assert report.exit_code == 0
    assert report.events == (), [(e.event_type.value, e.feed_id, e.details) for e in report.events]
    assert "heartbeat" in sender.calls[0][1].subject
    # The perturbations were really served: the swapped rows moved (provenance follows).
    moved = harness.state().appearances
    locators = {a.entity_key.rsplit("/", 1)[-1]: a.provenance.locator for a in moved.values()
                if a.feed_id == "coal_quarterly_reports"}  # fmt: skip
    assert locators["current-example.pdf"] == "table[0]/row[2]/link[0]"


def test_a_row_repeated_on_the_page_is_one_record_and_no_alert(harness: Harness) -> None:
    harness.baseline()
    before = harness.state().feeds["coal_quarterly_reports"].last_item_count
    harness.world.edit_page("coal_quarterly_reports", QUARTERLY_URL, _duplicate_first_row)
    report = harness.run(when=week(1), no_email=True)
    assert report.events == ()
    (feed_run,) = [r for r in report.feed_runs if r.feed_id == "coal_quarterly_reports"]
    assert feed_run.accepted and feed_run.item_count == before
