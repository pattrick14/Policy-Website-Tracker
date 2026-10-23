"""Evidence-backed deadline extraction and the deadline index (D-007).

Deadlines are found only in sentences that carry a deadline cue (comments,
suggestions, last date, extension, ...) and a date preceded by a cue such as
"by", "on or before", or "extended to". Indian convention is assumed: numeric
dates are DD/MM/YYYY. Two-digit years, impossible dates, day/month orders that
only parse as MM/DD, and dates without a year are flagged ``ambiguous`` with
``date=None``. A date without a stated time sorts at 23:59 IST and displays as
"time not stated". The index only records the current view; reminders are
never produced here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from typing import Any

from policy_tracker.core.serialize import from_data, to_data
from policy_tracker.reporting.types import Deadline

IST = timezone(timedelta(hours=5, minutes=30), "IST")
DEFAULT_TIME = "23:59"
UNKNOWN_SORT_KEY = "9999-12-31T23:59:00+05:30"
EVIDENCE_LIMIT = 300
TIME_NOT_STATED = "time not stated"

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_RE = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_ORD = r"(?:st|nd|rd|th)?"
_DATE_RE = re.compile(
    r"(?P<iso>\b(?P<iy>\d{4})-(?P<im>\d{2})-(?P<id>\d{2})\b)"
    r"|(?P<num>\b(?P<nd>\d{1,2})[./-](?P<nm>\d{1,2})[./-](?P<ny>\d{4}|\d{2})\b)"
    rf"|(?P<dmy>\b(?P<dd>\d{{1,2}}){_ORD}(?:\s+day\s+of)?[\s-]*(?P<dm>{_MONTH_RE})"
    r"(?:[\s,-]*(?P<dy>\d{4}|'\d{2}))?\b)"
    rf"|(?P<mdy>\b(?P<mm>{_MONTH_RE})\s*(?P<md>\d{{1,2}}){_ORD}\b(?:,?\s*(?P<my>\d{{4}}))?)",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z])"
    r"|(?P<h2>\d{1,2})[:.](?P<m2>\d{2})\s*(?:hrs?\.?|hours)?(?!\d)"
    r"|(?P<h3>\d{2})(?P<m3>\d{2})\s*(?:hrs?\.?|hours)\b",
    re.IGNORECASE,
)
_TOPIC_RE = re.compile(
    r"comment|suggestion|objection|views|inputs?|feedback|submi(?:t|ssion)|last\s+date|"
    r"due\s+date|deadline|extend|extension|bids?\b|proposals?\b|"
    r"टिप्पणी|सुझाव|अंतिम\s*तिथि|अंतिम\s*तारीख",
    re.IGNORECASE,
)
_STRONG_CUE_RE = re.compile(
    r"\b(?:by|before|till|until|upto|up\s+to|not\s+later\s+than|latest\s+by|last\s+date|"
    r"due\s+date|deadline|closing\s+date|extended)\b|तक|अंतिम\s*(?:तिथि|तारीख)",
    re.IGNORECASE,
)
_WEAK_CUE_RE = re.compile(
    r"(?:\b(?:is|was|are|shall\s+be|will\s+be|now|i\.\s?e\.?|viz\.?|on)|[:–-])\W*$",
    re.IGNORECASE,
)
_POST_CUE_RE = re.compile(r"\s*(?:,\s*)?(?:\S+\s+)?तक")
_NEGATIVE_PREFIX_RE = re.compile(
    r"\b(?:dated|dt|from|notified(?:\s+on)?|issued(?:\s+on)?|published(?:\s+on)?|"
    r"uploaded(?:\s+on)?|of|since|w\.?\s?e\.?\s?f)\W*$",
    re.IGNORECASE,
)
_EXTEND_RE = re.compile(r"\bextend(?:ed|s|ing)?\b|\bextension\b", re.IGNORECASE)
_EXT_TARGET_RE = re.compile(
    r"\b(?:to|till|until|upto|up\s+to|by|on\s+or\s+before|now)\W*(?:\w+\W+){0,2}$", re.IGNORECASE
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;।])\s+(?=[A-Z(\"'ऀ-ॿ])|\n+")
_DEADLINE_FIELD_RE = re.compile(r"last[\s_-]*date|deadline|due[\s_-]*date|closing", re.IGNORECASE)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _sentences(text: str) -> list[str]:
    return [part for part in (_clean(p) for p in _SENTENCE_SPLIT_RE.split(text)) if part]


def _year(raw: str | None) -> tuple[int | None, bool]:
    """Return (year, two_digit)."""

    if not raw:
        return None, False
    raw = raw.lstrip("'")
    return int(raw), len(raw) == 2


def _resolve(match: re.Match[str]) -> tuple[str | None, bool]:
    """Return (ISO date or None, ambiguous) for one date match."""

    if match.group("iso"):
        y, m, d = int(match["iy"]), int(match["im"]), int(match["id"])
        return _iso(y, m, d), _iso(y, m, d) is None
    if match.group("num"):
        d, m = int(match["nd"]), int(match["nm"])
        y, short = _year(match["ny"])
        if short:
            return None, True
        assert y is not None
        primary = _iso(y, m, d)
        if primary is not None:
            return primary, False
        return None, True  # impossible as DD/MM, or only valid as MM/DD
    if match.group("dmy"):
        d, m = int(match["dd"]), _MONTHS[match["dm"].lower().rstrip(".")]
        y, short = _year(match["dy"])
    else:
        d, m = int(match["md"]), _MONTHS[match["mm"].lower().rstrip(".")]
        y, short = _year(match["my"])
    if y is None or short:
        return None, True
    iso = _iso(y, m, d)
    return iso, iso is None


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return Date(y, m, d).isoformat()
    except ValueError:
        return None


def _time_near(sentence: str, start: int, end: int, spans: list[tuple[int, int]]) -> str | None:
    masked = list(sentence)
    for s, e in spans:
        masked[s:e] = " " * (e - s)
    text = "".join(masked)
    windows = (text[end : end + 40], text[max(0, start - 40) : start])
    for window in windows:
        found = _TIME_RE.search(window)
        if found is None:
            continue
        if found["h"] is not None:
            hour, minute = int(found["h"]), int(found["m"] or 0)
            pm = found["ap"].lower().startswith("p")
            if not 1 <= hour <= 12:
                continue
            hour = (hour % 12) + (12 if pm else 0)
        elif found["h2"] is not None:
            hour, minute = int(found["h2"]), int(found["m2"])
        else:
            hour, minute = int(found["h3"]), int(found["m3"])
        if hour == 24 and minute == 0:
            hour, minute = 23, 59
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


def sort_key(date: str | None, time: str | None) -> str:
    """ISO datetime in IST used for ordering; 23:59 when no time is stated."""

    if date is None:
        return UNKNOWN_SORT_KEY
    return f"{date}T{time or DEFAULT_TIME}:00+05:30"


def extract_deadlines(text: str, *, method: str) -> tuple[Deadline, ...]:
    """Find deadlines in free text; ``method`` names the source (e.g. "text_regex")."""

    found: list[Deadline] = []
    for sentence in _sentences(text or ""):
        if not _TOPIC_RE.search(sentence):
            continue
        matches = list(_DATE_RE.finditer(sentence))
        if not matches:
            continue
        spans = [m.span() for m in matches]
        extend = _EXTEND_RE.search(sentence)
        previous_end = 0
        for match in matches:
            window = sentence[max(previous_end, match.start() - 100) : match.start()]
            previous_end = match.end()
            if _NEGATIVE_PREFIX_RE.search(window):
                continue
            if extend is not None:
                if match.start() < extend.start():
                    continue
                if not _EXT_TARGET_RE.search(sentence[extend.end() : match.start()] or " "):
                    continue
                how = f"{method}:extension"
            else:
                local = _STRONG_CUE_RE.search(window) is not None
                earlier = _STRONG_CUE_RE.search(sentence[: match.start()]) is not None
                after = _POST_CUE_RE.match(sentence, match.end()) is not None
                if not (local or after or (earlier and _WEAK_CUE_RE.search(window))):
                    continue
                how = method
            iso, ambiguous = _resolve(match)
            time = _time_near(sentence, match.start(), match.end(), spans)
            found.append(
                Deadline(
                    raw=match.group(0).strip(),
                    date=iso,
                    time=time,
                    sort_key=sort_key(iso, time),
                    evidence=sentence[:EVIDENCE_LIMIT],
                    method=how,
                    ambiguous=ambiguous,
                )
            )
    return _supersede(_dedupe(found))


def extract_field_deadlines(metadata: Mapping[str, str]) -> tuple[Deadline, ...]:
    """Deadlines from listing fields such as "Last date for comments"."""

    found: list[Deadline] = []
    for key in sorted(metadata):
        if not _DEADLINE_FIELD_RE.search(key):
            continue
        value = _clean(metadata[key])
        evidence = f"{key}: {value}"[:EVIDENCE_LIMIT]
        matches = list(_DATE_RE.finditer(value))
        for match in matches[:1]:
            iso, ambiguous = _resolve(match)
            time = _time_near(value, match.start(), match.end(), [m.span() for m in matches])
            found.append(
                Deadline(
                    raw=match.group(0).strip(),
                    date=iso,
                    time=time,
                    sort_key=sort_key(iso, time),
                    evidence=evidence,
                    method="listing_field",
                    ambiguous=ambiguous,
                )
            )
    return _dedupe(found)


def _dedupe(deadlines: Iterable[Deadline]) -> list[Deadline]:
    seen: set[tuple[Any, ...]] = set()
    out: list[Deadline] = []
    for deadline in deadlines:
        key = signature(deadline)
        if key in seen:
            continue
        seen.add(key)
        out.append(deadline)
    return out


def _supersede(deadlines: list[Deadline]) -> tuple[Deadline, ...]:
    """Drop non-extension dates earlier than the latest stated extension."""

    extended = [d.sort_key for d in deadlines if d.method.endswith(":extension") and d.date]
    if not extended:
        return tuple(deadlines)
    latest = max(extended)
    return tuple(
        d
        for d in deadlines
        if d.method.endswith(":extension") or d.date is None or d.sort_key >= latest
    )


def merge(*groups: Iterable[Deadline]) -> tuple[Deadline, ...]:
    """Combine deadline groups, keeping the first evidence for each date/time."""

    combined = _dedupe(d for group in groups for d in group)
    return _supersede(sorted(combined, key=lambda d: (d.sort_key, d.raw, d.method)))


def signature(deadline: Deadline) -> tuple[Any, ...]:
    """What counts as "the same deadline" when comparing runs (evidence excluded)."""

    if deadline.date is None:
        return (None, deadline.time, deadline.ambiguous, _clean(deadline.raw).lower())
    return (deadline.date, deadline.time, deadline.ambiguous, None)


def deadline_changes(
    previous: Mapping[str, tuple[Deadline, ...]],
    current: Mapping[str, tuple[Deadline, ...]],
) -> set[str]:
    """Keys whose deadline set was discovered, changed, or removed. Never reminders."""

    changed: set[str] = set()
    for key in set(previous) | set(current):
        old = {signature(d) for d in previous.get(key, ())}
        new = {signature(d) for d in current.get(key, ())}
        if old != new:
            changed.add(key)
    return changed


def is_open(deadline: Deadline, now: datetime) -> bool:
    """True for a dated deadline whose sort time is not before ``now``."""

    if deadline.date is None:
        return False
    return datetime.fromisoformat(deadline.sort_key) >= now


def display(deadline: Deadline) -> str:
    """Human-readable form, e.g. "15 Nov 2026, 17:00 IST" or "... (time not stated)"."""

    if deadline.date is None:
        return f"{_clean(deadline.raw)} (ambiguous date)"
    day = Date.fromisoformat(deadline.date)
    text = f"{day.day} {day:%b %Y}"
    text += f", {deadline.time} IST" if deadline.time else f" ({TIME_NOT_STATED})"
    if deadline.ambiguous:
        text += " (ambiguous)"
    return text


def index_to_data(index: Mapping[str, tuple[Deadline, ...]]) -> dict[str, Any]:
    """JSON-compatible form of a deadline index for ``tracker-data``."""

    return {
        "kind": "deadline_index",
        "schema_version": 1,
        "records": {key: to_data(list(index[key])) for key in sorted(index) if index[key]},
    }


def index_from_data(data: Mapping[str, Any]) -> dict[str, tuple[Deadline, ...]]:
    if data.get("kind") != "deadline_index" or data.get("schema_version") != 1:
        raise ValueError("not a deadline_index document at schema_version 1")
    return {
        key: tuple(from_data(Deadline, item) for item in items)
        for key, items in data["records"].items()
    }
