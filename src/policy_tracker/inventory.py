"""Validation for the Step 1 source-inventory research gate."""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ALLOWED_RESEARCH_STATUSES = {"preliminary", "complete"}
ALLOWED_DECISIONS = {"track", "indirect", "exclude"}
ALLOWED_KINDS = {
    "authoritative_collection",
    "detail_parent",
    "discovery",
    "evolving_collection",
    "rolling",
    "rotating_archive",
}
ALLOWED_DISCOVERY_METHODS = {"api", "linked_detail", "navigation", "search", "sitemap"}
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REQUIRED_FEED_FIELDS = {
    "id",
    "purpose",
    "discovery_method",
    "kind",
    "decision",
    "enumeration",
    "identity",
    "date_semantics",
    "expected_mime_types",
    "language_behavior",
    "refresh_policy",
    "completeness",
    "history",
    "evidence_urls",
    "fixtures",
    "verified",
    "last_verified",
}
REQUIRED_COVERAGE_DECISION_FIELDS = {
    "scope",
    "decision",
    "reason",
    "evidence_urls",
    "coverage_limit",
    "refresh_policy",
    "fixtures",
    "last_verified",
}


def load_inventory(path: Path) -> Any:
    """Load an inventory YAML document."""

    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def validate_inventory(data: Any, *, require_complete: bool = False) -> list[str]:
    """Return stable, human-readable validation errors for an inventory document."""

    errors: list[str] = []
    if not isinstance(data, Mapping):
        return ["root: expected a mapping"]

    if data.get("schema_version") != 1:
        errors.append("schema_version: expected 1")

    sites = data.get("sites")
    if not _is_list(sites) or not sites:
        errors.append("sites: expected a non-empty list")
        return errors

    site_ids: set[str] = set()
    feed_ids: set[str] = set()
    for site_index, site in enumerate(sites):
        prefix = f"sites[{site_index}]"
        if not isinstance(site, Mapping):
            errors.append(f"{prefix}: expected a mapping")
            continue

        site_id = site.get("id")
        _validate_id(site_id, f"{prefix}.id", errors)
        if isinstance(site_id, str):
            if site_id in site_ids:
                errors.append(f"{prefix}.id: duplicate site id {site_id!r}")
            site_ids.add(site_id)

        for field in ("name", "base_url"):
            _require_text(site, field, prefix, errors)
        _validate_url(site.get("base_url"), f"{prefix}.base_url", errors)

        allowed_hosts = site.get("allowed_hosts")
        if not _is_string_list(allowed_hosts) or not allowed_hosts:
            errors.append(f"{prefix}.allowed_hosts: expected a non-empty string list")

        required_feed_ids = site.get("required_feed_ids")
        if not _is_string_list(required_feed_ids) or not required_feed_ids:
            errors.append(f"{prefix}.required_feed_ids: expected a non-empty string list")

        coverage_decisions = site.get("coverage_decisions")
        if not _is_list(coverage_decisions) or not coverage_decisions:
            errors.append(f"{prefix}.coverage_decisions: expected a non-empty list")
        else:
            for decision_index, decision in enumerate(coverage_decisions):
                _validate_coverage_decision(
                    decision,
                    f"{prefix}.coverage_decisions[{decision_index}]",
                    errors,
                    require_complete,
                )

        status = site.get("research_status")
        if status not in ALLOWED_RESEARCH_STATUSES:
            errors.append(
                f"{prefix}.research_status: expected one of {sorted(ALLOWED_RESEARCH_STATUSES)}"
            )
        if require_complete and status != "complete":
            errors.append(f"{prefix}.research_status: research gate requires 'complete'")

        feeds = site.get("feeds")
        if not _is_list(feeds) or not feeds:
            errors.append(f"{prefix}.feeds: expected a non-empty list")
            continue
        for feed_index, feed in enumerate(feeds):
            feed_prefix = f"{prefix}.feeds[{feed_index}]"
            _validate_feed(feed, feed_prefix, errors, feed_ids, require_complete)
        if require_complete and _is_string_list(required_feed_ids):
            configured_ids = {
                feed.get("id") for feed in feeds if isinstance(feed, Mapping)
            }
            for missing_id in sorted(set(required_feed_ids) - configured_ids):
                errors.append(
                    f"{prefix}.required_feed_ids: required feed {missing_id!r} is not configured"
                )

    return errors


