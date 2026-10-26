"""Feed evaluation, run ids, run reports, and per-feed rebaselines.

``evaluate_feeds`` applies every tracked feed (live feeds first). The durable
weekly sequence (retry pending delivery, crawl, digest, push, send, promote)
lives in ``policy_tracker.operations.pipeline``; ``run_feeds`` delegates to it.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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

if TYPE_CHECKING:
    from policy_tracker.operations.pipeline import Publisher, Services

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
    delivery: str | None = None  # delivered, pending, suppressed, or None (no email)
    delivery_id: str | None = None
    artifacts: tuple[str, ...] = ()  # digest files written (or rendered, in a dry run)
    dry_run_dir: str | None = None
    data_commit: str | None = None  # tracker-data head after the run (git mode)
    exit_override: int | None = None

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
        if self.exit_override is not None:
            return self.exit_override
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


def accepts_keyword(func: Callable[..., object], name: str) -> bool:
    """Whether ``func`` takes keyword ``name`` (explicitly or through ``**kwargs``)."""

    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        or (p.name == name and p.kind is not inspect.Parameter.POSITIONAL_ONLY)
        for p in parameters
    )


def authoritative_feeds(contracts: Mapping[str, FeedContract]) -> frozenset[str]:
    """Tracked feeds whose complete enumerations may establish removal."""

    return frozenset(
        feed_id
        for feed_id, contract in contracts.items()
        if contract.decision == "track" and contract.removal_authority
    )


def live_feeds(state: CatalogState, contracts: Mapping[str, FeedContract]) -> frozenset[str]:
    """Tracked feeds that are live (``is_live``) in ``state``."""

    return frozenset(
        feed_id
        for feed_id, contract in contracts.items()
        if contract.decision == "track" and is_live(state, contract)
    )


def evaluate_feeds(
    state: CatalogState,
    contracts: dict[str, FeedContract],
    run: RunContext,
    *,
    registry: AdapterRegistry,
    fetcher_factory: FetcherFactory,
    now: Callable[[], str],
    skip: frozenset[str] = frozenset(),
) -> tuple[CatalogState, tuple[Event, ...], tuple[FeedRun, ...]]:
    """Apply every tracked contract except ``skip`` sequentially, in ``feed_order``.

    Every ``apply_observation`` call receives ``authoritative_feeds`` (all tracked
    contracts with removal authority, skipped or not) when the engine accepts it.
    """

    events: list[Event] = []
    feed_runs: list[FeedRun] = []
    tracked = {k: c for k, c in contracts.items() if c.decision == "track"}
    extra: dict[str, object] = {}
    if accepts_keyword(engine.apply_observation, "authoritative_feeds"):
        extra["authoritative_feeds"] = authoritative_feeds(tracked)
    for contract in tracked.values():
        state = engine.ensure_feed_state(state, contract)
    for feed_id in feed_order(state, tracked):
        if feed_id in skip:
            continue
        contract = tracked[feed_id]
        observation, reason = _collect(contract, registry, fetcher_factory, now())
        try:
            outcome = engine.apply_observation(state, observation, contract, run, **extra)
        except ValueError as exc:  # malformed observation: fail this feed, keep its state
            reason = f"engine_rejected: {exc}"
            outcome = engine.apply_observation(state, None, contract, run, **extra)
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
    no_email: bool = False,
    services: Services | None = None,
) -> RunReport:
    """Run the durable pipeline once (``pipeline.run_pipeline``).

    ``mode="run"`` emails unless ``no_email``; without a configured sender it is
    blocked (``pipeline.DeliveryBlocked``) before anything is crawled or written.
    """

    from policy_tracker.operations import pipeline

    return pipeline.run_pipeline(
        config_path,
        data_dir,
        registry=registry,
        fetcher_factory=fetcher_factory,
        mode=mode,
        dry_run=dry_run,
        code_revision=code_revision,
        now=now,
        no_email=no_email,
        services=services,
    )


def rebaseline(
    config_path: Path,
    data_dir: Path,
    feed_id: str,
    *,
    reason: str,
    code_revision: str,
    now: Callable[[], str] = utc_now,
    publisher: Publisher | None = None,
) -> str:
    """Reset one feed's baseline through the normal candidate/CAS path; return the run id.

    Refused while a delivery is pending (its candidate would no longer apply).
    """

    if not reason.strip():
        raise ValueError("a rebaseline needs a non-empty reason")
    config = config_module.load_config(Path(config_path))
    contracts = config_module.load_contracts(config)
    if feed_id not in contracts:
        raise ValueError(f"unknown or untracked feed {feed_id!r}")
    chash = config_module.config_hash(Path(config_path))
    from policy_tracker.operations.outbox import Outbox
    from policy_tracker.operations.pipeline import DeliveryPending

    head = publisher.checkout_data() if publisher is not None else None
    store = DataStore(Path(data_dir))
    pending = Outbox(store.root).pending_ids()
    if pending:
        raise DeliveryPending(
            f"pending delivery {', '.join(pending)} must be delivered or abandoned before "
            "a rebaseline"
        )
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
        "base_data_commit": head,
    }
    rebaselines = ({"feed_id": feed_id, "reason": reason, "at": started_at},)
    store.write_candidate(candidate, context=context, rebaselines=rebaselines)
    store.promote(candidate)
    if publisher is not None and head is not None:
        publisher.commit_and_push(f"run {run_id}: rebaseline {feed_id}", head)
    return run_id
