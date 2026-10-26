"""Drills 9-10: the validator limitation (D-008) and OCR/extraction failures."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from acceptance_harness import (
    COAL,
    RECIPIENTS,
    FakeSender,
    Harness,
    World,
    document_entry,
    events_of,
    fixture_page,
    make_harness,
    make_pdf,
    week,
)

from policy_tracker.core.models import EventType
from policy_tracker.documents.enrich import CONDITIONAL_304, FULL_DOWNLOAD
from policy_tracker.operations import cli, pipeline
from policy_tracker.reporting.digest import SILENT_REPLACEMENT_NOTE

QUARTERLY = "coal_quarterly_reports"
QUARTERLY_URL = COAL + "major-statistics/quarterly-booklet"
TARGET_URL = COAL + "sites/default/files/2026-10/current-example.pdf"
TARGET_KEY = f"ministry_of_coal:document:id:{TARGET_URL}"
NEW_DOC_URL = COAL + "sites/default/files/2026-10/new-example.pdf"
NEW_DOC_KEY = f"ministry_of_coal:document:id:{NEW_DOC_URL}"
ORIGINAL = make_pdf(["Current example publication, original text"])
REPLACED = make_pdf(["Current example publication, silently corrected text"])
OLD_VALIDATORS = {"etag": '"v1"', "last_modified": "Mon, 05 Oct 2026 10:00:00 GMT"}
NEW_VALIDATORS = {"etag": '"v2"', "last_modified": "Mon, 09 Nov 2026 10:00:00 GMT"}


def production_services(harness: Harness, sender: FakeSender) -> pipeline.Services:
    """The collaborators ``policy-tracker run`` builds (cli.default_services), fake SMTP."""

    args = argparse.Namespace(publish=False, max_documents=harness.max_documents)
    services = cli.default_services(args, deliver=False)
    services.sender = sender
    services.recipients = RECIPIENTS
    services.message_id_domain = "example.org"
    return services


@pytest.fixture
def versioned(tmp_path: Path) -> Harness:
    """One authoritative Coal feed whose two files were hashed during the baseline."""

    harness = make_harness(tmp_path, World.only(QUARTERLY), max_documents=5)
    harness.world.documents[TARGET_URL] = document_entry(ORIGINAL, **OLD_VALIDATORS)
    harness.baseline()
    (version,) = harness.state().versions[TARGET_KEY]
    assert version.verification_method == FULL_DOWNLOAD and version.etag == '"v1"'
    return harness


def _replace(harness: Harness, validators: dict[str, str]) -> None:
    harness.world.documents[TARGET_URL] = document_entry(REPLACED, **validators)


def test_drill9_same_validators_and_304_hide_a_byte_replacement_d008(versioned: Harness) -> None:
    """Known, documented limitation: not a bug. The digest discloses it every week."""

    harness = versioned
    harness.enrich_extra["select"] = "all"  # revalidate every versioned document
    _replace(harness, OLD_VALIDATORS)  # new bytes, same ETag/Last-Modified -> server says 304
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    assert not events_of(report.events, EventType.CONTENT_REPLACED)
    history = harness.state().versions[TARGET_KEY]
    assert history[-1].verification_method == CONDITIONAL_304
    assert history[-1].byte_sha256 == history[0].byte_sha256  # the old hash is still believed
    assert SILENT_REPLACEMENT_NOTE in harness.digest(report)["notes"]
    assert "(D-008)" in sender.calls[0][1].text_body
    revalidations = [r for r in harness.fetchers[-1].replay.requests if r.url == TARGET_URL]
    assert revalidations and revalidations[-1].headers.get("If-None-Match") == '"v1"'


def test_drill9_new_validators_and_new_bytes_report_content_replaced(versioned: Harness) -> None:
    harness = versioned
    harness.enrich_extra["select"] = "all"
    _replace(harness, NEW_VALIDATORS)
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender)
    (event,) = events_of(report.events, EventType.CONTENT_REPLACED)
    assert event.entity_key == TARGET_KEY and event.details["new_etag"] == '"v2"'
    (item,) = harness.digest(report)["items"]
    assert item["event_type"] == "content_replaced"
    assert "File replaced (server validators changed)" in sender.calls[0][1].text_body
    rerun = harness.run(when=week(2), sender=sender)
    assert not events_of(rerun.events, EventType.CONTENT_REPLACED)  # reported once


def test_drill9_listing_change_triggers_revalidation_in_production(versioned: Harness) -> None:
    harness = versioned
    _replace(harness, NEW_VALIDATORS)
    harness.world.edit_page(
        QUARTERLY,
        QUARTERLY_URL,
        lambda html: html.replace(
            "Current example publication", "Current example publication (rev)"
        ),
    )
    sender = FakeSender()
    report = harness.run(when=week(1), services=production_services(harness, sender))
    assert {e.event_type for e in report.events} == {
        EventType.METADATA_CHANGED,
        EventType.CONTENT_REPLACED,
    }


def test_drill9_new_validators_detected_in_production_without_listing_change(
    versioned: Harness,
) -> None:
    harness = versioned
    _replace(harness, NEW_VALIDATORS)
    sender = FakeSender()
    report = harness.run(when=week(1), services=production_services(harness, sender))
    assert events_of(report.events, EventType.CONTENT_REPLACED)


@pytest.mark.parametrize(
    ("body", "status", "warnings", "labels"),
    [
        (
            make_pdf([None, None]),  # no text layer; OCR binaries unavailable
            "scanned",
            ["scanned", "ocr_unavailable"],
            ["Scanned document; no text layer", "OCR unavailable"],
        ),
        (
            b"%PDF-1.4\n%garbage that no parser can read\n",
            "failed",
            ["extraction_failed"],
            ["Text extraction failed; listing details kept"],
        ),
    ],
    ids=["scanned_without_ocr", "corrupt_pdf"],
)
def test_drill10_unreadable_pdf_keeps_the_record_and_warns(
    tmp_path: Path, body: bytes, status: str, warnings: list[str], labels: list[str]
) -> None:
    harness = make_harness(tmp_path, World.only(QUARTERLY))
    harness.baseline()
    harness.world.set_page(
        QUARTERLY,
        QUARTERLY_URL,
        fixture_page("ministry_of_coal", "direct_attachment_table_new_row.html"),
    )
    harness.world.documents[NEW_DOC_URL] = document_entry(body)
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender, max_documents=1)
    assert report.delivery == "delivered"
    state = harness.state()
    document = state.documents[NEW_DOC_KEY]  # discovery kept with its English listing data
    assert document.title == "Newly added example publication"
    assert document.published_date == "2026-10-22"
    if status == "scanned":
        assert state.versions[NEW_DOC_KEY][-1].extraction_status == "scanned"
    (item,) = harness.digest(report)["items"]
    assert item["entity_key"] == NEW_DOC_KEY and item["event_type"] == "new_document"
    assert item["warnings"] == warnings
    text = sender.calls[0][1].text_body
    for label in labels:
        assert f"Warning: {label}" in text
