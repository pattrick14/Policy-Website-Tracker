"""Ministry of Coal adapter (Step 4): Drupal HTML listing pages on coal.nic.in.

Every tracked Coal feed is one of four shapes:

- ``table``: attachment tables, optionally with a ``?page=N`` pager that ends
  at the page-0 "Last" link and optionally followed by an archive page (and
  its pager) reached from the "Archive ..." footer link. Archive rows carry
  ``section="archive"``; current rows of archive feeds carry ``"current"``.
- ``nested``: a report index whose rows are matters, each linking a detail
  page whose attachments are documents with ``matter_native_id``.
- ``link_list``: a main-content list of direct links.
- ``narrative``: one canonical page tracked as a matter with a content
  fingerprint.

Identity: a document's native id is its attachment href in the site's
canonical form (``core.identity.canonical_url``); a matter's is its page
href. A row without any link is kept as a linkless document whose native id is
scoped to the feed, section, title, displayed date, and occurrence order (see
``_linkless_id``). Requests go through the supplied ``Fetcher``, which applies
the D-017 rate limit; this module never sleeps.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, Tag

from policy_tracker.adapters.base import Fetcher, FetchResponse
from policy_tracker.adapters.blocks import detect_block_response
from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.identity import canonical_url, stable_hash
from policy_tracker.core.models import Provenance
from policy_tracker.core.observations import (
    CollectionEvidence,
    FeedObservation,
    ObservedDocument,
    ObservedMatter,
)

SITE_ID = "ministry_of_coal"
PARSER_VERSION = 1
ROWS_PER_PAGE = 50  # Drupal views on coal.nic.in page 50 rows
MAX_PAGES = 500  # sanity bound on a pager Last link

TABLE = "table"
NESTED = "nested"
LINK_LIST = "link_list"
NARRATIVE = "narrative"

CURRENT = "current"
ARCHIVE = "archive"

_COAL_NIC_HOSTS = frozenset({"coal.nic.in", "www.coal.nic.in"})
_FILE_SUFFIXES = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".jpg",
    ".jpeg",
    ".png",
)
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y")
_MIN_PLAUSIBLE_YEAR = 1900  # "08 Oct 0013" stays raw-only, never corrected
_FILE_SIZE = re.compile(r"File\s*Size\s*:?\s*([\d.,]+\s*[KMG]?B)", re.IGNORECASE)
_FILE_TYPE = re.compile(r"File\s*Type\s*:?\s*([A-Za-z0-9]+)", re.IGNORECASE)
_DATE_LIKE = re.compile(r"\s*\d{1,4}[/\-. ]")  # looks like a date, not a period label
_SERIAL_HEADERS = frozenset({"sn", "sno", "slno", "srno", "snumber", "serialno"})


@dataclass(frozen=True)
class FeedSpec:
    """How one tracked feed is enumerated (from its config contract and fixtures)."""

    shape: str = TABLE
    heading: str | None = None  # expected h1 text; a mismatch is only a diagnostic
    min_tables: int = 1  # fewer top-level tables on a content page is a parse failure
    doc_tables: tuple[int, ...] | None = (0,)  # indexes of attachment tables; None = all
    paged: bool = False  # page 0 must show a pager Last link
    archive_url: str | None = None  # expected rewritten archive footer href
    archive_paged: bool = False  # the archive page must show a pager Last link
    terminus_on_empty_page: bool = False  # a no-table page after page 0 is the pager end
    table_label: bool = False  # record the title-column header as metadata "table"


_NA = "https://www.coal.nic.in/nominated-authority/"
_PI = "https://www.coal.nic.in/public-information/"

FEED_SPECS: Mapping[str, FeedSpec] = {
    "coal_whats_new": FeedSpec(heading="What's New", paged=True, terminus_on_empty_page=True),
    "coal_acts_rules_policies": FeedSpec(
        heading="Acts Rules and Policies", min_tables=5, doc_tables=None, table_label=True
    ),
    "coal_monthly_statistics": FeedSpec(paged=True),
    "coal_statistical_reports": FeedSpec(paged=True),
    "coal_quarterly_reports": FeedSpec(),
    "coal_rsr_reports": FeedSpec(),
    "coal_coal_statistics": FeedSpec(),
    "coal_sustainability_reports": FeedSpec(),
    "coal_compensation_orders": FeedSpec(
        paged=True, archive_url=_NA + "archive-sanction-order-notice", archive_paged=True
    ),
    "coal_termination_orders": FeedSpec(
        heading="Termination Order",
        min_tables=2,
        doc_tables=(1,),
        archive_url=_NA + "archive-termination-order",
    ),
    "coal_national_coal_index": FeedSpec(
        heading="National Coal Index", paged=True, archive_url=_NA + "archive-national-coal-index"
    ),
    "coal_national_lignite_index": FeedSpec(),
    "coal_cabinet_summaries": FeedSpec(
        archive_url=_PI + "monthly-summary-cabinets/archive-monthly-summary-cabinets",
        archive_paged=True,
    ),
    "coal_standing_linkage_committee": FeedSpec(paged=True),
    "coal_annual_reports": FeedSpec(shape=NESTED),
    "coal_budget_reports": FeedSpec(shape=NESTED),
    "coal_other_reports": FeedSpec(shape=NESTED),
    "coal_action_plans": FeedSpec(shape=LINK_LIST, heading="Action Plan", min_tables=0),
    "coal_monthly_cabinet_reports": FeedSpec(
        paged=True,
        archive_url=_PI + "monthly-report-cabinet/archive-monthly-report-cabinet",
        archive_paged=True,
    ),
    "coal_papers_laid": FeedSpec(),
    "coal_sop": FeedSpec(),
    "coal_csr_guidelines": FeedSpec(),
    "coal_central_schemes": FeedSpec(shape=NARRATIVE, heading="Central Sector Schemes"),
    "coal_auction_allotment": FeedSpec(
        heading="Auction/Allotment",
        min_tables=2,
        doc_tables=(1,),
        paged=True,
        archive_url=_NA + "archive-auction-allotment",
        terminus_on_empty_page=True,
    ),
    "coal_minutes_of_meetings": FeedSpec(
        heading="Minutes of Meetings",
        archive_url=_PI + "minutes-metings/archive-minutes-metings",
        archive_paged=True,
    ),
    "coal_production_reviews": FeedSpec(heading="Production Reviews"),
    "coal_operationalisation_reviews": FeedSpec(min_tables=2, doc_tables=(1,)),
}

_ARCHIVE_PAGE = FeedSpec()  # archive pages hold one attachment table


# ---------------------------------------------------------------------------
# Pure helpers


def rewrite_archive_href(href: str, page_url: str) -> str:
    """Apply the tested archive_href_rewrite: force HTTPS and collapse the slashes after the host.

    ``http://www.coal.nic.in//<path>`` becomes ``https://www.coal.nic.in/<path>``;
    path and query are kept. Only coal.nic.in hosts are rewritten.
    """

    absolute = urljoin(page_url, href.strip())
    parts = urlsplit(absolute)
    if (parts.hostname or "").lower() not in _COAL_NIC_HOSTS:
        return absolute
    path = re.sub(r"^/{2,}", "/", parts.path) or "/"
    return urlunsplit(("https", parts.netloc.lower(), path, parts.query, ""))


def parse_date(raw: str | None) -> str | None:
    """ISO date for DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, or DD Mon YYYY; else None.

    Period labels ("September 2026"), empty cells, and implausible years
    ("08 Oct 0013") give None; the caller keeps the raw text.
    """

    text = " ".join((raw or "").split())
    if not text:
        return None
    text = re.sub(r"\bSept\b", "Sep", text, flags=re.IGNORECASE)
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
        if parsed.year < _MIN_PLAUSIBLE_YEAR:
            return None
        return parsed.date().isoformat()
    return None


def page_url(url: str, page: int) -> str:
    """URL of Drupal pager page ``page`` (page 0 is the bare URL)."""

    if page == 0:
        return url
    return f"{url}{'&' if '?' in url else '?'}page={page}"


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ").split())


def _content_root(html: bytes) -> Tag:
    """Parse ``html`` and return the main content element, without scripts or comments."""

    soup = BeautifulSoup(html, "lxml")
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()  # includes the injected anti-bot <script src="/bnith__...">
    return soup.find("main") or soup.body or soup


def _top_level_tables(root: Tag) -> list[Tag]:
    return [table for table in root.find_all("table") if table.find_parent("table") is None]


def _own_rows(table: Tag) -> list[Tag]:
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def _page_number(href: str, base: str) -> int | None:
    values = parse_qs(urlsplit(urljoin(base, href)).query).get("page")
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def has_pager(root: Tag) -> bool:
    return bool(root.select("nav.pager, nav[aria-label=Pagination], ul.pager__items, .pager"))


def pager_has_items(root: Tag) -> bool:
    """True when a pager shows any item (an empty pager list is the single-page shape)."""

    for pager in root.select("nav.pager, nav[aria-label=Pagination], ul.pager__items, .pager"):
        if pager.find("li") is not None or pager.find("a", href=True) is not None:
            return True
    return False


def last_page_link(root: Tag, base: str) -> int | None:
    """Page number of the pager "Last" link, or None when the page shows none."""

    for anchor in root.select("li.pager__item--last a[href]"):
        number = _page_number(anchor["href"], base)
        if number is not None:
            return number
    for pager in root.select("nav.pager, nav[aria-label=Pagination], ul.pager__items, .pager"):
        for anchor in pager.find_all("a", href=True):
            if _text(anchor).lower().startswith("last"):
                number = _page_number(anchor["href"], base)
                if number is not None:
                    return number
    return None


def archive_link(root: Tag, base: str) -> str | None:
    """Rewritten href of the "Archive ..." footer link, if the page has one."""

    for anchor in root.find_all("a", href=True):
        if _text(anchor).lower().startswith("archive"):
            return rewrite_archive_href(anchor["href"], base)
    return None


def _is_serial(header: str) -> bool:
    return re.sub(r"[^a-z]", "", header.lower()) in _SERIAL_HEADERS


@dataclass(frozen=True)
class _Columns:
    headers: tuple[str, ...]
    serial: int | None
    title: int | None
    attachment: int | None
    date: int | None


def _columns(headers: list[str]) -> _Columns:
    lowered = [header.lower() for header in headers]
    serial = next((i for i, h in enumerate(headers) if _is_serial(h)), None)
    date = next((i for i, h in enumerate(lowered) if "date" in h), None)
    attachment = next(
        (
            i
            for i, h in enumerate(lowered)
            if any(word in h for word in ("attachment", "download", "link"))
        ),
        None,
    )
    title = next((i for i, h in enumerate(lowered) if "title" in h), None)
    if title is None:
        title = next((i for i in range(len(headers)) if i not in {serial, date, attachment}), None)
    return _Columns(tuple(headers), serial, title, attachment, date)


@dataclass(frozen=True)
class Row:
    """One data row of an attachment table."""

    index: int  # position among the table's rows
    title: str
    hrefs: tuple[tuple[str, str], ...]  # (href, anchor text), distinct hrefs in order
    published_raw: str | None
    metadata: Mapping[str, str]


def table_rows(table: Tag, *, table_label: bool = False) -> list[Row]:
    """Data rows of one attachment table, keyed by its header row."""

    rows = _own_rows(table)
    header_row = next((r for r in rows if r.find("th") and not r.find("td")), None)
    headers = [_text(cell) for cell in _cells(header_row)] if header_row is not None else []
    columns = _columns(headers)
    result: list[Row] = []
    for index, row in enumerate(rows):
        if row is header_row or row.find("td") is None:
            continue
        cells = _cells(row)

        def cell(position: int | None, cells: list[Tag] = cells) -> Tag | None:
            if position is None or position >= len(cells):
                return None
            return cells[position]

        seen: dict[str, str] = {}
        for anchor in row.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            seen.setdefault(href, _text(anchor))
        title = _text(cell(columns.title)) or next(iter(seen.values()), "")
        raw_cell = cell(columns.date)
        raw = raw_cell.get_text().strip() if raw_cell is not None else ""
        metadata: dict[str, str] = {}
        if table_label and columns.title is not None and headers:
            metadata["table"] = headers[columns.title]
        for position, header in enumerate(headers):
            if position in {columns.serial, columns.title, columns.attachment, columns.date}:
                continue
            value = _text(cell(position))
            if header and value:
                metadata[header.lower()] = value
        file_text = _text(cell(columns.attachment)) or _text(row)
        if size := _FILE_SIZE.search(file_text):
            metadata["file_size"] = " ".join(size.group(1).split())
        if kind := _FILE_TYPE.search(file_text):
            metadata["file_type"] = kind.group(1).upper()
        result.append(
            Row(
                index=index,
                title=title,
                hrefs=tuple(seen.items()),
                published_raw=raw or None,
                metadata=metadata,
            )
        )
    return result


def content_fingerprint(root: Tag) -> str:
    """Hash of the main content's normalized text (scripts and comments already removed)."""

    return hashlib.sha256(_text(root).encode()).hexdigest()[:32]


