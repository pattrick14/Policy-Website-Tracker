"""Enrichment interface (frozen for Steps 6-7).

``policy_tracker.documents.enrich.enrich`` downloads or revalidates tracked
documents within the PROJECT_PLAN bounds and returns new versions, version
events, and extracted text for classification. It never discards a record
because extraction failed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from policy_tracker.core.models import CatalogState, Event


class ExtractionStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"  # page or time budget reached
    SCANNED = "scanned"  # no text layer; OCR unavailable or failed
    OCR = "ocr"  # text came from OCR
    HINDI_ONLY = "hindi_only"
    UNSUPPORTED = "unsupported"  # MIME type without an extractor
    FAILED = "failed"
    NOT_FETCHED = "not_fetched"  # validators unchanged, or fetch refused


@dataclass(frozen=True)
class ExtractedText:
    document_key: str
    status: ExtractionStatus
    text: str = ""  # bounded; never committed to tracker-data
    language: str | None = None  # "en", "hi", "bilingual", or None
    pages: int | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnrichmentLimits:
    max_bytes: int = 104_857_600
    max_redirects: int = 5
    max_pages: int = 200
    ocr_max_pages: int = 50
    document_time_budget_seconds: float = 60.0


@dataclass(frozen=True)
class EnrichmentResult:
    state: CatalogState  # adds DocumentVersion entries; input never mutated
    events: tuple[Event, ...]  # CONTENT_REPLACED only
    texts: Mapping[str, ExtractedText] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
