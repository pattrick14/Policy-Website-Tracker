"""CERC adapter: static HTML feeds on cercind.gov.in (Step 5).

One parser per tracked feed (``config/sources.yaml`` site ``cerc``). Every page
is parsed with BeautifulSoup + lxml after HTML comments are removed, because
CERC keeps stale and withdrawn hrefs inside comments.

Identity: documents are keyed by their source-scoped canonical URL (the engine
fallback), so a file listed on several feeds (for example the home bulletin
and a draft-regulation row) is one document. Row-level native ids from the
contracts (displayed serial, petition number, matter text) identify matters,
and child documents point at them through ``matter_native_id``. Archive years,
petition numbers, leaf pages and posting dates are kept as metadata.

Evidence: a failed or non-2xx fetch sets ``fetch_ok`` false; a missing heading,
table, year group, accordion panel or a malformed row (the contract's
``failure_indicators``) sets ``parse_valid`` false; ``enumeration_complete`` is
true only when every required page was fetched and parsed. Collection stops at
the first failed page because the engine quarantines the whole run anyway.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from policy_tracker.adapters.base import Fetcher
from policy_tracker.adapters.blocks import detect_block_response
from policy_tracker.adapters.cerc_parsers import (
    HEADING_TAGS,
    MONTH_NAME,
    YEAR_LABEL,
    anchors,
    find_heading,
    norm,
    own_text,
    parse_date,
    parse_html,
    scope_of,
    span,
    table_rows,
    text_of,
    usable_href,
)
from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.identity import canonical_url, stable_hash
from policy_tracker.core.models import Provenance
from policy_tracker.core.observations import (
    CollectionEvidence,
    FeedObservation,
    ObservedDocument,
    ObservedMatter,
)

SITE_ID = "cerc"
PARSER_VERSION = 1

_SERIAL = re.compile(r"^\s*(\d+)\s*\.?\s*$")


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Collection session


@dataclass
class _Collector:
    """Fetches pages for one feed and accumulates records and evidence."""

    contract: FeedContract
    fetcher: Fetcher
    observed_at: str
    matters: list[ObservedMatter] = field(default_factory=list)
    documents: list[ObservedDocument] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    mime_types: list[str] = field(default_factory=list)
    fetch_ok: bool = True
    parse_valid: bool = True
    blocked: bool = False
    terminated: bool = True  # the contract's termination rule was met
    pages_fetched: int = 0
    pages_expected: int = 0
    _seen_documents: set[str] = field(default_factory=set)
    _seen_matters: set[str] = field(default_factory=set)

    @property
    def failed(self) -> bool:
        return not (self.fetch_ok and self.parse_valid) or self.blocked or bool(self.mime_types)

    def page_url(self, href: str, *, relative_to: str | None = None) -> str:
        """Absolute canonical URL to fetch (spaces percent-encoded, canonical host)."""

        absolute = urljoin(relative_to or self.contract.base_url, href)
        return canonical_url(
            absolute, base_url=self.contract.base_url, rules=self.contract.host_rules
        )

    def fetch(self, url: str) -> BeautifulSoup | None:
        """Fetch and parse one required page; ``None`` (with evidence) on any failure."""

        self.pages_expected += 1
        try:
            response = self.fetcher.get(url)
        except Exception as exc:  # noqa: BLE001 - adapters never raise for source failures
            self.fetch_ok = False
            self.diagnostics.append(f"{url}: fetch raised {type(exc).__name__}: {exc}")
            return None
        if not response.ok:
            self.fetch_ok = False
            reason = response.error or f"HTTP {response.status_code}"
            self.diagnostics.append(f"{url}: fetch failed ({reason})")
            return None
        self.pages_fetched += 1
        mime = _header(response.headers, "content-type")
        if mime and "html" not in mime.lower():
            self.mime_types.append(mime.split(";")[0].strip())
            self.diagnostics.append(f"{url}: unexpected content type {mime!r}")
            return None
        reason = detect_block_response(response)
        if reason:
            self.blocked = True
            self.diagnostics.append(f"{url}: block page detected ({reason})")
            return None
        return parse_html(response.body)

    def invalid(self, url: str, message: str) -> None:
        self.parse_valid = False
        self.diagnostics.append(f"{url}: {message}")

    def heading(self, soup: BeautifulSoup, url: str, pattern: str, label: str) -> Tag | None:
        found = find_heading(soup, _rx(pattern))
        if found is None:
            self.invalid(url, f"missing {label} heading")
        return found

    def add_document(
        self,
        anchor: Tag,
        page: str,
        locator: str,
        *,
        title: str | None = None,
        matter: str | None = None,
        date_text: str | None = None,
        published_raw: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        href = usable_href(anchor)
        if href is None:
            return
        url = urljoin(page, href)
        key = self.page_url(url)
        if key in self._seen_documents:
            return  # same file listed twice (e.g. home and view-all): first sighting wins
        self._seen_documents.add(key)
        link_text = text_of(anchor)
        iso, raw = parse_date(date_text) if date_text is not None else (None, None)
        if published_raw is not None:
            raw = published_raw
        self.documents.append(
            ObservedDocument(
                url=url,
                title=norm(title) if title else link_text or href,
                provenance=Provenance(page_url=page, locator=locator),
                matter_native_id=matter,
                published_date=iso,
                published_raw=raw,
                metadata={k: v for k, v in (metadata or {}).items() if v},
            )
        )

    def add_matter(
        self,
        native_id: str,
        title: str,
        page: str,
        locator: str,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        if native_id in self._seen_matters:
            return native_id
        self._seen_matters.add(native_id)
        self.matters.append(
            ObservedMatter(
                native_id=native_id,
                title=title,
                url=None,
                provenance=Provenance(page_url=page, locator=locator),
                metadata={k: v for k, v in (metadata or {}).items() if v},
            )
        )
        return native_id

    def observation(self) -> FeedObservation:
        complete = not self.failed and self.terminated and self.pages_fetched == self.pages_expected
        evidence = CollectionEvidence(
            fetch_ok=self.fetch_ok,
            parse_valid=self.parse_valid and not self.blocked,
            enumeration_complete=complete,
            block_page_detected=self.blocked,
            pages_fetched=self.pages_fetched,
            pages_expected=self.pages_expected,
            unexpected_mime_types=tuple(self.mime_types),
            diagnostics=tuple(self.diagnostics),
        )
        return FeedObservation(
            feed_id=self.contract.feed_id,
            site_id=SITE_ID,
            observed_at=self.observed_at,
            parser_version=PARSER_VERSION,
            evidence=evidence,
            matters=tuple(self.matters),
            documents=tuple(self.documents),
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


# ---------------------------------------------------------------------------
# Shared page shapes


def _first_table(
    heading: Tag | None, soup: BeautifulSoup, header_pattern: str | None
) -> Tag | None:
    """First table after ``heading`` (or anywhere) whose header row matches."""

    candidates = heading.find_all_next("table") if heading is not None else soup.find_all("table")
    for table in candidates:
        if header_pattern is None:
            return table
        headers, _ = table_rows(table)
        if _rx(header_pattern).search(" | ".join(headers)):
            return table
    return None


def _field(headers: list[str], cells: list[Tag], pattern: str) -> Tag | None:
    """The cell under the first header matching ``pattern`` (colspans honoured)."""

    regex = _rx(pattern)
    column = next((i for i, h in enumerate(headers) if regex.search(h)), None)
    if column is None:
        return None
    position = 0
    for cell in cells:
        if position == column:
            return cell
        position += span(cell)
    return None


def _cells_width(cells: list[Tag]) -> int:
    return sum(span(cell) for cell in cells)


@dataclass(frozen=True)
class _TableSpec:
    """A flat attachment table: every link in a data row is a document."""

    heading: str | None
    label: str
    header: str | None = None  # header-row pattern that identifies the table
    published: str | None = None  # header of the published-date column
    fields: Mapping[str, str] = field(default_factory=dict)  # metadata key -> header pattern
    subject: str | None = None  # header whose text prefixes generic link titles
    strict_width: bool = True  # a linked row narrower than the header row is malformed


def _parse_table(
    c: _Collector,
    url: str,
    spec: _TableSpec,
    *,
    soup: BeautifulSoup | None = None,
    extra: Mapping[str, str] | None = None,
) -> int:
    """Parse one table page; returns the number of documents found."""

    soup = soup if soup is not None else c.fetch(url)
    if soup is None:
        return 0
    heading = None
    if spec.heading is not None:
        heading = c.heading(soup, url, spec.heading, spec.label)
        if heading is None:
            return 0
    table = _first_table(heading, soup, spec.header)
    if table is None:
        c.invalid(url, f"missing {spec.label} table")
        return 0
    headers, rows = table_rows(table)
    found = 0
    for row_index, row, cells in rows:
        links = [a for cell in cells for a in anchors(cell)]
        if not links:
            continue
        if spec.strict_width and headers and _cells_width(cells) != len(headers):
            c.invalid(url, f"malformed {spec.label} row {row_index}: {len(cells)} cells")
            continue
        metadata = dict(extra or {})
        for key, pattern in spec.fields.items():
            cell = _field(headers, cells, pattern)
            if cell is not None:
                metadata[key] = text_of(cell)
        published_cell = _field(headers, cells, spec.published) if spec.published else None
        subject_cell = _field(headers, cells, spec.subject) if spec.subject else None
        subject = text_of(subject_cell) if subject_cell is not None else ""
        for link_index, anchor in enumerate(links):
            title = text_of(anchor)
            if subject and subject != title:
                title = f"{subject} ({title})" if title else subject
            elif not title:
                title = own_text(row)
            date_text = text_of(published_cell) if published_cell is not None else None
            c.add_document(
                anchor,
                url,
                f"table/row[{row_index}]/link[{link_index}]",
                title=title,
                date_text=date_text,
                published_raw=date_text or None,
                metadata=metadata,
            )
            found += 1
    if not found:
        c.invalid(url, f"unexpected empty {spec.label} table")
    return found


def _parse_links(
    c: _Collector,
    url: str,
    heading_pattern: str,
    label: str,
    *,
    soup: BeautifulSoup | None = None,
    heading_required: bool = True,
    exclude_href: re.Pattern[str] | None = None,
    date_from_text: bool = True,
) -> int:
    """Parse a link-list page: every link after the heading is a document."""

    soup = soup if soup is not None else c.fetch(url)
    if soup is None:
        return 0
    heading = find_heading(soup, _rx(heading_pattern))
    if heading is None and heading_required:
        c.invalid(url, f"missing {label} heading")
        return 0
    scope = scope_of(heading) if heading is not None else (soup.body or soup)
    found = 0
    for index, anchor in enumerate(anchors(scope, after=heading)):
        href = usable_href(anchor) or ""
        if exclude_href is not None and exclude_href.search(href):
            continue
        text = text_of(anchor)
        c.add_document(
            anchor, url, f"links/link[{index}]", date_text=text if date_from_text else None
        )
        found += 1
    if not found:
        c.invalid(url, f"unexpected empty {label} list")
    return found


# ---------------------------------------------------------------------------
# Evolving collections: draft/current regulations, discussion papers


@dataclass(frozen=True)
class _EvolvingSpec:
    heading: str
    label: str
    prefix: str  # matter native id namespace
    year_groups: bool = False  # missing year-group rows is a failure indicator
    require_serial_one: bool = False  # termination rule "serial 1"
    gazette_no: str | None = None
    gazette_date: str | None = None
    history_class: re.Pattern[str] = re.compile(r"amend", re.IGNORECASE)


def _parse_evolving(c: _Collector, spec: _EvolvingSpec) -> None:
    url = c.page_url(c.contract.url or "")
    soup = c.fetch(url)
    if soup is None:
        return
    heading = c.heading(soup, url, spec.heading, spec.label)
    if heading is None:
        return
    table = _first_table(heading, soup, r"sl\.?\s*no|s\.\s*no")
    if table is None:
        c.invalid(url, f"missing {spec.label} table")
        return
    headers, rows = table_rows(table)
    year: str | None = None
    year_groups = 0
    serials: set[str] = set()
    history: list[tuple[Tag, str, str, int]] = []
    for row_index, row, cells in rows:
        if len(cells) == 1:
            label = text_of(cells[0])
            if YEAR_LABEL.match(label):
                year, year_groups = label, year_groups + 1
            elif anchors(cells[0]):
                c.invalid(url, f"malformed {spec.label} row {row_index}: single linked cell")
            continue
        serial_match = _SERIAL.match(text_of(cells[0]))
        links = [a for cell in cells[1:] for a in anchors(cell)]
        if serial_match is None:
            if links:
                c.invalid(url, f"malformed {spec.label} row {row_index}: no serial number")
            continue
        serial = serial_match[1]
        serials.add(serial)
        body = cells[1]
        history_nodes = [
            tag
            for tag in body.find_all(True)
            if any(spec.history_class.search(cls) for cls in tag.get("class") or [])
        ]
        history_ids = {id(a) for node in history_nodes for a in node.find_all("a")}
        metadata = {"serial": serial, "year_group": year or ""}
        date_cell = _field(headers, cells, spec.gazette_date) if spec.gazette_date else None
        date_text = text_of(date_cell) if date_cell is not None else None
        if spec.gazette_no:
            no_cell = _field(headers, cells, spec.gazette_no)
            metadata["gazette_no"] = text_of(no_cell) if no_cell is not None else ""
        if date_text:
            metadata["gazette_date"] = date_text
        title = own_text(body, exclude=history_nodes) or f"{spec.label} {serial}"
        matter = c.add_matter(
            f"{spec.prefix}:{serial}", title, url, f"table/row[{row_index}]", metadata
        )
        for link_index, anchor in enumerate(links):
            locator = f"table/row[{row_index}]/link[{link_index}]"
            if id(anchor) in history_ids:
                history.append((anchor, matter, locator, row_index))
                continue
            c.add_document(
                anchor,
                url,
                locator,
                matter=matter,
                date_text=date_text,
                published_raw=date_text,
                metadata={"serial": serial},
            )
    # Nested amendment-history links point at files that are usually the primary
    # files of older rows; emitting them after every primary link lets the
    # engine's first-sighting rule keep the file's own row as its parent.
    for anchor, matter, locator, _ in history:
        c.add_document(anchor, url, locator, matter=matter, metadata={"role": "amendment_history"})
    if not serials:
        c.invalid(url, f"unexpected empty {spec.label} table")
    if spec.year_groups and not year_groups:
        c.invalid(url, f"missing year groups in {spec.label} table")
    if spec.require_serial_one and serials and "1" not in serials:
        c.terminated = False
        c.diagnostics.append(f"{url}: serial 1 not reached; enumeration incomplete")


def _draft_regulations(c: _Collector) -> None:
    _parse_evolving(
        c,
        _EvolvingSpec(
            heading=r"^draft regulation",
            label="Draft Regulation",
            prefix="draft_reg",
            year_groups=True,
        ),
    )


def _current_regulations(c: _Collector) -> None:
    _parse_evolving(
        c,
        _EvolvingSpec(
            heading=r"current regulation",
            label="Current Regulations",
            prefix="current_reg",
            gazette_no=r"gazette\s*no",
            gazette_date=r"date",
        ),
    )


def _discussion_papers(c: _Collector) -> None:
    _parse_evolving(
        c,
        _EvolvingSpec(
            heading=r"discussion paper",
            label="Discussion Paper",
            prefix="disc_paper",
            require_serial_one=True,
        ),
    )


# ---------------------------------------------------------------------------
# Home bulletin (rolling)


def _home(c: _Collector) -> None:
    home = c.page_url(c.contract.url or "index-en.html")
    soup = c.fetch(home)
    if soup is None:
        return
    view_all = next((a for a in anchors(soup) if _rx(r"^view\s*all$").match(text_of(a))), None)
    _bulletin(c, home, soup)
    if c.failed:
        return
    view_href = usable_href(view_all) if view_all is not None else None
    if view_href is None:
        c.diagnostics.append(f"{home}: View All link not found; using viewall.html")
    view_url = c.page_url(view_href or "viewall.html", relative_to=home)
    view_soup = c.fetch(view_url)
    if view_soup is not None:
        _bulletin(c, view_url, view_soup)


def _bulletin(c: _Collector, url: str, soup: BeautifulSoup) -> None:
    heading = find_heading(soup, _rx(r"what'?s\s*new|view\s*all"))
    if heading is None:
        c.invalid(url, "missing bulletin heading")
        return
    container = heading.find_next(["ul", "table", "ol"])
    if container is None:
        c.invalid(url, "missing bulletin list")
        return
    links = [a for a in anchors(container) if not _rx(r"^view\s*all$").match(text_of(a))]
    if not links:
        c.invalid(url, "unexpected empty bulletin")
    for index, anchor in enumerate(links):
        c.add_document(anchor, url, f"bulletin/link[{index}]")


# ---------------------------------------------------------------------------
# Orders and ROPs


_ORDER_FIELDS = {
    "petition_no": r"petition",
    "posted_date": r"posting",
    "category": r"category",
}


def _orders_spec(label: str, heading: str | None, published: str) -> _TableSpec:
    return _TableSpec(
        heading=heading,
        label=label,
        header=r"petition",
        published=published,
        fields=_ORDER_FIELDS,
        strict_width=True,
    )


def _recent_orders(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    _parse_table(c, url, _orders_spec("order", r"^orders\b", r"date of order"))


def _recent_rops(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    spec = _orders_spec("ROP", r"records? of proceedings|\brops?\b", r"date of hearing")
    _parse_table(c, url, spec)


def _year_archives(c: _Collector, spec: _TableSpec, label: str) -> None:
    """Year index page, then every linked annual page (each must hold its table)."""

    index = c.page_url(c.contract.url or "")
    soup = c.fetch(index)
    if soup is None:
        return
    years: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in anchors(soup.body or soup):
        text = text_of(anchor)
        href = usable_href(anchor) or ""
        if not YEAR_LABEL.match(text) or not href.lower().split("?")[0].endswith((".html", ".htm")):
            continue
        page = c.page_url(href, relative_to=index)
        if page not in seen and page != index:
            seen.add(page)
            years.append((text, page))
    if not years:
        c.invalid(index, f"missing annual {label} archive links")
        return
    c.diagnostics.append(f"{index}: {len(years)} annual {label} pages listed")
    for year, page in years:
        _parse_table(c, page, spec, extra={"archive_year": year})
        if c.failed:
            return


def _order_archives(c: _Collector) -> None:
    _year_archives(c, _orders_spec("order", None, r"date of order"), "order")


def _rop_archives(c: _Collector) -> None:
    _year_archives(c, _orders_spec("ROP", None, r"date of hearing"), "ROP")


# ---------------------------------------------------------------------------
# Papers, reports, notices, advice


def _simple_table(spec: _TableSpec) -> Callable[[_Collector], None]:
    def parse(c: _Collector) -> None:
        _parse_table(c, c.page_url(c.contract.url or ""), spec)

    return parse


_working_papers = _simple_table(_TableSpec(heading=r"working paper", label="Working Paper"))
_study_reports = _simple_table(
    _TableSpec(heading=r"study report", label="Study Reports", published=r"date")
)
_public_notices = _simple_table(
    _TableSpec(heading=r"^public notice", label="Public Notice", published=r"date")
)


def _market_monitoring_parser(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    spec = _TableSpec(
        heading=r"market monitoring",
        label="Market Monitoring",
        fields={"reporting_year": r"year"},
    )
    soup = c.fetch(url)
    if soup is None:
        return
    before = len(c.documents)
    _parse_table(c, url, spec, soup=soup)
    # Reporting-period label is the published value (date_semantics: period label).
    c.documents[before:] = [
        _with(doc, published_raw=doc.metadata.get("reporting_year"),
              title=f"{doc.title} {doc.metadata.get('reporting_year', '')}".strip())
        for doc in c.documents[before:]
    ]  # fmt: skip


def _annual_reports(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    before = len(c.documents)
    _parse_links(c, url, r"annual report", "Annual Report", date_from_text=False)
    c.documents[before:] = [
        _with(doc, published_raw=doc.title, title=f"Annual Report {doc.title}")
        if YEAR_LABEL.match(doc.title)
        else doc
        for doc in c.documents[before:]
    ]


def _other_reports(c: _Collector) -> None:
    _parse_links(c, c.page_url(c.contract.url or ""), r"other report", "Other Report",
                 date_from_text=False)  # fmt: skip


def _statutory_advice(c: _Collector) -> None:
    _parse_links(c, c.page_url(c.contract.url or ""), r"advice to government", "Advice")


def _statutory_public_notices(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    soup = c.fetch(url)
    if soup is None:
        return
    heading = c.heading(soup, url, r"statutory public notice", "Statutory Public Notice")
    if heading is None:
        return
    table = _first_table(heading, soup, r"pet")
    if table is None:
        c.invalid(url, "missing Statutory Public Notice table")
        return
    headers, rows = table_rows(table)
    found = 0
    for row_index, row, cells in rows:
        links = [a for cell in cells for a in anchors(cell)]
        if not links:
            continue
        if headers and _cells_width(cells) != len(headers):
            c.invalid(url, f"malformed Statutory Public Notice row {row_index}")
            continue
        petition_cell = _field(headers, cells, r"pet")
        subject_cell = _field(headers, cells, r"subject")
        date_cell = _field(headers, cells, r"date")
        petition = text_of(petition_cell) if petition_cell is not None else ""
        subject = text_of(subject_cell) if subject_cell is not None else own_text(row)
        date_text = text_of(date_cell) if date_cell is not None else None
        matter = None
        if petition:
            matter = c.add_matter(
                f"spn:{petition}", subject or petition, url, f"table/row[{row_index}]",
                {"petition_no": petition},
            )  # fmt: skip
        for link_index, anchor in enumerate(links):
            link_text = text_of(anchor)
            language = (
                "hindi" if "hindi" in link_text.lower()
                else "english" if "english" in link_text.lower() else ""
            )  # fmt: skip
            c.add_document(
                anchor,
                url,
                f"table/row[{row_index}]/link[{link_index}]",
                title=f"{subject} ({link_text})" if subject else link_text,
                matter=matter,
                date_text=date_text,
                published_raw=date_text,
                metadata={"petition_no": petition, "language": language},
            )
            found += 1
    if not found:
        c.invalid(url, "unexpected empty Statutory Public Notice table")


# ---------------------------------------------------------------------------
# Hearings


def _year_of(node: Tag, top: Tag | None) -> str | None:
    """Year group of ``node``: a ``data-year`` ancestor or the preceding year label."""

    for parent in node.parents:
        year = parent.get("data-year") if isinstance(parent, Tag) else None
        if isinstance(year, str) and YEAR_LABEL.match(year):
            return year
    for previous in node.find_all_previous([*HEADING_TAGS, "strong", "b"]):
        if top is not None and previous is top:
            break
        if YEAR_LABEL.match(text_of(previous)):
            return text_of(previous)
    return None


def _public_hearings(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    soup = c.fetch(url)
    if soup is None:
        return
    heading = c.heading(soup, url, r"public hearing", "Public Hearing")
    if heading is None:
        return
    years: set[str] = set()
    found = 0
    for index, anchor in enumerate(anchors(scope_of(heading), after=heading)):
        block = anchor.find_parent(["p", "li", "tr"])
        year = _year_of(anchor, heading)
        if year:
            years.add(year)
        matter = None
        if block is not None:
            text = own_text(block)
            if text:
                key = stable_hash(year or "", text.lower())[:16]
                matter = c.add_matter(
                    f"public_hearing:{key}", text, url, f"matter[{year or ''}]",
                    {"year_group": year or ""},
                )  # fmt: skip
        c.add_document(
            anchor, url, f"links/link[{index}]", matter=matter, metadata={"year_group": year or ""}
        )
        found += 1
    if not found:
        c.invalid(url, "unexpected empty Public Hearing list")
    elif not years:
        c.invalid(url, "missing year groups in Public Hearing list")


_LAST_MODIFIED = re.compile(r"last\s*modified\s*:?\s*(.+)$", re.IGNORECASE)


def _hearing_schedule(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    soup = c.fetch(url)
    if soup is None:
        return
    heading = c.heading(soup, url, r"hearing schedule", "Hearing Schedule")
    if heading is None:
        return
    months: set[str] = set()
    found = 0
    for index, anchor in enumerate(anchors(scope_of(heading), after=heading)):
        month = ""
        previous = anchor.find_previous([*HEADING_TAGS, "strong", "b"])
        if previous is not None and previous is not heading and MONTH_NAME.match(text_of(previous)):
            month = text_of(previous)
            months.add(month)
        text = text_of(anchor)
        modified = _LAST_MODIFIED.search(text)
        title = text[: modified.start()].strip() if modified else text
        raw = norm(modified[1]) if modified else None
        c.add_document(
            anchor,
            url,
            f"links/link[{index}]",
            title=title or text,
            date_text=raw,
            published_raw=raw,
            metadata={"month": month},
        )
        found += 1
    if not found:
        c.invalid(url, "unexpected empty Hearing Schedule")
    elif not months:
        c.invalid(url, "missing month groups in Hearing Schedule")


# ---------------------------------------------------------------------------
# Minutes (D-018)


def _commission_minutes(c: _Collector) -> None:
    _parse_links(c, c.page_url(c.contract.url or ""), r"minutes of the meeting", "Minutes")


def _cac_minutes(c: _Collector) -> None:
    _parse_links(c, c.page_url(c.contract.url or ""), r"advisory committee", "CAC minutes")


def _coordination_forum_minutes(c: _Collector) -> None:
    _parse_links(
        c,
        c.page_url(c.contract.url or ""),
        r"coordination forum",
        "Coordination Forum minutes",
        heading_required=False,
    )


def _other_meeting_minutes(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    soup = c.fetch(url)
    if soup is None:
        return
    heading = c.heading(soup, url, r"minutes of the meetings", "Minutes of the Meetings")
    if heading is None:
        return
    table = _first_table(heading, soup, None)
    if table is None:
        c.invalid(url, "missing Minutes of the Meetings table")
        return
    _, rows = table_rows(table)
    found = 0
    for row_index, row, _cells in rows:
        meeting = own_text(row)  # a meeting date or an application period: kept as text
        for link_index, anchor in enumerate(anchors(row)):
            link_text = text_of(anchor)
            c.add_document(
                anchor,
                url,
                f"table/row[{row_index}]/link[{link_index}]",
                title=f"{meeting} ({link_text})" if meeting else link_text,
                metadata={"meeting": meeting},
            )
            found += 1
    if not found:
        c.invalid(url, "unexpected empty Minutes of the Meetings table")


# ---------------------------------------------------------------------------
# Tariff inputs (D-018): price indices and escalation rates


_CIL_PAGE = re.compile(r"coal_price_index_base\.html", re.IGNORECASE)


def _price_indices(c: _Collector) -> None:
    url = c.page_url(c.contract.url or "")
    soup = c.fetch(url)
    if soup is None:
        return
    if soup.select(".panel-collapse, .panel-group") == []:
        c.invalid(url, "missing accordion panel")
        return
    base_href = next(
        (usable_href(a) for a in anchors(soup) if _CIL_PAGE.search(usable_href(a) or "")),
        None,
    )
    _parse_links(c, url, r"coal price index", "Coal Price Index", soup=soup, exclude_href=_CIL_PAGE)
    if c.failed:
        return
    if base_href is None:
        c.invalid(url, "missing link to the CIL price notification list")
        return
    base_url = c.page_url(base_href, relative_to=url)
    _parse_links(c, base_url, r"coal india limited price notification", "CIL notification")


_HUB_LEAF = re.compile(r"escalation_rates_(gen|tran)\w*?_(\d{4})\.html", re.IGNORECASE)


def _escalation_rates(c: _Collector) -> None:
    index = c.page_url(c.contract.url or "")
    soup = c.fetch(index)
    if soup is None:
        return
    heading = c.heading(soup, index, r"escalation rates", "Escalation Rates")
    if heading is None:
        return
    years: list[tuple[str, str]] = []
    for anchor in anchors(scope_of(heading), after=heading):
        text = text_of(anchor)
        if YEAR_LABEL.match(text):
            page = c.page_url(usable_href(anchor) or "", relative_to=index)
            if page not in {p for _, p in years}:
                years.append((text, page))
    if not years:
        c.invalid(index, "missing year links on the Escalation Rates index")
        return
    c.diagnostics.append(f"{index}: {len(years)} escalation year pages listed")
    for year, page in years:
        year_soup = c.fetch(page)
        if year_soup is None:
            return
        leaves = {
            match[1].lower(): c.page_url(usable_href(a) or "", relative_to=page)
            for a in anchors(year_soup)
            if (match := _HUB_LEAF.search(usable_href(a) or ""))
        }
        if leaves:  # 2011+ hub page: generation and transmission leaf pages
            for series in ("gen", "tran"):
                if series not in leaves:
                    c.invalid(page, f"missing {series} leaf link on escalation hub {year}")
                    return
            for series, leaf in (("generation", leaves["gen"]), ("transmission", leaves["tran"])):
                leaf_soup = c.fetch(leaf)
                if leaf_soup is None:
                    return
                _escalation_leaf(c, leaf, leaf_soup, year, series)
                if c.failed:
                    return
        elif year.isdigit() and int(year) >= 2011:
            c.invalid(page, f"missing generation and transmission links on escalation hub {year}")
            return
        else:  # 2005-2010 year pages list documents directly
            _escalation_leaf(c, page, year_soup, year, "combined")
            if c.failed:
                return


def _escalation_leaf(c: _Collector, url: str, soup: BeautifulSoup, year: str, series: str) -> None:
    heading = c.heading(soup, url, r"escalation rates", "Escalation Rates leaf")
    if heading is None:
        return
    panels = soup.select(".panel-collapse")
    if series == "generation" and year.isdigit() and int(year) >= 2022 and not panels:
        c.invalid(url, "missing accordion panel on generation page")
        return
    leaf_title = text_of(heading)
    found = 0
    for index, anchor in enumerate(anchors(scope_of(heading), after=heading)):
        href = usable_href(anchor) or ""
        if href.lower().split("?")[0].endswith((".html", ".htm")):
            continue  # navigation, not a notification
        panel = anchor.find_parent(class_="panel")
        panel_title = text_of(panel.find(class_="panel-title")) if panel is not None else ""
        link_text = text_of(anchor)
        context = panel_title or leaf_title
        c.add_document(
            anchor,
            url,
            f"leaf/link[{index}]",
            title=f"{context}: {link_text}" if link_text else context,
            date_text=link_text,
            metadata={
                "archive_year": year,
                "series": series,
                "panel": panel_title,
                "leaf_page": url,
            },
        )
        found += 1
    if not found:
        c.invalid(url, "unexpected empty escalation leaf page")


# ---------------------------------------------------------------------------
# Adapter


def _with(doc: ObservedDocument, **changes: str | None) -> ObservedDocument:
    return replace(doc, **changes)  # type: ignore[arg-type]


FEED_PARSERS: dict[str, Callable[[_Collector], None]] = {
    "cerc_home": _home,
    "cerc_draft_regulations": _draft_regulations,
    "cerc_current_regulations": _current_regulations,
    "cerc_discussion_papers": _discussion_papers,
    "cerc_working_papers": _working_papers,
    "cerc_study_reports": _study_reports,
    "cerc_recent_orders": _recent_orders,
    "cerc_order_archives": _order_archives,
    "cerc_recent_rops": _recent_rops,
    "cerc_rop_archives": _rop_archives,
    "cerc_market_monitoring": _market_monitoring_parser,
    "cerc_annual_reports": _annual_reports,
    "cerc_other_reports": _other_reports,
    "cerc_public_hearings": _public_hearings,
    "cerc_hearing_schedule": _hearing_schedule,
    "cerc_public_notices": _public_notices,
    "cerc_statutory_public_notices": _statutory_public_notices,
    "cerc_statutory_advice": _statutory_advice,
    "cerc_commission_minutes": _commission_minutes,
    "cerc_cac_minutes": _cac_minutes,
    "cerc_other_meeting_minutes": _other_meeting_minutes,
    "cerc_coordination_forum_minutes": _coordination_forum_minutes,
    "cerc_price_indices": _price_indices,
    "cerc_escalation_rates": _escalation_rates,
}


class CercAdapter:
    """Adapter for every tracked CERC feed."""

    site_id = SITE_ID
    parser_version = PARSER_VERSION

    def feed_ids(self) -> frozenset[str]:
        return frozenset(FEED_PARSERS)

    def collect(
        self, contract: FeedContract, fetcher: Fetcher, *, observed_at: str
    ) -> FeedObservation:
        parser = FEED_PARSERS.get(contract.feed_id)
        if parser is None or contract.site_id != SITE_ID:
            raise ValueError(f"CERC adapter cannot collect feed {contract.feed_id!r}")
        collector = _Collector(contract=contract, fetcher=fetcher, observed_at=observed_at)
        try:
            parser(collector)
        except Exception as exc:  # noqa: BLE001 - unexpected markup is reported, not raised
            collector.invalid(contract.url or "", f"parser error {type(exc).__name__}: {exc}")
        return collector.observation()


def make_adapter() -> CercAdapter:
    return CercAdapter()
