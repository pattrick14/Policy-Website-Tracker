"""Digest interface (frozen for Steps 6-7).

``policy_tracker.reporting.digest.build_digest`` turns a candidate state, its
events, and extracted text into a ``Digest``. Renderers turn a ``Digest`` into
archive files and an email; operations stores and sends those without
re-reading source data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum

from policy_tracker.core.models import Discovery, EventType


class Category(IntEnum):
    """Digest order (PROJECT_PLAN, classification and weekly presentation)."""

    ACTION_REQUIRED = 1
    RULES_AND_REGULATION = 2
    POLICY_AND_DIRECTION = 3
    RESEARCH_AND_REPORTING = 4
    ROUTINE = 5


@dataclass(frozen=True)
class Deadline:
    raw: str  # source text, verbatim
    date: str | None  # ISO date, or None when ambiguous
    time: str | None  # "HH:MM" IST, or None for "time not stated"
    sort_key: str  # ISO datetime; 23:59 IST when no time is stated
    evidence: str  # sentence or field the deadline came from
    method: str  # e.g. "title_regex", "text_regex", "listing_field"
    ambiguous: bool = False


@dataclass(frozen=True)
class DigestItem:
    event_id: str
    event_type: EventType
    discovery: Discovery
    site_id: str
    feed_id: str
    entity_key: str
    title: str
    url: str | None
    published_date: str | None
    published_raw: str | None
    first_seen_at: str
    category: Category
    reasons: tuple[str, ...]  # matched classification rules
    deadlines: tuple[Deadline, ...] = ()
    deadline_changed: bool = False
    warnings: tuple[str, ...] = ()  # e.g. hindi_only, scanned, extraction_failed
    details: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedHealth:
    feed_id: str
    site_id: str
    accepted: bool
    enumeration_status: str
    item_count: int
    quarantine_reasons: tuple[str, ...] = ()
    consecutive_failures: int = 0
    baseline_status: str = ""


@dataclass(frozen=True)
class Digest:
    run_id: str
    generated_at: str
    period_label: str  # e.g. "Week of 26 Oct 2026"
    partial: bool  # any feed failed or was incomplete
    items: tuple[DigestItem, ...]  # sorted: category, then deadline, then title
    health: tuple[FeedHealth, ...]
    open_deadlines: tuple[DigestItem, ...]  # complete current view for open_deadlines.csv
    gap_warning: str | None = None  # set when the previous run is over ten days old
    notes: tuple[str, ...] = ()  # coverage initialization, known limits (D-008)


@dataclass(frozen=True)
class RenderedFile:
    path: str  # relative to tracker-data, e.g. "digests/2026/10/<run_id>.md"
    content: bytes
    media_type: str


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text_body: str
    html_body: str
    attachments: tuple[RenderedFile, ...] = ()  # CSV only when the total stays under 5 MiB
    size_bytes: int = 0
