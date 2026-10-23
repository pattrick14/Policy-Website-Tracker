"""Validation for the source inventory and feed-contract configuration.

Structural validation is a closed schema: every top-level, default, site, feed,
completeness, and coverage-decision key must be known and well typed. The
``--require-complete`` flag additionally enforces the Step 1 research gate.
"""

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
# Connection-level failures that may trigger a host fallback (D-020). HTTP
# error statuses and block pages are deliberately absent.
ALLOWED_HOST_FALLBACK_TRIGGERS = {
    "connect_timeout",
    "tls_handshake_timeout",
    "read_timeout",
    "connection_reset",
    "empty_reply",
}
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HOST_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
)
PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_]+)\}")

TOP_LEVEL_KEYS = {"schema_version", "defaults", "contract_templates", "sites"}

REQUIRED_SITE_FIELDS = {
    "id",
    "name",
    "base_url",
    "allowed_hosts",
    "required_feed_ids",
    "coverage_decisions",
    "research_status",
    "feeds",
}
# Fetch-policy keys a site may set; unset keys fall back to ``defaults``.
SITE_FETCH_FIELDS = {
    "request_rate_per_host_per_second",
    "request_timeout_seconds",
    "max_redirects",
    "max_document_bytes",
    "rate_limit_shared_across_hosts",
    "canonical_host",
    "fallback_hosts",
    "host_fallback_on",
    "max_attempts_per_url",
    "retry_backoff_seconds",
    "abort_after_consecutive_failed_urls",
}
OPTIONAL_SITE_FIELDS = SITE_FETCH_FIELDS | {"collapse_duplicate_slashes"}
# Host-specific keys are site-only; everything else may also be a global default.
DEFAULT_FIELDS = (SITE_FETCH_FIELDS - {"canonical_host", "fallback_hosts", "host_fallback_on"}) | {
    "timezone",
    "language_policy",
}

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
OPTIONAL_FEED_FIELDS = {
    "url",
    "url_template",
    "archive_url",
    "archive_url_template",
    "archive_href_rewrite",
    "status_passes",
    "categories",
    "coverage_revision",
    "parser_version",
}
REQUIRED_COMPLETENESS_FIELDS = {"removal_authority", "failure_indicators"}
OPTIONAL_COMPLETENESS_FIELDS = {"removal_rule", "child_term_reconciliation", "notes"}
ARCHIVE_HREF_REWRITE_FIELDS = {"applies_to", "rewrite_to", "rule"}
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

    _reject_unknown_keys(data, TOP_LEVEL_KEYS, "root", errors)
    if data.get("schema_version") != 1:
        errors.append("schema_version: expected 1")

    if "defaults" in data:
        defaults = data["defaults"]
        if not isinstance(defaults, Mapping):
            errors.append("defaults: expected a mapping")
        else:
            _reject_unknown_keys(defaults, DEFAULT_FIELDS, "defaults", errors)
            for field in ("timezone", "language_policy"):
                if field in defaults:
                    _require_text(defaults, field, "defaults", errors)
            _validate_fetch_settings(defaults, "defaults", errors, allowed_hosts=None)
    if "contract_templates" in data and not isinstance(data["contract_templates"], Mapping):
        errors.append("contract_templates: expected a mapping")

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

        _reject_unknown_keys(site, REQUIRED_SITE_FIELDS | OPTIONAL_SITE_FIELDS, prefix, errors)

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
        host_set: set[str] | None = None
        if not _is_string_list(allowed_hosts) or not allowed_hosts:
            errors.append(f"{prefix}.allowed_hosts: expected a non-empty string list")
        else:
            host_set = set(allowed_hosts)
            for index, host in enumerate(allowed_hosts):
                if not HOST_PATTERN.fullmatch(host):
                    errors.append(
                        f"{prefix}.allowed_hosts[{index}]: expected a lowercase host name"
                    )
            _validate_host_in(site.get("base_url"), host_set, f"{prefix}.base_url", errors)

        _validate_fetch_settings(site, prefix, errors, allowed_hosts=host_set)
        if "collapse_duplicate_slashes" in site:
            _require_bool(site, "collapse_duplicate_slashes", prefix, errors)

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
            _validate_feed(feed, feed_prefix, errors, feed_ids, require_complete, host_set)
        if require_complete and _is_string_list(required_feed_ids):
            configured_ids = {feed.get("id") for feed in feeds if isinstance(feed, Mapping)}
            for missing_id in sorted(set(required_feed_ids) - configured_ids):
                errors.append(
                    f"{prefix}.required_feed_ids: required feed {missing_id!r} is not configured"
                )

    return errors


