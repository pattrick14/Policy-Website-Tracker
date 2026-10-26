"""Build the weekly ``Digest`` from a candidate state (PROJECT_PLAN Step 6).

``build_digest`` is pure: it reads the candidate, the promoted state it was
built on, and this run's extracted text, and returns the digest plus the new
deadline index for operations to persist. Baseline discoveries are never shown
(D-006); backfill items are labelled as newly covered history; deadlines are
reported only when discovered or changed (D-007).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from policy_tracker.core.engine import owning_appearance
from policy_tracker.core.identity import stable_hash
from policy_tracker.core.models import (
    Appearance,
    BaselineStatus,
    CandidateState,
    CatalogState,
    Discovery,
    Document,
    EntityKind,
    EnumerationStatus,
    Event,
    EventType,
    Matter,
    RecordStatus,
)
from policy_tracker.documents.types import ExtractedText, ExtractionStatus
from policy_tracker.reporting import deadlines as dl
from policy_tracker.reporting.classify import FALLBACK_REASON, Taxonomy, classify, load_taxonomy
from policy_tracker.reporting.types import Category, Deadline, Digest, DigestItem, FeedHealth

GAP_DAYS = 10
BACKFILL_LABEL = "Backfill: newly covered history"
SILENT_REPLACEMENT_NOTE = (
    "Same-URL file replacements are detected from server validators (ETag, Last-Modified, "
    "size) and listing metadata after the first download. A server can replace a file "
    "without changing these, and such a replacement is not detected (D-008)."
)

# Most significant first; one item per entity shows the highest-ranked event.
EVENT_RANK: dict[EventType, int] = {
    EventType.NEW_DOCUMENT: 0,
    EventType.NEW_MATTER: 1,
    EventType.CONTENT_REPLACED: 2,
    EventType.DEADLINE_CHANGED: 3,
    EventType.METADATA_CHANGED: 4,
    EventType.REAPPEARED: 5,
    EventType.WITHDRAWN: 6,
    EventType.STATUS_CHANGED: 7,
    EventType.NEW_APPEARANCE: 8,
}

_NEW_RECORD_EVENTS = frozenset({EventType.NEW_DOCUMENT, EventType.NEW_MATTER})

SECTIONS: tuple[tuple[str, str], ...] = (
    ("action", "Action required and deadline changes"),
    ("law_policy", "Law and policy"),
    ("research", "Reports and research"),
    ("other", "Other updates"),
    ("routine", "Routine appendix"),
)

_STATUS_WARNINGS: dict[str, str] = {
    ExtractionStatus.HINDI_ONLY.value: "hindi_only",
    ExtractionStatus.SCANNED.value: "scanned",
    ExtractionStatus.FAILED.value: "extraction_failed",
    ExtractionStatus.UNSUPPORTED.value: "unsupported_format",
    ExtractionStatus.PARTIAL.value: "extraction_partial",
    ExtractionStatus.OCR.value: "ocr_text",
}
_PASSTHROUGH_WARNINGS = frozenset({"ocr_unavailable", "ocr_failed", "hindi_only", "scanned"})


def section_of(item: DigestItem) -> str:
    """Presentation section key (see ``SECTIONS``)."""

    if item.category is Category.ACTION_REQUIRED or item.deadline_changed:
        return "action"
    if item.category in (Category.RULES_AND_REGULATION, Category.POLICY_AND_DIRECTION):
        return "law_policy"
    if item.category is Category.RESEARCH_AND_REPORTING:
        return "research"
    return "other" if FALLBACK_REASON in item.reasons else "routine"


def parse_time(value: str) -> datetime:
    """Parse an ISO 8601 timestamp; naive values are taken as UTC."""

    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def period_label(generated_at: str) -> str:
    """ "Week of <Monday>" in IST for the run time."""

    local = parse_time(generated_at).astimezone(dl.IST).date()
    monday = local - timedelta(days=local.weekday())
    return f"Week of {monday.day} {monday:%b %Y}"


def _record(state: CatalogState, kind: EntityKind | None, key: str) -> Document | Matter | None:
    if kind is EntityKind.MATTER:
        return state.matters.get(key) or state.documents.get(key)
    return state.documents.get(key) or state.matters.get(key)


def _warnings(state: CatalogState, key: str, texts: Mapping[str, ExtractedText]) -> list[str]:
    out: list[str] = []
    text = texts.get(key)
    if text is not None:
        status = text.status.value
        out.extend(w for w in text.warnings if w in _PASSTHROUGH_WARNINGS)
    else:
        versions = state.versions.get(key, ())
        status = versions[-1].extraction_status or "" if versions else ""
    if status in _STATUS_WARNINGS:
        out.insert(0, _STATUS_WARNINGS[status])
    return sorted(set(out), key=out.index)


def _classify_text(record: Document | Matter | None, text: ExtractedText | None) -> str:
    parts = [] if record is None else [f"{k}: {v}" for k, v in sorted(record.metadata.items())]
    if text is not None and text.text:
        parts.append(text.text)
    return "\n".join(parts)


def build_deadline_index(
    state: CatalogState,
    texts: Mapping[str, ExtractedText],
    previous: Mapping[str, tuple[Deadline, ...]] | None,
) -> dict[str, tuple[Deadline, ...]]:
    """Current deadlines for every record; text deadlines carry over until re-extracted."""

    previous = previous or {}
    index: dict[str, tuple[Deadline, ...]] = {}
    records: list[Document | Matter] = [*state.matters.values(), *state.documents.values()]
    for record in records:
        text = texts.get(record.key)
        if text is not None and text.text:
            from_text = dl.extract_deadlines(text.text, method="text_regex")
        else:
            from_text = tuple(
                d for d in previous.get(record.key, ()) if d.method.startswith("text_regex")
            )
        merged = dl.merge(
            dl.extract_field_deadlines(record.metadata),
            dl.extract_deadlines(record.title, method="title_regex"),
            from_text,
        )
        if merged:
            index[record.key] = merged
    return dict(sorted(index.items()))


def _deadline_summary(deadlines: tuple[Deadline, ...]) -> str:
    return "; ".join(dl.display(d) for d in deadlines) if deadlines else "none"


def _earliest(item: DigestItem) -> str:
    keys = [d.sort_key for d in item.deadlines]
    return min(keys) if keys else "￿"


def _sort_key(item: DigestItem) -> tuple[int, str, str, str, str]:
    return (
        int(item.category),
        _earliest(item),
        item.title.casefold(),
        item.entity_key,
        item.event_id,
    )


def build_digest(
    candidate: CandidateState,
    previous: CatalogState,
    texts: Mapping[str, ExtractedText],
    *,
    generated_at: str,
    previous_run_at: str | None,
    previous_deadlines: Mapping[str, tuple[Deadline, ...]] | None = None,
    taxonomy: Taxonomy | None = None,
    authoritative_feeds: frozenset[str] | None = None,
) -> tuple[Digest, Mapping[str, tuple[Deadline, ...]]]:
    """Return the digest and the deadline index to persist for the next run.

    ``authoritative_feeds`` (feed ids with ``removal_authority``) makes the
    owning feed shown for synthesized rows follow the engine's owner rule,
    which ranks authoritative feeds above rolling ones; pass the same set given
    to ``engine.apply_observation``. ``None`` uses the legacy rule.
    """

    tax = taxonomy or load_taxonomy()
    state = candidate.state
    now = parse_time(generated_at)
    by_entity: dict[str, list[Appearance]] = {}
    for appearance in state.appearances.values():
        by_entity.setdefault(appearance.entity_key, []).append(appearance)
    live_feeds = {
        feed_id
        for feed_id, feed in previous.feeds.items()
        if feed.baseline_status is BaselineStatus.COMPLETE
    }

    index = build_deadline_index(state, texts, previous_deadlines)
    events = [e for e in candidate.events if e.discovery is not Discovery.BASELINE]
    grouped: dict[str, list[Event]] = {}
    for event in events:
        grouped.setdefault(event.entity_key, []).append(event)

    # A deadline change is reported for records that existed before this run, on a live
    # feed; deadlines of records new this run are shown as discovered, not as changes.
    new_records = {
        key
        for key, entity_events in grouped.items()
        if any(e.event_type in _NEW_RECORD_EVENTS for e in entity_events)
    }
    reportable: set[str] = set()
    if previous_deadlines is not None:
        for key in dl.deadline_changes(previous_deadlines, index) - new_records:
            record = _record(state, None, key)
            on_live_feed = any(a.feed_id in live_feeds for a in by_entity.get(key, ()))
            known = key in previous_deadlines or key in grouped
            if (
                record is not None
                and on_live_feed
                and (known or record.discovery is not Discovery.BASELINE)
            ):
                reportable.add(key)

    for key in sorted(reportable - set(grouped)):
        record = _record(state, None, key)
        assert record is not None
        kind = EntityKind.MATTER if key in state.matters else EntityKind.DOCUMENT
        signature = repr(sorted(repr(dl.signature(d)) for d in index.get(key, ())))
        grouped[key] = [
            Event(
                event_id=stable_hash("deadline_changed", kind.value, key, signature),
                event_type=EventType.DEADLINE_CHANGED,
                run_id=candidate.run_id,
                feed_id=_owning_feed(by_entity.get(key, ()), authoritative_feeds),
                entity_kind=kind,
                entity_key=key,
                occurred_at=generated_at,
                discovery=Discovery.LIVE,
                coverage_revision=0,
            )
        ]

    items: list[DigestItem] = []
    for key, entity_events in grouped.items():
        ranked = sorted(
            entity_events,
            key=lambda e: (
                EVENT_RANK.get(e.event_type, 99),
                e.discovery is not Discovery.LIVE,
                e.event_id,
            ),
        )
        chosen = ranked[0]
        others = sorted({e.event_type.value for e in ranked[1:]} - {chosen.event_type.value})
        items.append(
            _item(
                state,
                chosen,
                texts,
                index,
                tax,
                previous_deadlines=previous_deadlines or {},
                deadline_changed=key in reportable,
                other_events=others,
                now=now,
            )
        )
    items.sort(key=_sort_key)

    open_items = _open_deadlines(
        state, texts, index, items, by_entity, tax, now, authoritative_feeds
    )
    digest = Digest(
        run_id=candidate.run_id,
        generated_at=generated_at,
        period_label=period_label(generated_at),
        partial=False,
        items=tuple(items),
        health=(),
        open_deadlines=open_items,
        gap_warning=_gap_warning(now, previous_run_at),
        notes=_notes(candidate, previous, previous_deadlines is None),
    )
    health = _health(candidate, by_entity, tax)
    partial = any(
        not h.accepted or h.enumeration_status != EnumerationStatus.COMPLETE.value for h in health
    )
    return dataclasses.replace(digest, health=health, partial=partial), index


def _owning_feed(
    appearances: list[Appearance] | tuple[Appearance, ...],
    authoritative_feeds: frozenset[str] | None = None,
) -> str:
    """Owning feed id per ``engine.owner_rank``; ``""`` when there is no appearance."""

    owner = owning_appearance(
        appearances,
        None if authoritative_feeds is None else authoritative_feeds.__contains__,
    )
    return owner.feed_id if owner is not None else ""


def _item(
    state: CatalogState,
    event: Event,
    texts: Mapping[str, ExtractedText],
    index: Mapping[str, tuple[Deadline, ...]],
    tax: Taxonomy,
    *,
    previous_deadlines: Mapping[str, tuple[Deadline, ...]],
    deadline_changed: bool,
    other_events: list[str],
    now: datetime,
) -> DigestItem:
    key = event.entity_key
    record = _record(state, event.entity_kind, key)
    title = record.title if record is not None else event.details.get("title", key)
    site_id = record.site_id if record is not None else tax.site_for_feed(event.feed_id)
    category, reasons = classify(
        title,
        _classify_text(record, texts.get(key)),
        event.feed_id,
        site_id,
        event.event_type,
        taxonomy=tax,
    )
    deadlines = index.get(key, ())
    if deadline_changed and category is not Category.ACTION_REQUIRED:
        category, reasons = Category.ACTION_REQUIRED, (*reasons, "deadline:changed")
    elif any(dl.is_open(d, now) for d in deadlines) and category not in (
        Category.ACTION_REQUIRED,
        Category.ROUTINE,
    ):
        category, reasons = Category.ACTION_REQUIRED, (*reasons, "deadline:found")
    details = dict(event.details)
    if event.discovery is Discovery.BACKFILL:
        details["discovery_label"] = BACKFILL_LABEL
    if other_events:
        details["other_events"] = ",".join(other_events)
    if deadline_changed:
        details["previous_deadlines"] = _deadline_summary(previous_deadlines.get(key, ()))
    document = record if isinstance(record, Document) else None
    url = _display_url(record, texts.get(key))
    return DigestItem(
        event_id=event.event_id,
        event_type=event.event_type,
        discovery=event.discovery,
        site_id=site_id,
        feed_id=event.feed_id,
        entity_key=key,
        title=title,
        url=url,
        published_date=document.published_date if document else None,
        published_raw=document.published_raw if document else None,
        first_seen_at=record.first_seen_at if record is not None else event.occurred_at,
        category=category,
        reasons=reasons,
        deadlines=deadlines,
        deadline_changed=deadline_changed,
        warnings=tuple(_warnings(state, key, texts)),
        details=dict(sorted(details.items())),
    )


def _display_url(record: Matter | Document | None, text: ExtractedText | None) -> str | None:
    """The file enrichment resolved (e.g. a WordPress attachment record), else the canonical URL."""

    if record is None:
        return None
    for warning in text.warnings if text is not None else ():
        if warning.startswith("resolved:"):
            return warning.removeprefix("resolved:")
    return record.canonical_url


def _open_deadlines(
    state: CatalogState,
    texts: Mapping[str, ExtractedText],
    index: Mapping[str, tuple[Deadline, ...]],
    items: list[DigestItem],
    by_entity: Mapping[str, list[Appearance]],
    tax: Taxonomy,
    now: datetime,
    authoritative_feeds: frozenset[str] | None = None,
) -> tuple[DigestItem, ...]:
    shown = {item.entity_key: item for item in items}
    out: list[DigestItem] = []
    for key, deadlines in index.items():
        upcoming = tuple(d for d in deadlines if dl.is_open(d, now))
        record = _record(state, None, key)
        if not upcoming or record is None or record.status is not RecordStatus.ACTIVE:
            continue
        kind = EntityKind.MATTER if key in state.matters else EntityKind.DOCUMENT
        feed_id = _owning_feed(by_entity.get(key, ()), authoritative_feeds)
        # The complete current view is classified from the record alone, never from
        # this week's event, so identical reruns produce an identical CSV.
        category, reasons = classify(
            record.title,
            _classify_text(record, texts.get(key)),
            feed_id,
            record.site_id,
            taxonomy=tax,
        )
        if category not in (Category.ACTION_REQUIRED, Category.ROUTINE):
            category, reasons = Category.ACTION_REQUIRED, (*reasons, "deadline:found")
        if key in shown:
            out.append(
                dataclasses.replace(
                    shown[key], deadlines=upcoming, category=category, reasons=reasons
                )
            )
            continue
        document = record if isinstance(record, Document) else None
        out.append(
            DigestItem(
                event_id="",
                event_type=EventType.NEW_MATTER
                if kind is EntityKind.MATTER
                else EventType.NEW_DOCUMENT,
                discovery=record.discovery,
                site_id=record.site_id,
                feed_id=feed_id,
                entity_key=key,
                title=record.title,
                url=_display_url(record, texts.get(key)),
                published_date=document.published_date if document else None,
                published_raw=document.published_raw if document else None,
                first_seen_at=record.first_seen_at,
                category=category,
                reasons=reasons,
                deadlines=upcoming,
                warnings=tuple(_warnings(state, key, texts)),
                details={"row_kind": "open_deadline"},
            )
        )
    out.sort(key=lambda i: (_earliest(i), i.title.casefold(), i.entity_key))
    return tuple(out)


def _health(
    candidate: CandidateState, by_entity: Mapping[str, list[Appearance]], tax: Taxonomy
) -> tuple[FeedHealth, ...]:
    state = candidate.state
    feed_sites: dict[str, str] = {}
    for key, appearances in by_entity.items():
        record = _record(state, None, key)
        if record is None:
            continue
        for appearance in appearances:
            feed_sites.setdefault(appearance.feed_id, record.site_id)
    runs = {run.feed_id: run for run in candidate.feed_runs}
    out: list[FeedHealth] = []
    for feed_id in sorted(set(runs) | set(state.feeds)):
        feed = state.feeds.get(feed_id)
        site = tax.site_for_feed(feed_id) or feed_sites.get(feed_id, "")
        run = runs.get(feed_id)
        baseline = feed.baseline_status.value if feed else ""
        failures = feed.consecutive_failures if feed else 0
        if run is None:
            out.append(
                FeedHealth(
                    feed_id=feed_id,
                    site_id=site,
                    accepted=False,
                    enumeration_status="not_run",
                    item_count=(feed.last_item_count or 0) if feed else 0,
                    quarantine_reasons=("not_run",),
                    consecutive_failures=failures,
                    baseline_status=baseline,
                )
            )
            continue
        out.append(
            FeedHealth(
                feed_id=feed_id,
                site_id=site,
                accepted=run.accepted,
                enumeration_status=run.enumeration_status.value,
                item_count=run.item_count,
                quarantine_reasons=tuple(run.quarantine_reasons),
                consecutive_failures=failures,
                baseline_status=baseline,
            )
        )
    return tuple(out)


def _gap_warning(now: datetime, previous_run_at: str | None) -> str | None:
    if not previous_run_at:
        return None
    gap = now - parse_time(previous_run_at)
    if gap <= timedelta(days=GAP_DAYS):
        return None
    return (
        f"The previous run was {gap.days} days ago ({previous_run_at}). This digest covers "
        "everything found since then, but items that appeared and left rolling feeds in the "
        "gap may have been missed."
    )


def _notes(candidate: CandidateState, previous: CatalogState, first_index: bool) -> tuple[str, ...]:
    notes: list[str] = []
    for feed_id in sorted(candidate.state.feeds):
        feed = candidate.state.feeds[feed_id]
        before = previous.feeds.get(feed_id)
        complete = feed.baseline_status is BaselineStatus.COMPLETE
        was_complete = before is not None and before.baseline_status is BaselineStatus.COMPLETE
        if complete and not was_complete:
            count = feed.last_item_count
            counted = f"{count} existing records were" if count is not None else "records were"
            notes.append(
                f"Coverage initialized for {feed_id}: {counted} recorded silently as the "
                "baseline and are not reported as new."
            )
        elif feed.baseline_status is BaselineStatus.PARTIAL:
            notes.append(
                f"Baseline for {feed_id} is still incomplete; its records are recorded silently "
                "until a complete enumeration succeeds."
            )
        elif (
            complete
            and was_complete
            and before is not None
            and (feed.baselined_revision or 0) > (before.baselined_revision or 0)
        ):
            notes.append(
                f"Coverage for {feed_id} expanded to revision {feed.baselined_revision}; "
                f'newly covered history is labelled "{BACKFILL_LABEL}".'
            )
    if first_index:
        notes.append(
            "Deadline index initialized this run: deadlines already open are listed in "
            "open_deadlines.csv and are not reported as changes."
        )
    notes.append(SILENT_REPLACEMENT_NOTE)
    return tuple(notes)
