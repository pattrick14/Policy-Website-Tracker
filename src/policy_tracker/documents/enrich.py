"""Bounded document acquisition, revalidation, and versioning (Step 6, D-008).

``enrich`` is the only entry point. For each selected document it either
acquires the file for the first time (full GET, signature check, SHA-256,
bounded extraction) or revalidates it with a conditional GET built from the
latest ``DocumentVersion``. Version history is append-only except that a
trailing *confirmation* record (``conditional_get_304`` or
``conditional_get_200_unchanged``) is replaced by the next confirmation, so
weekly checks of an unchanged file do not grow history without bound.

Known limit (D-008): after the first hash, replacement detection relies on the
server's validators. A server that silently replaces bytes while keeping the
same ETag/Last-Modified (and answering 304) is not detected.

Failures never prove anything: a failed, refused, oversized, or
signature-mismatched response records no version, emits no event, and leaves
the document and its history untouched; a diagnostic explains why. Documents
are never discarded. File bytes and text are never written outside a temporary
directory; text is returned only in ``EnrichmentResult.texts``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from policy_tracker.adapters.base import Fetcher, FetchResponse
from policy_tracker.core.contracts import FeedContract, RunContext
from policy_tracker.core.engine import owning_appearance
from policy_tracker.core.identity import stable_hash
from policy_tracker.core.models import (
    Appearance,
    BaselineStatus,
    CatalogState,
    Discovery,
    DocumentVersion,
    EntityKind,
    Event,
    EventType,
    RecordStatus,
)
from policy_tracker.documents import signatures
from policy_tracker.documents.extract import Extractor, normalize_text
from policy_tracker.documents.types import (
    EnrichmentLimits,
    EnrichmentResult,
    ExtractedText,
    ExtractionStatus,
)

SELECT_CHANGED = "changed"  # event-touched documents plus documents without a version
SELECT_ALL = "all"  # additionally revalidate every other active document

FULL_DOWNLOAD = "full_download"
CONDITIONAL_304 = "conditional_get_304"
CONDITIONAL_UNCHANGED = "conditional_get_200_unchanged"  # 200 with identical bytes/validators
CONFIRMATIONS = frozenset({CONDITIONAL_304, CONDITIONAL_UNCHANGED})

DEFAULT_LIMITS = EnrichmentLimits()

TRIGGER_EVENTS = frozenset(
    {
        EventType.NEW_DOCUMENT,
        EventType.NEW_APPEARANCE,
        EventType.METADATA_CHANGED,
        EventType.REAPPEARED,
    }
)


def enrich(
    state: CatalogState,
    events: Sequence[Event],
    contracts: Mapping[str, FeedContract],
    fetcher_for: Callable[[FeedContract], Fetcher],
    *,
    run: RunContext,
    limits: EnrichmentLimits = DEFAULT_LIMITS,
    select: str = SELECT_CHANGED,
    max_documents: int | None = None,
    extractor: Extractor | None = None,
    resolvers: Mapping[str, Resolver] | None = None,
    live_feed_ids: frozenset[str] | None = None,
    revalidate: int = 0,
) -> EnrichmentResult:
    """Acquire or revalidate selected documents; return new state, events, and texts.

    Selection order is: documents touched by this run's NEW_DOCUMENT,
    NEW_APPEARANCE, METADATA_CHANGED, or REAPPEARED events (sorted by key),
    then active documents with no version yet (sorted), then, with
    ``select="all"``, every other active document (sorted). ``max_documents``
    caps how many are processed; the silent baseline therefore hashes in
    batches, and unprocessed documents are picked up by later runs because
    they still have no version. The owning feed's contract (the present
    appearance seen first, feed id breaking ties, among ``contracts``) chooses
    the fetcher and host allowlist. ``resolvers`` (default ``DEFAULT_RESOLVERS``)
    map a site id to a function that turns a JSON attachment record served at
    the canonical URL into the file URL to fetch.

    ``live_feed_ids`` is the set of feeds whose baseline was already complete
    *before* this run. When given, ``CONTENT_REPLACED`` is emitted only if the
    owning feed is in it; otherwise the replacement is recorded silently with a
    ``content_replaced_silent:baseline_incomplete`` diagnostic. This keeps a
    feed that completes its baseline in this very run from announcing
    replacements of records that were never announced. With ``None`` (the
    default) the owning feed's baseline status in ``state`` decides.

    ``revalidate`` adds up to that many already-versioned active documents not
    otherwise selected, least recently verified first (then key), so files
    replaced under an unchanged listing are rechecked on a rotating schedule
    (D-008). It is applied after the ``max_documents`` cut.
    """

    if select not in (SELECT_CHANGED, SELECT_ALL):
        raise ValueError(f"select must be {SELECT_CHANGED!r} or {SELECT_ALL!r}, not {select!r}")
    if max_documents is not None and max_documents < 0:
        raise ValueError("max_documents must be None or >= 0")
    if revalidate < 0:
        raise ValueError("revalidate must be >= 0")
    job = _Job(
        state,
        contracts,
        fetcher_for,
        run,
        limits,
        extractor or Extractor(),
        DEFAULT_RESOLVERS if resolvers is None else resolvers,
        live_feed_ids,
    )
    selected = select_documents(state, events, select)
    if max_documents is not None and len(selected) > max_documents:
        deferred = len(selected) - max_documents
        selected = selected[:max_documents]
        job.diagnostics.append(f"deferred {deferred} document(s) beyond max_documents")
    if revalidate:
        rotation = revalidation_order(state, exclude=set(selected))[:revalidate]
        selected = [*selected, *rotation]
        if rotation:
            job.diagnostics.append(f"revalidating {len(rotation)} document(s) by rotation")
    for key in selected:
        try:
            job.process(key)
        except Exception as exc:  # noqa: BLE001 - one bad document never aborts the run
            job.diagnostics.append(f"{key}: enrichment_error:{type(exc).__name__}")
            job.texts[key] = ExtractedText(
                key, ExtractionStatus.FAILED, warnings=("enrichment_error",)
            )
    for key, notes in job.notes.items():
        if key in job.texts:
            text = job.texts[key]
            job.texts[key] = replace(text, warnings=notes + text.warnings)
    return EnrichmentResult(
        state=replace(state, versions=job.versions),
        events=tuple(sorted(job.events.values(), key=lambda e: (e.entity_key, e.event_id))),
        texts=dict(sorted(job.texts.items())),
        diagnostics=tuple(job.diagnostics),
    )


# ---------------------------------------------------------------------------
# Attachment-record resolution


@dataclass(frozen=True)
class Resolution:
    file_url: str
    warnings: tuple[str, ...] = ()


Resolver = Callable[[FetchResponse], "Resolution | str"]
"""Turn a JSON record response into a ``Resolution``, or return a failure reason."""

# Preferred attachment slots: English first, then the bilingual file, then
# anything else, with Hindi-only files last.
_SLOT_PRIORITY = {"pdf_english": 0, "pdf_both": 1, "pdf_hindi": 3}


def _is_json(response: FetchResponse) -> bool:
    media = signatures.media_type(_header(response.headers, "Content-Type"))
    if media is not None and (media == "application/json" or media.endswith("+json")):
        return True
    return response.body.lstrip()[:1] in (b"{", b"[")


def _file_objects(node: object, path: tuple[str, ...]) -> list[tuple[tuple[str, ...], str]]:
    found: list[tuple[tuple[str, ...], str]] = []
    if isinstance(node, Mapping):
        url = node.get("url")
        if isinstance(url, str) and url.strip() and "mime_type" in node:
            found.append((path, url.strip()))
        for name in sorted(node, key=str):
            if name != "url":
                found.extend(_file_objects(node[name], (*path, str(name))))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_file_objects(item, (*path, f"{index:06d}")))
    return found


def wordpress_attachment(response: FetchResponse) -> Resolution | str:
    """Ministry of Power ``post-page/post`` record: pick the file under ``posts.acf_data``.

    File objects are dicts with ``url`` and ``mime_type``. Preference is
    ``pdf_english``, then ``pdf_both``, then other slots in key order, then
    ``pdf_hindi``. When several distinct files exist, the warning
    ``other_language_files:<n>`` counts the ones not fetched.
    """

    try:
        payload = json.loads(response.body)
    except (ValueError, UnicodeDecodeError):
        return "invalid_json"
    posts = payload.get("posts") if isinstance(payload, Mapping) else None
    if isinstance(posts, list):
        posts = posts[0] if posts else None
    acf = posts.get("acf_data") if isinstance(posts, Mapping) else None
    if not isinstance(acf, Mapping):
        return "no_acf_data"
    files = _file_objects(acf, ())
    if not files:
        return "no_file_objects"
    files.sort(key=lambda item: (_SLOT_PRIORITY.get(item[0][0], 2), item[0]))
    urls = list(dict.fromkeys(url for _path, url in files))
    warnings = (f"other_language_files:{len(urls) - 1}",) if len(urls) > 1 else ()
    return Resolution(urls[0], warnings)


DEFAULT_RESOLVERS: Mapping[str, Resolver] = {"ministry_of_power": wordpress_attachment}


def select_documents(state: CatalogState, events: Sequence[Event], select: str) -> list[str]:
    """Return document keys in processing order (see ``enrich``)."""

    touched = sorted(
        {
            event.entity_key
            for event in events
            if event.entity_kind is EntityKind.DOCUMENT
            and event.event_type in TRIGGER_EVENTS
            and event.entity_key in state.documents
        }
    )
    seen = set(touched)
    active = [
        key
        for key in sorted(state.documents)
        if key not in seen and state.documents[key].status is RecordStatus.ACTIVE
    ]
    unversioned = [key for key in active if not state.versions.get(key)]
    rest = [key for key in active if state.versions.get(key)] if select == SELECT_ALL else []
    return touched + unversioned + rest


def revalidation_order(state: CatalogState, *, exclude: set[str]) -> list[str]:
    """Versioned active documents, least recently verified first, then by key."""

    candidates = [
        (state.versions[key][-1].observed_at, key)
        for key in state.documents
        if key not in exclude
        and state.documents[key].status is RecordStatus.ACTIVE
        and state.versions.get(key)
    ]
    return [key for _, key in sorted(candidates)]


def owning_feed(
    appearances: Iterable[Appearance], contracts: Mapping[str, FeedContract]
) -> str | None:
    """Feed id of the owning appearance among one document's appearances with a contract.

    Mirrors the engine's owner rule (``engine.owner_rank``): present first, then
    feeds with ``removal_authority`` before rolling/recent feeds, then earliest
    first sighting, then feed id.
    """

    owner = owning_appearance(
        (appearance for appearance in appearances if appearance.feed_id in contracts),
        lambda feed_id: contracts[feed_id].removal_authority,
    )
    return owner.feed_id if owner is not None else None


def content_replaced_id(document_key: str, old_sha256: str, new_sha256: str) -> str:
    return stable_hash(EventType.CONTENT_REPLACED.value, document_key, old_sha256, new_sha256)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            value = value.strip()
            return value or None
    return None


def _confirm(
    history: tuple[DocumentVersion, ...], record: DocumentVersion
) -> tuple[DocumentVersion, ...]:
    """Replace a trailing confirmation, else append; keeps unchanged-file history bounded."""

    if history and history[-1].verification_method in CONFIRMATIONS:
        return history[:-1] + (record,)
    return history + (record,)


@dataclass
class _Job:
    state: CatalogState
    contracts: Mapping[str, FeedContract]
    fetcher_for: Callable[[FeedContract], Fetcher]
    run: RunContext
    limits: EnrichmentLimits
    extractor: Extractor
    resolvers: Mapping[str, Resolver]
    live_feed_ids: frozenset[str] | None = None
    versions: dict[str, tuple[DocumentVersion, ...]] = field(init=False)
    events: dict[str, Event] = field(default_factory=dict)
    texts: dict[str, ExtractedText] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    fetchers: dict[str, Fetcher] = field(default_factory=dict)
    notes: dict[str, tuple[str, ...]] = field(default_factory=dict)  # prepended to warnings
    by_document: dict[str, list[Appearance]] = field(init=False)

    def __post_init__(self) -> None:
        self.versions = dict(self.state.versions)
        self.by_document = {}
        for appearance in self.state.appearances.values():
            if appearance.entity_kind is EntityKind.DOCUMENT:
                self.by_document.setdefault(appearance.entity_key, []).append(appearance)

    def _skip(self, key: str, reason: str, status: ExtractionStatus) -> None:
        self.diagnostics.append(f"{key}: {reason}")
        self.texts[key] = ExtractedText(key, status, warnings=(reason,))

    def _allowed(self, contract: FeedContract, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        hosts = contract.host_rules.allowed_hosts | set(contract.fetch.fallback_hosts)
        return host in hosts

    def process(self, key: str) -> None:
        document = self.state.documents[key]
        feed_id = owning_feed(self.by_document.get(key, ()), self.contracts)
        if feed_id is None:
            self._skip(key, "no_contract", ExtractionStatus.NOT_FETCHED)
            return
        contract = self.contracts[feed_id]
        url = document.canonical_url
        if not self._allowed(contract, url):
            self._skip(key, "host_not_allowed", ExtractionStatus.NOT_FETCHED)
            return
        if feed_id not in self.fetchers:
            self.fetchers[feed_id] = self.fetcher_for(contract)
        fetcher = self.fetchers[feed_id]
        history = self.versions.get(key, ())
        previous = next((v for v in reversed(history) if v.byte_sha256), None)
        request: dict[str, str] = {}
        if previous is not None:
            latest = history[-1]
            if latest.etag:
                request["If-None-Match"] = latest.etag
            if latest.last_modified:
                request["If-Modified-Since"] = latest.last_modified

        resolver = self.resolvers.get(document.site_id)
        response: FetchResponse | None = None
        if resolver is not None:
            record = fetcher.get(url, headers=None)  # the record itself is never conditional
            if not record.ok:
                reason = record.error or f"http_{record.status_code}"
                self._skip(key, f"fetch_failed:{reason}", ExtractionStatus.NOT_FETCHED)
                return
            if _is_json(record):
                resolution = resolver(record)
                if isinstance(resolution, str):
                    self._skip(key, f"resolve_failed:{resolution}", ExtractionStatus.NOT_FETCHED)
                    return
                url = resolution.file_url
                self.notes[key] = (f"resolved:{url}", *resolution.warnings)
                self.diagnostics.append(f"{key}: resolved:{url}")
                if not self._allowed(contract, url):
                    self._skip(key, "host_not_allowed", ExtractionStatus.NOT_FETCHED)
                    return
            else:  # the canonical URL already serves the file: no second request
                response = record
                request = {}
        if response is None:
            response = fetcher.get(url, headers=request or None)

        if response.error is None and response.status_code == 304 and request:
            self._not_modified(key, history, response)
            return
        if not response.ok:
            reason = response.error or f"http_{response.status_code}"
            self._skip(key, f"fetch_failed:{reason}", ExtractionStatus.NOT_FETCHED)
            return
        if not self._allowed(contract, response.url):
            self._skip(key, "redirect_host_not_allowed", ExtractionStatus.NOT_FETCHED)
            return
        max_bytes = min(self.limits.max_bytes, contract.fetch.max_document_bytes)
        if len(response.body) > max_bytes:
            self._skip(key, f"too_large:{len(response.body)}", ExtractionStatus.NOT_FETCHED)
            return
        if not response.body:
            self._skip(key, "empty_body", ExtractionStatus.NOT_FETCHED)
            return
        content_type = _header(response.headers, "Content-Type")
        detected = signatures.detect(response.body)
        claimed = signatures.claimed_type(url, None) or signatures.claimed_type(
            response.url, content_type
        )
        if not signatures.satisfies(claimed, detected):
            self._skip(
                key,
                f"signature_mismatch:claimed={claimed}:detected={detected}",
                ExtractionStatus.FAILED,
            )
            return

        text = self.extractor.extract(key, response.body, detected, self.limits)
        self.texts[key] = text
        normalized = normalize_text(text.text)
        record = DocumentVersion(
            document_key=key,
            observed_at=self.run.started_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            content_length=len(response.body),
            content_type=signatures.media_type(content_type),
            byte_sha256=hashlib.sha256(response.body).hexdigest(),
            text_fingerprint=stable_hash(normalized) if normalized else None,
            extraction_status=text.status.value,
            verification_method=FULL_DOWNLOAD,
        )
        if previous is None:
            self.versions[key] = history + (record,)
            return
        if record.byte_sha256 == previous.byte_sha256:
            latest = history[-1]
            same = (record.etag, record.last_modified, record.content_type) == (
                latest.etag,
                latest.last_modified,
                latest.content_type,
            )
            if same:
                record = replace(record, verification_method=CONDITIONAL_UNCHANGED)
                self.versions[key] = _confirm(history, record)
            else:  # validators moved but bytes did not: record them, no event
                self.versions[key] = history + (record,)
            return
        self.versions[key] = history + (record,)
        self._replaced(key, feed_id, contract, previous, record, url)

    def _not_modified(
        self, key: str, history: tuple[DocumentVersion, ...], response: FetchResponse
    ) -> None:
        latest = history[-1]
        record = replace(
            latest,
            observed_at=self.run.started_at,
            etag=_header(response.headers, "ETag") or latest.etag,
            last_modified=_header(response.headers, "Last-Modified") or latest.last_modified,
            verification_method=CONDITIONAL_304,
        )
        self.versions[key] = _confirm(history, record)
        self.texts[key] = ExtractedText(
            key, ExtractionStatus.NOT_FETCHED, warnings=("validators_unchanged",)
        )

    def _replaced(
        self,
        key: str,
        feed_id: str,
        contract: FeedContract,
        old: DocumentVersion,
        new: DocumentVersion,
        url: str,
    ) -> None:
        old_hash, new_hash = old.byte_sha256 or "", new.byte_sha256 or ""
        if self.live_feed_ids is not None:
            live = feed_id in self.live_feed_ids
        else:
            feed_state = self.state.feeds.get(feed_id)
            live = feed_state is not None and feed_state.baseline_status is BaselineStatus.COMPLETE
        if not live:
            self.diagnostics.append(f"{key}: content_replaced_silent:baseline_incomplete")
            return
        event_id = content_replaced_id(key, old_hash, new_hash)
        details = {
            "old_sha256": old_hash,
            "new_sha256": new_hash,
            "old_etag": old.etag or "",
            "new_etag": new.etag or "",
            "old_last_modified": old.last_modified or "",
            "new_last_modified": new.last_modified or "",
            "old_observed_at": old.observed_at,
            "url": url,
        }
        self.events[event_id] = Event(
            event_id=event_id,
            event_type=EventType.CONTENT_REPLACED,
            run_id=self.run.run_id,
            feed_id=feed_id,
            entity_kind=EntityKind.DOCUMENT,
            entity_key=key,
            occurred_at=self.run.started_at,
            discovery=Discovery.LIVE,
            coverage_revision=contract.coverage_revision,
            details=dict(sorted(details.items())),
        )