def _validate_fetch_settings(
    mapping: Mapping[str, Any],
    prefix: str,
    errors: list[str],
    *,
    allowed_hosts: set[str] | None,
) -> None:
    """Validate fetch-policy keys present on ``defaults`` or a site (D-017, D-020)."""

    for field in ("request_rate_per_host_per_second", "request_timeout_seconds"):
        if field in mapping and not _is_positive_number(mapping[field]):
            errors.append(f"{prefix}.{field}: expected a positive number")
    if "max_redirects" in mapping and not _is_int(mapping["max_redirects"], minimum=0):
        errors.append(f"{prefix}.max_redirects: expected a non-negative integer")
    for field in (
        "max_document_bytes",
        "max_attempts_per_url",
        "abort_after_consecutive_failed_urls",
    ):
        if field in mapping and not _is_int(mapping[field], minimum=1):
            errors.append(f"{prefix}.{field}: expected an integer >= 1")
    if "rate_limit_shared_across_hosts" in mapping:
        _require_bool(mapping, "rate_limit_shared_across_hosts", prefix, errors)
    if "retry_backoff_seconds" in mapping:
        backoff = mapping["retry_backoff_seconds"]
        if not _is_list(backoff) or not all(_is_number(item) and item >= 0 for item in backoff):
            errors.append(
                f"{prefix}.retry_backoff_seconds: expected a list of non-negative numbers"
            )

    if "canonical_host" in mapping:
        host = mapping["canonical_host"]
        if not isinstance(host, str) or not host.strip():
            errors.append(f"{prefix}.canonical_host: expected non-empty text")
        elif allowed_hosts is not None and host not in allowed_hosts:
            errors.append(f"{prefix}.canonical_host: {host!r} is not in allowed_hosts")
    if "fallback_hosts" in mapping:
        hosts = mapping["fallback_hosts"]
        if not _is_string_list(hosts) or not hosts:
            errors.append(f"{prefix}.fallback_hosts: expected a non-empty string list")
        elif allowed_hosts is not None:
            for host in hosts:
                if host not in allowed_hosts:
                    errors.append(f"{prefix}.fallback_hosts: {host!r} is not in allowed_hosts")
    if "host_fallback_on" in mapping:
        triggers = mapping["host_fallback_on"]
        if not _is_string_list(triggers) or not triggers:
            errors.append(f"{prefix}.host_fallback_on: expected a non-empty string list")
        else:
            for trigger in triggers:
                if trigger not in ALLOWED_HOST_FALLBACK_TRIGGERS:
                    errors.append(
                        f"{prefix}.host_fallback_on: {trigger!r} is not one of "
                        f"{sorted(ALLOWED_HOST_FALLBACK_TRIGGERS)}"
                    )
            if "fallback_hosts" not in mapping:
                errors.append(f"{prefix}.host_fallback_on: requires fallback_hosts")


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
    _reject_unknown_keys(coverage, REQUIRED_COVERAGE_DECISION_FIELDS, prefix, errors)

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
    for index, url in enumerate(evidence_urls if _is_list(evidence_urls) else []):
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
    allowed_hosts: set[str] | None,
) -> None:
    if not isinstance(feed, Mapping):
        errors.append(f"{prefix}: expected a mapping")
        return

    missing = sorted(REQUIRED_FEED_FIELDS - set(feed))
    for field in missing:
        errors.append(f"{prefix}.{field}: required field is missing")
    _reject_unknown_keys(feed, REQUIRED_FEED_FIELDS | OPTIONAL_FEED_FIELDS, prefix, errors)

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
    if "url_template" in feed:
        _validate_template(feed["url_template"], f"{prefix}.url_template", errors)
    _validate_host_in(endpoint, allowed_hosts, f"{prefix}.endpoint", errors)

    if "archive_url" in feed:
        _validate_url(feed["archive_url"], f"{prefix}.archive_url", errors)
        _validate_host_in(feed["archive_url"], allowed_hosts, f"{prefix}.archive_url", errors)
    if "archive_url_template" in feed:
        template = feed["archive_url_template"]
        _validate_url(template, f"{prefix}.archive_url_template", errors, allow_template=True)
        _validate_template(template, f"{prefix}.archive_url_template", errors)
        _validate_host_in(template, allowed_hosts, f"{prefix}.archive_url_template", errors)
        if "url_template" not in feed:
            errors.append(f"{prefix}.archive_url_template: requires url_template")
    if "archive_href_rewrite" in feed:
        _validate_archive_href_rewrite(
            feed["archive_href_rewrite"], f"{prefix}.archive_href_rewrite", errors
        )
    if "status_passes" in feed:
        _validate_unique_string_list(feed, "status_passes", prefix, errors)
    if "categories" in feed:
        _validate_unique_string_list(feed, "categories", prefix, errors)
    template_fields = set(PLACEHOLDER_PATTERN.findall(str(feed.get("url_template", ""))))
    if "category" in template_fields and "categories" not in feed:
        errors.append(f"{prefix}.categories: required when url_template uses {{category}}")
    for field in ("coverage_revision", "parser_version"):
        if field in feed and not _is_int(feed[field], minimum=1):
            errors.append(f"{prefix}.{field}: expected an integer >= 1")

    _validate_enum(feed, "discovery_method", ALLOWED_DISCOVERY_METHODS, prefix, errors)
    _validate_enum(feed, "kind", ALLOWED_KINDS, prefix, errors)
    _validate_enum(feed, "decision", ALLOWED_DECISIONS, prefix, errors)

    for field in (
        "enumeration",
        "identity",
        "date_semantics",
        "refresh_policy",
        "completeness",
        "history",
    ):
        if not isinstance(feed.get(field), Mapping) or not feed.get(field):
            errors.append(f"{prefix}.{field}: expected a non-empty mapping")
    completeness = feed.get("completeness")
    if isinstance(completeness, Mapping) and completeness:
        _validate_completeness(completeness, feed.get("kind"), f"{prefix}.completeness", errors)

    for field in ("expected_mime_types", "evidence_urls"):
        if not _is_string_list(feed.get(field)) or not feed.get(field):
            errors.append(f"{prefix}.{field}: expected a non-empty string list")
    evidence_urls = feed.get("evidence_urls")
    for index, url in enumerate(evidence_urls if _is_list(evidence_urls) else []):
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


