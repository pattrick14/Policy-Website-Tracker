"""Versioned state, event, and run records (Step 2 frozen contract).

Every record is an immutable dataclass. Serialized forms carry
``SCHEMA_VERSION``; changing a field requires a new version and a migration in
``policy_tracker.core.migrations``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


SCHEMA_VERSION = 1


class FeedKind(StrEnum):
    """Feed kinds from the Step 1 inventory (``config/sources.yaml``)."""

    AUTHORITATIVE_COLLECTION = "authoritative_collection"
    DETAIL_PARENT = "detail_parent"
    DISCOVERY = "discovery"
    EVOLVING_COLLECTION = "evolving_collection"
    ROLLING = "rolling"
    ROTATING_ARCHIVE = "rotating_archive"


class EntityKind(StrEnum):
    MATTER = "matter"
    DOCUMENT = "document"


class Discovery(StrEnum):
    """How a record entered the catalog; never reported as newly published unless LIVE."""

    BASELINE = "baseline"  # silent initial or per-feed recovery baseline (D-006)
    BACKFILL = "backfill"  # exposed by a coverage expansion (coverage_revision bump)
    LIVE = "live"  # first seen on a feed whose baseline is already complete


class RecordStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"  # removed after two complete authoritative enumerations


class EventType(StrEnum):
    NEW_MATTER = "new_matter"
    NEW_DOCUMENT = "new_document"
    NEW_APPEARANCE = "new_appearance"  # known record appears on another feed
    METADATA_CHANGED = "metadata_changed"
    STATUS_CHANGED = "status_changed"  # e.g. current -> archive section
    DEADLINE_CHANGED = "deadline_changed"  # emitted by Step 6 enrichment
    CONTENT_REPLACED = "content_replaced"  # emitted by Step 6 version checks
    WITHDRAWN = "withdrawn"
    REAPPEARED = "reappeared"


class FetchStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


class ParseStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    BLOCKED = "blocked"  # HTTP 200 block or CAPTCHA page


class EnumerationStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class BaselineStatus(StrEnum):
    NOT_STARTED = "not_started"
    PARTIAL = "partial"  # some runs attempted, no complete enumeration yet
    COMPLETE = "complete"


@dataclass(frozen=True)
class Provenance:
    """Where a record was observed: the fetched page and a locator within it."""

    page_url: str
    locator: str = ""  # e.g. "table[2]/row[14]/link[1]" or "posts[3].attachments[0]"


@dataclass(frozen=True)
class Matter:
    key: str
    site_id: str
    native_id: str | None
    canonical_url: str | None
    title: str
    metadata: Mapping[str, str]
    first_seen_at: str  # ISO 8601 UTC
    last_seen_at: str
    discovery: Discovery
    status: RecordStatus = RecordStatus.ACTIVE
    provenance: Provenance | None = None


@dataclass(frozen=True)
class Document:
    key: str
    site_id: str
    native_id: str | None
    canonical_url: str
    original_urls: tuple[str, ...]  # every href form seen, in first-seen order
    title: str
    matter_key: str | None  # set only on explicit source evidence
    published_date: str | None  # ISO date when parseable; never first_seen
    published_raw: str | None  # source date text, verbatim
    metadata: Mapping[str, str]
    metadata_fingerprint: str
    first_seen_at: str
    last_seen_at: str
    discovery: Discovery
    status: RecordStatus = RecordStatus.ACTIVE
    provenance: Provenance | None = None


@dataclass(frozen=True)
class Appearance:
    """One feed's sighting of a matter or document. Many appearances per record."""

    key: str  # appearance_key(feed_id, entity_kind, entity_key)
    feed_id: str
    entity_kind: EntityKind
    entity_key: str
    first_seen_at: str
    last_seen_at: str
    present: bool
    missing_complete_runs: int  # consecutive complete enumerations without it
    section: str | None = None  # e.g. "current" / "archive" for status passes
    provenance: Provenance | None = None


@dataclass(frozen=True)
class DocumentVersion:
    """Byte/validator observation of a document; populated by Step 6."""

    document_key: str
    observed_at: str
    etag: str | None = None
    last_modified: str | None = None
    content_length: int | None = None
    content_type: str | None = None
    byte_sha256: str | None = None
    text_fingerprint: str | None = None
    extraction_status: str | None = None  # e.g. ok, scanned, hindi_only, failed
    verification_method: str | None = None  # e.g. full_download, conditional_get


@dataclass(frozen=True)
class Event:
    event_id: str  # deterministic: same snapshot -> same id, no duplicates
    event_type: EventType
    run_id: str
    feed_id: str
    entity_kind: EntityKind
    entity_key: str
    occurred_at: str
    discovery: Discovery
    coverage_revision: int
    details: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedState:
    feed_id: str
    baseline_status: BaselineStatus
    coverage_revision: int  # current revision from config
    baselined_revision: int | None  # revision whose baseline completed
    parser_version: int
    last_complete_run_id: str | None = None
    last_item_count: int | None = None
    consecutive_failures: int = 0


@dataclass(frozen=True)
class FeedRun:
    run_id: str
    feed_id: str
    started_at: str
    finished_at: str
    fetch_status: FetchStatus
    parse_status: ParseStatus
    enumeration_status: EnumerationStatus
    accepted: bool  # True only when state advanced for this feed
    item_count: int
    previous_item_count: int | None
    coverage_revision: int
    parser_version: int
    config_hash: str
    code_revision: str
    quarantine_reasons: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogState:
    schema_version: int = SCHEMA_VERSION
    matters: Mapping[str, Matter] = field(default_factory=dict)
    documents: Mapping[str, Document] = field(default_factory=dict)
    appearances: Mapping[str, Appearance] = field(default_factory=dict)
    versions: Mapping[str, tuple[DocumentVersion, ...]] = field(default_factory=dict)
    feeds: Mapping[str, FeedState] = field(default_factory=dict)


@dataclass(frozen=True)
class CandidateState:
    """State plus the events and runs that produced it, before promotion."""

    run_id: str
    base_state_hash: str
    state: CatalogState
    events: tuple[Event, ...]
    feed_runs: tuple[FeedRun, ...]


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"


@dataclass(frozen=True)
class PendingDelivery:
    """Immutable outbox bundle header (D-011); Step 7 implements delivery."""

    delivery_id: str
    run_id: str
    message_id: str
    base_data_commit: str
    code_revision: str
    config_hash: str
    digest_paths: tuple[str, ...]
    recipient_set_sha256: str  # addresses live only in secrets, never in Git
    recipient_count: int
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
