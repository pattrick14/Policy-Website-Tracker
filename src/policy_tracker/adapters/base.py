"""Adapter interface (Step 2 frozen contract).

An adapter turns one feed contract into a ``FeedObservation``. It fetches only
through the supplied ``Fetcher``, so fixture tests replay recorded responses
and production applies the per-site fetch policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.observations import FeedObservation


@dataclass(frozen=True)
class FetchResponse:
    url: str  # final URL after redirects
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    served_by_host: str | None = None  # differs from the URL host after a fallback
    error: str | None = None  # connection-level failure class, e.g. "connection_reset"

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status_code < 300


class Fetcher(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResponse: ...


class Adapter(Protocol):
    site_id: str
    parser_version: int

    def feed_ids(self) -> frozenset[str]:
        """Feed ids this adapter can collect."""

    def collect(self, contract: FeedContract, fetcher: Fetcher, *, observed_at: str) -> FeedObservation:
        """Enumerate one feed. Never raises for source failures: report them in evidence."""


@dataclass
class AdapterRegistry:
    adapters: dict[str, Adapter] = field(default_factory=dict)  # by feed id

    def register(self, adapter: Adapter) -> None:
        for feed_id in adapter.feed_ids():
            if feed_id in self.adapters:
                raise ValueError(f"feed {feed_id!r} already has an adapter")
            self.adapters[feed_id] = adapter

    def for_feed(self, feed_id: str) -> Adapter | None:
        return self.adapters.get(feed_id)