def _validate_completeness(
    completeness: Mapping[str, Any], kind: Any, prefix: str, errors: list[str]
) -> None:
    for field in sorted(REQUIRED_COMPLETENESS_FIELDS - set(completeness)):
        errors.append(f"{prefix}.{field}: required field is missing")
    _reject_unknown_keys(
        completeness, REQUIRED_COMPLETENESS_FIELDS | OPTIONAL_COMPLETENESS_FIELDS, prefix, errors
    )
    if "removal_authority" in completeness:
        _require_bool(completeness, "removal_authority", prefix, errors)
        if kind == "rolling" and completeness["removal_authority"] is not False:
            errors.append(f"{prefix}.removal_authority: rolling feeds never establish removal")
    if "failure_indicators" in completeness:
        indicators = completeness["failure_indicators"]
        if not _is_string_list(indicators) or not indicators:
            errors.append(f"{prefix}.failure_indicators: expected a non-empty string list")
    for field in sorted(OPTIONAL_COMPLETENESS_FIELDS & set(completeness)):
        _require_text(completeness, field, prefix, errors)


def _validate_archive_href_rewrite(rewrite: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(rewrite, Mapping):
        errors.append(f"{prefix}: expected a mapping")
        return
    for field in sorted(ARCHIVE_HREF_REWRITE_FIELDS - set(rewrite)):
        errors.append(f"{prefix}.{field}: required field is missing")
    _reject_unknown_keys(rewrite, ARCHIVE_HREF_REWRITE_FIELDS, prefix, errors)
    for field in sorted(ARCHIVE_HREF_REWRITE_FIELDS & set(rewrite)):
        _require_text(rewrite, field, prefix, errors)


def _validate_template(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        return  # _validate_url already reported the type
    if not PLACEHOLDER_PATTERN.search(value):
        errors.append(f"{path}: expected at least one {{placeholder}}")
    if "{" in PLACEHOLDER_PATTERN.sub("", value) or "}" in PLACEHOLDER_PATTERN.sub("", value):
        errors.append(f"{path}: placeholders must be lower_snake_case names in braces")


def _validate_host_in(
    value: Any, allowed_hosts: set[str] | None, path: str, errors: list[str]
) -> None:
    if allowed_hosts is None or not isinstance(value, str):
        return
    host = urlparse(PLACEHOLDER_PATTERN.sub("placeholder", value)).hostname
    if host and host not in allowed_hosts:
        errors.append(f"{path}: host {host!r} is not in the site's allowed_hosts")


def _validate_unique_string_list(
    mapping: Mapping[str, Any], field: str, prefix: str, errors: list[str]
) -> None:
    value = mapping.get(field)
    if not _is_string_list(value) or not value:
        errors.append(f"{prefix}.{field}: expected a non-empty string list")
    elif len(set(value)) != len(value):
        errors.append(f"{prefix}.{field}: entries must be unique")


def _reject_unknown_keys(
    mapping: Mapping[str, Any], allowed: set[str], prefix: str, errors: list[str]
) -> None:
    for key in sorted(set(mapping) - allowed, key=str):
        errors.append(f"{prefix}.{key}: unknown key")


def _validate_id(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        errors.append(f"{path}: expected lower_snake_case identifier")


def _validate_url(
    value: Any, path: str, errors: list[str], *, allow_template: bool = False
) -> None:
    if not isinstance(value, str):
        errors.append(f"{path}: expected an HTTPS URL")
        return
    candidate = PLACEHOLDER_PATTERN.sub("placeholder", value) if allow_template else value
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


def _require_bool(mapping: Mapping[str, Any], field: str, prefix: str, errors: list[str]) -> None:
    if not isinstance(mapping.get(field), bool):
        errors.append(f"{prefix}.{field}: expected a boolean")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_number(value: Any) -> bool:
    return _is_number(value) and value > 0


def _is_int(value: Any, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


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
