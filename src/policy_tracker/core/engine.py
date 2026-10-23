"""Pure per-feed state/diff engine (Step 2).

``apply_observation`` folds one adapter observation into a ``CatalogState`` and
returns the new state, the semantic events, and the feed's run record. It never
mutates its inputs, performs no I/O, and is deterministic: the same base state
and observation always yield the same state and the same event ids.

Semantics are documented in ``docs/STATE_ENGINE.md``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from policy_tracker.core.contracts import FeedContract, RunContext
from policy_tracker.core.identity import (
    appearance_key,
    canonical_url,
    entity_key,
    stable_hash,
)
from policy_tracker.core.models import (
    Appearance,
    BaselineStatus,
    CatalogState,
    Discovery,
    Document,
    EntityKind,
    EnumerationStatus,
    Event,
    EventType,
    FeedRun,
    FeedState,
    FetchStatus,
    Matter,
    ParseStatus,
    Provenance,
    RecordStatus,
)
from policy_tracker.core.observations import FeedObservation, ObservedDocument, ObservedMatter

# Quarantine reasons recorded on ``FeedRun.quarantine_reasons``.
ADAPTER_FAILED = "adapter_failed"
FETCH_FAILED = "fetch_failed"
PARSE_INVALID = "parse_invalid"
BLOCK_PAGE = "block_page"
UNEXPECTED_MIME = "unexpected_mime_type"
ROW_COUNT_COLLAPSE = "row_count_collapse"
UNEXPECTED_EMPTY = "unexpected_empty"
STALE_OBSERVATION = "stale_observation"

#: Consecutive complete authoritative enumerations an item must be missing from.
REMOVAL_THRESHOLD = 2

_EVENT_ORDER = {event_type: index for index, event_type in enumerate(EventType)}
_NONE = "\x00"  # distinguishes None from "" inside fingerprints


@dataclass(frozen=True)
class FeedOutcome:
    """Result of applying one feed observation."""

    state: CatalogState  # new state; input state never mutated
    events: tuple[Event, ...]
    feed_run: FeedRun


# ---------------------------------------------------------------------------
# Public helpers


def ensure_feed_state(state: CatalogState, contract: FeedContract) -> CatalogState:
    """Return ``state`` with a ``FeedState`` for the contract's feed.

    A new feed starts ``NOT_STARTED`` at the contract's parser version, so its
    first runs baseline silently. An existing feed's ``coverage_revision`` is
    synced to the contract; a bump there is what triggers backfill.
    """

    feed = state.feeds.get(contract.feed_id)
    if feed is None:
        feed = FeedState(
            feed_id=contract.feed_id,
            baseline_status=BaselineStatus.NOT_STARTED,
            coverage_revision=contract.coverage_revision,
            baselined_revision=None,
            parser_version=contract.parser_version,
        )
    elif feed.coverage_revision != contract.coverage_revision:
        feed = replace(feed, coverage_revision=contract.coverage_revision)
    else:
        return state
    feeds = dict(state.feeds)
    feeds[contract.feed_id] = feed
    return replace(state, feeds=feeds)


def rebaseline_feed(state: CatalogState, feed_id: str, *, reason: str) -> CatalogState:
    """Reset one feed's baseline so its next complete accepted run is silent.

    Records and appearances are kept. ``last_item_count`` is cleared so a
    legitimate shrink (e.g. after a site redesign) is not quarantined as a
    row-count collapse. ``reason`` is required for the operator's audit trail;
    the frozen ``FeedState`` has no field for it, so callers must log it.
    """

    if not reason.strip():
        raise ValueError("rebaseline_feed requires a non-empty reason")
    if feed_id not in state.feeds:
        raise KeyError(f"unknown feed {feed_id!r}")
    feeds = dict(state.feeds)
    feeds[feed_id] = replace(
        state.feeds[feed_id],
        baseline_status=BaselineStatus.NOT_STARTED,
        baselined_revision=None,
        last_item_count=None,
    )
    return replace(state, feeds=feeds)


def normalize_space(value: str | None) -> str | None:
    """Collapse runs of whitespace and strip; ``None`` stays ``None``."""

    if value is None:
        return None
    return " ".join(value.split())


def document_fingerprint(
    *,
    title: str,
    published_raw: str | None,
    published_date: str | None,
    metadata: Mapping[str, str],
    matter_key: str | None,
) -> str:
    """Stable hash of a document's normalized listing metadata."""

    pairs = [f"{key}={normalize_space(value)}" for key, value in sorted(metadata.items())]
    return stable_hash(
        normalize_space(title) or "",
        _opt(normalize_space(published_raw)),
        _opt(published_date),
        "\x1e".join(pairs),
        _opt(matter_key),
    )


