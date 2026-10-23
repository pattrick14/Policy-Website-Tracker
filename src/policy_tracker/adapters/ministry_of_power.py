"""Ministry of Power adapter (Step 3): WordPress-style JSON APIs.

Feeds (``config/sources.yaml``, site ``ministry_of_power``):

- ``mop_whats_new``: one ``post-page/whats_new`` response; rolling, no removal authority.
- ``mop_whats_new_archive``: one unparameterized ``post_status=archive`` response whose
  ``posts`` length must equal ``total_items``. Its ``post_date`` values are CMS-migration
  artifacts: kept verbatim as ``published_raw`` but never parsed into ``published_date``.
- ``mop_documents``: per selected root category, a ``publish`` and an ``archive`` pass,
  each paginated to ``total_pages`` plus the empty page that follows it, then a
  child-term reconciliation against the documents taxonomy.

Every post becomes an ``ObservedMatter`` (native id = post id). Every attachment id in
``acf_data.file`` becomes an ``ObservedDocument`` with ``native_id`` = attachment id and
``matter_native_id`` = the post id; its ``url`` is the attachment-record API URL, which
Step 6 resolves to the file. Groups carrying only an ``external_link`` become documents
keyed by that href. Source failures are reported in ``CollectionEvidence``, never raised.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from policy_tracker.adapters.base import Adapter, Fetcher, FetchResponse
from policy_tracker.adapters.blocks import detect_block_page
from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.models import Provenance
from policy_tracker.core.observations import (
    CollectionEvidence,
    FeedObservation,
    ObservedDocument,
    ObservedMatter,
)

SITE_ID = "ministry_of_power"
API_ROOT = "https://www.powermin.gov.in/cms/wp-json/"
WHATS_NEW_URL = f"{API_ROOT}post-page/whats_new"
WHATS_NEW_ARCHIVE_URL = f"{WHATS_NEW_URL}?post_status=archive"
TAXONOMY_URL = f"{API_ROOT}taxonomy/documents_category"
DOCUMENTS_URL_TEMPLATE = (
    f"{API_ROOT}document/documents?document_category={{category}}&limit={{limit}}&page={{page}}"
)
ARCHIVE_STATUS_SUFFIX = "&post_status=archive"
DOCUMENTS_ARCHIVE_URL_TEMPLATE = DOCUMENTS_URL_TEMPLATE + ARCHIVE_STATUS_SUFFIX
ATTACHMENT_URL_TEMPLATE = f"{API_ROOT}post-page/post?id={{id}}"

WHATS_NEW = "mop_whats_new"
WHATS_NEW_ARCHIVE = "mop_whats_new_archive"
DOCUMENTS = "mop_documents"
FEED_IDS = frozenset({WHATS_NEW, WHATS_NEW_ARCHIVE, DOCUMENTS})

# Mirrors mop_documents.categories; tests assert the two stay equal.
CATEGORIES = (
    "reports",
    "acts-and-policy",
    "orders-and-notices",
    "gazette-notifications",
    "guidelines",
    "publications",
)
STATUS_PASSES = ("publish", "archive")
# Root terms with a recorded exclude decision (D-013); other unselected roots are flagged.
REVIEWED_UNSELECTED_ROOTS = frozenset({"press-release"})
DEFAULT_PAGE_LIMIT = 50
MAX_PAGES_PER_PASS = 500  # guard against a runaway total_pages value

_WP_DATETIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$")
_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_YMD = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")


# ---------------------------------------------------------------------------
# Small parsing helpers


def wp_datetime_iso(raw: object) -> str | None:
    """``2025-11-10 15:40:04`` -> ``2025-11-10T15:40:04`` (site-local time, no offset)."""

    if not isinstance(raw, str):
        return None
    match = _WP_DATETIME.match(raw.strip())
    if not match or match.group(1) == "0000":
        return None
    y, mo, d, h, mi, s = match.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def acf_date_iso(raw: object) -> str | None:
    """Parse an ACF display date (``dd/mm/yyyy``, ``yyyymmdd`` or ``yyyy-mm-dd``)."""

    if not isinstance(raw, str):
        return None
    text = raw.strip()
    try:
        if match := _DMY.match(text):
            day, month, year = (int(part) for part in match.groups())
            return date(year, month, day).isoformat()
        if match := _YMD.match(text):
            year, month, day = (int(part) for part in match.groups())
            return date(year, month, day).isoformat()
    except ValueError:
        return None
    return None


def _post_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value.strip().isdigit() and int(value) > 0:
        return value.strip()
    return None


def _text(value: object) -> str:
    return html.unescape(value).strip() if isinstance(value, str) else ""


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _is_json_type(content_type: str | None) -> bool:
    media = (content_type or "").split(";", 1)[0].strip().lower()
    return media == "application/json" or media.endswith("+json")


# ---------------------------------------------------------------------------
# Evidence accumulation


class _Blocked(Exception):
    """Stop issuing requests after a block page."""


@dataclass
class _Collection:
    fetcher: Fetcher
    fetch_ok: bool = True
    parse_valid: bool = True
    complete: bool = True
    blocked: bool = False
    pages_fetched: int = 0
    pages_expected: int = 0
    mimes: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    matters: list[ObservedMatter] = field(default_factory=list)
    documents: list[ObservedDocument] = field(default_factory=list)

    def incomplete(self, message: str) -> None:
        self.complete = False
        self.diagnostics.append(message)

    def invalid(self, message: str) -> None:
        self.parse_valid = False
        self.incomplete(message)

    def fetch_json(self, url: str) -> Any | None:
        """GET ``url`` and decode JSON; ``None`` (with evidence recorded) on any failure."""

        try:
            response: FetchResponse = self.fetcher.get(url, headers={"Accept": "application/json"})
        except Exception as exc:  # noqa: BLE001 - adapters never raise for source failures
            self.fetch_ok = False
            self.incomplete(f"{url}: fetch raised {type(exc).__name__}: {exc}")
            return None
        if response.error is not None:
            self.fetch_ok = False
            self.incomplete(f"{url}: fetch error {response.error}")
            return None
        self.pages_fetched += 1
        content_type = _header(response.headers, "content-type")
        block = detect_block_page(response.body, content_type)
        if block is not None:
            self.blocked = True
            self.parse_valid = False
            if not response.ok:
                self.fetch_ok = False
            self.incomplete(f"{url}: HTTP {response.status_code} block page ({block})")
            raise _Blocked
        if not response.ok:
            self.fetch_ok = False
            self.incomplete(f"{url}: HTTP {response.status_code}")
            return None
        if content_type is not None and not _is_json_type(content_type):
            media = content_type.split(";", 1)[0].strip().lower() or content_type
            if media not in self.mimes:
                self.mimes.append(media)
            self.diagnostics.append(f"{url}: unexpected content type {content_type!r}")
        try:
            return json.loads(response.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            self.invalid(f"{url}: response is not valid JSON ({exc})")
            return None

    def evidence(self) -> CollectionEvidence:
        return CollectionEvidence(
            fetch_ok=self.fetch_ok,
            parse_valid=self.parse_valid,
            enumeration_complete=(
                self.complete and self.fetch_ok and self.parse_valid and not self.blocked
            ),
            block_page_detected=self.blocked,
            pages_fetched=self.pages_fetched,
            pages_expected=self.pages_expected,
            unexpected_mime_types=tuple(self.mimes),
            diagnostics=tuple(self.diagnostics),
        )


# ---------------------------------------------------------------------------
# Post -> matter + attachments -> documents


@dataclass(frozen=True)
class _PostContext:
    page_url: str
    index: int
    section: str | None
    # (raw, iso) date to put on documents; iso may be None when the raw date is untrusted.
    doc_date: Callable[[Mapping[str, Any], int], tuple[str | None, str | None]]


def _validate_posts(payload: Any, url: str, run: _Collection) -> list[Mapping[str, Any]] | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("posts"), list):
        run.invalid(f"{url}: expected an object with a 'posts' array")
        return None
    posts = payload["posts"]
    for index, post in enumerate(posts):
        if not isinstance(post, Mapping) or _post_id(post.get("ID")) is None:
            run.invalid(f"{url}: posts[{index}] has no usable ID")
            return None
    return posts


def _int_field(payload: Mapping[str, Any], name: str, url: str, run: _Collection) -> int | None:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        run.invalid(f"{url}: '{name}' is missing or not a non-negative integer")
        return None
    return value


def _attachment_groups(acf: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Normalize ``acf_data.file`` to groups of ``{title, type, external_link, ids}``."""

    raw = acf.get("file")
    if not isinstance(raw, list):
        return []
    groups: list[Mapping[str, Any]] = []
    flat: list[object] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            ids = entry.get("file")
            if not isinstance(ids, list):
                ids = [ids] if ids not in (None, "", False) else []
            groups.append(
                {
                    "title": _text(entry.get("title")),
                    "type": _text(entry.get("type")),
                    "external_link": _text(entry.get("external_link")),
                    "ids": ids,
                }
            )
        else:
            flat.append(entry)
    if flat:  # What's New shape: acf_data = {type, file: [ids]}
        groups.insert(
            0, {"title": "", "type": _text(acf.get("type")), "external_link": "", "ids": flat}
        )
    return groups


