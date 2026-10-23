"""The durable delivery sequence, end to end, with injected fakes and a local git remote."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ops_helpers import FakeSender, GitRemote, git

from policy_tracker.adapters.base import AdapterRegistry, FetchResponse
from policy_tracker.core import config as config_module
from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.models import DeliveryStatus, Discovery, EventType, Provenance
from policy_tracker.core.observations import CollectionEvidence, FeedObservation, ObservedDocument
from policy_tracker.documents.types import EnrichmentResult
from policy_tracker.operations import cli, pipeline, runner
from policy_tracker.operations.outbox import Outbox, RecipientMismatch, make_message_id
from policy_tracker.operations.publish import BranchRace, GitPublisher
from policy_tracker.operations.store import DataStore
from policy_tracker.reporting.deadlines import index_from_data
from policy_tracker.reporting.types import Deadline, Digest, RenderedEmail, RenderedFile

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "sources.yaml"
RECIPIENTS = ("alice@example.org", "bob@example.net")


class FakeAdapter:
    parser_version = 1

    def __init__(self, contract: FeedContract) -> None:
        self.contract = contract
        self.site_id = contract.site_id
        self.paths = ["docs/a.pdf"]
        self.calls = 0

    def feed_ids(self) -> frozenset[str]:
        return frozenset({self.contract.feed_id})

    def collect(self, contract: FeedContract, fetcher, *, observed_at: str) -> FeedObservation:
        self.calls += 1
        page = contract.url or contract.base_url
        return FeedObservation(
            feed_id=contract.feed_id,
            site_id=contract.site_id,
            observed_at=observed_at,
            parser_version=contract.parser_version,
            evidence=CollectionEvidence(
                fetch_ok=True, parse_valid=True, enumeration_complete=True, pages_fetched=1
            ),
            documents=tuple(
                ObservedDocument(
                    url=contract.base_url + path,
                    title=f"Document {path}",
                    provenance=Provenance(page, f"row[{i}]"),
                    published_raw="01.10.2026",
                    published_date="2026-10-01",
                )
                for i, path in enumerate(self.paths)
            ),
        )


class OfflineFetcher:
    def get(self, url: str, *, headers=None) -> FetchResponse:
        return FetchResponse(url=url, status_code=0, headers={}, body=b"", error="offline")


def fake_enrich(state, events, contracts, fetcher_for, *, run, **options) -> EnrichmentResult:
    return EnrichmentResult(state=state, events=(), texts={}, diagnostics=("checked",))


class FakeDigestBuilder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, candidate, previous, texts, *, generated_at, previous_run_at,
                 previous_deadlines=None):  # fmt: skip
        self.calls.append({"previous_run_at": previous_run_at, "previous": previous_deadlines})
        digest = Digest(
            run_id=candidate.run_id,
            generated_at=generated_at,
            period_label="Week of 26 Oct 2026",
            partial=True,
            items=(),
            health=(),
            open_deadlines=(),
            notes=tuple(e.event_id for e in candidate.events if e.discovery is Discovery.LIVE),
        )
        deadline = Deadline(
            raw="by 30.11.2026",
            date="2026-11-30",
            time=None,
            sort_key="2026-11-30T23:59:00+05:30",
            evidence=f"comments by 30.11.2026 ({candidate.run_id})",
            method="title_regex",
        )
        return digest, {f"doc:{candidate.run_id}": (deadline,)}


def _md(digest: Digest) -> RenderedFile:
    path = f"digests/{digest.run_id[:4]}/{digest.run_id[4:6]}/{digest.run_id}.md"
    return RenderedFile(
        path, f"# {digest.period_label}\n{len(digest.notes)} live\n".encode(), "text/markdown"
    )


def _open_deadlines(digest: Digest) -> RenderedFile:
    return RenderedFile("open_deadlines.csv", f"run,{digest.run_id}\n".encode(), "text/csv")


def _email(digest: Digest) -> RenderedEmail:
    csv = RenderedFile(f"digests/x/{digest.run_id}.csv", b"title\n", "text/csv")
    return RenderedEmail(f"Digest {digest.run_id}", "text", "<p>html</p>", (csv,), 99)


RENDERER = pipeline.Renderer(files=(_md,), rolling=(_open_deadlines,), email=_email)


@pytest.fixture(scope="module")
def contracts() -> dict[str, FeedContract]:
    return config_module.load_contracts(config_module.load_config(CONFIG))


@pytest.fixture
def adapter(contracts: dict[str, FeedContract]) -> FakeAdapter:
    return FakeAdapter(contracts[min(contracts)])


@pytest.fixture
def clock():
    start = datetime(2026, 10, 26, 2, 30, tzinfo=UTC)
    ticks = itertools.count()
    return lambda: (start + timedelta(minutes=next(ticks))).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "tracker-data"
    DataStore(path).initialize()
    return path


def _services(sender=None, *, recipients=RECIPIENTS, publisher=None, builder=None):
    return pipeline.Services(
        enricher=fake_enrich,
        digest_builder=builder or FakeDigestBuilder(),
        renderer=RENDERER,
        sender=sender,
        recipients=tuple(recipients),
        publisher=publisher,
        message_id_domain="example.org",
        fetcher_state=lambda site: {"circuit_open": False, "consecutive_failed_urls": 0},
    )


def _run(data_dir, adapter, services, clock, *, mode="run", dry_run=False, no_email=False):
    registry = AdapterRegistry()
    if adapter is not None:
        registry.register(adapter)
    return pipeline.run_pipeline(
        CONFIG,
        data_dir,
        registry=registry,
        fetcher_factory=lambda contract: OfflineFetcher(),
        mode=mode,
        dry_run=dry_run,
        code_revision="rev",
        now=clock,
        no_email=no_email,
        services=services,
    )


def _baseline(data_dir, adapter, clock, publisher=None):
    report = _run(data_dir, adapter, pipeline.Services(publisher=publisher), clock, mode="baseline")
    assert report.promoted
    return report


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


# -- local data directory -------------------------------------------------------------


def test_baseline_has_no_outbox_and_sends_nothing(data_dir, adapter, clock) -> None:
    sender = FakeSender()
    report = _run(data_dir, adapter, _services(sender), clock, mode="baseline")
    assert report.promoted and report.delivery is None and sender.calls == []
    assert Outbox(data_dir).ids() == []
    assert not list((data_dir / "digests").rglob("*.md"))
    manifest = DataStore(data_dir).load_run(report.run_id)
    assert manifest["context"]["delivery"] == "none"
    assert manifest["context"]["fetchers"]


def test_delivery_pushes_bundle_before_send_and_promotes_after(data_dir, adapter, clock) -> None:
    store = DataStore(data_dir)
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    seen: dict = {}

    class CheckingSender(FakeSender):
        def __call__(self, email, *, message_id, recipients):
            seen["documents"] = len(store.load_state().documents)  # not yet promoted
            seen["pending"] = Outbox(data_dir).pending_ids()
            return super().__call__(email, message_id=message_id, recipients=recipients)

    sender = CheckingSender()
    report = _run(data_dir, adapter, _services(sender), clock)
    assert report.exit_code == 0 and report.delivery == "delivered" and report.promoted
    assert seen == {"documents": 1, "pending": [report.run_id]}
    (message_id, email, recipients) = sender.calls[0]
    assert message_id == make_message_id(report.run_id, "example.org")
    assert recipients == RECIPIENTS and email.subject == f"Digest {report.run_id}"
    assert len(store.load_state().documents) == 2
    header = Outbox(data_dir).load_header(report.run_id)
    assert header.status is DeliveryStatus.DELIVERED and header.attempts == 1
    assert header.recipient_count == 2 and header.base_data_commit == "local"
    assert set(header.digest_paths) == set(report.artifacts)
    for path in header.digest_paths:
        assert (data_dir / path).is_file()
    assert list(index_from_data(store.load_deadline_index())) == [f"doc:{report.run_id}"]
    assert (data_dir / "catalog" / "deadlines.json").is_file()
    assert (data_dir / "open_deadlines.csv").read_text() == f"run,{report.run_id}\n"
    assert store.load_run(report.run_id)["status"] == "promoted"
    assert not store.candidate_dir(report.run_id).exists()


@pytest.mark.parametrize("outcome", ["partial", "refused", "timeout"])
def test_failed_delivery_stays_pending(data_dir, adapter, clock, outcome: str) -> None:
    store = DataStore(data_dir)
    _baseline(data_dir, adapter, clock)
    base = store.state_hash()
    adapter.paths.append("docs/b.pdf")
    report = _run(data_dir, adapter, _services(FakeSender([outcome])), clock)
    assert report.exit_code == pipeline.EXIT_DELIVERY_PENDING and report.delivery == "pending"
    assert not report.promoted and store.state_hash() == base
    header = Outbox(data_dir).load_header(report.run_id)
    assert header.status is DeliveryStatus.PENDING and header.attempts == 1
    assert store.load_run(report.run_id)["status"] == "candidate"
    # A failed send never advances the deadline index or the open-deadlines view.
    assert store.load_deadline_index() is None and not (data_dir / "open_deadlines.csv").exists()
    (attempt,) = Outbox(data_dir).load_attempts(report.run_id)
    assert attempt["ambiguous"] is (outcome == "timeout")
    blob = b"".join(_tree(data_dir).values())
    assert b"alice" not in blob and b"example.net" not in blob


def test_retry_before_crawl_uses_same_bundle(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    first = _run(data_dir, adapter, _services(FakeSender(["partial"])), clock)
    calls = adapter.calls

    sender = FakeSender(["timeout"])
    second = _run(data_dir, adapter, _services(sender), clock)
    assert second.exit_code == pipeline.EXIT_DELIVERY_PENDING
    assert adapter.calls == calls  # no new crawl while pending
    assert second.run_id == first.run_id and second.feed_runs == ()
    assert sender.calls[0][0] == make_message_id(first.run_id, "example.org")
    assert Outbox(data_dir).load_header(first.run_id).attempts == 2

    sender = FakeSender(["ok"])
    third = _run(data_dir, adapter, _services(sender), clock)
    assert third.exit_code == 0 and third.run_id != first.run_id
    assert adapter.calls == calls + 1  # crawl resumed after the retry succeeded
    retry, heartbeat = sender.calls
    assert retry[0] == make_message_id(first.run_id, "example.org")
    assert retry[1].subject == f"Digest {first.run_id}"  # the stored bundle, not a re-render
    assert heartbeat[0] == make_message_id(third.run_id, "example.org")
    store = DataStore(data_dir)
    assert store.load_run(first.run_id)["status"] == "promoted"
    assert len(store.load_state().documents) == 2
    assert Outbox(data_dir).load_header(first.run_id).attempts == 3
    assert not [e for e in third.events if e.discovery is Discovery.LIVE]  # nothing re-reported


def test_post_send_crash_resends_same_bundle_locally(
    data_dir, adapter, clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    real = Outbox.record_attempt
    crashed: list[bool] = []

    def crash_once(self, delivery_id, attempt, *, delivered):
        if delivered and not crashed:
            crashed.append(True)
            raise KeyboardInterrupt("runner killed after SMTP accepted")
        return real(self, delivery_id, attempt, delivered=delivered)

    monkeypatch.setattr(Outbox, "record_attempt", crash_once)
    sender = FakeSender()
    with pytest.raises(KeyboardInterrupt):
        _run(data_dir, adapter, _services(sender), clock)
    (run_id,) = Outbox(data_dir).pending_ids()
    report = _run(data_dir, adapter, _services(sender), clock)
    assert report.exit_code == 0
    first, second, _heartbeat = sender.calls
    assert first[0] == second[0] == make_message_id(run_id, "example.org")  # duplicate, same id
    assert first[1] == second[1]
    assert Outbox(data_dir).load_header(run_id).status is DeliveryStatus.DELIVERED


def test_recipient_set_change_is_refused(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    _run(data_dir, adapter, _services(FakeSender(["partial"])), clock)
    sender = FakeSender()
    services = _services(sender, recipients=("alice@example.org", "mallory@example.com"))
    with pytest.raises(RecipientMismatch):
        _run(data_dir, adapter, services, clock)
    assert sender.calls == []


def test_dry_run_mutates_nothing(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    before = _tree(data_dir)
    sender = FakeSender()
    report = _run(data_dir, adapter, _services(sender), clock, dry_run=True)
    assert _tree(data_dir) == before and sender.calls == [] and not report.promoted
    rendered = Path(report.dry_run_dir)
    assert (rendered / "email" / "subject.txt").read_text().startswith("Digest")
    assert (rendered / "open_deadlines.csv").is_file()
    assert [e for e in report.events if e.event_type is EventType.NEW_DOCUMENT]


def test_dry_run_with_pending_delivery_stops(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    _run(data_dir, adapter, _services(FakeSender(["partial"])), clock)
    before, calls = _tree(data_dir), adapter.calls
    report = _run(data_dir, adapter, _services(FakeSender()), clock, dry_run=True)
    assert report.delivery == "pending" and adapter.calls == calls and _tree(data_dir) == before


def test_missing_sender_blocks_before_crawl(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    calls, before = adapter.calls, _tree(data_dir)
    with pytest.raises(pipeline.DeliveryBlocked):
        _run(data_dir, adapter, _services(None), clock)
    with pytest.raises(pipeline.DeliveryBlocked):
        _run(data_dir, adapter, _services(FakeSender(), recipients=()), clock)
    assert adapter.calls == calls and _tree(data_dir) == before


def test_no_email_run_archives_and_promotes(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    report = _run(data_dir, adapter, _services(None), clock, no_email=True)
    assert report.promoted and report.delivery == "suppressed" and Outbox(data_dir).ids() == []
    assert all((data_dir / path).is_file() for path in report.artifacts)
    assert len(DataStore(data_dir).load_state().documents) == 2


def test_pending_delivery_blocks_silent_commands(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    _run(data_dir, adapter, _services(FakeSender(["refused"])), clock)
    with pytest.raises(pipeline.DeliveryPending):
        _run(data_dir, adapter, _services(None), clock, no_email=True)
    with pytest.raises(pipeline.DeliveryPending):
        _run(data_dir, adapter, pipeline.Services(), clock, mode="baseline")
    with pytest.raises(pipeline.DeliveryPending):
        runner.rebaseline(
            CONFIG, data_dir, adapter.contract.feed_id, reason="x", code_revision="r", now=clock
        )


def test_abandon_discards_candidate_and_next_run_redetects(data_dir, adapter, clock) -> None:
    store = DataStore(data_dir)
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    stuck = _run(data_dir, adapter, _services(FakeSender(["refused"])), clock)
    pipeline.abandon_delivery(data_dir, stuck.run_id, reason="provider outage", now=clock)
    assert Outbox(data_dir).pending_ids() == []
    manifest = store.load_run(stuck.run_id)
    assert (
        manifest["status"] == "not_promoted"
        and "provider outage" in manifest["discarded"]["reason"]
    )
    sender = FakeSender()
    report = _run(data_dir, adapter, _services(sender), clock)
    assert report.exit_code == 0 and len(sender.calls) == 1
    new_docs = [e for e in report.events if e.event_type is EventType.NEW_DOCUMENT]
    assert len(new_docs) == 1 and new_docs[0].discovery is Discovery.LIVE


def test_all_feeds_failed_sends_nothing(data_dir, clock) -> None:
    sender = FakeSender()
    report = _run(data_dir, None, _services(sender), clock)
    assert report.exit_code == runner.EXIT_ALL_FEEDS_FAILED and sender.calls == []
    assert Outbox(data_dir).ids() == []


def test_report_and_logs_never_contain_recipients(
    data_dir, adapter, clock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    _baseline(data_dir, adapter, clock)
    adapter.paths.append("docs/b.pdf")
    reports = [
        _run(data_dir, adapter, _services(FakeSender([outcome])), clock)
        for outcome in ("partial", "ok")
    ]
    text = caplog.text + json.dumps([list(r.messages) for r in reports])
    for address in RECIPIENTS:
        assert address not in text
    assert "accepted 1, refused 1" in text


# -- git-backed tracker-data -----------------------------------------------------------


def _git_publisher(remote: GitRemote) -> GitPublisher:
    return GitPublisher(repo=remote.main, worktree=remote.tmp / "data")


def _init_branch(remote: GitRemote) -> GitPublisher:
    publisher = _git_publisher(remote)
    publisher.init_data_branch(lambda root: DataStore(root).initialize(), "init")
    return publisher


def _log(remote: GitRemote) -> list[str]:
    return git("log", "--format=%s", "tracker-data", cwd=remote.bare).splitlines()


def test_git_delivery_commits_pending_then_delivered(git_remote, adapter, clock) -> None:
    publisher = _init_branch(git_remote)
    data = publisher.worktree
    _baseline(data, adapter, clock, publisher=_git_publisher(git_remote))
    adapter.paths.append("docs/b.pdf")
    pushed_at_send: list[str | None] = []

    class CheckingSender(FakeSender):
        def __call__(self, email, *, message_id, recipients):
            pushed_at_send.append(git_remote.remote_head())
            return super().__call__(email, message_id=message_id, recipients=recipients)

    report = _run(
        data, adapter, _services(CheckingSender(), publisher=_git_publisher(git_remote)), clock
    )
    assert report.exit_code == 0
    log = _log(git_remote)
    assert "pending delivery staged before SMTP" in log[1]
    assert "delivered; candidate promoted" in log[0]
    assert "baseline promoted silently" in log[2]
    assert pushed_at_send[0] == git("rev-parse", "tracker-data~1", cwd=git_remote.bare)
    assert report.data_commit == git_remote.remote_head()
    header = Outbox(data).load_header(report.run_id)
    assert header.base_data_commit == git("rev-parse", "tracker-data~2", cwd=git_remote.bare)


def test_git_post_send_crash_resends_same_bundle(git_remote, adapter, clock) -> None:
    _init_branch(git_remote)
    data = git_remote.tmp / "data"
    _baseline(data, adapter, clock, publisher=_git_publisher(git_remote))
    adapter.paths.append("docs/b.pdf")

    class CrashingPublisher(GitPublisher):
        def commit_and_push(self, message, expected_remote_head):
            if "delivered" in message:
                raise KeyboardInterrupt("runner lost before the success marker was pushed")
            return super().commit_and_push(message, expected_remote_head)

    sender = FakeSender()
    crashing = CrashingPublisher(repo=git_remote.main, worktree=data)
    with pytest.raises(KeyboardInterrupt):
        _run(data, adapter, _services(sender, publisher=crashing), clock)
    assert "pending delivery staged" in _log(git_remote)[0]

    report = _run(data, adapter, _services(sender, publisher=_git_publisher(git_remote)), clock)
    assert report.exit_code == 0
    first, retry, _heartbeat = sender.calls
    assert first[0] == retry[0] and first[1] == retry[1]  # at-least-once: same Message-ID
    run_id = Outbox(data).ids()[0]
    assert Outbox(data).load_header(run_id).status is DeliveryStatus.DELIVERED
    assert Outbox(data).load_header(run_id).attempts == 1  # the crashed attempt was never recorded
    assert DataStore(data).load_run(run_id)["status"] == "promoted"


def test_git_branch_race_aborts_before_smtp(git_remote, adapter, clock) -> None:
    _init_branch(git_remote)
    data = git_remote.tmp / "data"
    _baseline(data, adapter, clock, publisher=_git_publisher(git_remote))
    adapter.paths.append("docs/b.pdf")
    racer: list[str] = []

    class RacedPublisher(GitPublisher):
        def commit_and_push(self, message, expected_remote_head):
            if not racer:
                racer.append(git_remote.race())
            return super().commit_and_push(message, expected_remote_head)

    sender = FakeSender()
    publisher = RacedPublisher(repo=git_remote.main, worktree=data)
    with pytest.raises(BranchRace):
        _run(data, adapter, _services(sender, publisher=publisher), clock)
    assert sender.calls == []
    assert git_remote.remote_head() == racer[0]
    tree = git("ls-tree", "-r", "--name-only", racer[0], cwd=git_remote.bare).splitlines()
    assert [path for path in tree if path.startswith("outbox/")] == ["outbox/.gitkeep"]


def test_git_dry_run_pushes_nothing(git_remote, adapter, clock) -> None:
    _init_branch(git_remote)
    data = git_remote.tmp / "data"
    _baseline(data, adapter, clock, publisher=_git_publisher(git_remote))
    adapter.paths.append("docs/b.pdf")
    head = git_remote.remote_head()
    before = _tree(data)
    sender = FakeSender()
    report = _run(data, adapter, _services(sender, publisher=_git_publisher(git_remote)), clock,
                  dry_run=True)  # fmt: skip
    assert git_remote.remote_head() == head and _tree(data) == before and sender.calls == []
    assert report.dry_run_dir


def test_cli_init_branch_and_published_run(
    git_remote, adapter, clock, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    data = git_remote.tmp / "data"
    common = ["--data-dir", str(data), "--repo", str(git_remote.main)]
    assert cli.main(["init-data-branch", *common]) == 0
    assert cli.main(["init-data-branch", *common]) == 2  # refuses an existing branch
    registry = AdapterRegistry()
    registry.register(adapter)
    sender = FakeSender()
    monkeypatch.setattr(cli, "default_registry", lambda: registry)
    monkeypatch.setattr(cli, "default_fetcher_factory", lambda contract: OfflineFetcher())
    monkeypatch.setattr(runner, "utc_now", clock)

    def services(args, *, deliver):
        return _services(sender if deliver else None, publisher=cli._publisher(args))

    monkeypatch.setattr(cli, "default_services", services)
    args = ["--config", str(CONFIG), *common, "--publish"]
    assert cli.main(["baseline", *args, "--no-email"]) == 0
    adapter.paths.append("docs/b.pdf")
    assert cli.main(["run", *args]) == 0
    assert len(sender.calls) == 1
    assert cli.main(["outbox-status", *common]) == 0
    out = capsys.readouterr().out
    assert "delivery: delivered" in out and "no pending deliveries" in out
    for address in RECIPIENTS:
        assert address not in out
    assert "delivered; candidate promoted" in _log(git_remote)[0]


def test_deadline_index_and_previous_run_feed_the_next_digest(data_dir, adapter, clock) -> None:
    _baseline(data_dir, adapter, clock)
    builder = FakeDigestBuilder()
    first = _run(data_dir, adapter, _services(FakeSender(), builder=builder), clock)
    second = _run(data_dir, adapter, _services(FakeSender(), builder=builder), clock)
    assert second.exit_code == 0
    assert builder.calls[0]["previous"] is None
    assert list(builder.calls[1]["previous"]) == [f"doc:{first.run_id}"]
    manifest = DataStore(data_dir).load_run(first.run_id)
    assert builder.calls[1]["previous_run_at"] == manifest["context"]["generated_at"]


def test_cli_with_real_reporting_modules(
    data_dir, adapter, clock, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Default services: real enrich (offline fetcher), digest, renderers; fake SMTP send."""

    from policy_tracker.operations import smtp

    registry = AdapterRegistry()
    registry.register(adapter)
    monkeypatch.setattr(cli, "default_registry", lambda: registry)
    monkeypatch.setattr(cli, "default_fetcher_factory", lambda contract: OfflineFetcher())
    monkeypatch.setattr(runner, "utc_now", clock)
    env = {
        "SMTP_HOST": "smtp.example.org",
        "SMTP_PORT": "465",
        "SMTP_SECURITY": "ssl",
        "SMTP_USERNAME": "user",
        "SMTP_PASSWORD": "pw-secret",
        "SMTP_FROM": "tracker@example.org",
        "SMTP_TO": ",".join(RECIPIENTS),
        "TRACKER_ARTIFACT_BASE_URL": "https://example.org/archive",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sent: list = []

    def fake_send(email, *, message_id, settings, recipients):
        sent.append((message_id, email, settings.security))
        return smtp.SendResult(accepted=tuple(recipients), refused={}, ambiguous=False)

    monkeypatch.setattr(smtp, "send", fake_send)
    args = ["--config", str(CONFIG), "--data-dir", str(data_dir)]
    assert cli.main(["baseline", *args, "--no-email"]) == 0
    adapter.paths.append("docs/b.pdf")
    assert cli.main(["run", *args]) == 0
    (message_id, email, security) = sent[0]
    assert security == "ssl" and message_id.endswith("@example.org>")
    assert "Document docs/b.pdf" in email.text_body
    store = DataStore(data_dir)
    run_id = store.run_ids()[-1]
    header = Outbox(data_dir).load_header(run_id)
    assert header.status is DeliveryStatus.DELIVERED
    assert all((data_dir / path).is_file() for path in header.digest_paths)
    assert (data_dir / "open_deadlines.csv").is_file()
    assert store.load_deadline_index()["kind"] == "deadline_index"
    out = capsys.readouterr()
    for secret in ("pw-secret", *RECIPIENTS):
        assert secret not in out.out and secret not in out.err
