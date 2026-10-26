"""What an adapter returns for one feed (Step 2 frozen contract).

Adapters normalize source markup into these records plus completeness
evidence. They never mutate state or send email; the engine decides what an
observation means.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from policy_tracker.core.models import Provenance


@dataclass(frozen=True)
class ObservedMatter:
    native_id: str | None
    title: str
    url: str | None  # as found in markup
    provenance: Provenance
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ObservedDocument:
    url: str  # href as found in markup, before canonicalization
    title: str
    provenance: Provenance
    native_id: str | None = None
    matter_native_id: str | None = None  # explicit parent evidence only
    published_date: str | None = None  # ISO date when the adapter parsed one
    published_raw: str | None = None
    section: str | None = None  # e.g. "current" / "archive"
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionEvidence:
    """Fetch success, parse validity, and complete enumeration are separate."""

    fetch_ok: bool
    parse_valid: bool
    enumeration_complete: bool
    block_page_detected: bool = False
    pages_fetched: int = 0
    pages_expected: int | None = None
    unexpected_mime_types: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedObservation:
    feed_id: str
    site_id: str
    observed_at: str  # ISO 8601 UTC
    parser_version: int
    evidence: CollectionEvidence
    matters: tuple[ObservedMatter, ...] = ()
    documents: tuple[ObservedDocument, ...] = ()
