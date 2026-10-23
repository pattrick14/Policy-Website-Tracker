"""Unit tests for evidence-backed deadline extraction and the deadline index."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from policy_tracker.reporting.deadlines import (
    UNKNOWN_SORT_KEY,
    deadline_changes,
    display,
    extract_deadlines,
    extract_field_deadlines,
    index_from_data,
    index_to_data,
    is_open,
    merge,
)
from policy_tracker.reporting.types import Deadline


def one(text: str) -> Deadline:
    found = extract_deadlines(text, method="text_regex")
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize(
    ("text", "date", "time"),
    [
        ("Comments may be sent by 15.11.2026.", "2026-11-15", None),
        ("Comments may be sent by 15-11-2026.", "2026-11-15", None),
        ("Comments may be sent by 15/11/2026.", "2026-11-15", None),
        ("Comments on or before 03/11/2026.", "2026-11-03", None),  # DD/MM convention
        ("Suggestions on or before 15th November, 2026.", "2026-11-15", None),
        ("Suggestions not later than November 15, 2026.", "2026-11-15", None),
        ("Comments latest by 15 Nov 2026 till 5:00 PM.", "2026-11-15", "17:00"),
        ("Comments by 5:30 p.m. on 15.11.2026.", "2026-11-15", "17:30"),
        ("Last date for submission of comments: 15.11.2026 (17:00 hrs)", "2026-11-15", "17:00"),
        ("Last date for comments is 15.11.2026 at 1700 hrs.", "2026-11-15", "17:00"),
        ("Comments till 2026-11-15.", "2026-11-15", None),
        ("टिप्पणी 15.11.2026 तक भेजें।", "2026-11-15", None),
    ],
)
def test_patterns(text: str, date: str, time: str | None) -> None:
    deadline = one(text)
    assert (deadline.date, deadline.time, deadline.ambiguous) == (date, time, False)
    assert deadline.evidence == text
    expected_time = time or "23:59"
    assert deadline.sort_key == f"{date}T{expected_time}:00+05:30"


def test_requires_topic_and_cue() -> None:
    assert extract_deadlines("Notification dated 01.10.2026 on tariff.", method="m") == ()
    assert extract_deadlines("Meeting held on 15.11.2026.", method="m") == ()
    text = "Comments on the draft notified on 01.10.2026 may be sent by 15.11.2026."
    assert one(text).date == "2026-11-15"
    text = "Last date for comments on draft dated 01.10.2026 is 15.11.2026."
    assert one(text).date == "2026-11-15"


def test_extension_takes_new_date() -> None:
    deadline = one(
        "The last date for submission of comments has been extended from 15.11.2026 to 30.11.2026."
    )
    assert deadline.date == "2026-11-30"
    assert deadline.method == "text_regex:extension"
    deadline = one("The last date of 15.11.2026 is extended till 30th November, 2026 (5 PM).")
    assert (deadline.date, deadline.time) == ("2026-11-30", "17:00")


def test_extension_supersedes_original_in_other_sentence() -> None:
    text = (
        "Comments were invited by 15.11.2026.\n"
        "On requests from stakeholders, the last date is extended up to 30.11.2026."
    )
    found = extract_deadlines(text, method="text_regex")
    assert [d.date for d in found] == ["2026-11-30"]


@pytest.mark.parametrize(
    "text",
    [
        "Comments by 15/11/26.",  # two-digit year
        "Comments by 31.02.2026.",  # impossible date
        "Comments by 11/15/2026.",  # valid only as MM/DD
        "Comments by 15th November.",  # no year
    ],
)
def test_ambiguous(text: str) -> None:
    deadline = one(text)
    assert deadline.ambiguous
    assert deadline.date is None
    assert deadline.sort_key == UNKNOWN_SORT_KEY
    assert display(deadline).endswith("(ambiguous date)")


def test_display_time_not_stated() -> None:
    assert display(one("Comments by 15.11.2026.")) == "15 Nov 2026 (time not stated)"
    assert display(one("Comments by 15.11.2026 by 17:00.")) == "15 Nov 2026, 17:00 IST"


def test_listing_field() -> None:
    found = extract_field_deadlines(
        {"Last Date for Comments": "15.11.2026 5:00 PM", "Subject": "Comments by 1.1.2027"}
    )
    assert len(found) == 1
    assert found[0].method == "listing_field"
    assert (found[0].date, found[0].time) == ("2026-11-15", "17:00")
    assert found[0].evidence == "Last Date for Comments: 15.11.2026 5:00 PM"


def test_merge_dedupes_by_date_and_time() -> None:
    a = extract_deadlines("Comments by 15.11.2026.", method="title_regex")
    b = extract_deadlines("Comments are invited by 15th November 2026.", method="text_regex")
    merged = merge(a, b)
    assert len(merged) == 1 and merged[0].method == "title_regex"


def test_deadline_changes() -> None:
    old = {"a": extract_deadlines("Comments by 15.11.2026.", method="t")}
    same_new_evidence = {"a": extract_deadlines("Suggestions by 15 Nov 2026.", method="x")}
    assert deadline_changes(old, same_new_evidence) == set()
    extended = {"a": extract_deadlines("Comments extended to 30.11.2026.", method="t")}
    assert deadline_changes(old, extended) == {"a"}
    timed = {"a": extract_deadlines("Comments by 15.11.2026 by 5 PM.", method="t")}
    assert deadline_changes(old, timed) == {"a"}
    assert deadline_changes(old, {}) == {"a"}
    assert deadline_changes({}, old) == {"a"}
    assert deadline_changes({"b": ()}, {}) == set()


def test_is_open() -> None:
    deadline = one("Comments by 15.11.2026.")
    assert is_open(deadline, datetime(2026, 11, 15, 18, 0, tzinfo=UTC))  # 23:30 IST
    assert not is_open(deadline, datetime(2026, 11, 15, 18, 30, tzinfo=UTC))
    assert not is_open(one("Comments by 15/11/26."), datetime(2000, 1, 1, tzinfo=UTC))


def test_index_round_trip() -> None:
    index = {"k": extract_deadlines("Comments by 15.11.2026 by 5 PM.", method="t"), "e": ()}
    data = index_to_data(index)
    assert list(data["records"]) == ["k"]
    assert index_from_data(data) == {"k": index["k"]}
