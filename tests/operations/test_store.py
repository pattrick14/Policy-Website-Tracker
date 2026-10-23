"""Tests for the tracker-data store: layout, staging, CAS promotion, atomic writes."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from policy_tracker.core import migrations, serialize
from policy_tracker.core import models as m
from policy_tracker.operations import store as store_module
from policy_tracker.operations.store import DataStore, PromotionConflict, StoreError

RUN_A = "20261026T023000Z-0000000a"
RUN_B = "20261102T023000Z-0000000b"


def _feed_state(**changes) -> m.FeedState:
    base = m.FeedState(
        feed_id="feed_a",
        baseline_status=m.BaselineStatus.COMPLETE,
        coverage_revision=1,
        baselined_revision=1,
        parser_version=1,
    )
    return replace(base, **changes)


def _state(title: str = "Draft Regulations") -> m.CatalogState:
    matter = m.Matter(
        key="site:matter:id:1",
        site_id="site",
        native_id="1",
        canonical_url=None,
        title=title,
        metadata={"category": "regulations"},
        first_seen_at="2026-10-26T02:30:00Z",
        last_seen_at="2026-10-26T02:30:00Z",
        discovery=m.Discovery.BASELINE,
        provenance=m.Provenance("https://example.gov.in/list", "row[1]"),
    )
    return m.CatalogState(matters={matter.key: matter}, feeds={"feed_a": _feed_state()})


def _feed_run(run_id: str, *, accepted: bool = True) -> m.FeedRun:
    return m.FeedRun(
        run_id=run_id,
        feed_id="feed_a",
        started_at="2026-10-26T02:30:00Z",
        finished_at="2026-10-26T02:31:00Z",
        fetch_status=m.FetchStatus.OK,
        parse_status=m.ParseStatus.VALID,
        enumeration_status=m.EnumerationStatus.COMPLETE,
        accepted=accepted,
        item_count=1,
        previous_item_count=None,
        coverage_revision=1,
        parser_version=1,
        config_hash="c" * 64,
        code_revision="rev",
    )


def _event(run_id: str) -> m.Event:
    return m.Event(
        event_id="e1",
        event_type=m.EventType.NEW_MATTER,
        run_id=run_id,
        feed_id="feed_a",
        entity_kind=m.EntityKind.MATTER,
        entity_key="site:matter:id:1",
        occurred_at="2026-10-26T02:30:00Z",
        discovery=m.Discovery.BASELINE,
        coverage_revision=1,
    )


def _candidate(store: DataStore, run_id: str, state: m.CatalogState, **kw) -> m.CandidateState:
    return m.CandidateState(
        run_id=run_id,
        base_state_hash=kw.get("base", store.state_hash()),
        state=state,
        events=kw.get("events", (_event(run_id),)),
        feed_runs=(_feed_run(run_id),),
    )


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def store(tmp_path: Path) -> DataStore:
    data = DataStore(tmp_path / "data")
    data.initialize()
    return data


def test_initialize_creates_layout(store: DataStore) -> None:
    root = store.root
    for name in ("catalog", "events", "runs", "health", "outbox", "digests", "candidates"):
        assert (root / name).is_dir()
    schema = json.loads((root / "schema.json").read_text())
    assert schema["schema_version"] == m.SCHEMA_VERSION and schema["kind"] == "tracker_data"
    for part in store_module.CATALOG_PARTS:
        document = json.loads((root / "catalog" / f"{part}.json").read_text())
        assert document["records"] == {} and document["schema_version"] == m.SCHEMA_VERSION
    assert store.load_state() == m.CatalogState()
    assert store.state_hash() == serialize.state_hash(m.CatalogState())


def test_initialize_refuses_non_empty(tmp_path: Path, store: DataStore) -> None:
    with pytest.raises(StoreError, match="non-empty"):
        store.initialize()
    other = tmp_path / "other"
    other.mkdir()
    (other / "stray.txt").write_text("x")
    with pytest.raises(StoreError, match="non-empty"):
        DataStore(other).initialize()


def test_initialize_allows_bare_git_checkout(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    DataStore(root).initialize()
    assert DataStore(root).is_initialized()


def test_uninitialized_store_refuses_to_load(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="not an initialized"):
        DataStore(tmp_path).load_state()


def test_write_candidate_does_not_promote(store: DataStore) -> None:
    base = store.state_hash()
    candidate = _candidate(store, RUN_A, _state())
    store.write_candidate(candidate)
    assert store.state_hash() == base
    assert store.load_run(RUN_A)["status"] == "candidate"
    assert store.load_candidate(RUN_A) == candidate
    assert store.events_path(RUN_A) == store.root / "events/2026/10" / f"{RUN_A}.json"


def test_promote_writes_catalog_and_records_run(store: DataStore) -> None:
    candidate = _candidate(store, RUN_A, _state())
    store.write_candidate(candidate)
    new_hash = store.promote(candidate)
    assert store.load_state() == candidate.state
    assert store.state_hash() == new_hash
    manifest = store.load_run(RUN_A)
    assert manifest["status"] == "promoted"
    assert manifest["base_state_hash"] == candidate.base_state_hash
    assert manifest["config_hash"] == "c" * 64 and manifest["code_revision"] == "rev"
    assert manifest["promoted_state_hash"] == new_hash
    assert not store.candidate_dir(RUN_A).exists()
    assert store.load_events(RUN_A) == candidate.events
    assert list(store.iter_events()) == list(candidate.events)
    health = json.loads(store.health_path.read_text())
    assert health["feeds"]["feed_a"]["accepted"] is True
    assert health["feeds"]["feed_a"]["baseline_status"] == "complete"


def test_promote_compare_and_swap_refuses_stale_base(store: DataStore) -> None:
    first = _candidate(store, RUN_A, _state("first"))
    second = _candidate(store, RUN_B, _state("second"))
    store.write_candidate(first)
    store.write_candidate(second)
    store.promote(first)
    with pytest.raises(PromotionConflict):
        store.promote(second)
    assert store.load_state() == first.state
    assert store.load_run(RUN_B)["status"] == "candidate"


def test_promote_requires_recorded_run_and_is_once_only(store: DataStore) -> None:
    candidate = _candidate(store, RUN_A, _state())
    with pytest.raises(StoreError, match="must be recorded"):
        store.promote(candidate)
    store.write_candidate(candidate)
    store.promote(candidate)
    with pytest.raises(StoreError, match="already promoted"):
        store.promote(candidate)


def test_run_ids_are_never_overwritten(store: DataStore) -> None:
    candidate = _candidate(store, RUN_A, _state())
    store.write_candidate(candidate)
    with pytest.raises(StoreError, match="already recorded"):
        store.write_candidate(candidate)
    with pytest.raises(StoreError, match="already recorded"):
        store.write_run(candidate)


def test_bad_run_id_and_foreign_events_are_rejected(store: DataStore) -> None:
    with pytest.raises(StoreError, match="does not match"):
        store.write_run(_candidate(store, "latest", _state(), events=()))
    candidate = replace(_candidate(store, RUN_A, _state()), events=(_event(RUN_B),))
    with pytest.raises(StoreError, match="belongs to run"):
        store.write_run(candidate)


def test_write_run_records_without_staging(store: DataStore) -> None:
    before = _tree(store.root / "catalog")
    candidate = _candidate(store, RUN_A, _state(), events=())
    store.write_run(candidate, context={"mode": "run"}, status="not_promoted")
    assert _tree(store.root / "catalog") == before
    assert not store.candidate_dir(RUN_A).exists()
    manifest = store.load_run(RUN_A)
    assert manifest["status"] == "not_promoted" and manifest["context"] == {"mode": "run"}
    assert manifest["events_path"] is None


def test_atomic_write_leaves_old_file_on_failure(
    store: DataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = store.root / "catalog"
    before = _tree(catalog)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    candidate = _candidate(store, RUN_A, _state())
    with pytest.raises(OSError, match="disk full"):
        store.write_candidate(candidate)
    monkeypatch.undo()
    assert _tree(catalog) == before
    assert list(store.root.rglob("*.tmp")) == []


def test_torn_catalog_is_detected(store: DataStore) -> None:
    candidate = _candidate(store, RUN_A, _state())
    store.write_candidate(candidate)
    store.promote(candidate)
    path = store.root / "catalog" / "matters.json"
    document = json.loads(path.read_text())
    document["state_hash"] = "0" * 64
    path.write_text(serialize.dumps(document))
    with pytest.raises(StoreError, match="disagree"):
        store.load_state()


def test_tampered_catalog_hash_mismatch_is_detected(store: DataStore) -> None:
    path = store.root / "catalog" / "feeds.json"
    document = json.loads(path.read_text())
    document["records"] = {"feed_a": serialize.to_data(_feed_state())}
    path.write_text(serialize.dumps(document))
    with pytest.raises(StoreError, match="does not match its recorded state_hash"):
        store.load_state()


def test_old_catalog_is_migrated_on_load(store: DataStore, monkeypatch: pytest.MonkeyPatch) -> None:
    for part in store_module.CATALOG_PARTS:
        path = store.root / "catalog" / f"{part}.json"
        document = json.loads(path.read_text())
        document["schema_version"] = 0
        if part == "feeds":
            legacy = serialize.to_data(_feed_state())
            legacy["failures"] = legacy.pop("consecutive_failures")
            document["records"] = {"feed_a": legacy}
        path.write_text(serialize.dumps(document))

    def v0_to_v1(document: dict) -> dict:
        assert document["kind"] == "catalog_state"
        for feed in document["feeds"].values():
            feed["consecutive_failures"] = feed.pop("failures")
        return document

    monkeypatch.setattr(migrations, "MIGRATIONS", {0: v0_to_v1})
    state = store.load_state()
    assert state.feeds["feed_a"] == _feed_state()
    candidate = _candidate(store, RUN_A, state, events=())
    store.write_candidate(candidate)
    store.promote(candidate)
    for part in store_module.CATALOG_PARTS:
        document = json.loads((store.root / "catalog" / f"{part}.json").read_text())
        assert document["schema_version"] == m.SCHEMA_VERSION


def test_newer_schema_is_refused(store: DataStore) -> None:
    path = store.root / "schema.json"
    document = json.loads(path.read_text())
    document["schema_version"] = m.SCHEMA_VERSION + 1
    path.write_text(serialize.dumps(document))
    with pytest.raises(migrations.MigrationError, match="newer"):
        store.load_state()


def test_files_are_canonical_json(store: DataStore) -> None:
    candidate = _candidate(store, RUN_A, _state())
    store.write_candidate(candidate)
    store.promote(candidate)
    for path in store.root.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert text == serialize.dumps(json.loads(text)), path


def test_duplicate_event_ids_in_a_run_are_rejected(store: DataStore) -> None:
    candidate = replace(_candidate(store, RUN_A, _state()), events=(_event(RUN_A),) * 2)
    with pytest.raises(StoreError, match="duplicate event id"):
        store.write_run(candidate)


def test_promote_finishes_after_crash_past_catalog_write(
    store: DataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after the catalog write but before the manifest is marked is recoverable."""

    candidate = _candidate(store, RUN_A, _state())
    store.write_candidate(candidate)
    store.stage_rolling(RUN_A, [("open_deadlines.csv", b"title\n")])
    real = DataStore._write_schema
    crashed: list[bool] = []

    def crash_once(self: DataStore) -> None:
        if not crashed:
            crashed.append(True)
            raise KeyboardInterrupt("killed mid-promotion")
        real(self)

    monkeypatch.setattr(DataStore, "_write_schema", crash_once)
    with pytest.raises(KeyboardInterrupt):
        store.promote(candidate)
    assert store.load_state() == candidate.state  # catalog advanced
    assert store.load_run(RUN_A)["status"] == "candidate"  # manifest not marked
    retried = store.load_candidate(RUN_A)
    new_hash = store.promote(retried)  # no PromotionConflict
    manifest = store.load_run(RUN_A)
    assert manifest["status"] == "promoted" and manifest["promoted_state_hash"] == new_hash
    assert (store.root / "open_deadlines.csv").read_bytes() == b"title\n"
    assert not store.candidate_dir(RUN_A).exists()
    # A genuinely different newer state is still refused.
    other = _candidate(store, RUN_B, _state("other"), base="0" * 64)
    store.write_candidate(other)
    with pytest.raises(PromotionConflict):
        store.promote(other)


def test_health_is_published_only_at_promotion(store: DataStore) -> None:
    candidate = _candidate(store, RUN_A, _state())
    store.write_candidate(candidate)
    assert not store.health_path.exists()  # staged, not published
    assert store.load_run(RUN_A)["health"]["feed_a"]["accepted"] is True  # per-run record
    store.discard_candidate(RUN_A, reason="delivery abandoned", at="2026-10-27T00:00:00Z")
    assert not store.health_path.exists()  # abandoned: health never described it

    second = _candidate(store, RUN_B, _state())
    store.write_candidate(second)
    store.promote(second)
    health = json.loads(store.health_path.read_text())
    assert health["run_id"] == RUN_B and health["feeds"]["feed_a"]["run_id"] == RUN_B


def test_not_promoted_run_updates_health_immediately(store: DataStore) -> None:
    failed = m.CandidateState(
        run_id=RUN_A,
        base_state_hash=store.state_hash(),
        state=store.load_state(),
        events=(),
        feed_runs=(_feed_run(RUN_A, accepted=False),),
    )
    store.write_run(failed, status="not_promoted")
    health = json.loads(store.health_path.read_text())
    assert health["feeds"]["feed_a"]["accepted"] is False
