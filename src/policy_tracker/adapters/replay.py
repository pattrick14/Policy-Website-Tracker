"""Deterministic ``Fetcher`` for adapter fixture tests.

``ReplayFetcher`` maps exact URLs to recorded responses (inline bytes or a
fixture file) and answers any other URL with HTTP 404, so a test fails loudly
when an adapter requests something unexpected. It records every request,
supports per-URL response sequences (for retries and refetches), simulated
connection failures, and conditional requests against recorded validators.

Example::

    fetcher = ReplayFetcher(fixture_root="tests/fixtures/cerc")
    fetcher.add("https://cercind.gov.in/Draft_reg.html", fixture="draft_regulations.html")
    fetcher.add("https://cercind.gov.in/x.pdf", body=b"%PDF-1.7", headers={"ETag": '"a"'})
    fetcher.add("https://cercind.gov.in/down.html", error="connection_reset")
    observation = adapter.collect(contract, fetcher, observed_at=...)
    assert fetcher.unused() == []
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from policy_tracker.adapters.base import FetchResponse

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv",
}


@dataclass(frozen=True)
class ReplayEntry:
    """One recorded response. Give ``body`` or ``fixture`` (a path), or ``error``."""

    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None
    fixture: str | Path | None = None
    error: str | None = None  # simulate a connection-level failure class
    served_by_host: str | None = None  # defaults to the URL host
    final_url: str | None = None  # simulate a redirect that ended elsewhere


@dataclass(frozen=True)
class ReplayRequest:
    url: str
    headers: Mapping[str, str]


def _strip_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


class ReplayFetcher:
    """Replays recorded responses by exact URL (fragment ignored); unknown URLs get 404.

    Sequences: ``add`` the same URL several times (or use ``add_sequence``) and
    successive requests receive successive entries; the last one repeats.

    Conditional requests: when ``conditional`` is True (the default) and a
    request's ``If-None-Match`` equals the entry's ``ETag``, or its
    ``If-Modified-Since`` equals the entry's ``Last-Modified``, a 304 with an
    empty body and the entry's headers is returned instead.
    """

    def __init__(
        self,
        routes: Mapping[str, ReplayEntry | bytes | str | Path] | None = None,
        *,
        fixture_root: str | Path | None = None,
        conditional: bool = True,
    ) -> None:
        self.fixture_root = Path(fixture_root) if fixture_root is not None else None
        self.conditional = conditional
        self.requests: list[ReplayRequest] = []
        self.unmatched: list[str] = []
        self._routes: dict[str, list[ReplayEntry]] = {}
        self._served: dict[str, int] = {}
        for url, value in (routes or {}).items():
            if isinstance(value, ReplayEntry):
                self._append(url, value)
            elif isinstance(value, bytes):
                self.add(url, body=value)
            else:
                self.add(url, fixture=value)

    # -- building ---------------------------------------------------------------

    def add(
        self,
        url: str,
        *,
        status: int = 200,
        body: bytes | str | None = None,
        fixture: str | Path | None = None,
        headers: Mapping[str, str] | None = None,
        content_type: str | None = None,
        error: str | None = None,
        served_by_host: str | None = None,
        final_url: str | None = None,
    ) -> ReplayFetcher:
        """Record a response for ``url`` (chainable). ``str`` bodies are UTF-8 encoded."""

        if body is not None and fixture is not None:
            raise ValueError("give body or fixture, not both")
        merged = dict(headers or {})
        inferred = content_type
        if inferred is None and fixture is not None:
            inferred = _CONTENT_TYPES.get(Path(fixture).suffix.lower())
        if inferred and not any(k.lower() == "content-type" for k in merged):
            merged["Content-Type"] = inferred
        entry = ReplayEntry(
            status=status,
            headers=merged,
            body=body.encode() if isinstance(body, str) else body,
            fixture=fixture,
            error=error,
            served_by_host=served_by_host,
            final_url=final_url,
        )
        if fixture is not None:
            self._load(entry)  # fail at setup time if the fixture is missing
        return self._append(url, entry)

    def add_sequence(self, url: str, entries: Iterable[ReplayEntry]) -> ReplayFetcher:
        for entry in entries:
            self._append(url, entry)
        return self

    def _append(self, url: str, entry: ReplayEntry) -> ReplayFetcher:
        self._routes.setdefault(_strip_fragment(url), []).append(entry)
        return self

    # -- Fetcher protocol -------------------------------------------------------

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResponse:
        request_headers = dict(headers or {})
        self.requests.append(ReplayRequest(url, request_headers))
        key = _strip_fragment(url)
        entries = self._routes.get(key)
        host = (urlsplit(url).hostname or "").lower() or None
        if not entries:
            self.unmatched.append(url)
            return FetchResponse(
                url=url, status_code=404, headers={}, body=b"", served_by_host=host
            )
        index = self._served.get(key, 0)
        self._served[key] = index + 1
        entry = entries[min(index, len(entries) - 1)]
        served = entry.served_by_host or host
        final_url = entry.final_url or url
        if entry.error is not None:
            return FetchResponse(
                url=final_url,
                status_code=0,
                headers={},
                body=b"",
                served_by_host=served,
                error=entry.error,
            )
        if self.conditional and self._not_modified(entry, request_headers):
            return FetchResponse(
                url=final_url,
                status_code=304,
                headers=dict(entry.headers),
                body=b"",
                served_by_host=served,
            )
        return FetchResponse(
            url=final_url,
            status_code=entry.status,
            headers=dict(entry.headers),
            body=self._load(entry),
            served_by_host=served,
        )

    # -- inspection -------------------------------------------------------------

    @property
    def requested_urls(self) -> list[str]:
        return [request.url for request in self.requests]

    def unused(self) -> list[str]:
        """Recorded URLs that were never requested."""

        return [url for url in self._routes if url not in self._served]

    # -- helpers ----------------------------------------------------------------

    def _load(self, entry: ReplayEntry) -> bytes:
        if entry.fixture is None:
            return entry.body or b""
        path = Path(entry.fixture)
        if self.fixture_root is not None and not path.is_absolute():
            path = self.fixture_root / path
        return path.read_bytes()

    @staticmethod
    def _not_modified(entry: ReplayEntry, request_headers: Mapping[str, str]) -> bool:
        def lookup(mapping: Mapping[str, str], name: str) -> str | None:
            for key, value in mapping.items():
                if key.lower() == name:
                    return value
            return None

        if entry.status != 200:
            return False
        etag = lookup(entry.headers, "etag")
        if_none_match = lookup(request_headers, "if-none-match")
        if etag and if_none_match:
            return etag.strip() in {tag.strip() for tag in if_none_match.split(",")}
        modified = lookup(entry.headers, "last-modified")
        if_modified_since = lookup(request_headers, "if-modified-since")
        return bool(
            modified and if_modified_since and modified.strip() == if_modified_since.strip()
        )