def owner_rank(
    appearance: Appearance, is_authoritative: Callable[[str], bool] | None = None
) -> tuple[bool, bool, str, str]:
    """Sort key of the owner rule; the smallest key owns the record.

    Order: present appearances first, then (when ``is_authoritative`` is
    given) appearances on feeds with removal authority before rolling/recent
    feeds, then earliest ``first_seen_at``, then feed id. With
    ``is_authoritative=None`` every feed ranks as authoritative (the legacy
    rule), which is still consistent across feeds but lets a rolling feed that
    saw a record first keep it.
    """

    rolling = is_authoritative is not None and not is_authoritative(appearance.feed_id)
    return (not appearance.present, rolling, appearance.first_seen_at, appearance.feed_id)


def owning_appearance(
    appearances: Iterable[Appearance], is_authoritative: Callable[[str], bool] | None = None
) -> Appearance | None:
    """The owning appearance among one record's appearances (``None`` if empty)."""

    candidates = list(appearances)
    if not candidates:
        return None
    return min(candidates, key=lambda a: owner_rank(a, is_authoritative))


# ---------------------------------------------------------------------------
# Main entry point


def apply_observation(
    state: CatalogState,
    observation: FeedObservation | None,
    contract: FeedContract,
    run: RunContext,
    *,
    authoritative_feeds: frozenset[str] | None = None,
) -> FeedOutcome:
    """Fold one feed observation into ``state``.

    ``observation=None`` means the adapter could not run: a failed run is
    recorded and only ``consecutive_failures`` changes.

    ``authoritative_feeds`` is the set of feed ids whose contracts have
    ``removal_authority=True``. Operations should pass it on every call (the
    same set for every feed of a run) so that the owner rule ranks
    authoritative feeds above rolling/recent feeds: a home bulletin or What's
    New sighting that precedes the authoritative listing then does not own the
    record, and the authoritative feed's title, dates, metadata, and
    corrections apply. The current contract's own ``removal_authority`` is
    always used for the current feed. With ``None`` (the default) the
    authority term is ignored for every feed and the legacy rule applies
    (present, first seen, feed id); this keeps ownership consistent across
    feeds, but a rolling feed that saw a record first owns it.
    """

    if observation is not None:
        if observation.feed_id != contract.feed_id:
            raise ValueError(
                f"observation feed {observation.feed_id!r} != contract {contract.feed_id!r}"
            )
        if observation.site_id != contract.site_id:
            raise ValueError(
                f"observation site {observation.site_id!r} != contract {contract.site_id!r}"
            )

    state = ensure_feed_state(state, contract)
    feed = state.feeds[contract.feed_id]

    if observation is None:
        return _reject(
            state,
            feed,
            contract,
            run,
            observation=None,
            item_count=0,
            reasons=(ADAPTER_FAILED,),
            diagnostics=(),
        )

    batch = _collect(observation, contract)
    latest_seen = _latest_feed_sighting(state, contract.feed_id)
    reasons = _quarantine_reasons(observation, contract, feed, batch.item_count, latest_seen)
    if reasons:
        return _reject(
            state,
            feed,
            contract,
            run,
            observation=observation,
            item_count=batch.item_count,
            reasons=reasons,
            diagnostics=batch.diagnostics,
        )
    return _Apply(
        state, feed, observation, contract, run, batch, latest_seen, authoritative_feeds
    ).run()


# ---------------------------------------------------------------------------
# Sighting collection (identity + in-observation dedupe)


@dataclass
class _MatterSighting:
    key: str
    canonical: str | None
    observed: ObservedMatter