def _emit_post(
    post: Mapping[str, Any], ctx: _PostContext, run: _Collection, extra_metadata: Mapping[str, str]
) -> None:
    post_id = _post_id(post.get("ID")) or ""
    acf = post.get("acf_data") if isinstance(post.get("acf_data"), Mapping) else {}
    title = _text(post.get("post_title")) or _text(acf.get("title"))
    locator = f"posts[{ctx.index}]"
    metadata: dict[str, str] = dict(extra_metadata)
    for key, value in (
        ("post_slug", _text(post.get("post_slug"))),
        ("post_date", wp_datetime_iso(post.get("post_date")) or ""),
        ("post_modified", wp_datetime_iso(post.get("post_modified")) or ""),
        ("type", _text(acf.get("type"))),
    ):
        if value:
            metadata[key] = value
    terms = post.get("documents_category")
    if isinstance(terms, list):
        slugs = sorted(
            {_text(term.get("slug")) for term in terms if isinstance(term, Mapping)} - {""}
        )
        if slugs:
            metadata["terms"] = ",".join(slugs)
    run.matters.append(
        ObservedMatter(
            native_id=post_id,
            title=title,
            url=None,
            provenance=Provenance(ctx.page_url, locator),
            metadata=metadata,
        )
    )

    groups = _attachment_groups(acf)
    total = sum(len(group["ids"]) or (1 if group["external_link"] else 0) for group in groups)
    published_raw, published_date = ctx.doc_date(post, total)
    position = 0
    for group_index, group in enumerate(groups):
        doc_title = group["title"] or title
        # Group position lives only in the provenance locator: positional metadata
        # would emit METADATA_CHANGED for every attachment when groups are reordered.
        group_meta: dict[str, str] = {}
        if group["title"]:
            group_meta["group_title"] = group["title"]
        if group["type"]:
            group_meta["file_type"] = group["type"]
        hrefs: list[tuple[str | None, str]] = []
        for raw_id in group["ids"]:
            attachment_id = _post_id(raw_id)
            if attachment_id is None:
                run.diagnostics.append(
                    f"{ctx.page_url}: {locator} group {group_index} has a non-id file {raw_id!r}"
                )
                continue
            hrefs.append((attachment_id, ATTACHMENT_URL_TEMPLATE.format(id=attachment_id)))
        if not hrefs and group["external_link"]:
            hrefs.append((None, group["external_link"]))
        if not hrefs:
            run.diagnostics.append(f"{ctx.page_url}: {locator} group {group_index} is empty")
        for native_id, href in hrefs:
            run.documents.append(
                ObservedDocument(
                    url=href,
                    title=doc_title,
                    provenance=Provenance(ctx.page_url, f"{locator}.attachments[{position}]"),
                    native_id=native_id,
                    matter_native_id=post_id,
                    published_date=published_date,
                    published_raw=published_raw,
                    section=ctx.section,
                    metadata=group_meta,
                )
            )
            position += 1


