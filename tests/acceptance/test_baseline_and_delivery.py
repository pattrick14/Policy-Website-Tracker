"""Drills 1-3: silent baseline, one live publication, idempotent rerun (PROJECT_PLAN Step 8).

Also drives the same sequence through the ``policy-tracker`` CLI in publish mode
against a local bare remote, exactly as the workflow does.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from acceptance_harness import (
    COAL,
    CONFIG,
    EXPECTED_CERC_COUNTS,
    RECIPIENTS,
    FakeSender,
    Harness,
    World,
    WorldFetcher,
    document_entry,
    events_of,
    feed_appearances,
    fixture_page,
    git,
    make_git_remote,
    make_harness,
    make_pdf,
    week,
)

from policy_tracker.adapters.registry import default_registry
from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.identity import canonical_url
from policy_tracker.core.models import BaselineStatus, DeliveryStatus, Discovery, EventType
from policy_tracker.operations import cli, runner, smtp
from policy_tracker.operations.outbox import make_message_id

QUARTERLY = "coal_quarterly_reports"
QUARTERLY_URL = COAL + "major-statistics/quarterly-booklet"
NEW_DOC_URL = COAL + "sites/default/files/2026-10/new-example.pdf"
NEW_DOC_KEY = f"ministry_of_coal:document:id:{NEW_DOC_URL}"
NEW_TITLE = "Newly added example publication"
DEADLINE_PDF = make_pdf(
    [
        (
            "Newly added example publication. Comments and suggestions are invited from "
            "stakeholders on or before 30.11.2026."
        )
    ]
)


def add_new_quarterly_row(world: World) -> None:
    world.set_page(
        QUARTERLY,
        QUARTERLY_URL,
        fixture_page("ministry_of_coal", "direct_attachment_table_new_row.html"),
    )
    world.documents[NEW_DOC_URL] = document_entry(DEADLINE_PDF)


def expected_item_counts(harness: Harness, observed_at: str) -> dict[str, tuple[int, int]]:
    """Per replayed feed: (raw adapter rows, distinct records), straight from the adapters.

    Distinct records are counted with the identity rules (native id first, then
    the canonical URL), independently of the engine's own counting.
    """

    contracts = harness.contracts()
    registry = default_registry()
    out: dict[str, tuple[int, int]] = {}
    for feed_id in harness.world.feeds:
        contract = contracts[feed_id]
        adapter = registry.for_feed(feed_id)
        assert adapter is not None
        fetcher = WorldFetcher(harness.world)
        observation = adapter.collect(contract, fetcher, observed_at=observed_at)
        assert observation.evidence.enumeration_complete, (feed_id, observation.evidence)
        assert fetcher.unmatched == [], (feed_id, fetcher.unmatched)

        def canon(url: str, contract: FeedContract = contract) -> str:
            return canonical_url(url, base_url=contract.base_url, rules=contract.host_rules)

        matters = {m.native_id or (canon(m.url) if m.url else repr(m.provenance))
                   for m in observation.matters}  # fmt: skip
        documents = {d.native_id or canon(d.url) for d in observation.documents}
        raw = len(observation.matters) + len(observation.documents)
        out[feed_id] = (raw, len(matters) + len(documents))
    return out


def test_drill1_silent_baseline_records_everything_and_sends_nothing(harness: Harness) -> None:
    sender = FakeSender()
    report = harness.baseline(sender=sender)  # SMTP is configured; baseline must not use it
    assert report.exit_code == 0 and report.delivery is None
    assert sender.calls == []
    assert harness.outbox.ids() == []
    assert not [p for p in (harness.data_dir / "digests").rglob("*") if p.name != ".gitkeep"]
    assert not list((harness.data_dir / "events").rglob("*.json"))  # no events at all
    assert report.events == () and report.live_events == ()
    manifest = harness.store.load_run(report.run_id)
    assert manifest["status"] == "promoted" and manifest["context"]["delivery"] == "none"

    state = harness.state()
    expected = expected_item_counts(harness, observed_at="2026-10-26T03:00:00Z")
    replayed = set(harness.world.feeds)
    assert len(replayed) == 36
    for feed_id, (raw, distinct) in sorted(expected.items()):
        feed = state.feeds[feed_id]
        assert feed.baseline_status is BaselineStatus.COMPLETE, feed_id
        assert feed.last_complete_run_id == report.run_id
        assert feed.last_item_count == distinct, (feed_id, feed.last_item_count, distinct)
        assert len(feed_appearances(state, feed_id)) == distinct, feed_id
        assert raw >= distinct
    # CERC fixture totals agree with the adapter tests' independent expectations.
    for feed_id, documents in EXPECTED_CERC_COUNTS.items():
        assert sum(1 for a in feed_appearances(state, feed_id).values()
                   if a.entity_kind.value == "document") == documents, feed_id  # fmt: skip
    assert all(d.discovery is Discovery.BASELINE for d in state.documents.values())
    assert all(m.discovery is Discovery.BASELINE for m in state.matters.values())

    # Every tracked feed that is not replayed failed and keeps an unstarted baseline.
    failed = set(report.failed_feeds)
    assert failed == set(harness.contracts()) - replayed and len(failed) == 18
    for feed_run in report.feed_runs:
        if feed_run.feed_id in failed:
            assert "fetch_failed" in feed_run.quarantine_reasons
            assert state.feeds[feed_run.feed_id].baseline_status is BaselineStatus.NOT_STARTED
            assert state.feeds[feed_run.feed_id].consecutive_failures == 1
    assert harness.fetchers[-1].unmatched == []


def test_drill2_one_new_document_is_reported_once_and_delivered(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.baseline()
    add_new_quarterly_row(harness.world)
    sender = FakeSender()
    report = harness.run(when=week(1), sender=sender, max_documents=2)

    assert report.exit_code == 0 and report.delivery == "delivered" and report.promoted
    live = report.live_events
    assert [(e.event_type, e.entity_key) for e in live] == [(EventType.NEW_DOCUMENT, NEW_DOC_KEY)]
    digest = harness.digest(report)
    (item,) = digest["items"]
    assert (item["event_type"], item["title"], item["feed_id"]) == (
        "new_document", NEW_TITLE, QUARTERLY,
    )  # fmt: skip
    assert item["published_date"] == "2026-10-22" and item["first_seen_at"].startswith("2026-11")
    assert [d["date"] for d in item["deadlines"]] == ["2026-11-30"]
    assert item["deadlines"][0]["method"] == "text_regex"
    assert digest["partial"] is True  # 18 tracked feeds are unreachable offline
    assert digest["gap_warning"] is None

    # Archive: immutable digests, outbox bundle, promoted catalog and rolling views.
    assert {Path(p).suffix for p in report.artifacts} == {".md", ".json", ".csv"}
    for path in report.artifacts:
        assert (harness.data_dir / path).is_file()
    header = harness.outbox.load_header(report.run_id)
    assert header.status is DeliveryStatus.DELIVERED and header.attempts == 1
    assert set(header.digest_paths) == set(report.artifacts)
    assert harness.store.load_run(report.run_id)["status"] == "promoted"
    state = harness.state()
    assert state.documents[NEW_DOC_KEY].discovery is Discovery.LIVE
    assert state.versions[NEW_DOC_KEY][-1].extraction_status == "ok"
    rows = list(csv.DictReader(io.StringIO((harness.data_dir / "open_deadlines.csv").read_text())))
    assert [(r["title"], r["deadline_date"]) for r in rows] == [(NEW_TITLE, "2026-11-30")]

    # Email: one message, deterministic Message-ID, the item and its deadline.
    ((message_id, email, recipients),) = sender.calls
    assert message_id == make_message_id(report.run_id, "example.org")
    assert recipients == RECIPIENTS
    assert NEW_TITLE in email.text_body and NEW_TITLE in email.html_body
    assert "30 Nov 2026" in email.text_body
    assert "PARTIAL" in email.subject and "heartbeat" not in email.subject
    blob = b"".join(p.read_bytes() for p in harness.data_dir.rglob("*") if p.is_file())
    assert not any(address.encode() in blob for address in RECIPIENTS)


def test_drill3_identical_rerun_is_a_heartbeat_with_no_duplicate_events(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.baseline()
    add_new_quarterly_row(harness.world)
    sender = FakeSender()
    first = harness.run(when=week(1), sender=sender, max_documents=1)

    dry = harness.run(when=week(2), sender=sender, dry_run=True)
    assert dry.events == () and len(sender.calls) == 1
    second = harness.run(when=week(2, days=1), sender=sender)
    assert second.exit_code == 0 and second.delivery == "delivered"
    assert second.events == ()
    assert harness.digest(second)["items"] == []
    assert len(sender.calls) == 2
    heartbeat = sender.calls[1][1]
    assert "no new items (heartbeat)" in heartbeat.subject
    assert "This heartbeat confirms that the tracker ran." in heartbeat.text_body

    third = harness.run(when=week(3), sender=sender)
    assert third.events == ()
    every = harness.all_events()
    assert [e.entity_key for e in every] == [NEW_DOC_KEY]
    assert every[0].run_id == first.run_id
    assert len({(e.run_id, e.event_id) for e in every}) == len(every)


def test_open_deadlines_view_is_stable_across_identical_reruns(tmp_path: Path) -> None:
    harness = make_harness(tmp_path)
    harness.baseline()
    add_new_quarterly_row(harness.world)
    sender = FakeSender()
    harness.run(when=week(1), sender=sender, max_documents=1)
    view = (harness.data_dir / "open_deadlines.csv").read_text()
    harness.run(when=week(2), sender=sender)
    rerun = (harness.data_dir / "open_deadlines.csv").read_text()
    assert ",action_required," in view
    assert rerun == view


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    env = {
        "SMTP_HOST": "smtp.example.org",
        "SMTP_PORT": "465",
        "SMTP_SECURITY": "ssl",
        "SMTP_USERNAME": "tracker",
        "SMTP_PASSWORD": "acceptance-secret",
        "SMTP_FROM": "tracker@example.org",
        "SMTP_TO": ",".join(RECIPIENTS),
        "TRACKER_MAX_DOCUMENTS": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sent: list[tuple[str, object]] = []

    def fake_send(email, *, message_id, settings, recipients):
        assert settings.security == "ssl"
        sent.append((message_id, email))
        return smtp.SendResult(accepted=tuple(recipients), refused={}, ambiguous=False)

    monkeypatch.setattr(smtp, "send", fake_send)
    return sent


def test_cli_publish_mode_baseline_run_and_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_env, capsys: pytest.CaptureFixture
) -> None:
    """The production entry point end to end: real registry, enrichment, digest, git."""

    remote = make_git_remote(tmp_path)
    data = tmp_path / "tracker-data"
    harness = make_harness(tmp_path / "unused")
    harness.data_dir = data
    current = {"factory": harness.fetcher_factory()}  # a fresh simulated web per CLI run
    monkeypatch.setattr(cli, "default_fetcher_factory", lambda c: current["factory"](c))
    monkeypatch.setattr(runner, "utc_now", harness.clock)
    common = ["--data-dir", str(data), "--repo", str(remote.main)]
    args = ["--config", str(CONFIG), *common, "--publish"]

    assert cli.main(["init-data-branch", *common]) == 0
    assert cli.main(["baseline", *args, "--no-email", "--max-documents", "0"]) == 0
    assert cli_env == []

    add_new_quarterly_row(harness.world)
    harness.clock.at(week(1))
    current["factory"] = harness.fetcher_factory()
    assert cli.main(["run", *args]) == 0  # TRACKER_MAX_DOCUMENTS=1: the new document only
    harness.clock.at(week(2))
    current["factory"] = harness.fetcher_factory()
    assert cli.main(["run", *args, "--max-documents", "0"]) == 0

    (first_id, first), (second_id, second) = cli_env
    assert first_id != second_id and first_id.endswith("@example.org>")
    assert NEW_TITLE in first.text_body and "heartbeat" in second.subject  # type: ignore[attr-defined]
    log = git("log", "--format=%s", "tracker-data", cwd=remote.bare).splitlines()
    assert [line.split(": ", 1)[-1] for line in log] == [
        "delivery attempt 1 delivered; candidate promoted",
        "pending delivery staged before SMTP",
        "delivery attempt 1 delivered; candidate promoted",
        "pending delivery staged before SMTP",
        "baseline promoted silently (no email)",
        "Initialize tracker-data layout",
    ]
    store_events = list(harness.store.iter_events())
    assert events_of(store_events, EventType.NEW_DOCUMENT) == store_events
    assert len(store_events) == 1
    out = capsys.readouterr()
    for secret in ("acceptance-secret", *RECIPIENTS):
        assert secret not in out.out and secret not in out.err
