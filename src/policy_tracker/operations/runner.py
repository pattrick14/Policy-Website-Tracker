"""Orchestrate one tracker run: collect feeds, apply the engine, stage, and promote.

Step 2 has no email. ``baseline`` and ``run`` both record events and promote
immediately; Step 7 inserts the durable outbox (``_stage_delivery``) between
``write_candidate`` and ``promote`` for post-baseline runs, and adds the
retry-pending-delivery-before-crawl check at the start of ``run_feeds``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from policy_tracker.adapters.base import AdapterRegistry, Fetcher
from policy_tracker.core import config as config_module
from policy_tracker.core import engine
from policy_tracker.core.contracts import FeedContract, RunContext
from policy_tracker.core.identity import stable_hash
from policy_tracker.core.models import (
    BaselineStatus,
    CandidateState,
    CatalogState,
    Discovery,
    Event,
    FeedRun,
)
from policy_tracker.core.observations import FeedObservation
from policy_tracker.core.serialize import state_hash
from policy_tracker.operations.store import DataStore

Mode = Literal["baseline", "run"]
FetcherFactory = Callable[[FeedContract], Fetcher]

EXIT_OK = 0
EXIT_ALL_FEEDS_FAILED = 1


@dataclass(frozen=True)
class RunReport:
    run_id: str
    mode: str
    dry_run: bool
    promoted: bool
    base_state_hash: str
    state_hash: str  # candidate state hash (promoted unless dry run / all failed)
    feed_runs: tuple[FeedRun, ...]
    events: tuple[Event, ...]
    messages: tuple[str, ...] = ()

    @property
    def accepted_feeds(self) -> tuple[str, ...]:
        return tuple(run.feed_id for run in self.feed_runs if run.accepted)

    @property
    def failed_feeds(self) -> tuple[str, ...]:
        return tuple(run.feed_id for run in self.feed_runs if not run.accepted)

    @property
    def live_events(self) -> tuple[Event, ...]:
        return tuple(event for event in self.events if event.discovery is Discovery.LIVE)

    @property
    def partial(self) -> bool:
        return bool(self.failed_feeds)

    @property
    def exit_code(self) -> int:
        return EXIT_OK if self.accepted_feeds else EXIT_ALL_FEEDS_FAILED


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(started_at: str, config_hash: str) -> str:
    """Deterministic run id such as ``20261026T023000Z-1a2b3c4d``."""

    moment = datetime.fromisoformat(started_at)
    if moment.tzinfo is None:
        raise ValueError(f"started_at {started_at!r} must carry a UTC offset")
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{stable_hash(started_at, config_hash)[:8]}"


def _collect(
    contract: FeedContract,
    registry: AdapterRegistry,
    fetcher_factory: FetcherFactory,
    observed_at: str,
) -> tuple[FeedObservation | None, str | None]:
    """Return the adapter's observation, or ``None`` plus a failure reason."""

    adapter = registry.for_feed(contract.feed_id)
    if adapter is None:
        return None, "no_adapter"
    try:
        observation = adapter.collect(contract, fetcher_factory(contract), observed_at=observed_at)
    except Exception as exc:  # noqa: BLE001 - adapters must not raise; never sink the run
        return None, f"adapter_error: {type(exc).__name__}"
    if observation.feed_id != contract.feed_id or observation.site_id != contract.site_id:
        return None, f"adapter_mismatch: {observation.site_id}/{observation.feed_id}"
    return observation, None


def _stage_delivery(store: DataStore, candidate: CandidateState, mode: Mode) -> None:
    """Step 7 seam: render the digest and push a pending outbox entry before promotion.

    Step 2 sends nothing, so this is a no-op; baseline runs never get an outbox entry.
    """

    del store, candidate, mode


def is_live(state: CatalogState, contract: FeedContract) -> bool:
    """A feed whose baseline is complete at the contract's current coverage revision."""

    feed = state.feeds.get(contract.feed_id)
    return (
        feed is not None
        and feed.baseline_status is BaselineStatus.COMPLETE
        and feed.baselined_revision == contract.coverage_revision
    )


def feed_order(state: CatalogState, contracts: Mapping[str, FeedContract]) -> list[str]:
    """Live feeds first, then baselining/backfill feeds; each group sorted by feed id.

    Within a run, the first feed to see a record creates it. Applying live feeds
    first means a record seen by both a live and a baselining feed is reported as
    a LIVE ``NEW_DOCUMENT``/``NEW_MATTER`` instead of being absorbed silently.
    """

    return sorted(contracts, key=lambda feed_id: (not is_live(state, contracts[feed_id]), feed_id))