@dataclass
class _DocumentSighting:
    key: str
    canonical: str
    observed: ObservedDocument
    hrefs: list[str] = field(default_factory=list)


@dataclass
class _Batch:
    matters: dict[str, _MatterSighting]
    documents: dict[str, _DocumentSighting]
    diagnostics: tuple[str, ...]

    @property
    def item_count(self) -> int:
        return len(self.matters) + len(self.documents)


def _collect(observation: FeedObservation, contract: FeedContract) -> _Batch:
    """Key every observed record; duplicates collapse with first provenance winning."""

    site = observation.site_id
    matters: dict[str, _MatterSighting] = {}
    documents: dict[str, _DocumentSighting] = {}
    duplicates = 0
    for observed_matter in observation.matters:
        canonical = (
            canonical_url(
                observed_matter.url, base_url=contract.base_url, rules=contract.host_rules
            )
            if observed_matter.url
            else None
        )
        key = entity_key(
            site,
            EntityKind.MATTER,
            native_id=observed_matter.native_id,
            canonical=canonical,
            provenance=observed_matter.provenance,
        )
        if key in matters:
            duplicates += 1
            continue
        matters[key] = _MatterSighting(key, canonical, observed_matter)
    for observed_doc in observation.documents:
        canonical = canonical_url(
            observed_doc.url, base_url=contract.base_url, rules=contract.host_rules
        )
        key = entity_key(
            site,
            EntityKind.DOCUMENT,
            native_id=observed_doc.native_id,
            canonical=canonical,
            provenance=observed_doc.provenance,
        )
        sighting = documents.get(key)
        if sighting is None:
            sighting = documents[key] = _DocumentSighting(key, canonical, observed_doc)
        else:
            duplicates += 1
        if observed_doc.url not in sighting.hrefs:
            sighting.hrefs.append(observed_doc.url)
    diagnostics = tuple(observation.evidence.diagnostics)
    if duplicates:
        diagnostics += (f"collapsed {duplicates} duplicate sighting(s) within the observation",)
    return _Batch(matters, documents, diagnostics)


# ---------------------------------------------------------------------------
# Quarantine and rejected runs


def _quarantine_reasons(
    observation: FeedObservation,
    contract: FeedContract,
    feed: FeedState,
    item_count: int,
    latest_seen: str | None,
) -> tuple[str, ...]:
    evidence = observation.evidence
    reasons: list[str] = []
    if not evidence.fetch_ok:
        reasons.append(FETCH_FAILED)
    if not evidence.parse_valid:
        reasons.append(PARSE_INVALID)
    if evidence.block_page_detected:
        reasons.append(BLOCK_PAGE)
    if evidence.unexpected_mime_types:
        reasons.append(UNEXPECTED_MIME)
    last = feed.last_item_count
    if last and contract.removal_authority and item_count == 0:
        reasons.append(UNEXPECTED_EMPTY)
    elif (
        last
        and evidence.enumeration_complete
        and item_count < contract.row_count_collapse_threshold * last
    ):
        reasons.append(ROW_COUNT_COLLAPSE)
    if latest_seen is not None and observation.observed_at < latest_seen:
        reasons.append(STALE_OBSERVATION)
    return tuple(reasons)


def _reject(
    state: CatalogState,
    feed: FeedState,
    contract: FeedContract,
    run: RunContext,
    *,
    observation: FeedObservation | None,
    item_count: int,
    reasons: tuple[str, ...],
    diagnostics: tuple[str, ...],
) -> FeedOutcome:
    feeds = dict(state.feeds)
    feeds[feed.feed_id] = replace(feed, consecutive_failures=feed.consecutive_failures + 1)
    new_state = replace(state, feeds=feeds)
    if observation is None:
        fetch, parse, enumeration = (
            FetchStatus.FAILED,
            ParseStatus.INVALID,
            EnumerationStatus.UNKNOWN,
        )
        finished_at = run.started_at
        parser_version = contract.parser_version
    else:
        evidence = observation.evidence
        fetch = FetchStatus.OK if evidence.fetch_ok else FetchStatus.FAILED
        parse = _parse_status(observation)
        usable = evidence.fetch_ok and parse is ParseStatus.VALID
        enumeration = _enumeration_status(observation) if usable else EnumerationStatus.UNKNOWN
        finished_at = observation.observed_at
        parser_version = observation.parser_version
        if evidence.unexpected_mime_types:
            mimes = ", ".join(evidence.unexpected_mime_types)
            diagnostics += (f"unexpected MIME types: {mimes}",)
    feed_run = FeedRun(
        run_id=run.run_id,
        feed_id=feed.feed_id,
        started_at=run.started_at,
        finished_at=finished_at,
        fetch_status=fetch,
        parse_status=parse,
        enumeration_status=enumeration,
        accepted=False,
        item_count=item_count,
        previous_item_count=feed.last_item_count,
        coverage_revision=contract.coverage_revision,
        parser_version=parser_version,
        config_hash=run.config_hash,
        code_revision=run.code_revision,
        quarantine_reasons=reasons,
        diagnostics=diagnostics,
    )
    return FeedOutcome(state=new_state, events=(), feed_run=feed_run)