def _whats_new_date(post: Mapping[str, Any], _total: int) -> tuple[str | None, str | None]:
    raw = post.get("post_date") if isinstance(post.get("post_date"), str) else None
    iso = wp_datetime_iso(raw)
    return raw, iso[:10] if iso else None


def _archive_date(post: Mapping[str, Any], _total: int) -> tuple[str | None, str | None]:
    # Migration artifact (contract date_semantics): keep the text, never parse it as published.
    raw = post.get("post_date") if isinstance(post.get("post_date"), str) else None
    return raw, None


def _documents_date(post: Mapping[str, Any], total: int) -> tuple[str | None, str | None]:
    # The parent's ACF date describes its file only when the parent holds exactly one.
    acf = post.get("acf_data") if isinstance(post.get("acf_data"), Mapping) else {}
    raw = acf.get("date") if isinstance(acf.get("date"), str) and acf.get("date") else None
    if raw is None or total != 1:
        return None, None
    return raw, acf_date_iso(raw)


# ---------------------------------------------------------------------------
# The adapter


class MinistryOfPowerAdapter:
    """Collects ``mop_whats_new``, ``mop_whats_new_archive`` and ``mop_documents``."""

    site_id = SITE_ID
    parser_version = 1

    def __init__(
        self,
        *,
        categories: Sequence[str] = CATEGORIES,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        taxonomy_url: str = TAXONOMY_URL,
    ) -> None:
        if page_limit < 1:
            raise ValueError("page_limit must be positive")
        self.categories = tuple(categories)
        self.page_limit = page_limit
        self.taxonomy_url = taxonomy_url

    def feed_ids(self) -> frozenset[str]:
        return FEED_IDS

    def collect(
        self, contract: FeedContract, fetcher: Fetcher, *, observed_at: str
    ) -> FeedObservation:
        if contract.feed_id not in FEED_IDS or contract.site_id != SITE_ID:
            raise ValueError(
                f"{contract.site_id}/{contract.feed_id} is not a Ministry of Power feed"
            )
        run = _Collection(fetcher)
        try:
            if contract.feed_id == WHATS_NEW:
                self._single_response(run, contract.url or WHATS_NEW_URL, None, _whats_new_date)
            elif contract.feed_id == WHATS_NEW_ARCHIVE:
                self._single_response(
                    run, contract.url or WHATS_NEW_ARCHIVE_URL, "archive", _archive_date
                )
            else:
                self._documents(run, contract)
        except _Blocked:
            run.diagnostics.append("stopped after a block page; no further requests sent")
        return FeedObservation(
            feed_id=contract.feed_id,
            site_id=SITE_ID,
            observed_at=observed_at,
            parser_version=self.parser_version,
            evidence=run.evidence(),
            matters=tuple(run.matters),
            documents=tuple(run.documents),
        )

    # -- What's New and its archive ---------------------------------------

    def _single_response(
        self,
        run: _Collection,
        url: str,
        section: str | None,
        doc_date: Callable[[Mapping[str, Any], int], tuple[str | None, str | None]],
    ) -> None:
        """One body holds the whole feed; ``posts`` length must equal ``total_items``."""

        run.pages_expected += 1
        payload = run.fetch_json(url)
        if payload is None:
            run.complete = False
            return
        posts = _validate_posts(payload, url, run)
        if posts is None:
            return
        total = _int_field(payload, "total_items", url, run)
        if total is None:
            return
        if "total_pages" in payload:
            run.diagnostics.append(f"{url}: response carries pagination fields (paginated shape)")
        ids = [_post_id(post.get("ID")) for post in posts]
        if len(set(ids)) != len(ids):
            run.diagnostics.append(f"{url}: {len(ids) - len(set(ids))} duplicate post id(s)")
        if len(posts) != total:
            run.incomplete(f"{url}: {len(posts)} posts but total_items={total}")
        status = {"post_status": section} if section else {}
        for index, post in enumerate(posts):
            _emit_post(post, _PostContext(url, index, section, doc_date), run, status)

    # -- Documents: taxonomy, two status passes per category, reconciliation

    def _page_url(self, template: str, category: str, page: int) -> str:
        return template.format(category=category, limit=self.page_limit, page=page)

    def _documents(self, run: _Collection, contract: FeedContract) -> None:
        template = contract.url or DOCUMENTS_URL_TEMPLATE
        templates = {"publish": template, "archive": template + ARCHIVE_STATUS_SUFFIX}
        taxonomy = self._taxonomy(run)
        post_terms: dict[str, set[str]] = {}  # post id -> term ids/slugs, both passes
        publish_totals: dict[str, int] = {}
        for category in self.categories:
            for status in STATUS_PASSES:
                total = self._pass(run, templates[status], category, status, post_terms)
                if status == "publish" and total is not None:
                    publish_totals[category] = total
        if taxonomy is not None:
            self._reconcile(run, taxonomy, post_terms, publish_totals)
        else:
            run.incomplete("child-term reconciliation not possible without the taxonomy")

    def _taxonomy(self, run: _Collection) -> list[Mapping[str, Any]] | None:
        run.pages_expected += 1
        url = self.taxonomy_url
        payload = run.fetch_json(url)
        if payload is None:
            return None
        terms = payload.get("documents_category") if isinstance(payload, Mapping) else payload
        if not isinstance(terms, list) or not all(
            isinstance(term, Mapping)
            and _post_id(term.get("term_id")) is not None
            and isinstance(term.get("slug"), str)
            and isinstance(term.get("parent"), int)
            and isinstance(term.get("count"), int)
            for term in terms
        ):
            run.invalid(f"{url}: expected a documents_category list of terms")
            return None
        return terms

    def _pass(
        self,
        run: _Collection,
        template: str,
        category: str,
        status: str,
        post_terms: dict[str, set[str]],
    ) -> int | None:
        """Enumerate one category/status pass; return its total_items when readable."""

        label = f"{category}/{status}"
        first_url = self._page_url(template, category, 1)
        run.pages_expected += 1
        first = run.fetch_json(first_url)
        if first is None:
            run.incomplete(f"{label}: pass not enumerated")
            return None
        header = self._page_header(run, first, first_url, 1)
        if header is None:
            return None
        total_items, total_pages = header
        if total_pages == 0:
            if total_items != 0 or first["posts"]:
                run.incomplete(f"{label}: total_pages=0 with {total_items} items")
            return total_items
        if total_pages > MAX_PAGES_PER_PASS:
            run.invalid(f"{label}: total_pages={total_pages} exceeds {MAX_PAGES_PER_PASS}")
            return None
        run.pages_expected += total_pages  # pages 2..N plus the empty terminus
        seen: list[str] = []
        page, payload, url = 1, first, first_url
        while True:
            posts = payload["posts"]
            if not posts:
                run.incomplete(f"{label}: pagination gap, page {page} of {total_pages} is empty")
                return total_items
            for index, post in enumerate(posts):
                post_id = _post_id(post.get("ID")) or ""
                seen.append(post_id)
                self._record_terms(post, post_id, post_terms)
                declared = post.get("post_status")
                if isinstance(declared, str) and declared != status:
                    run.diagnostics.append(f"{url}: posts[{index}] post_status={declared!r}")
                _emit_post(
                    post,
                    _PostContext(url, index, status, _documents_date),
                    run,
                    {"post_status": status},
                )
            page += 1
            url = self._page_url(template, category, page)
            payload = run.fetch_json(url)
            if payload is None:
                run.incomplete(f"{label}: page {page} not read")
                return total_items
            header = self._page_header(run, payload, url, page)
            if header is None:
                return total_items
            if header != (total_items, total_pages):
                run.incomplete(
                    f"{label}: totals changed mid-pass from {(total_items, total_pages)} to "
                    f"{header} at page {page}"
                )
                return total_items
            if page > total_pages:
                break
        if payload["posts"]:
            run.incomplete(f"{label}: page {page} after total_pages={total_pages} is not empty")
        if len(set(seen)) != len(seen):
            run.incomplete(f"{label}: {len(seen) - len(set(seen))} post(s) repeated across pages")
        if len(set(seen)) != total_items:
            run.incomplete(f"{label}: enumerated {len(set(seen))} posts, total_items={total_items}")
        return total_items

    @staticmethod
    def _page_header(run: _Collection, payload: Any, url: str, page: int) -> tuple[int, int] | None:
        if _validate_posts(payload, url, run) is None:
            return None
        total_items = _int_field(payload, "total_items", url, run)
        total_pages = _int_field(payload, "total_pages", url, run)
        if total_items is None or total_pages is None:
            return None
        current = payload.get("current_page")
        if current is not None and current != page:
            run.incomplete(f"{url}: current_page={current!r}, requested page {page}")
            return None
        return total_items, total_pages

    @staticmethod
    def _record_terms(
        post: Mapping[str, Any], post_id: str, post_terms: dict[str, set[str]]
    ) -> None:
        terms = post.get("documents_category")
        bucket = post_terms.setdefault(post_id, set())
        if isinstance(terms, list):
            for term in terms:
                if isinstance(term, Mapping):
                    if (term_id := _post_id(term.get("term_id"))) is not None:
                        bucket.add(f"id:{term_id}")
                    if isinstance(term.get("slug"), str):
                        bucket.add(f"slug:{term['slug']}")

    def _reconcile(
        self,
        run: _Collection,
        taxonomy: Iterable[Mapping[str, Any]],
        post_terms: Mapping[str, set[str]],
        publish_totals: Mapping[str, int],
    ) -> None:
        """Child terms of selected roots must not count more posts than roots enumerated."""

        terms = list(taxonomy)
        roots = {term["slug"]: term for term in terms if term["parent"] == 0}
        for category in self.categories:
            if category not in roots:
                run.incomplete(f"taxonomy: selected root {category!r} is missing")
        for slug in sorted(set(roots) - set(self.categories) - REVIEWED_UNSELECTED_ROOTS):
            run.diagnostics.append(f"taxonomy: unreviewed root term {slug!r}")
        for slug, total in publish_totals.items():
            if slug in roots and roots[slug]["count"] != total:
                run.diagnostics.append(
                    f"taxonomy: {slug!r} count {roots[slug]['count']} != published total {total}"
                )
        selected_ids = {int(roots[slug]["term_id"]) for slug in self.categories if slug in roots}
        for term in terms:
            if term["parent"] not in selected_ids:
                continue
            keys = {f"id:{term['term_id']}", f"slug:{term['slug']}"}
            carried = sum(1 for found in post_terms.values() if found & keys)
            if carried < term["count"]:
                run.incomplete(
                    f"child-term reconciliation mismatch: {term['slug']!r} taxonomy count "
                    f"{term['count']}, root-enumerated posts carrying it {carried}"
                )


def make_adapter() -> Adapter:
    """Registry factory."""

    return MinistryOfPowerAdapter()
