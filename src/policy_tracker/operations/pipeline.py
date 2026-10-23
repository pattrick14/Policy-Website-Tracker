"""The durable weekly pipeline (PROJECT_PLAN "Durable delivery sequence", D-011).

1. Retry every pending outbox entry before crawling, with the same immutable
   bundle, recipient set and Message-ID. If one still fails, stop: no new crawl.
2. Crawl (live feeds first), enrich, build the digest, render files and email.
3. Stage the candidate, digest files and pending bundle; commit and push them
   before contacting SMTP.
4. Send with the bundle's deterministic Message-ID over verified TLS.
5. Only when every recipient accepted: promote the candidate, mark the entry
   delivered, and commit and push again.
6. Partial acceptance, refusal or an ambiguous timeout leaves the entry pending
   (the attempt is recorded and pushed) and the run exits ``EXIT_DELIVERY_PENDING``.
7. ``baseline`` promotes silently: no digest, no outbox entry, no email.

``dry_run`` performs no email, commit, push or durable mutation; it renders into
a temporary directory. ``no_email`` (``run`` only) archives the digest and
promotes without an outbox entry. Every collaborator is injected through
``Services`` so tests run offline.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from policy_tracker.adapters.base import AdapterRegistry
from policy_tracker.core import config as config_module
from policy_tracker.core.contracts import FeedContract, RunContext
from policy_tracker.core.models import (
    CandidateState,
    CatalogState,
    Discovery,
    Event,
    PendingDelivery,
)
from policy_tracker.core.serialize import state_hash
from policy_tracker.documents.types import EnrichmentResult, ExtractedText
from policy_tracker.operations import runner
from policy_tracker.operations.outbox import (
    Attempt,
    Outbox,
    OutboxError,
    make_message_id,
    normalize_recipients,
    recipient_set_sha256,
)
from policy_tracker.operations.smtp import SendResult
from policy_tracker.operations.store import DataStore, StoreError
from policy_tracker.reporting import deadlines
from policy_tracker.reporting.types import Digest, RenderedEmail, RenderedFile

EXIT_DELIVERY_PENDING = 3
EXIT_BRANCH_RACE = 4


class DeliveryBlocked(RuntimeError):
    """A run that must email cannot: SMTP, recipients or the renderer are not configured."""


class DeliveryPending(StoreError):
    """A pending delivery must be delivered or abandoned before this command can run."""


class Enricher(Protocol):
    def __call__(
        self,
        state: CatalogState,
        events: tuple[Event, ...],
        contracts: Mapping[str, FeedContract],
        fetcher_for: runner.FetcherFactory,
        *,
        run: RunContext,
        **options: Any,
    ) -> EnrichmentResult: ...


class DigestBuilder(Protocol):
    def __call__(
        self,
        candidate: CandidateState,
        previous: CatalogState,
        texts: Mapping[str, ExtractedText],
        *,
        generated_at: str,
        previous_run_at: str | None,
        previous_deadlines: Any = None,
    ) -> tuple[Digest, Any]: ...


class Sender(Protocol):
    def __call__(
        self, email: RenderedEmail, *, message_id: str, recipients: Sequence[str]
    ) -> SendResult: ...


class Publisher(Protocol):
    def checkout_data(self, expected_base: str | None = None) -> str: ...
    def commit_and_push(self, message: str, expected_remote_head: str) -> str: ...


@dataclass(frozen=True)
class Renderer:
    """Digest renderers: immutable per-run files, rolling views, and the email."""

    files: Sequence[Callable[[Digest], RenderedFile]]
    email: Callable[[Digest], RenderedEmail]
    rolling: Sequence[Callable[[Digest], RenderedFile]] = ()  # e.g. open_deadlines.csv


@dataclass
class Services:
    enricher: Enricher | None = None
    digest_builder: DigestBuilder | None = None
    renderer: Renderer | None = None
    sender: Sender | None = None
    recipients: tuple[str, ...] = field(default=(), repr=False)  # never logged
    publisher: Publisher | None = None  # None: local data directory, no git
    message_id_domain: str | None = None
    enrich_options: Mapping[str, Any] = field(default_factory=dict)
    # deadline index <-> catalog/deadlines.json document
    encode_deadlines: Callable[[Any], Mapping[str, Any]] = deadlines.index_to_data
    decode_deadlines: Callable[[Mapping[str, Any]], Any] = deadlines.index_from_data
    # site_id -> breaker/health summary of that site's fetcher (None when unused)
    fetcher_state: Callable[[str], Mapping[str, Any] | None] | None = None


@dataclass
class _Session:
    """The data checkout plus the expected remote head for compare-and-swap pushes."""

    store: DataStore
    outbox: Outbox
    publisher: Publisher | None
    head: str | None

    def commit(self, message: str) -> None:
        if self.publisher is None:
            return
        if self.head is None:
            raise StoreError("no data-branch head checked out")
        self.head = self.publisher.commit_and_push(message, self.head)


@dataclass(frozen=True)
class DeliveryOutcome:
    delivery_id: str
    delivered: bool
    attempts: int
    summary: str


def _require_delivery(services: Services) -> None:
    missing = [
        name
        for name, value in (
            ("SMTP sender", services.sender),
            ("recipients (SMTP_TO)", services.recipients),
            ("digest builder", services.digest_builder),
            ("renderer", services.renderer),
        )
        if not value
    ]
    if missing:
        raise DeliveryBlocked(f"email delivery blocked: missing {', '.join(missing)}")


def _promote(store: DataStore, run_id: str) -> None:
    """Promote a staged candidate with its deadline index and rolling views (idempotent)."""

    if not store.is_promoted(run_id):
        store.promote(store.load_candidate(run_id))


def deliver_pending(
    session: _Session, delivery_id: str, services: Services, now: Callable[[], str]
) -> DeliveryOutcome:
    """Send one stored bundle; on full acceptance promote, mark delivered, and push."""

    if services.sender is None:
        raise DeliveryBlocked("email delivery blocked: no SMTP sender configured")
    bundle = session.outbox.load(delivery_id)
    recipients = session.outbox.verify_recipients(bundle.header, services.recipients)
    result = services.sender(
        bundle.email, message_id=bundle.header.message_id, recipients=recipients
    )
    delivered = result.delivered_to_all(len(recipients))
    attempt = Attempt(
        at=now(),
        accepted=len(result.accepted),
        refused=len(result.refused),
        ambiguous=result.ambiguous,
        error=result.error,
    )
    if delivered:
        _promote(session.store, bundle.header.run_id)
    header = session.outbox.record_attempt(delivery_id, attempt, delivered=delivered)
    verdict = "delivered; candidate promoted" if delivered else "still pending"
    session.commit(f"run {delivery_id}: delivery attempt {header.attempts} {verdict}")
    return DeliveryOutcome(delivery_id, delivered, header.attempts, result.summary())


def _context(mode: str, started_at: str, dry_run: bool, head: str | None, **extra: Any) -> dict:
    return {
        "mode": mode,
        "started_at": started_at,
        "dry_run": dry_run,
        "base_data_commit": head,
        **extra,
    }


def _fetcher_states(services: Services, contracts: Mapping[str, FeedContract]) -> dict[str, Any]:
    """Per-site fetcher breaker state for the run manifest (best effort)."""

    if services.fetcher_state is None:
        return {}
    out: dict[str, Any] = {}
    for site_id in sorted({c.site_id for c in contracts.values() if c.decision == "track"}):
        try:
            state = services.fetcher_state(site_id)
        except Exception:  # noqa: BLE001 - diagnostics only
            state = None
        if state is not None:
            out[site_id] = dict(state)
    return out


def _write_dry_run(files: Sequence[RenderedFile], email: RenderedEmail | None) -> str:
    root = Path(tempfile.mkdtemp(prefix="policy-tracker-dry-run-"))
    for item in files:
        target = root.joinpath(*Path(item.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content)
    if email is not None:
        (root / "email").mkdir()
        (root / "email" / "subject.txt").write_text(email.subject + "\n", encoding="utf-8")
        (root / "email" / "body.txt").write_text(email.text_body, encoding="utf-8")
        (root / "email" / "body.html").write_text(email.html_body, encoding="utf-8")
        for attachment in email.attachments:
            (root / "email" / Path(attachment.path).name).write_bytes(attachment.content)
    return str(root)


def run_pipeline(
    config_path: Path,
    data_dir: Path,
    *,
    registry: AdapterRegistry,
    fetcher_factory: runner.FetcherFactory,
    mode: runner.Mode,
    dry_run: bool,
    code_revision: str,
    now: Callable[[], str] = runner.utc_now,
    no_email: bool = False,
    services: Services | None = None,
) -> runner.RunReport:
    """Run the durable sequence once; see the module docstring."""

    if mode not in ("baseline", "run"):
        raise ValueError(f"unknown mode {mode!r}")
    services = services or Services()
    deliver = mode == "run" and not no_email
    if deliver and not dry_run:
        _require_delivery(services)  # blocked before any crawl or mutation
    config = config_module.load_config(Path(config_path))
    contracts = config_module.load_contracts(config)
    chash = config_module.config_hash(Path(config_path))

    publisher = services.publisher
    head = publisher.checkout_data() if publisher is not None else None
    store = DataStore(Path(data_dir))
    session = _Session(store=store, outbox=Outbox(store.root), publisher=publisher, head=head)
    messages: list[str] = []

    # (1) Retry pending deliveries before crawling.
    pending = session.outbox.pending_ids() if store.is_initialized() else []
    if pending:
        if dry_run:
            current = store.state_hash()
            return runner.RunReport(
                run_id=pending[0],
                mode=mode,
                dry_run=True,
                promoted=False,
                base_state_hash=current,
                state_hash=current,
                feed_runs=(),
                events=(),
                messages=(
                    (
                        f"pending delivery {', '.join(pending)}: a real run retries it before "
                        "crawling; dry run stopped"
                    ),
                ),
                delivery="pending",
                delivery_id=pending[0],
                data_commit=head,
            )
        if not deliver:
            raise DeliveryPending(
                f"pending delivery {', '.join(pending)} must be delivered (policy-tracker run) "
                "or abandoned (policy-tracker abandon-delivery) first"
            )
        for delivery_id in pending:
            outcome = deliver_pending(session, delivery_id, services, now)
            messages.append(
                f"retried pending delivery {delivery_id} (attempt {outcome.attempts}): "
                f"{outcome.summary}; {'delivered' if outcome.delivered else 'still pending'}"
            )
            if not outcome.delivered:
                current = store.state_hash()
                messages.append("new crawl not started while a delivery is pending")
                return runner.RunReport(
                    run_id=delivery_id,
                    mode=mode,
                    dry_run=False,
                    promoted=False,
                    base_state_hash=current,
                    state_hash=current,
                    feed_runs=(),
                    events=(),
                    messages=tuple(messages),
                    delivery="pending",
                    delivery_id=delivery_id,
                    exit_override=EXIT_DELIVERY_PENDING,
                    data_commit=session.head,
                )

    # (2) Crawl, enrich, digest.
    base_data_commit = session.head
    base_state = store.load_state()
    base_hash = state_hash(base_state)
    started_at = now()
    run = runner.RunContext(
        run_id=runner.make_run_id(started_at, chash),
        started_at=started_at,
        config_hash=chash,
        code_revision=code_revision,
    )
    state, events, feed_runs = runner.evaluate_feeds(
        base_state, contracts, run, registry=registry, fetcher_factory=fetcher_factory, now=now
    )
    all_failed = not any(feed_run.accepted for feed_run in feed_runs)
    texts: Mapping[str, ExtractedText] = {}
    if not all_failed and services.enricher is not None:
        try:
            enriched = services.enricher(
                state, events, contracts, fetcher_factory, run=run, **services.enrich_options
            )
        except Exception as exc:  # noqa: BLE001 - enrichment never sinks discovery
            messages.append(f"enrichment failed ({type(exc).__name__}); listing data kept")
        else:
            state = enriched.state
            events = (*events, *enriched.events)
            texts = enriched.texts
            if enriched.diagnostics:
                messages.append(f"enrichment: {len(enriched.diagnostics)} diagnostic(s)")
    candidate = CandidateState(
        run_id=run.run_id,
        base_state_hash=base_hash,
        state=state,
        events=events,
        feed_runs=feed_runs,
    )
    fetchers = _fetcher_states(services, contracts)
    live = sum(1 for event in events if event.discovery is Discovery.LIVE)
    if mode == "baseline" and live:
        messages.append(
            f"baseline mode: {live} live event(s) recorded but not delivered (no email in baseline)"
        )

    def report(**extra: Any) -> runner.RunReport:
        return runner.RunReport(
            run_id=run.run_id,
            mode=mode,
            dry_run=dry_run,
            promoted=extra.pop("promoted", False),
            base_state_hash=base_hash,
            state_hash=state_hash(state),
            feed_runs=feed_runs,
            events=events,
            messages=tuple(messages),
            data_commit=session.head,
            **extra,
        )

    if all_failed:
        messages.append("all feeds failed: catalog not promoted")
        if not dry_run:
            context = _context(mode, started_at, dry_run, base_data_commit, fetchers=fetchers)
            store.write_run(candidate, context=context, status="not_promoted")
            session.commit(f"run {run.run_id}: all feeds failed; diagnostics recorded")
        return report()

    if mode == "baseline":
        if dry_run:
            return report()
        context = _context(
            mode, started_at, dry_run, base_data_commit, delivery="none", fetchers=fetchers
        )
        store.write_candidate(candidate, context=context)
        store.promote(candidate)
        session.commit(f"run {run.run_id}: baseline promoted silently (no email)")
        return report(promoted=True)

    files: list[RenderedFile] = []
    rolling: list[RenderedFile] = []
    email: RenderedEmail | None = None
    index_data: Mapping[str, Any] | None = None
    generated_at: str | None = None
    if services.digest_builder is not None and services.renderer is not None:
        previous_index = store.load_deadline_index()
        generated_at = now()
        digest, index = services.digest_builder(
            candidate,
            base_state,
            texts,
            generated_at=generated_at,
            previous_run_at=store.last_promoted_run_at(),
            previous_deadlines=(
                services.decode_deadlines(previous_index) if previous_index is not None else None
            ),
        )
        index_data = services.encode_deadlines(index) if index is not None else None
        files = [render(digest) for render in services.renderer.files]
        rolling = [render(digest) for render in services.renderer.rolling]
        if deliver:
            email = services.renderer.email(digest)
    else:
        messages.append("no digest renderer configured: digest not archived")

    if dry_run:
        directory = _write_dry_run([*files, *rolling], email)
        messages.append(f"dry run: rendered {len(files) + len(rolling)} file(s) to {directory}")
        if email is not None:
            messages.append(
                f"dry run: email {email.size_bytes} bytes, "
                f"{len(email.attachments)} attachment(s); not sent"
            )
        return report(dry_run_dir=directory, artifacts=tuple(f.path for f in files))

    # (3) Stage candidate, digest and pending bundle; push before SMTP.
    delivery_mode = "smtp" if deliver else "suppressed"
    context = _context(
        mode,
        started_at,
        dry_run,
        base_data_commit,
        delivery=delivery_mode,
        fetchers=fetchers,
        generated_at=generated_at,
    )
    store.write_candidate(candidate, context=context)
    if index_data is not None:
        store.stage_deadline_index(run.run_id, index_data)
    store.stage_rolling(run.run_id, [(f.path, f.content) for f in rolling])
    store.write_artifacts([(f.path, f.content) for f in files])
    artifacts = tuple(f.path for f in files)  # immutable per-run digest files
    if not deliver:
        _promote(store, run.run_id)
        session.commit(f"run {run.run_id}: promoted; email suppressed (--no-email)")
        messages.append("email suppressed (--no-email): digest archived, no outbox entry")
        return report(promoted=True, delivery="suppressed", artifacts=artifacts)

    assert email is not None
    recipients = normalize_recipients(services.recipients)
    header = PendingDelivery(
        delivery_id=run.run_id,
        run_id=run.run_id,
        message_id=make_message_id(run.run_id, services.message_id_domain),
        base_data_commit=base_data_commit or "local",
        code_revision=code_revision,
        config_hash=chash,
        digest_paths=artifacts,
        recipient_set_sha256=recipient_set_sha256(recipients),
        recipient_count=len(recipients),
    )
    session.outbox.create(header, email)
    session.commit(f"run {run.run_id}: pending delivery staged before SMTP")

    # (4)-(6) Send, then promote and mark delivered only on full acceptance.
    outcome = deliver_pending(session, run.run_id, services, now)
    messages.append(
        f"delivery {run.run_id} (attempt {outcome.attempts}): {outcome.summary}; "
        f"{'delivered' if outcome.delivered else 'pending; the next run retries it first'}"
    )
    return report(
        promoted=outcome.delivered,
        delivery="delivered" if outcome.delivered else "pending",
        delivery_id=run.run_id,
        artifacts=artifacts,
        exit_override=None if outcome.delivered else EXIT_DELIVERY_PENDING,
    )


def abandon_delivery(
    data_dir: Path,
    delivery_id: str,
    *,
    reason: str,
    now: Callable[[], str] = runner.utc_now,
    publisher: Publisher | None = None,
) -> None:
    """Give up on a stuck pending delivery: archive the bundle, discard its candidate.

    The catalog stays at the candidate's base state, so the next run detects the
    same changes again and sends a fresh digest.
    """

    head = publisher.checkout_data() if publisher is not None else None
    store = DataStore(Path(data_dir))
    session = _Session(store=store, outbox=Outbox(store.root), publisher=publisher, head=head)
    if delivery_id not in session.outbox.pending_ids():
        raise OutboxError(f"{delivery_id} is not a pending delivery")
    at = now()
    session.outbox.abandon(delivery_id, reason=reason, at=at)
    if store.run_path(delivery_id).exists() and not store.is_promoted(delivery_id):
        store.discard_candidate(delivery_id, reason=f"delivery abandoned: {reason}", at=at)
    session.commit(f"run {delivery_id}: pending delivery abandoned by operator")


def pending_deliveries(data_dir: Path) -> list[PendingDelivery]:
    store = DataStore(Path(data_dir))
    outbox = Outbox(store.root)
    return [outbox.load_header(i) for i in outbox.pending_ids()]
