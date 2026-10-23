"""Drills 11 and 13: SMTP failures, at-least-once resend, and data-branch races."""

from __future__ import annotations

from pathlib import Path

import pytest
from acceptance_harness import (
    COAL,
    CONFIG,
    RECIPIENTS,
    FakeSender,
    GitRemote,
    Harness,
    World,
    events_of,
    fixture_page,
    git,
    make_git_remote,
    make_harness,
    tree,
    week,
)

from policy_tracker.core.models import DeliveryStatus, EventType
from policy_tracker.operations import cli, pipeline, runner, smtp
from policy_tracker.operations.outbox import Outbox, make_message_id
from policy_tracker.operations.publish import GitPublisher

QUARTERLY = "coal_quarterly_reports"
QUARTERLY_URL = COAL + "major-statistics/quarterly-booklet"
NEW_TITLE = "Newly added example publication"


def stage_new_row(harness: Harness) -> None:
    harness.world.set_page(
        QUARTERLY,
        QUARTERLY_URL,
        fixture_page("ministry_of_coal", "direct_attachment_table_new_row.html"),
    )


@pytest.mark.parametrize("outcome", ["refused", "partial", "timeout"])
def test_drill11_failed_send_stays_pending_and_is_retried_first_with_same_message_id(
    core: Harness, outcome: str
) -> None:
    harness = core
    harness.baseline()
    base_hash = harness.store.state_hash()
    stage_new_row(harness)

    first = harness.run(when=week(1), sender=FakeSender([outcome]))
    assert first.exit_code == pipeline.EXIT_DELIVERY_PENDING and first.delivery == "pending"
    assert not first.promoted and harness.store.state_hash() == base_hash
    assert harness.outbox.pending_ids() == [first.run_id]
    stored = harness.outbox.load(first.run_id).email
    assert NEW_TITLE in stored.text_body
    (attempt,) = harness.outbox.load_attempts(first.run_id)
    assert attempt["ambiguous"] is (outcome == "timeout")
    assert attempt["accepted"] == (1 if outcome == "partial" else 0)
    blob = b"".join(tree(harness.data_dir).values())
    assert not any(address.encode() in blob for address in RECIPIENTS)

    # Still failing: the retry happens, and no new crawl starts.
    still = FakeSender([outcome])
    second = harness.run(when=week(2), sender=still)
    assert second.exit_code == pipeline.EXIT_DELIVERY_PENDING and second.feed_runs == ()
    assert harness.fetchers[-1].requested_urls == []
    ((message_id, email, _),) = still.calls
    assert message_id == make_message_id(first.run_id, "example.org") and email == stored

    # Recovered: the stored bundle goes first, then this week's crawl and heartbeat.
    sender = FakeSender(["ok"])
    third = harness.run(when=week(3), sender=sender)
    assert third.exit_code == 0 and third.delivery == "delivered"
    (retry_id, retry_email, _), (heartbeat_id, heartbeat, _) = sender.calls
    assert retry_id == make_message_id(first.run_id, "example.org") and retry_email == stored
    assert heartbeat_id == make_message_id(third.run_id, "example.org")
    assert "heartbeat" in heartbeat.subject and NEW_TITLE not in heartbeat.text_body
    header = harness.outbox.load_header(first.run_id)
    assert header.status is DeliveryStatus.DELIVERED and header.attempts == 3
    assert harness.store.load_run(first.run_id)["status"] == "promoted"
    assert len(events_of(harness.all_events(), EventType.NEW_DOCUMENT)) == 1


def _git_publisher(remote: GitRemote, data: Path) -> GitPublisher:
    return GitPublisher(repo=remote.main, worktree=data)


def _published(tmp_path: Path) -> tuple[Harness, GitRemote]:
    remote = make_git_remote(tmp_path)
    harness = make_harness(tmp_path / "local", World.core())
    data = tmp_path / "tracker-data"
    _git_publisher(remote, data).init_data_branch(
        lambda root: pipeline.DataStore(root).initialize(), "init"
    )
    harness.data_dir = data
    harness.publisher_factory = lambda: _git_publisher(remote, data)
    harness.baseline()
    return harness, remote


