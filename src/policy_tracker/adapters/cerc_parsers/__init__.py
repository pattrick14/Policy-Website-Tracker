"""Shared HTML helpers for the CERC adapter (static pages, BeautifulSoup + lxml)."""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterable, Iterator

from bs4 import BeautifulSoup, Comment, Tag

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_SKIP_SCHEMES = ("#", "javascript:", "mailto:", "tel:")

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4,
    "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_NUMERIC_DATE = re.compile(r"(?<![\d.\-/])(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4}|\d{2})(?![\d])")
_DAY_MONTH_YEAR = re.compile(
    rf"(?<![\w])(\d{{1,2}})(?:st|nd|rd|th)?[\s,]*(?:of\s+)?({_MONTH_RE})\.?[\s,]*(\d{{4}})(?!\d)",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR = re.compile(
    rf"(?<![\w])({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?!\d)",
    re.IGNORECASE,
)
MONTH_NAME = re.compile(rf"^\s*({_MONTH_RE})\s*(?:,?\s*\d{{4}})?\s*$", re.IGNORECASE)
YEAR_LABEL = re.compile(r"^\s*(\d{4})(?:\s*-\s*(\d{2}|\d{4}))?\s*$")


def parse_html(body: bytes) -> BeautifulSoup:
    """Parse a page with lxml and drop every HTML comment (stale hrefs live there)."""

    soup = BeautifulSoup(body, "lxml")
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    return soup


def norm(text: str | None) -> str:
    return " ".join((text or "").split())


def text_of(node: Tag | None) -> str:
    return norm(node.get_text(" ")) if node is not None else ""


def find_heading(soup: BeautifulSoup | Tag, pattern: re.Pattern[str]) -> Tag | None:
    """First h1-h6 whose normalized text matches ``pattern``."""

    for heading in soup.find_all(HEADING_TAGS):
        if pattern.search(text_of(heading)):
            return heading
    return None


def usable_href(anchor: Tag) -> str | None:
    href = anchor.get("href")
    if not isinstance(href, str):
        return None
    href = href.strip()
    if not href or href.lower().startswith(_SKIP_SCHEMES):
        return None
    return href


def anchors(scope: Tag | BeautifulSoup, *, after: Tag | None = None) -> list[Tag]:
    """Anchors with a document-like href in ``scope``, optionally only those after ``after``."""

    found = [a for a in scope.find_all("a") if usable_href(a)]
    if after is None:
        return found
    following = {id(a) for a in after.find_all_next("a")}
    return [a for a in found if id(a) in following]


def scope_of(heading: Tag) -> Tag:
    """The content container of a page heading: its parent element."""

    parent = heading.parent
    return parent if isinstance(parent, Tag) else heading


def own_text(node: Tag, *, exclude: Iterable[Tag] = ()) -> str:
    """Text of ``node`` outside anchors and outside the ``exclude`` subtrees."""

    excluded = {id(tag) for tag in exclude}
    parts: list[str] = []
    for string in node.find_all(string=True):
        skip = False
        for parent in string.parents:
            if parent is node:
                break
            if parent.name == "a" or id(parent) in excluded:
                skip = True
                break
        if not skip:
            parts.append(str(string))
    return norm(" ".join(parts))


def table_rows(table: Tag) -> tuple[list[str], list[tuple[int, Tag, list[Tag]]]]:
    """Header texts and (row index, row, cells) for each data row of ``table``."""

    headers: list[str] = []
    rows: list[tuple[int, Tag, list[Tag]]] = []
    for index, row in enumerate(_own_rows(table)):
        cells = [cell for cell in row.find_all(["td", "th"], recursive=False)]
        if not cells:
            continue
        if not headers and all(cell.name == "th" for cell in cells):
            headers = [text_of(cell) for cell in cells]
            continue
        rows.append((index, row, cells))
    return headers, rows


def _own_rows(table: Tag) -> Iterator[Tag]:
    for row in table.find_all("tr"):
        if row.find_parent("table") is table:
            yield row


def span(cell: Tag) -> int:
    try:
        return max(1, int(str(cell.get("colspan", "1"))))
    except ValueError:
        return 1


def parse_date(text: str | None) -> tuple[str | None, str | None]:
    """First unambiguous date in ``text`` as (ISO date, matched source text).

    Numeric dates are day-first (DD-MM-YYYY, DD.MM.YYYY, DD/MM/YY), as CERC
    displays them; a two-digit year pivots at 50. Textual dates accept ordinals
    ("17th April, 2026", "30th, March, 2010", "6 September 2024").
    """

    if not text:
        return None, None
    candidates: list[tuple[int, str, int, int, int]] = []
    for match in _NUMERIC_DATE.finditer(text):
        day, month, year = int(match[1]), int(match[2]), match[3]
        full_year = int(year) if len(year) == 4 else _pivot(int(year))
        candidates.append((match.start(), match[0], full_year, month, day))
    for match in _DAY_MONTH_YEAR.finditer(text):
        candidates.append(
            (match.start(), match[0], int(match[3]), _MONTHS[match[2].lower()], int(match[1]))
        )
    for match in _MONTH_DAY_YEAR.finditer(text):
        candidates.append(
            (match.start(), match[0], int(match[3]), _MONTHS[match[1].lower()], int(match[2]))
        )
    for _, raw, year, month, day in sorted(candidates):
        iso = _iso(year, month, day)
        if iso:
            return iso, norm(raw)
    return None, None


def _pivot(two_digit: int) -> int:
    return 2000 + two_digit if two_digit < 50 else 1900 + two_digit


def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None