def _parse_status(observation: FeedObservation) -> ParseStatus:
    evidence = observation.evidence
    if evidence.block_page_detected:
        return ParseStatus.BLOCKED
    if not evidence.parse_valid or not evidence.fetch_ok:
        return ParseStatus.INVALID
    return ParseStatus.VALID


def _enumeration_status(observation: FeedObservation) -> EnumerationStatus:
    if observation.evidence.enumeration_complete:
        return EnumerationStatus.COMPLETE
    return EnumerationStatus.PARTIAL


def _latest_feed_sighting(state: CatalogState, feed_id: str) -> str | None:
    latest: str | None = None
    for appearance in state.appearances.values():
        if appearance.feed_id == feed_id and (latest is None or appearance.last_seen_at > latest):
            latest = appearance.last_seen_at
    return latest


# ---------------------------------------------------------------------------
# Accepted runs


class _Apply:
    """Mutable working copy for one accepted observation; inputs stay untouched."""

    def __init__(
        self,
        state: CatalogState,
        feed: FeedState,
        observation: FeedObservation,
        contract: FeedContract,
        run: RunContext,
        batch: _Batch,
        latest_seen: str | None,
        authoritative_feeds: frozenset[str] | None = None,
    ) -> None:
        self.base = state
        self.authoritative_feeds = authoritative_feeds
        self.feed = feed
        self.obs = observation
        self.contract = contract
        self.run_ctx = run
        self.batch = batch
        self.now = observation.observed_at
        self.feed_id = contract.feed_id
        self.complete = observation.evidence.enumeration_complete

        self.silent = feed.baseline_status is not BaselineStatus.COMPLETE
        backfill = not self.silent and (
            feed.baselined_revision is None or contract.coverage_revision > feed.baselined_revision
        )
        if self.silent:
            self.new_discovery = Discovery.BASELINE
        elif backfill:
            self.new_discovery = Discovery.BACKFILL
        else:
            self.new_discovery = Discovery.LIVE
        self.migration = observation.parser_version != feed.parser_version
        # A replay of an already-applied snapshot must not count as another
        # complete enumeration (idempotency of removal counters).
        replay = feed.last_complete_run_id == run.run_id or (
            latest_seen is not None and self.now <= latest_seen
        )
        self.count_removals = self.complete and contract.removal_authority and not replay

        self.matters: dict[str, Matter] = dict(state.matters)
        self.documents: dict[str, Document] = dict(state.documents)
        self.appearances: dict[str, Appearance] = dict(state.appearances)
        self.by_entity: dict[tuple[EntityKind, str], list[str]] = {}
        for key, appearance in state.appearances.items():
            self.by_entity.setdefault((appearance.entity_kind, appearance.entity_key), []).append(
                key
            )
        self.events: dict[str, Event] = {}
        self.diagnostics = list(batch.diagnostics)
        if self.migration:
            self.diagnostics.append(
                f"parser migration {feed.parser_version} -> {observation.parser_version}: "
                "metadata and section changes applied silently"
            )

    # -- driver -----------------------------------------------------------

    def run(self) -> FeedOutcome:
        for sighting in self.batch.matters.values():
            self._apply_matter(sighting)
        for sighting in self.batch.documents.values():
            self._apply_document(sighting)
        if self.count_removals:
            self._count_missing()

        feeds = dict(self.base.feeds)
        feeds[self.feed_id] = self._next_feed_state()
        new_state = replace(
            self.base,
            matters=self.matters,
            documents=self.documents,
            appearances=self.appearances,
            feeds=feeds,
        )
        events = tuple(
            sorted(
                self.events.values(),
                key=lambda e: (
                    e.entity_kind.value,
                    e.entity_key,
                    _EVENT_ORDER[e.event_type],
                    e.event_id,
                ),
            )
        )
        feed_run = FeedRun(
            run_id=self.run_ctx.run_id,
            feed_id=self.feed_id,
            started_at=self.run_ctx.started_at,
            finished_at=self.now,
            fetch_status=FetchStatus.OK,
            parse_status=ParseStatus.VALID,
            enumeration_status=_enumeration_status(self.obs),
            accepted=True,
            item_count=self.batch.item_count,
            previous_item_count=self.feed.last_item_count,
            coverage_revision=self.contract.coverage_revision,
            parser_version=self.obs.parser_version,
            config_hash=self.run_ctx.config_hash,
            code_revision=self.run_ctx.code_revision,
            diagnostics=tuple(self.diagnostics),
        )
        return FeedOutcome(state=new_state, events=events, feed_run=feed_run)

    def _next_feed_state(self) -> FeedState:
        feed = replace(self.feed, consecutive_failures=0)
        if self.complete:
            return replace(
                feed,
                baseline_status=BaselineStatus.COMPLETE,
                baselined_revision=self.contract.coverage_revision,
                last_complete_run_id=self.run_ctx.run_id,
                last_item_count=self.batch.item_count,
                parser_version=self.obs.parser_version,
            )
        if feed.baseline_status is not BaselineStatus.COMPLETE:
            return replace(feed, baseline_status=BaselineStatus.PARTIAL)
        return feed

    # -- matters ----------------------------------------------------------

    def _apply_matter(self, sighting: _MatterSighting) -> None:
        observed = sighting.observed
        key = sighting.key
        title = normalize_space(observed.title) or ""
        metadata = _clean_metadata(observed.metadata)
        existing = self.matters.get(key)
        if existing is None:
            self.matters[key] = Matter(
                key=key,
                site_id=self.obs.site_id,
                native_id=observed.native_id,
                canonical_url=sighting.canonical,
                title=title,
                metadata=metadata,
                first_seen_at=self.now,
                last_seen_at=self.now,
                discovery=self.new_discovery,
                provenance=observed.provenance,
            )
            self._touch_appearance(EntityKind.MATTER, key, observed.provenance, None, new=True)
            self._emit(
                EventType.NEW_MATTER,
                EntityKind.MATTER,
                key,
                self.new_discovery,
                {
                    "title": title,
                    "canonical_url": sighting.canonical or "",
                    "page_url": observed.provenance.page_url,
                    **_prefixed("metadata.", metadata),
                },
            )
            return

        self._touch_appearance(EntityKind.MATTER, key, observed.provenance, None, new=False)
        matter = replace(existing, last_seen_at=max(existing.last_seen_at, self.now))
        changes: dict[str, tuple[str | None, str | None]] = {}
        if self._is_owner(EntityKind.MATTER, key):
            merged = self._owner_metadata(matter.metadata, metadata)
            _diff(changes, "title", matter.title, title)
            _diff(changes, "canonical_url", matter.canonical_url, sighting.canonical)
            _diff_metadata(changes, matter.metadata, merged)
            if changes:
                matter = replace(
                    matter, title=title, canonical_url=sighting.canonical, metadata=merged
                )
        else:
            filled = _fill_metadata(matter.metadata, metadata)
            _diff_metadata(changes, matter.metadata, filled)
            if changes:
                matter = replace(matter, metadata=filled)
        if changes:
            self._metadata_event(EntityKind.MATTER, key, changes)
        self.matters[key] = replace(matter, status=self._record_status(EntityKind.MATTER, key))

    # -- documents --------------------------------------------------------

    def _resolve_matter(self, native_id: str | None) -> str | None:
        """Matter key from explicit parent evidence only; unresolved stays None."""

        if not native_id:
            return None
        key = entity_key(
            self.obs.site_id,
            EntityKind.MATTER,
            native_id=native_id,
            canonical=None,
            provenance=None,
        )
        matter = self.matters.get(key)
        if matter is not None and matter.site_id == self.obs.site_id:
            return key
        return None

    def _apply_document(self, sighting: _DocumentSighting) -> None:
        observed = sighting.observed
        key = sighting.key
        title = normalize_space(observed.title) or ""
        metadata = _clean_metadata(observed.metadata)
        matter_key = self._resolve_matter(observed.matter_native_id)
        existing = self.documents.get(key)
        if existing is None:
            self.documents[key] = Document(
                key=key,
                site_id=self.obs.site_id,
                native_id=observed.native_id,
                canonical_url=sighting.canonical,
                original_urls=tuple(sighting.hrefs),
                title=title,
                matter_key=matter_key,
                published_date=observed.published_date,
                published_raw=observed.published_raw,
                metadata=metadata,
                metadata_fingerprint=document_fingerprint(
                    title=title,
                    published_raw=observed.published_raw,
                    published_date=observed.published_date,
                    metadata=metadata,
                    matter_key=matter_key,
                ),
                first_seen_at=self.now,
                last_seen_at=self.now,
                discovery=self.new_discovery,
                provenance=observed.provenance,
            )
            self._touch_appearance(
                EntityKind.DOCUMENT, key, observed.provenance, observed.section, new=True
            )
            self._emit(
                EventType.NEW_DOCUMENT,
                EntityKind.DOCUMENT,
                key,
                self.new_discovery,
                {
                    "title": title,
                    "canonical_url": sighting.canonical,
                    "published_date": observed.published_date or "",
                    "published_raw": observed.published_raw or "",
                    "matter_key": matter_key or "",
                    "section": observed.section or "",
                    "page_url": observed.provenance.page_url,
                    **_prefixed("metadata.", metadata),
                },
            )
            return

        self._touch_appearance(
            EntityKind.DOCUMENT, key, observed.provenance, observed.section, new=False
        )
        doc = existing
        hrefs = tuple(doc.original_urls) + tuple(
            href for href in sighting.hrefs if href not in doc.original_urls
        )
        doc = replace(doc, original_urls=hrefs, last_seen_at=max(doc.last_seen_at, self.now))

        changes: dict[str, tuple[str | None, str | None]] = {}
        owner = self._is_owner(EntityKind.DOCUMENT, key)
        # A sighting with no date at all (e.g. an archive table without a date
        # column) never clears a known date; only a different date changes it.
        undated = not observed.published_date and not normalize_space(observed.published_raw)
        published_date = doc.published_date if undated else observed.published_date
        published_raw = doc.published_raw if undated else observed.published_raw
        if owner:
            merged = self._owner_metadata(doc.metadata, metadata)
            _diff(changes, "title", doc.title, title)
            _diff(changes, "canonical_url", doc.canonical_url, sighting.canonical)
            _diff(changes, "published_date", doc.published_date, published_date)
            _diff(changes, "published_raw", doc.published_raw, published_raw)
            _diff_metadata(changes, doc.metadata, merged)
            if changes:
                doc = replace(
                    doc,
                    title=title,
                    canonical_url=sighting.canonical,
                    published_date=published_date,
                    published_raw=published_raw,
                    metadata=merged,
                )
        else:
            # A non-owner may fill gaps the owner has not filled: a date when the
            # record has no date evidence at all, and absent metadata keys. It
            # never overwrites a value, so feeds cannot flip-flop.
            stored_undated = not doc.published_date and not normalize_space(doc.published_raw)
            if stored_undated and not undated:
                _diff(changes, "published_date", doc.published_date, observed.published_date)
                _diff(changes, "published_raw", doc.published_raw, observed.published_raw)
                doc = replace(
                    doc,
                    published_date=observed.published_date,
                    published_raw=observed.published_raw,
                )
            filled = _fill_metadata(doc.metadata, metadata)
            _diff_metadata(changes, doc.metadata, filled)
            doc = replace(doc, metadata=filled)
        # matter_key: any feed may supply it once; only the owner may change it;
        # it is never cleared by a sighting that lacks parent evidence.
        if (
            matter_key is not None
            and matter_key != doc.matter_key
            and (doc.matter_key is None or owner)
        ):
            changes["matter_key"] = (doc.matter_key, matter_key)
            doc = replace(doc, matter_key=matter_key)
        if changes:
            doc = replace(
                doc,
                metadata_fingerprint=document_fingerprint(
                    title=doc.title,
                    published_raw=doc.published_raw,
                    published_date=doc.published_date,
                    metadata=doc.metadata,
                    matter_key=doc.matter_key,
                ),
            )
            self._metadata_event(EntityKind.DOCUMENT, key, changes)
        self.documents[key] = replace(doc, status=self._record_status(EntityKind.DOCUMENT, key))

    # -- appearances ------------------------------------------------------

    def _touch_appearance(
        self,
        kind: EntityKind,
        key: str,
        provenance: Provenance,
        section: str | None,
        *,
        new: bool,
    ) -> None:
        """Record this feed's sighting; emit appearance-level events."""

        akey = appearance_key(self.feed_id, kind, key)
        existing = self.appearances.get(akey)
        if existing is None:
            self.appearances[akey] = Appearance(
                key=akey,
                feed_id=self.feed_id,
                entity_kind=kind,
                entity_key=key,
                first_seen_at=self.now,
                last_seen_at=self.now,
                present=True,
                missing_complete_runs=0,
                section=section,
                provenance=provenance,
            )
            self.by_entity.setdefault((kind, key), []).append(akey)
            if not new:
                details = {"appearance_key": akey, "page_url": provenance.page_url}
                if section:
                    details["section"] = section
                self._emit(EventType.NEW_APPEARANCE, kind, key, self.new_discovery, details)
            return

        if not existing.present:
            self._emit(
                EventType.REAPPEARED,
                kind,
                key,
                Discovery.LIVE,
                {"appearance_key": akey, "previous_last_seen_at": existing.last_seen_at},
            )
        if section != existing.section and not self.migration:
            self._emit(
                EventType.STATUS_CHANGED,
                kind,
                key,
                Discovery.LIVE,
                {
                    "appearance_key": akey,
                    "section.old": existing.section or "",
                    "section.new": section or "",
                },
            )
        self.appearances[akey] = replace(
            existing,
            last_seen_at=max(existing.last_seen_at, self.now),
            present=True,
            missing_complete_runs=0,
            section=section,
            provenance=provenance,
        )

    def _count_missing(self) -> None:
        """Advance removal counters after a complete authoritative enumeration."""

        seen = {appearance_key(self.feed_id, EntityKind.MATTER, k) for k in self.batch.matters}
        seen |= {appearance_key(self.feed_id, EntityKind.DOCUMENT, k) for k in self.batch.documents}
        own = sorted(
            key
            for key, appearance in self.appearances.items()
            if appearance.feed_id == self.feed_id and key not in seen
        )
        for akey in own:
            appearance = self.appearances[akey]
            missing = appearance.missing_complete_runs + 1
            withdraw = appearance.present and missing >= REMOVAL_THRESHOLD
            appearance = replace(
                appearance,
                missing_complete_runs=missing,
                present=appearance.present and not withdraw,
            )
            self.appearances[akey] = appearance
            if not withdraw:
                continue
            kind, key = appearance.entity_kind, appearance.entity_key
            status = self._record_status(kind, key)
            self._set_record_status(kind, key, status)
            self._emit(
                EventType.WITHDRAWN,
                kind,
                key,
                Discovery.LIVE,
                {
                    "appearance_key": akey,
                    "last_seen_at": appearance.last_seen_at,
                    "missing_complete_runs": str(missing),
                    "record_status": status.value,
                },
            )

    # -- ownership and status ---------------------------------------------

    def _is_authoritative(self, feed_id: str) -> bool:
        if feed_id == self.feed_id:
            return self.contract.removal_authority
        assert self.authoritative_feeds is not None
        return feed_id in self.authoritative_feeds

    def _is_owner(self, kind: EntityKind, key: str) -> bool:
        """True when this feed owns the record (see ``owner_rank``).

        Only the owner may rewrite listing metadata, so two feeds that render
        the same record differently do not flip-flop METADATA_CHANGED events.
        """

        primary = owning_appearance(
            (self.appearances[a] for a in self.by_entity.get((kind, key), [])),
            None if self.authoritative_feeds is None else self._is_authoritative,
        )
        return primary is None or primary.feed_id == self.feed_id

    def _owner_metadata(
        self, stored: Mapping[str, str], observed: Mapping[str, str]
    ) -> dict[str, str]:
        """The owner's sighting sets the keys it carries and keeps keys it omits.

        An omitted key is absence of evidence (like an undated sighting), and
        it may have been filled by another feed; dropping it would let the two
        feeds flip-flop. A parser migration replaces the metadata wholesale
        (silently), so keys a parser no longer emits do not linger.
        """

        if self.migration:
            return dict(observed)
        return dict(sorted({**stored, **observed}.items()))

    def _record_status(self, kind: EntityKind, key: str) -> RecordStatus:
        present = any(self.appearances[a].present for a in self.by_entity.get((kind, key), []))
        return RecordStatus.ACTIVE if present else RecordStatus.WITHDRAWN

    def _set_record_status(self, kind: EntityKind, key: str, status: RecordStatus) -> None:
        if kind is EntityKind.MATTER and key in self.matters:
            self.matters[key] = replace(self.matters[key], status=status)
        elif kind is EntityKind.DOCUMENT and key in self.documents:
            self.documents[key] = replace(self.documents[key], status=status)

    # -- events -----------------------------------------------------------

    def _metadata_event(
        self, kind: EntityKind, key: str, changes: dict[str, tuple[str | None, str | None]]
    ) -> None:
        if self.migration:
            return
        details = {"changed_fields": ",".join(sorted(changes))}
        for name, (old, new) in changes.items():
            details[f"{name}.old"] = old or ""
            details[f"{name}.new"] = new or ""
        self._emit(EventType.METADATA_CHANGED, kind, key, Discovery.LIVE, details)

    def _emit(
        self,
        event_type: EventType,
        kind: EntityKind,
        key: str,
        discovery: Discovery,
        details: Mapping[str, str],
    ) -> None:
        if self.silent:
            return
        payload = json.dumps(dict(details), sort_keys=True, ensure_ascii=False)
        event_id = stable_hash(event_type.value, self.feed_id, kind.value, key, payload)
        self.events[event_id] = Event(
            event_id=event_id,
            event_type=event_type,
            run_id=self.run_ctx.run_id,
            feed_id=self.feed_id,
            entity_kind=kind,
            entity_key=key,
            occurred_at=self.now,
            discovery=discovery,
            coverage_revision=self.contract.coverage_revision,
            details=dict(sorted(details.items())),
        )


