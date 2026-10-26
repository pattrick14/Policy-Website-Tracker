"""Deterministic, rule-based digest classification (D-004).

Rules live in ``config/taxonomy.yaml``; see the comments there for the decision
order. ``classify`` returns the category plus every rule that decided it, so a
reader can audit why an item landed in a section.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from policy_tracker.core.models import EventType
from policy_tracker.reporting.types import Category

DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parents[3] / "config" / "taxonomy.yaml"

CATEGORY_NAMES: dict[str, Category] = {
    "action_required": Category.ACTION_REQUIRED,
    "rules_and_regulation": Category.RULES_AND_REGULATION,
    "policy_and_direction": Category.POLICY_AND_DIRECTION,
    "research_and_reporting": Category.RESEARCH_AND_REPORTING,
    "routine": Category.ROUTINE,
}
CATEGORY_KEYS: dict[Category, str] = {value: key for key, value in CATEGORY_NAMES.items()}

FALLBACK_REASON = "fallback:unclassified"


class TaxonomyError(ValueError):
    """Raised when ``taxonomy.yaml`` is malformed."""


@dataclass(frozen=True)
class Term:
    source: str  # the term as written in the taxonomy
    pattern: re.Pattern[str]

    def search(self, text: str) -> bool:
        return self.pattern.search(text) is not None


@dataclass(frozen=True)
class Taxonomy:
    text_scan_chars: int
    title_terms: Mapping[Category, tuple[Term, ...]]
    labels: Mapping[Category, str]
    action_text_terms: tuple[Term, ...]
    promote_terms: tuple[Term, ...]
    promoted_default: Category
    routine_feeds: frozenset[str]
    feed_defaults: Mapping[str, Category]
    feed_site_prefixes: Mapping[str, str]

    def site_for_feed(self, feed_id: str) -> str:
        for prefix, site in sorted(self.feed_site_prefixes.items(), key=lambda kv: -len(kv[0])):
            if feed_id.startswith(prefix):
                return site
        return ""


def compile_term(term: object) -> Term:
    """Compile one taxonomy term (see the syntax notes in ``taxonomy.yaml``)."""

    if isinstance(term, Mapping):
        name, spec = term.get("name"), term.get("match")
        if not isinstance(name, str) or not name.strip():
            raise TaxonomyError(f"named term needs a name: {term!r}")
        return Term(source=name, pattern=compile_term(spec).pattern)
    if not isinstance(term, str) or not term.strip():
        raise TaxonomyError(f"invalid term {term!r}")
    if term.startswith("re_cs:"):
        pattern = re.compile(term[6:])
    elif term.startswith("re:"):
        pattern = re.compile(term[3:], re.IGNORECASE)
    elif any(ord(ch) > 127 for ch in term):
        pattern = re.compile(re.escape(term))
    else:
        words = [re.escape(word) for word in term.split()]
        body = r"\s+".join(words)
        pattern = re.compile(rf"(?<!\w){body}(?:s|es)?(?!\w)", re.IGNORECASE)
    return Term(source=term, pattern=pattern)


def _category(name: object, where: str) -> Category:
    if name not in CATEGORY_NAMES:
        raise TaxonomyError(f"{where}: unknown category {name!r}")
    return CATEGORY_NAMES[str(name)]


def _terms(values: object, where: str) -> tuple[Term, ...]:
    if values is None:
        return ()
    if not isinstance(values, Sequence) or isinstance(values, str):
        raise TaxonomyError(f"{where}: expected a list of terms")
    return tuple(compile_term(value) for value in values)


def parse_taxonomy(data: Mapping[str, object]) -> Taxonomy:
    """Build a ``Taxonomy`` from the parsed YAML document."""

    if not isinstance(data, Mapping) or data.get("schema_version") != 2:
        raise TaxonomyError("taxonomy.yaml must be a mapping with schema_version: 2")
    categories = data.get("categories")
    if not isinstance(categories, Mapping):
        raise TaxonomyError("taxonomy.yaml: missing categories")
    title_terms: dict[Category, tuple[Term, ...]] = {}
    labels: dict[Category, str] = {}
    for name, spec in categories.items():
        category = _category(name, "categories")
        if not isinstance(spec, Mapping):
            raise TaxonomyError(f"categories.{name}: expected a mapping")
        if spec.get("priority") != int(category):
            raise TaxonomyError(f"categories.{name}: priority must be {int(category)}")
        title_terms[category] = _terms(spec.get("title_terms"), f"categories.{name}")
        labels[category] = str(spec.get("label") or name)
    promote = data.get("promote") or {}
    if not isinstance(promote, Mapping):
        raise TaxonomyError("promote: expected a mapping")
    defaults = data.get("feed_defaults") or {}
    prefixes = data.get("feed_site_prefixes") or {}
    if not isinstance(defaults, Mapping) or not isinstance(prefixes, Mapping):
        raise TaxonomyError("feed_defaults and feed_site_prefixes must be mappings")
    return Taxonomy(
        text_scan_chars=int(data.get("text_scan_chars", 4000)),
        title_terms=title_terms,
        labels=labels,
        action_text_terms=_terms(data.get("action_text_terms"), "action_text_terms"),
        promote_terms=_terms(promote.get("terms"), "promote.terms"),
        promoted_default=_category(
            promote.get("promoted_default", "policy_and_direction"), "promote"
        ),
        routine_feeds=frozenset(str(feed) for feed in data.get("routine_feeds") or ()),
        feed_defaults={
            str(feed): _category(value, f"feed_defaults.{feed}") for feed, value in defaults.items()
        },
        feed_site_prefixes={str(key): str(value) for key, value in prefixes.items()},
    )


@lru_cache(maxsize=8)
def load_taxonomy(path: str | Path | None = None) -> Taxonomy:
    """Load and compile ``config/taxonomy.yaml`` (cached per path)."""

    target = Path(path) if path is not None else DEFAULT_TAXONOMY_PATH
    try:
        with target.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise TaxonomyError(f"cannot read {target}: {exc}") from exc
    return parse_taxonomy(data)


def _first_match(terms: Sequence[Term], text: str) -> Term | None:
    for term in terms:
        if term.search(text):
            return term
    return None


def _by_title(taxonomy: Taxonomy, title: str, *, skip_routine: bool) -> list[tuple[Category, str]]:
    found: list[tuple[Category, str]] = []
    for category in sorted(taxonomy.title_terms):
        if skip_routine and category is Category.ROUTINE:
            continue
        term = _first_match(taxonomy.title_terms[category], title)
        if term is not None:
            found.append((category, term.source))
    return found


def classify(
    title: str,
    text: str,
    feed_id: str,
    site_id: str,
    event_type: EventType | None = None,
    *,
    taxonomy: Taxonomy | None = None,
) -> tuple[Category, tuple[str, ...]]:
    """Return ``(category, reasons)`` for one record.

    ``text`` is listing metadata plus extracted document text; only its first
    ``text_scan_chars`` characters are scanned. ``site_id`` is accepted for
    future site-specific rules and is not used by the current taxonomy.
    """

    del site_id
    tax = taxonomy or load_taxonomy()
    title = " ".join((title or "").split())
    head = (text or "")[: tax.text_scan_chars]
    reasons: list[str] = []

    routine_term = _first_match(tax.title_terms.get(Category.ROUTINE, ()), title)
    gated = feed_id in tax.routine_feeds or routine_term is not None
    promoted = False
    if gated:
        promote_term = _first_match(tax.promote_terms, title) or _first_match(
            tax.promote_terms, head
        )
        if promote_term is None:
            if feed_id in tax.routine_feeds:
                reasons.append(f"feed_default:{feed_id}")
            if routine_term is not None:
                reasons.append(f"title:routine:{routine_term.source}")
            category = Category.ROUTINE
        else:
            promoted = True
            reasons.append(f"promoted:{promote_term.source}")
    if not gated or promoted:
        matches = _by_title(tax, title, skip_routine=True)
        if matches:
            category, term = matches[0]
            reasons.append(f"title:{CATEGORY_KEYS[category]}:{term}")
        elif not promoted and feed_id in tax.feed_defaults:
            category = tax.feed_defaults[feed_id]
            reasons.append(f"feed_default:{feed_id}")
        else:
            text_matches = _by_title(tax, head, skip_routine=True) if head else []
            if text_matches:
                category, term = text_matches[0]
                reasons.append(f"text:{CATEGORY_KEYS[category]}:{term}")
            elif promoted:
                category = tax.promoted_default
                reasons.append(f"promoted_default:{CATEGORY_KEYS[category]}")
            else:
                category = Category.ROUTINE
                reasons.append(FALLBACK_REASON)

    action = _first_match(tax.action_text_terms, head) if head else None
    if action is not None and (promoted or not gated) and category is not Category.ACTION_REQUIRED:
        category = Category.ACTION_REQUIRED
        reasons.append(f"text:action_required:{action.source}")
    if event_type is EventType.DEADLINE_CHANGED and category is not Category.ACTION_REQUIRED:
        category = Category.ACTION_REQUIRED
        reasons.append("event:deadline_changed")
    return category, tuple(reasons)
