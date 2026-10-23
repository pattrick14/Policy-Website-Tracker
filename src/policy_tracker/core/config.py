"""Load ``config/sources.yaml`` into typed feed contracts.

The YAML is validated with the closed inventory schema
(``policy_tracker.inventory.validate_inventory``) before any contract is built,
so a contract never comes from a partly understood config. Every feed with
``decision: track`` becomes a ``FeedContract``; ``indirect`` and ``exclude``
feeds stay research records only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from policy_tracker.core.contracts import FeedContract, FetchPolicy
from policy_tracker.core.identity import HostRules
from policy_tracker.core.models import FeedKind
from policy_tracker.inventory import SITE_FETCH_FIELDS, validate_inventory

DEFAULT_COVERAGE_REVISION = 1
DEFAULT_PARSER_VERSION = 1
# FetchPolicy fields without a dataclass default: defaults or the site must set them.
REQUIRED_FETCH_FIELDS = (
    "request_rate_per_host_per_second",
    "request_timeout_seconds",
    "max_redirects",
    "max_document_bytes",
)


class ConfigError(ValueError):
    """The configuration cannot produce feed contracts; ``problems`` lists why."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        lines = "\n".join(f"  - {problem}" for problem in self.problems)
        super().__init__(f"invalid feed configuration ({len(self.problems)} problem(s)):\n{lines}")


def load_config(path: Path) -> Mapping[str, Any]:
    """Parse the config file with the safe YAML loader (merge keys supported)."""

    try:
        with Path(path).open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError([f"{path}: {exc}"]) from exc
    if not isinstance(data, Mapping):
        raise ConfigError([f"{path}: root: expected a mapping"])
    return data


def config_hash(source: Path | str | bytes) -> str:
    """Hex SHA-256 of the config file bytes, recorded on every run."""

    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    return hashlib.sha256(data).hexdigest()


def load_contracts(config: Mapping[str, Any]) -> dict[str, FeedContract]:
    """Build a contract for every tracked feed, keyed by feed id, in config order."""

    errors = validate_inventory(config)
    if errors:
        raise ConfigError(errors)

    defaults = config.get("defaults") or {}
    problems: list[str] = []
    contracts: dict[str, FeedContract] = {}
    for site_index, site in enumerate(config["sites"]):
        prefix = f"sites[{site_index}]"
        fetch = _fetch_policy(defaults, site, prefix, problems)
        host_rules = _host_rules(site)
        for feed_index, feed in enumerate(site["feeds"]):
            if feed["decision"] != "track":
                continue
            if fetch is None:
                continue  # already reported once for the site
            feed_prefix = f"{prefix}.feeds[{feed_index}]"
            try:
                kind = FeedKind(feed["kind"])
            except ValueError:
                problems.append(f"{feed_prefix}.kind: {feed['kind']!r} has no FeedKind")
                continue
            contracts[feed["id"]] = FeedContract(
                feed_id=feed["id"],
                site_id=site["id"],
                url=feed.get("url", feed.get("url_template")),
                base_url=site["base_url"],
                kind=kind,
                decision=feed["decision"],
                removal_authority=feed["completeness"]["removal_authority"],
                coverage_revision=feed.get("coverage_revision", DEFAULT_COVERAGE_REVISION),
                parser_version=feed.get("parser_version", DEFAULT_PARSER_VERSION),
                host_rules=host_rules,
                fetch=fetch,
                status_passes=tuple(feed.get("status_passes", ())),
            )
    if problems:
        raise ConfigError(problems)
    return contracts


def _fetch_policy(
    defaults: Mapping[str, Any], site: Mapping[str, Any], prefix: str, problems: list[str]
) -> FetchPolicy | None:
    settings = {
        key: site.get(key, defaults.get(key))
        for key in SITE_FETCH_FIELDS
        if key in site or key in defaults
    }
    missing = [key for key in REQUIRED_FETCH_FIELDS if key not in settings]
    if missing:
        problems.append(
            f"{prefix}: fetch policy needs {', '.join(missing)} in the site or in defaults"
        )
        return None
    return FetchPolicy(
        request_rate_per_host_per_second=float(settings["request_rate_per_host_per_second"]),
        request_timeout_seconds=float(settings["request_timeout_seconds"]),
        max_redirects=settings["max_redirects"],
        max_document_bytes=settings["max_document_bytes"],
        rate_limit_shared_across_hosts=settings.get("rate_limit_shared_across_hosts", False),
        canonical_host=settings.get("canonical_host"),
        fallback_hosts=tuple(settings.get("fallback_hosts", ())),
        host_fallback_on=tuple(settings.get("host_fallback_on", ())),
        max_attempts_per_url=settings.get("max_attempts_per_url", 1),
        retry_backoff_seconds=tuple(float(s) for s in settings.get("retry_backoff_seconds", ())),
        abort_after_consecutive_failed_urls=settings.get("abort_after_consecutive_failed_urls"),
    )


def _host_rules(site: Mapping[str, Any]) -> HostRules:
    canonical = site.get("canonical_host") or (urlsplit(site["base_url"]).hostname or "").lower()
    allowed = frozenset(site["allowed_hosts"])
    # Only tested mirrors alias to the canonical host: the www/apex pair and any
    # D-020 fallback host. Other allowed hosts (coal.gov.in file links) keep
    # their own identity until a tested rule covers them.
    bare = canonical.removeprefix("www.")
    mirrors = {canonical, bare, f"www.{bare}", *site.get("fallback_hosts", ())}
    return HostRules(
        allowed_hosts=allowed,
        host_aliases={host: canonical for host in sorted(allowed & mirrors)},
        collapse_duplicate_slashes=site.get("collapse_duplicate_slashes", False),
    )