# ---------------------------------------------------------------------------
# Small pure helpers


def _opt(value: str | None) -> str:
    return _NONE if value is None else value


def _clean_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    return {key: normalize_space(value) or "" for key, value in sorted(metadata.items())}


def _fill_metadata(stored: Mapping[str, str], observed: Mapping[str, str]) -> dict[str, str]:
    """Stored metadata plus the observed keys it lacks (non-empty values only)."""

    filled = dict(stored)
    for name, value in observed.items():
        if name not in filled and value:
            filled[name] = value
    return dict(sorted(filled.items()))


def _prefixed(prefix: str, metadata: Mapping[str, str]) -> dict[str, str]:
    return {f"{prefix}{key}": value for key, value in metadata.items()}


def _diff(
    changes: dict[str, tuple[str | None, str | None]],
    name: str,
    old: str | None,
    new: str | None,
) -> None:
    """Record a change unless the values differ only in whitespace."""

    if normalize_space(old) != normalize_space(new):
        changes[name] = (old, new)


def _diff_metadata(
    changes: dict[str, tuple[str | None, str | None]],
    old: Mapping[str, str],
    new: Mapping[str, str],
) -> None:
    for name in sorted(set(old) | set(new)):
        _diff(changes, f"metadata.{name}", old.get(name), new.get(name))