def _is_file_href(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(_FILE_SUFFIXES) or "/sites/default/files/" in path


def _content_type(response: FetchResponse) -> str | None:
    for key, value in response.headers.items():
        if key.lower() == "content-type":
            return value
    return None


# ---------------------------------------------------------------------------
# Collection


class _Stop(Exception):
    """Abort the feed: the failure is already recorded in evidence."""


@dataclass
class _Collector:
    contract: FeedContract
    spec: FeedSpec
    fetcher: Fetcher
    observed_at: str
    fetch_ok: bool = True
    parse_valid: bool = True
    complete: bool = True
    blocked: bool = False
    pages_fetched: int = 0
    pages_expected: int | None = 0
    mime_types: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    matters: list[ObservedMatter] = field(default_factory=list)
    documents: list[ObservedDocument] = field(default_factory=list)
    _doc_ids: set[str] = field(default_factory=set)
    _matter_ids: set[str] = field(default_factory=set)
    _linkless: dict[tuple[str, ...], int] = field(default_factory=dict)
    _repeats: int = 0

    # -- evidence --------------------------------------------------------

    def invalid(self, message: str) -> None:
        self.parse_valid = False
        self.diagnostics.append(message)
        raise _Stop

    def incomplete(self, message: str) -> None:
        self.complete = False
        self.diagnostics.append(message)

    def expect(self, pages: int | None) -> None:
        if pages is None or self.pages_expected is None:
            self.pages_expected = None
        else:
            self.pages_expected += pages

    # -- fetching --------------------------------------------------------

    def fetch(self, url: str) -> Tag:
        """Fetch and parse one HTML page, or record why not and stop."""

        response = self.fetcher.get(url)
        if not response.ok:
            self.fetch_ok = False
            reason = response.error or f"HTTP {response.status_code}"
            self.diagnostics.append(f"fetch failed for {url}: {reason}")
            raise _Stop
        self.pages_fetched += 1
        block = detect_block_response(response)
        if block is not None:
            self.blocked = True
            self.diagnostics.append(f"block page ({block}) at {url}; stopping the feed")
            raise _Stop
        content_type = _content_type(response)
        if content_type is not None and "html" not in content_type.lower():
            self.mime_types.append(content_type)
            self.diagnostics.append(f"unexpected content type {content_type!r} at {url}")
            raise _Stop
        return _content_root(response.body)

    def check_heading(self, root: Tag, url: str, expected: str | None) -> None:
        heading = _text(root.find("h1"))
        if not heading:
            self.invalid(f"missing page heading at {url}")
        if expected and heading.lower() != expected.lower():
            self.diagnostics.append(f"heading {heading!r} differs from expected {expected!r}")

    # -- records ---------------------------------------------------------

    def native_id(self, href: str) -> str:
        return canonical_url(href, base_url=self.contract.base_url, rules=self.contract.host_rules)

    def add_row(
        self,
        row: Row,
        *,
        page: str,
        locator: str,
        listing_url: str,
        section: str | None,
        matter_native_id: str | None = None,
    ) -> None:
        published = parse_date(row.published_raw)
        if published is None and _DATE_LIKE.match(row.published_raw or ""):
            self.diagnostics.append(f"unparsed date {row.published_raw!r} kept raw ({locator})")
        if not row.hrefs:
            self.add_linkless(row, page, locator, listing_url, section, published)
            return
        multiple = len(row.hrefs) > 1
        for link_index, (href, anchor_text) in enumerate(row.hrefs):
            url = href if href.startswith(("/", "http://", "https://")) else urljoin(page, href)
            metadata = dict(row.metadata)
            if multiple and anchor_text:
                metadata["link_text"] = anchor_text
            self.add_document(
                ObservedDocument(
                    url=url,
                    title=row.title,
                    provenance=Provenance(page, f"{locator}/link[{link_index}]"),
                    native_id=self.native_id(url),
                    matter_native_id=matter_native_id,
                    published_date=published,
                    published_raw=row.published_raw,
                    section=section,
                    metadata=metadata,
                )
            )

    def add_linkless(
        self,
        row: Row,
        page: str,
        locator: str,
        listing_url: str,
        section: str | None,
        published: str | None,
    ) -> None:
        """Keep a row with no link as a linkless appearance (D-001: capture every item)."""

        title = " ".join(row.title.split())
        signature = (section or "", title, " ".join((row.published_raw or "").split()))
        ordinal = self._linkless.get(signature, 0)
        self._linkless[signature] = ordinal + 1
        native = "linkless:" + stable_hash(self.contract.feed_id, *signature, str(ordinal))
        self.add_document(
            ObservedDocument(
                url=listing_url,
                title=row.title,
                provenance=Provenance(page, locator),
                native_id=native,
                published_date=published,
                published_raw=row.published_raw,
                section=section,
                metadata={**row.metadata, "linkless": "true"},
            )
        )

    def add_document(self, document: ObservedDocument) -> None:
        if document.native_id in self._doc_ids:
            self._repeats += 1  # tie-repeated or duplicate row: keep the first provenance
            return
        self._doc_ids.add(document.native_id or "")
        self.documents.append(document)

    # -- shapes ----------------------------------------------------------

    def table_page(
        self, root: Tag, url: str, spec: FeedSpec, *, listing_url: str, section: str | None
    ) -> int:
        tables = _top_level_tables(root)
        if len(tables) < max(spec.min_tables, 1):
            self.invalid(f"expected at least {max(spec.min_tables, 1)} table(s) at {url}")
        indexes = range(len(tables)) if spec.doc_tables is None else spec.doc_tables
        if spec.doc_tables is not None and len(tables) > max(spec.min_tables, 1):
            self.diagnostics.append(f"{len(tables)} tables at {url}; using {list(indexes)}")
        count = 0
        for table_index in indexes:
            rows = table_rows(tables[table_index], table_label=spec.table_label)
            for row in rows:
                self.add_row(
                    row,
                    page=url,
                    locator=f"table[{table_index}]/row[{row.index}]",
                    listing_url=listing_url,
                    section=section,
                )
            count += len(rows)
        return count

    def table_listing(self, url: str, spec: FeedSpec, *, paged: bool, section: str | None) -> Tag:
        """Enumerate a (possibly paged) table listing; return the page-0 content root."""

        root = self.fetch(url)
        self.check_heading(root, url, spec.heading)
        last = last_page_link(root, url)
        unpaged = last is None
        if last is None:
            if paged:
                self.incomplete(f"missing pager Last page link at {url}")
            elif pager_has_items(root):
                # A populated pager without a readable Last link may hide rows on later
                # pages; never mark page 0 alone complete (removal-authority feeds). An
                # empty pager list is the observed single-page shape and stays complete.
                self.incomplete(f"pager items shown without a Last page link at {url}")
            last = 0
        elif last > MAX_PAGES:
            self.invalid(f"pager Last page {last} exceeds the bound {MAX_PAGES} at {url}")
        elif not paged:
            self.diagnostics.append(f"unexpected pager (Last page {last}) at {url}; following it")
        self.expect(last + 1)
        count = self.table_page(root, url, spec, listing_url=url, section=section)
        self.check_page_rows(count, url, last > 0, before_terminus=0 < last)
        if unpaged and not paged and count == ROWS_PER_PAGE:
            # Exactly one full pager page with no pager: rows may continue on ?page=1.
            self.incomplete(
                f"{count} rows (a full {ROWS_PER_PAGE}-row page) without a pager at {url}"
            )
        for number in range(1, last + 1):
            target = page_url(url, number)
            page_root = self.fetch(target)
            terminus = spec.terminus_on_empty_page and has_pager(page_root)
            if terminus and not _top_level_tables(page_root):
                if number >= last:
                    # The observed out-of-range shape: the Last link's own page is empty.
                    self.diagnostics.append(
                        f"{target} shows the pager without tables: pager terminus reached"
                    )
                    self.expect(number - last)
                    break
                # An empty render before the Last page is not the end of the
                # collection; keep reading later pages but never claim completeness.
                self.incomplete(
                    f"{target} shows the pager without tables before the Last page {last}"
                )
                continue
            self.check_heading(page_root, target, spec.heading)
            here = last_page_link(page_root, target)
            if here is not None and here != last:
                self.incomplete(f"pager Last page changed from {last} to {here} at {target}")
            count = self.table_page(page_root, target, spec, listing_url=url, section=section)
            self.check_page_rows(count, target, True, before_terminus=number < last)
        return root

    def check_page_rows(self, count: int, url: str, paged: bool, *, before_terminus: bool) -> None:
        if paged and count > ROWS_PER_PAGE:
            self.invalid(f"{count} rows exceed {ROWS_PER_PAGE} per pager page at {url}")
        if before_terminus and count == 0:
            self.invalid(f"empty table before the pager terminus at {url}")

    def collect_table(self) -> None:
        spec = self.spec
        url = self.contract.url or ""
        section = CURRENT if spec.archive_url else None
        root = self.table_listing(url, spec, paged=spec.paged, section=section)
        if spec.archive_url is None:
            return
        found = archive_link(root, url)
        if found is None:
            self.incomplete(f"missing Archive link at {url}")
            return
        if found != spec.archive_url:
            self.incomplete(f"Archive link {found} differs from the contract {spec.archive_url}")
            return
        self.table_listing(found, _ARCHIVE_PAGE, paged=spec.archive_paged, section=ARCHIVE)

    def collect_nested(self) -> None:
        url = self.contract.url or ""
        root = self.fetch(url)
        self.check_heading(root, url, self.spec.heading)
        tables = _top_level_tables(root)
        if not tables:
            self.invalid(f"missing parent index table at {url}")
        if last_page_link(root, url) is not None:
            self.incomplete(f"unexpected pager on parent index {url}")
        self.expect(1)
        parents: list[tuple[str, str]] = []
        for row in table_rows(tables[0]):
            locator = f"table[0]/row[{row.index}]"
            if not row.hrefs:
                self.add_row(row, page=url, locator=locator, listing_url=url, section=None)
                continue
            for link_index, (href, _anchor) in enumerate(row.hrefs):
                absolute = urljoin(url, href)
                host = (urlsplit(absolute).hostname or "").lower()
                if _is_file_href(absolute) or host not in self.contract.host_rules.allowed_hosts:
                    self.add_row(
                        replace(row, hrefs=((href, _anchor),)),
                        page=url,
                        locator=locator,
                        listing_url=url,
                        section=None,
                    )
                    continue
                native = self.native_id(href)
                if native not in self._matter_ids:
                    self._matter_ids.add(native)
                    self.matters.append(
                        ObservedMatter(
                            native_id=native,
                            title=row.title,
                            url=href,
                            provenance=Provenance(url, f"{locator}/link[{link_index}]"),
                            metadata=dict(row.metadata),
                        )
                    )
                    parents.append((absolute, native))
        self.expect(len(parents))
        for detail_url, parent_id in parents:
            detail = self.fetch(detail_url)
            self.check_heading(detail, detail_url, None)
            detail_tables = _top_level_tables(detail)
            if not detail_tables:
                self.invalid(f"missing attachment table on detail page {detail_url}")
            for row in table_rows(detail_tables[0]):
                self.add_row(
                    row,
                    page=detail_url,
                    locator=f"table[0]/row[{row.index}]",
                    listing_url=detail_url,
                    section=None,
                    matter_native_id=parent_id,
                )

    def collect_link_list(self) -> None:
        url = self.contract.url or ""
        root = self.fetch(url)
        self.check_heading(root, url, self.spec.heading)
        self.expect(1)
        lists = [
            node
            for node in root.find_all(["ol", "ul"])
            if node.find_parent(["nav", "table"]) is None
            and "pager__items" not in (node.get("class") or [])
        ]
        found = 0
        for list_index, node in enumerate(lists):
            for item_index, item in enumerate(node.find_all("li", recursive=False)):
                anchor = item.find("a", href=True)
                if anchor is None:
                    continue
                found += 1
                row = Row(item_index, _text(anchor), ((anchor["href"].strip(), ""),), None, {})
                self.add_row(
                    row,
                    page=url,
                    locator=f"list[{list_index}]/item[{item_index}]",
                    listing_url=url,
                    section=None,
                )
        if not found:
            self.invalid(f"missing main-content link list at {url}")

    def collect_narrative(self) -> None:
        url = self.contract.url or ""
        root = self.fetch(url)
        self.check_heading(root, url, self.spec.heading)
        self.expect(1)
        items = [
            [_text(item) for item in node.find_all("li", recursive=False)]
            for node in root.find_all(["ol", "ul"])
            if node.find_parent(["nav", "table"]) is None
        ]
        schemes = next((entries for entries in items if len(entries) >= 3), None)
        if schemes is None:
            self.invalid(f"missing scheme list at {url}")
        tables = _top_level_tables(root)
        if not tables:
            self.invalid(f"missing budget table at {url}")
        self.matters.append(
            ObservedMatter(
                native_id=self.native_id(url),
                title=_text(root.find("h1")),
                url=url,
                provenance=Provenance(url, "main"),
                metadata={
                    "content_fingerprint": content_fingerprint(root),
                    "schemes": "; ".join(schemes or ()),
                },
            )
        )
        for table_index, table in enumerate(tables):
            for row in table_rows(table):
                if row.hrefs:
                    self.add_row(
                        row,
                        page=url,
                        locator=f"table[{table_index}]/row[{row.index}]",
                        listing_url=url,
                        section=None,
                    )

    def run(self) -> FeedObservation:
        shapes = {
            TABLE: self.collect_table,
            NESTED: self.collect_nested,
            LINK_LIST: self.collect_link_list,
            NARRATIVE: self.collect_narrative,
        }
        try:
            if not self.contract.url:
                self.invalid("contract has no URL")
            shapes[self.spec.shape]()
        except _Stop:
            pass
        failed = not (self.fetch_ok and self.parse_valid) or self.blocked or self.mime_types
        if self._repeats:
            self.diagnostics.append(
                f"{self._repeats} repeated href sighting(s) collapsed; first provenance kept"
            )
        return FeedObservation(
            feed_id=self.contract.feed_id,
            site_id=SITE_ID,
            observed_at=self.observed_at,
            parser_version=PARSER_VERSION,
            evidence=CollectionEvidence(
                fetch_ok=self.fetch_ok,
                parse_valid=self.parse_valid,
                enumeration_complete=self.complete and not failed,
                block_page_detected=self.blocked,
                pages_fetched=self.pages_fetched,
                pages_expected=self.pages_expected,
                unexpected_mime_types=tuple(self.mime_types),
                diagnostics=tuple(self.diagnostics),
            ),
            matters=tuple(self.matters),
            documents=tuple(self.documents),
        )


class MinistryOfCoalAdapter:
    """Adapter for every tracked ``ministry_of_coal`` feed."""

    site_id = SITE_ID
    parser_version = PARSER_VERSION

    def __init__(self, specs: Mapping[str, FeedSpec] | None = None) -> None:
        self.specs = dict(FEED_SPECS if specs is None else specs)

    def feed_ids(self) -> frozenset[str]:
        return frozenset(self.specs)

    def collect(
        self, contract: FeedContract, fetcher: Fetcher, *, observed_at: str
    ) -> FeedObservation:
        spec = self.specs.get(contract.feed_id)
        if spec is None or contract.site_id != SITE_ID:
            raise ValueError(f"{contract.feed_id!r} is not a Ministry of Coal feed")
        return _Collector(contract, spec, fetcher, observed_at).run()


def make_adapter() -> MinistryOfCoalAdapter:
    return MinistryOfCoalAdapter()


__all__ = [
    "FEED_SPECS",
    "FeedSpec",
    "MinistryOfCoalAdapter",
    "make_adapter",
    "parse_date",
    "rewrite_archive_href",
]
