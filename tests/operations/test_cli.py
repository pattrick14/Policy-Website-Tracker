"""End-to-end CLI tests with a fake in-test adapter on the real feed configuration."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from policy_tracker.adapters.base import AdapterRegistry, FetchResponse
from policy_tracker.core import config as config_module
from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.models import BaselineStatus, Discovery, EventType
from policy_tracker.core.observations import (
    CollectionEvidence,
    FeedObservation,
    ObservedDocument,
)
from policy_tracker.core.serialize import state_hash
from policy_tracker.operations import cli, pipeline, runner
from policy_tracker.operations.store import DataStore

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "sources.yaml"
REAL_DEFAULT_SERVICES = cli.default_services


class FakeAdapter:
    """Returns a complete synthetic enumeration of ``self.paths`` for one feed."""

    parser_version = 1

    def __init__(self, contract: FeedContract) -> None:
        self.contract = contract
        self.site_id = contract.site_id
        self.paths: list[str] = ["docs/a.pdf"]
        self.calls = 0

    def feed_ids(self) -> frozenset[str]:
        return frozenset({self.contract.feed_id})

    def collect(self, contract: FeedContract, fetcher, *, observed_at: str) -> FeedObservation:
        self.calls += 1
        page = contract.url or contract.base_url
        documents = tuple(
            ObservedDocument(
                url=contract.base_url + path,
                title=f"Document {path}",
                provenance=_provenance(page, index),
                published_raw="01.10.2026",
                published_date="2026-10-01",
            )
            for index, path in enumerate(self.paths)
        )
        return FeedObservation(
            feed_id=contract.feed_id,
            site_id=contract.site_id,
            observed_at=observed_at,
            parser_version=contract.parser_version,
            evidence=CollectionEvidence(
                fetch_ok=True, parse_valid=True, enumeration_complete=True, pages_fetched=1
            ),
            documents=documents,
        )


class OfflineFetcher:
    def get(self, url: str, *, headers=None) -> FetchResponse:
        return FetchResponse(url=url, status_code=0, headers={}, body=b"", error="offline")


def _provenance(page: str, index: int):
    from policy_tracker.core.models import Provenance

    return Provenance(page_url=page, locator=f"row[{index}]")


@pytest.fixture(scope="module")
def contracts() -> dict[str, FeedContract]:
    return config_module.load_contracts(config_module.load_config(CONFIG))


@pytest.fixture
def adapter(contracts: dict[str, FeedContract], monkeypatch: pytest.MonkeyPatch) -> FakeAdapter:
    contract = contracts[min(contracts)]
    fake = FakeAdapter(contract)
    registry = AdapterRegistry()
    registry.register(fake)
    monkeypatch.setattr(cli, "default_registry", lambda: registry)
    return fake


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 10, 26, 2, 30, tzinfo=UTC)
    ticks = itertools.count()

    def now() -> str:
        return (start + timedelta(minutes=next(ticks))).strftime("%Y-%m-%dT%H:%M:%SZ")

    monkeypatch.setattr(runner, "utc_now", now)
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef")


@pytest.fixture(autouse=True)
def offline_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """No enrichment, digest, SMTP or git: these tests cover state handling only."""

    monkeypatch.setattr(cli, "default_services", lambda args, *, deliver: pipeline.Services())
    # Independent of the adapters registered in production, and never on the network.
    monkeypatch.setattr(cli, "default_registry", AdapterRegistry)
    monkeypatch.setattr(cli, "default_fetcher_factory", lambda contract: OfflineFetcher())
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_FROM", "SMTP_TO"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "tracker-data"
    assert cli.main(["init-data", "--data-dir", str(path)]) == 0
    return path


def _cli(*args: str) -> int:
    return cli.main([*args])


def _run(data_dir: Path, *extra: str) -> int:
    if "--dry-run" not in extra:
        extra = ("--no-email", *extra)
    return _cli("run", "--config", str(CONFIG), "--data-dir", str(data_dir), *extra)


def _baseline(data_dir: Path) -> int:
    return _cli("baseline", "--config", str(CONFIG), "--data-dir", str(data_dir), "--no-email")


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _last_run_events(store: DataStore):
    return store.load_events(store.run_ids()[-1])


def test_validate_config_accepts_repository_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli("validate-config", "--config", str(CONFIG)) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_config_rejects_broken_config(tmp_path: Path) -> None:
    broken = tmp_path / "sources.yaml"
    broken.write_text("schema_version: 1\nsites: nope\n")
    assert _cli("validate-config", "--config", str(broken)) == 1


def test_init_data_refuses_non_empty(data_dir: Path) -> None:
    assert _cli("init-data", "--data-dir", str(data_dir)) == 2


def test_run_requires_initialized_data(tmp_path: Path, adapter: FakeAdapter) -> None:
    assert _run(tmp_path / "missing") == 2


def test_baseline_silent_then_run_emits_new_document(data_dir: Path, adapter: FakeAdapter) -> None:
    store = DataStore(data_dir)
    assert _baseline(data_dir) == 0
    baseline_events = _last_run_events(store)
    assert all(event.discovery is not Discovery.LIVE for event in baseline_events)
    state = store.load_state()
    feed = state.feeds[adapter.contract.feed_id]
    assert feed.baseline_status is BaselineStatus.COMPLETE
    assert len(state.documents) == 1
    # Every other tracked feed has no adapter: recorded as failed, state untouched.
    manifest = store.load_run(store.run_ids()[-1])
    assert manifest["status"] == "promoted"
    assert manifest["code_revision"] == "0123456789abcdef"
    failed = [run for run in manifest["feed_runs"] if not run["accepted"]]
    assert failed and all("no_adapter" in run["diagnostics"] for run in failed)

    adapter.paths.append("docs/b.pdf")
    assert _run(data_dir) == 0
    live = [event for event in _last_run_events(store) if event.discovery is Discovery.LIVE]
    new_docs = [event for event in live if event.event_type is EventType.NEW_DOCUMENT]
    assert len(new_docs) == 1
    assert new_docs[0].entity_key.endswith("docs/b.pdf")
    assert len(store.load_state().documents) == 2

    # Rerunning the same snapshot creates no duplicate semantic events.
    events_before = list(store.iter_events())
    documents_before = store.load_state().documents
    assert _run(data_dir) == 0
    assert _last_run_events(store) == ()
    assert list(store.iter_events()) == events_before
    assert store.load_state().documents.keys() == documents_before.keys()
    assert len(store.run_ids()) == 3


def test_dry_run_writes_nothing(data_dir: Path, adapter: FakeAdapter) -> None:
    assert _baseline(data_dir) == 0
    adapter.paths.append("docs/b.pdf")
    before = _tree(data_dir)
    assert _run(data_dir, "--dry-run") == 0
    assert adapter.calls == 2
    assert _tree(data_dir) == before


def test_all_feeds_failed_is_recorded_not_promoted(data_dir: Path) -> None:
    store = DataStore(data_dir)
    base = store.state_hash()
    catalog = _tree(data_dir / "catalog")
    assert _run(data_dir) == runner.EXIT_ALL_FEEDS_FAILED  # empty default registry
    assert store.state_hash() == base
    assert _tree(data_dir / "catalog") == catalog
    (run_id,) = store.run_ids()
    manifest = store.load_run(run_id)
    assert manifest["status"] == "not_promoted"
    assert all(not run["accepted"] for run in manifest["feed_runs"])
    health = json.loads((data_dir / "health" / "feeds.json").read_text())
    assert all(entry["accepted"] is False for entry in health["feeds"].values())


def test_rebaseline_feed_makes_next_run_silent(data_dir: Path, adapter: FakeAdapter) -> None:
    store = DataStore(data_dir)
    feed_id = adapter.contract.feed_id
    assert _baseline(data_dir) == 0
    args = ["--config", str(CONFIG), "--data-dir", str(data_dir)]
    assert _cli("rebaseline-feed", feed_id, "--reason", "site redesign", *args) == 0
    manifest = store.load_run(store.run_ids()[-1])
    assert manifest["context"]["reason"] == "site redesign"
    assert manifest["context"]["mode"] == "rebaseline-feed"
    assert store.load_state().feeds[feed_id].baseline_status is BaselineStatus.NOT_STARTED
    (record,) = manifest["rebaselines"]
    assert record["feed_id"] == feed_id and record["reason"] == "site redesign"
    health = json.loads((data_dir / "health" / "feeds.json").read_text())
    assert health["feeds"][feed_id]["last_rebaseline"]["reason"] == "site redesign"

    adapter.paths.append("docs/c.pdf")
    assert _run(data_dir) == 0
    assert not [e for e in _last_run_events(store) if e.discovery is Discovery.LIVE]
    assert store.load_state().feeds[feed_id].baseline_status is BaselineStatus.COMPLETE
    health = json.loads((data_dir / "health" / "feeds.json").read_text())
    assert health["feeds"][feed_id]["last_rebaseline"]["reason"] == "site redesign"


def test_rebaseline_unknown_feed_fails(data_dir: Path) -> None:
    args = ["--config", str(CONFIG), "--data-dir", str(data_dir)]
    assert _cli("rebaseline-feed", "no_such_feed", "--reason", "x", *args) == 2


def test_run_id_is_deterministic() -> None:
    a = runner.make_run_id("2026-10-26T02:30:00Z", "abc")
    assert a == runner.make_run_id("2026-10-26T02:30:00Z", "abc")
    assert a.startswith("20261026T023000Z-") and len(a) == len("20261026T023000Z-") + 8
    assert a != runner.make_run_id("2026-10-26T02:30:00Z", "abd")


def test_runner_state_hash_reports_candidate(data_dir: Path, adapter: FakeAdapter) -> None:
    report = runner.run_feeds(
        CONFIG,
        data_dir,
        registry=cli.default_registry(),
        fetcher_factory=cli.default_fetcher_factory,
        mode="baseline",
        dry_run=False,
        code_revision="rev",
        now=runner.utc_now,
    )
    assert report.promoted and report.exit_code == 0 and report.partial
    assert report.state_hash == state_hash(DataStore(data_dir).load_state())
    assert report.accepted_feeds == (adapter.contract.feed_id,)


def _two_feeds_on_one_site(contracts: dict[str, FeedContract]) -> tuple[FeedContract, FeedContract]:
    by_site: dict[str, list[str]] = {}
    for feed_id in sorted(contracts):
        by_site.setdefault(contracts[feed_id].site_id, []).append(feed_id)
    first, second = next(ids for ids in by_site.values() if len(ids) >= 2)[:2]
    return contracts[first], contracts[second]


def _report(data_dir: Path, registry: AdapterRegistry, mode: str) -> runner.RunReport:
    return runner.run_feeds(
        CONFIG,
        data_dir,
        registry=registry,
        fetcher_factory=cli.default_fetcher_factory,
        mode=mode,
        dry_run=False,
        code_revision="rev",
        now=runner.utc_now,
        no_email=True,
    )


def test_live_feeds_apply_before_baselining_feeds(
    data_dir: Path, contracts: dict[str, FeedContract]
) -> None:
    """A record first seen in one run by a live and a baselining feed is a LIVE NEW_DOCUMENT."""

    early, late = _two_feeds_on_one_site(contracts)  # ``early`` sorts first by feed id
    live_adapter = FakeAdapter(late)
    registry = AdapterRegistry()
    registry.register(live_adapter)
    assert _report(data_dir, registry, "baseline").promoted  # only ``late`` is baselined

    baselining_adapter = FakeAdapter(early)  # new feed: silently baselines this run
    registry.register(baselining_adapter)
    live_adapter.paths.append("docs/shared.pdf")
    baselining_adapter.paths = ["docs/shared.pdf"]
    report = _report(data_dir, registry, "run")
    assert [run.feed_id for run in report.feed_runs][:2] == [late.feed_id, early.feed_id]
    shared = [e for e in report.events if e.entity_key.endswith("docs/shared.pdf")]
    new_docs = [e for e in shared if e.event_type is EventType.NEW_DOCUMENT]
    assert len(new_docs) == 1
    assert new_docs[0].discovery is Discovery.LIVE and new_docs[0].feed_id == late.feed_id


def test_feed_order_puts_live_feeds_first(contracts: dict[str, FeedContract]) -> None:
    from dataclasses import replace

    from policy_tracker.core import engine
    from policy_tracker.core.models import CatalogState

    early, late = _two_feeds_on_one_site(contracts)
    state = CatalogState()
    for contract in (early, late):
        state = engine.ensure_feed_state(state, contract)
    pair = {early.feed_id: early, late.feed_id: late}
    assert runner.feed_order(state, pair) == [early.feed_id, late.feed_id]
    feeds = dict(state.feeds)
    feeds[late.feed_id] = replace(
        feeds[late.feed_id],
        baseline_status=BaselineStatus.COMPLETE,
        baselined_revision=late.coverage_revision,
    )
    live_state = replace(state, feeds=feeds)
    assert runner.feed_order(live_state, pair) == [late.feed_id, early.feed_id]
    # A coverage bump makes the feed a backfill feed again.
    bumped = {early.feed_id: early, late.feed_id: replace(late, coverage_revision=99)}
    assert runner.feed_order(live_state, bumped) == [early.feed_id, late.feed_id]


def test_run_without_smtp_config_is_blocked_before_crawl(
    data_dir: Path, adapter: FakeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_services", REAL_DEFAULT_SERVICES)
    assert _baseline(data_dir) == 0
    calls = adapter.calls
    before = _tree(data_dir)
    assert _cli("run", "--config", str(CONFIG), "--data-dir", str(data_dir)) == 2
    assert adapter.calls == calls
    assert _tree(data_dir) == before