@pytest.mark.parametrize("mode", ["local", "publish"])
def test_drill11_crash_after_smtp_accept_resends_same_message_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """At-least-once: the success marker was never recorded, so the bundle goes out twice."""

    if mode == "local":
        harness = make_harness(tmp_path, World.core())
        harness.baseline()
        real = Outbox.record_attempt
        crashed: list[bool] = []

        def crash_once(self, delivery_id, attempt, *, delivered):
            if delivered and not crashed:
                crashed.append(True)
                raise KeyboardInterrupt("runner killed after SMTP accepted the message")
            return real(self, delivery_id, attempt, delivered=delivered)

        monkeypatch.setattr(Outbox, "record_attempt", crash_once)
        crashing_publisher = None
    else:
        harness, remote = _published(tmp_path)

        class CrashingPublisher(GitPublisher):
            def commit_and_push(self, message: str, expected_remote_head: str) -> str:
                if "delivered" in message:
                    raise KeyboardInterrupt("runner lost before the success marker was pushed")
                return super().commit_and_push(message, expected_remote_head)

        crashing_publisher = CrashingPublisher(repo=remote.main, worktree=harness.data_dir)

    stage_new_row(harness)
    sender = FakeSender()
    services = harness.services(sender, publisher=crashing_publisher)
    with pytest.raises(KeyboardInterrupt):
        harness.run(when=week(1), services=services)
    if mode == "publish":
        harness.publisher_factory = lambda: _git_publisher(remote, harness.data_dir)
    (pending,) = harness.outbox.pending_ids() if mode == "local" else [None]
    report = harness.run(when=week(2), sender=sender)
    assert report.exit_code == 0
    (first_id, first, _), (again_id, again, _), (heartbeat_id, _, _) = sender.calls
    assert first_id == again_id and first == again  # the duplicate carries the same Message-ID
    assert heartbeat_id != first_id
    delivered = [i for i in harness.outbox.ids() if i != report.run_id]
    (run_id,) = delivered
    assert pending in (None, run_id)
    assert first_id == make_message_id(run_id, "example.org")
    header = harness.outbox.load_header(run_id)
    assert header.status is DeliveryStatus.DELIVERED
    assert harness.store.load_run(run_id)["status"] == "promoted"
    assert len(events_of(harness.all_events(), EventType.NEW_DOCUMENT)) == 1


def test_drill13_branch_race_aborts_before_smtp_and_overwrites_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    harness, remote = _published(tmp_path)
    stage_new_row(harness)
    racer: list[str] = []

    class RacedPublisher(GitPublisher):
        def commit_and_push(self, message: str, expected_remote_head: str) -> str:
            if not racer:  # someone else pushes to tracker-data mid-run
                racer.append(remote.race())
            return super().commit_and_push(message, expected_remote_head)

    for key, value in {
        "SMTP_HOST": "smtp.example.org", "SMTP_PORT": "587", "SMTP_SECURITY": "starttls",
        "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p", "SMTP_FROM": "tracker@example.org",
        "SMTP_TO": ",".join(RECIPIENTS), "TRACKER_MAX_DOCUMENTS": "0",
    }.items():  # fmt: skip
        monkeypatch.setenv(key, value)
    sent: list[str] = []

    def fake_send(email, *, message_id, settings, recipients):
        sent.append(message_id)
        return smtp.SendResult(accepted=tuple(recipients), refused={}, ambiguous=False)

    monkeypatch.setattr(smtp, "send", fake_send)
    monkeypatch.setattr(
        cli, "_publisher", lambda args: RacedPublisher(repo=args.repo, worktree=args.data_dir)
    )
    factory = harness.fetcher_factory()
    monkeypatch.setattr(cli, "default_fetcher_factory", factory)
    harness.clock.at(week(1))
    monkeypatch.setattr(runner, "utc_now", harness.clock)
    before = git("rev-parse", "tracker-data", cwd=remote.bare)
    args = ["run", "--config", str(CONFIG), "--data-dir", str(harness.data_dir)]
    code = cli.main([*args, "--repo", str(remote.main), "--publish"])

    assert code == pipeline.EXIT_BRANCH_RACE
    assert "data branch race" in capsys.readouterr().err
    assert sent == []  # aborted before SMTP
    (racer_head,) = racer
    assert remote.remote_head() == racer_head
    assert git("rev-parse", f"{racer_head}~1", cwd=remote.bare) == before  # nothing replaced
    files = git("ls-tree", "-r", "--name-only", racer_head, cwd=remote.bare).splitlines()
    assert "race.txt" in files
    assert [f for f in files if f.startswith("outbox/")] == ["outbox/.gitkeep"]

    # The next run (no racer) starts from the racer's head and delivers normally.
    harness.publisher_factory = lambda: _git_publisher(remote, harness.data_dir)
    sender = FakeSender()
    report = harness.run(when=week(1, days=1), sender=sender)
    assert report.exit_code == 0 and len(sender.calls) == 1
    assert NEW_TITLE in sender.calls[0][1].text_body
    log = git("log", "--format=%s", "tracker-data", cwd=remote.bare).splitlines()
    assert log[2] == "concurrent"  # the other writer's commit is preserved in history