def evaluate_feeds(
    state: CatalogState,
    contracts: dict[str, FeedContract],
    run: RunContext,
    *,
    registry: AdapterRegistry,
    fetcher_factory: FetcherFactory,
    now: Callable[[], str],
) -> tuple[CatalogState, tuple[Event, ...], tuple[FeedRun, ...]]:
    """Apply every tracked contract sequentially, in ``feed_order``."""

    events: list[Event] = []
    feed_runs: list[FeedRun] = []
    tracked = {k: c for k, c in contracts.items() if c.decision == "track"}
    for contract in tracked.values():
        state = engine.ensure_feed_state(state, contract)
    for feed_id in feed_order(state, tracked):
        contract = tracked[feed_id]
        observation, reason = _collect(contract, registry, fetcher_factory, now())
        try:
            outcome = engine.apply_observation(state, observation, contract, run)
        except ValueError as exc:  # malformed observation: fail this feed, keep its state
            reason = f"engine_rejected: {exc}"
            outcome = engine.apply_observation(state, None, contract, run)
        feed_run = outcome.feed_run
        if reason is not None and reason not in feed_run.diagnostics:
            feed_run = dataclasses.replace(feed_run, diagnostics=(*feed_run.diagnostics, reason))
        state = outcome.state
        events.extend(outcome.events)
        feed_runs.append(feed_run)
    return state, tuple(events), tuple(feed_runs)


def run_feeds(
    config_path: Path,
    data_dir: Path,
    *,
    registry: AdapterRegistry,
    fetcher_factory: FetcherFactory,
    mode: Mode,
    dry_run: bool,
    code_revision: str,
    now: Callable[[], str] = utc_now,
) -> RunReport:
    """Run every tracked feed once; promote unless ``dry_run`` or every feed failed."""

    if mode not in ("baseline", "run"):
        raise ValueError(f"unknown mode {mode!r}")
    config = config_module.load_config(Path(config_path))
    contracts = config_module.load_contracts(config)
    chash = config_module.config_hash(Path(config_path))
    store = DataStore(Path(data_dir))
    # Step 7: retry any pending outbox delivery here, before crawling.
    base_state = store.load_state()
    base_hash = state_hash(base_state)
    started_at = now()
    run = RunContext(
        run_id=make_run_id(started_at, chash),
        started_at=started_at,
        config_hash=chash,
        code_revision=code_revision,
    )
    state, events, feed_runs = evaluate_feeds(
        base_state, contracts, run, registry=registry, fetcher_factory=fetcher_factory, now=now
    )
    candidate = CandidateState(
        run_id=run.run_id,
        base_state_hash=base_hash,
        state=state,
        events=events,
        feed_runs=feed_runs,
    )
    messages: list[str] = []
    live = sum(1 for event in events if event.discovery is Discovery.LIVE)
    if mode == "baseline" and live:
        messages.append(
            f"baseline mode: {live} live event(s) recorded but not delivered (no email in baseline)"
        )
    all_failed = not any(feed_run.accepted for feed_run in feed_runs)
    if all_failed:
        messages.append("all feeds failed: catalog not promoted")
    context = {"mode": mode, "started_at": started_at, "dry_run": dry_run}

    promoted = False
    if not dry_run:
        if all_failed:
            store.write_run(candidate, context=context, status="not_promoted")
        else:
            store.write_candidate(candidate, context=context)
            if mode == "run":
                _stage_delivery(store, candidate, mode)
            store.promote(candidate)
            promoted = True
    return RunReport(
        run_id=run.run_id,
        mode=mode,
        dry_run=dry_run,
        promoted=promoted,
        base_state_hash=base_hash,
        state_hash=state_hash(state),
        feed_runs=feed_runs,
        events=events,
        messages=tuple(messages),
    )


def rebaseline(
    config_path: Path,
    data_dir: Path,
    feed_id: str,
    *,
    reason: str,
    code_revision: str,
    now: Callable[[], str] = utc_now,
) -> str:
    """Reset one feed's baseline through the normal candidate/CAS path; return the run id."""

    if not reason.strip():
        raise ValueError("a rebaseline needs a non-empty reason")
    config = config_module.load_config(Path(config_path))
    contracts = config_module.load_contracts(config)
    if feed_id not in contracts:
        raise ValueError(f"unknown or untracked feed {feed_id!r}")
    chash = config_module.config_hash(Path(config_path))
    store = DataStore(Path(data_dir))
    base_state = store.load_state()
    started_at = now()
    run_id = make_run_id(started_at, chash)
    state = engine.ensure_feed_state(base_state, contracts[feed_id])
    state = engine.rebaseline_feed(state, feed_id, reason=reason)
    candidate = CandidateState(
        run_id=run_id,
        base_state_hash=state_hash(base_state),
        state=state,
        events=(),
        feed_runs=(),
    )
    context = {
        "mode": "rebaseline-feed",
        "started_at": started_at,
        "feed_id": feed_id,
        "reason": reason,
        "config_hash": chash,
        "code_revision": code_revision,
    }
    rebaselines = ({"feed_id": feed_id, "reason": reason, "at": started_at},)
    store.write_candidate(candidate, context=context, rebaselines=rebaselines)
    store.promote(candidate)
    return run_id
