"""Typed view of one feed contract, as the engine and adapters need it.

``policy_tracker.core.config`` builds these from ``config/sources.yaml``.
Fields here are the frozen Step 2 interface; the free-text research fields
(purpose, history, evidence) stay in the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass

from policy_tracker.core.identity import HostRules
from policy_tracker.core.models import FeedKind


@dataclass(frozen=True)
class FetchPolicy:
    request_rate_per_host_per_second: float
    request_timeout_seconds: float
    max_redirects: int
    max_document_bytes: int
    rate_limit_shared_across_hosts: bool = False
    canonical_host: str | None = None
    fallback_hosts: tuple[str, ...] = ()
    host_fallback_on: tuple[str, ...] = ()
    max_attempts_per_url: int = 1
    retry_backoff_seconds: tuple[float, ...] = ()
    abort_after_consecutive_failed_urls: int | None = None


@dataclass(frozen=True)
class FeedContract:
    feed_id: str
    site_id: str
    url: str | None
    base_url: str
    kind: FeedKind
    decision: str  # only "track" feeds are collected
    removal_authority: bool  # False for rolling/recent lists: never infer removal
    coverage_revision: int  # bump to expose newly covered history as backfill
    parser_version: int  # bump with a parser migration
    host_rules: HostRules
    fetch: FetchPolicy
    status_passes: tuple[str, ...] = ()  # e.g. ("publish", "archive")
    row_count_collapse_threshold: float = 0.5  # quarantine below 50% of last count

    @property
    def is_rolling(self) -> bool:
        return not self.removal_authority


@dataclass(frozen=True)
class RunContext:
    """Pinned inputs of one run (code revision, config hash, clock)."""

    run_id: str
    started_at: str  # ISO 8601 UTC
    config_hash: str
    code_revision: str