def _validate_coverage_decision(
    coverage: Any,
    prefix: str,
    errors: list[str],
    require_complete: bool,
) -> None:
    if not isinstance(coverage, Mapping):
        errors.append(f"{prefix}: expected a mapping")
        return

    missing = sorted(REQUIRED_COVERAGE_DECISION_FIELDS - set(coverage))
    for field in missing:
        errors.append(f"{prefix}.{field}: required field is missing")

    for field in ("scope", "reason", "coverage_limit"):
        _require_text(coverage, field, prefix, errors)
    _validate_enum(coverage, "decision", ALLOWED_DECISIONS, prefix, errors)

    if not isinstance(coverage.get("refresh_policy"), Mapping) or not coverage.get(
        "refresh_policy"
    ):
        errors.append(f"{prefix}.refresh_policy: expected a non-empty mapping")

    evidence_urls = coverage.get("evidence_urls")
    if not _is_string_list(evidence_urls) or not evidence_urls:
        errors.append(f"{prefix}.evidence_urls: expected a non-empty string list")
    for index, url in enumerate(evidence_urls or []):
        _validate_url(url, f"{prefix}.evidence_urls[{index}]", errors)

    fixtures = coverage.get("fixtures")
    if not _is_string_list(fixtures):
        errors.append(f"{prefix}.fixtures: expected a string list")
    if require_complete and not fixtures:
        errors.append(f"{prefix}.fixtures: research gate requires at least one fixture")

    last_verified = coverage.get("last_verified")
    if not isinstance(last_verified, str) or not DATE_PATTERN.fullmatch(last_verified):
        errors.append(f"{prefix}.last_verified: expected an ISO date (YYYY-MM-DD)")


def _validate_feed(
    feed: Any,
    prefix: str,
    errors: list[str],
    feed_ids: set[str],
    require_complete: bool,
) -> None:
    if not isinstance(feed, Mapping):
        errors.append(f"{prefix}: expected a mapping")
        return

    missing = sorted(REQUIRED_FEED_FIELDS - set(feed))
    for field in missing:
        errors.append(f"{prefix}.{field}: required field is missing")

    feed_id = feed.get("id")
    _validate_id(feed_id, f"{prefix}.id", errors)
    if isinstance(feed_id, str):
        if feed_id in feed_ids:
            errors.append(f"{prefix}.id: duplicate feed id {feed_id!r}")
        feed_ids.add(feed_id)

    _require_text(feed, "purpose", prefix, errors)
    if ("url" in feed) == ("url_template" in feed):
        errors.append(f"{prefix}: provide exactly one of url or url_template")
    endpoint = feed.get("url", feed.get("url_template"))
    _validate_url(endpoint, f"{prefix}.endpoint", errors, allow_template="url_template" in feed)

    _validate_enum(feed, "discovery_method", ALLOWED_DISCOVERY_METHODS, prefix, errors)
    _validate_enum(feed, "kind", ALLOWED_KINDS, prefix, errors)
    _validate_enum(feed, "decision", ALLOWED_DECISIONS, prefix, errors)

    for field in ("enumeration", "identity", "date_semantics", "refresh_policy", "completeness", "history"):
        if not isinstance(feed.get(field), Mapping) or not feed.get(field):
            errors.append(f"{prefix}.{field}: expected a non-empty mapping")

    for field in ("expected_mime_types", "evidence_urls"):
        if not _is_string_list(feed.get(field)) or not feed.get(field):
            errors.append(f"{prefix}.{field}: expected a non-empty string list")
    for index, url in enumerate(feed.get("evidence_urls", [])):
        _validate_url(url, f"{prefix}.evidence_urls[{index}]", errors)

    _require_text(feed, "language_behavior", prefix, errors)
    fixtures = feed.get("fixtures")
    if not _is_string_list(fixtures):
        errors.append(f"{prefix}.fixtures: expected a string list")

    verified = feed.get("verified")
    if not isinstance(verified, bool):
        errors.append(f"{prefix}.verified: expected a boolean")
    last_verified = feed.get("last_verified")
    if not isinstance(last_verified, str) or not DATE_PATTERN.fullmatch(last_verified):
        errors.append(f"{prefix}.last_verified: expected an ISO date (YYYY-MM-DD)")

    if require_complete:
        if verified is not True:
            errors.append(f"{prefix}.verified: research gate requires true")
        if not fixtures:
            errors.append(f"{prefix}.fixtures: research gate requires at least one fixture")
        history = feed.get("history")
        if isinstance(history, Mapping) and history.get("boundary_verified") is not True:
            errors.append(f"{prefix}.history.boundary_verified: research gate requires true")


def _validate_id(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        errors.append(f"{path}: expected lower_snake_case identifier")


def _validate_url(value: Any, path: str, errors: list[str], *, allow_template: bool = False) -> None:
    if not isinstance(value, str):
        errors.append(f"{path}: expected an HTTPS URL")
        return
    candidate = re.sub(r"\{[a-z_]+\}", "placeholder", value) if allow_template else value
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        errors.append(f"{path}: expected an HTTPS URL")


def _validate_enum(
    mapping: Mapping[str, Any],
    field: str,
    allowed: set[str],
    prefix: str,
    errors: list[str],
) -> None:
    if mapping.get(field) not in allowed:
        errors.append(f"{prefix}.{field}: expected one of {sorted(allowed)}")


def _require_text(mapping: Mapping[str, Any], field: str, prefix: str, errors: list[str]) -> None:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{prefix}.{field}: expected non-empty text")


def _is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_string_list(value: Any) -> bool:
    return _is_list(value) and all(isinstance(item, str) and item.strip() for item in value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=Path("config/sources.yaml"))
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="also enforce the Step 1 exit gate",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        data = load_inventory(args.config)
    except (OSError, yaml.YAMLError) as exc:
        print(f"{args.config}: {exc}")
        return 2

    errors = validate_inventory(data, require_complete=args.require_complete)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"{args.config}: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
