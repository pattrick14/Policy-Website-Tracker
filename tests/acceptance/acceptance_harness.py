"""Offline end-to-end harness for the Step 8 acceptance and failure drills.

The harness drives ``pipeline.run_pipeline`` (and, in a few drills, the CLI) the
way production does: the real ``config/sources.yaml`` contracts, the real
default adapter registry, real enrichment, the real ``build_digest`` and
renderers, a real ``DataStore`` (optionally a git worktree of a local bare
remote), and a ``FakeSender`` in place of SMTP.

Only the network is replaced. ``World`` maps every URL the adapters request for
a chosen set of *replayed* feeds to the existing sanitized fixtures in
``tests/fixtures/{ministry_of_power,ministry_of_coal,cerc}``; the URL maps are
imported from the adapter tests so the two suites cannot drift apart. Every
other tracked feed gets a fetcher that fails with ``connection_refused``.
Document downloads made by enrichment are answered with tiny PDFs generated in
memory (nothing is written to the repository), and Ministry of Power
attachment-record lookups get a synthetic record pointing at such a PDF.

The clock is injected: ``Clock`` returns successive seconds from a settable
start, so a "weekly" run is a clock jump, never a sleep.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from policy_tracker.adapters import ministry_of_power as mop
from policy_tracker.adapters.base import Fetcher, FetchResponse
from policy_tracker.adapters.registry import default_registry
from policy_tracker.adapters.replay import ReplayEntry, ReplayFetcher
from policy_tracker.core import config as config_module
from policy_tracker.core.contracts import FeedContract
from policy_tracker.core.models import CatalogState, Event, EventType
from policy_tracker.documents.enrich import enrich
from policy_tracker.documents.extract import Extractor
from policy_tracker.operations import cli, pipeline, runner
from policy_tracker.operations.outbox import Outbox
from policy_tracker.operations.store import DataStore
from policy_tracker.reporting.digest import build_digest

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "sources.yaml"
FIXTURES = REPO / "tests" / "fixtures"
RECIPIENTS = ("ops-a@example.org", "ops-b@example.net")
MESSAGE_DOMAIN = "example.org"
START = datetime(2026, 10, 26, 2, 30, tzinfo=UTC)  # a Monday, the nominal cron slot
HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=UTF-8"


# ---------------------------------------------------------------------------
# Reuse of the adapter tests' fixture routing (one source of truth)


def _load(name: str, relative: str) -> ModuleType:
    """Import a helper module from another test directory under a private name."""

    module_name = f"_acceptance_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, REPO / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


MOP_TESTS = _load("mop_tests", "tests/adapters/test_ministry_of_power.py")
COAL_TESTS = _load("coal_tests", "tests/adapters/test_ministry_of_coal.py")
CERC_TESTS = _load("cerc_tests", "tests/adapters/test_cerc.py")
OPS = _load("ops_helpers", "tests/operations/ops_helpers.py")
ENRICH_TESTS = _load("enrich_tests", "tests/enrichment/test_enrichment.py")

FakeSender = OPS.FakeSender
GitRemote = OPS.GitRemote
make_git_remote = OPS.make_git_remote
git = OPS.git
make_pdf: Callable[[list[str | None]], bytes] = ENRICH_TESTS.make_pdf

COAL = COAL_TESTS.SITE
COAL_NA = COAL_TESTS.NA
COAL_PI = COAL_TESTS.PI
CERC = CERC_TESTS.BASE
EXPECTED_CERC_COUNTS: Mapping[str, int] = CERC_TESTS.EXPECTED_COUNTS
docs_url = MOP_TESTS.docs_url
with_new_draft_file: Callable[[str], str] = CERC_TESTS.with_new_draft_file


def _strip_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def page(body: bytes | str, content_type: str = HTML, **headers: str) -> ReplayEntry:
    data = body.encode("utf-8") if isinstance(body, str) else body
    return ReplayEntry(status=200, headers={"Content-Type": content_type, **headers}, body=data)


def fixture_page(site: str, name: str) -> ReplayEntry:
    path = FIXTURES / site / name
    content_type = JSON if path.suffix == ".json" else HTML
    return page(path.read_bytes(), content_type)


def failure(error: str = "connection_reset") -> ReplayEntry:
    return ReplayEntry(error=error)


def http_status(status: int, body: bytes = b"", content_type: str = HTML) -> ReplayEntry:
    return ReplayEntry(status=status, headers={"Content-Type": content_type}, body=body)


def mop_feeds() -> dict[str, dict[str, ReplayEntry]]:
    site = "ministry_of_power"
    return {
        "mop_documents": {
            url: fixture_page(site, name) for url, name in MOP_TESTS.DOCUMENT_ROUTES.items()
        },
        "mop_whats_new": {mop.WHATS_NEW_URL: fixture_page(site, "adapter_whats_new.json")},
        "mop_whats_new_archive": {
            mop.WHATS_NEW_ARCHIVE_URL: fixture_page(site, "adapter_whats_new_archive.json")
        },
    }


def _coal(routes: Mapping[str, str]) -> dict[str, ReplayEntry]:
    return {url: fixture_page("ministry_of_coal", name) for url, name in routes.items()}


def coal_feeds() -> dict[str, dict[str, ReplayEntry]]:
    """Coal feeds whose adapter tests route every request (``assert_clean`` cases)."""

    whats_new = COAL + "whats-new"
    whats_new_routes = {
        whats_new: "whats_new_paged.html",
        whats_new + "?page=40": ("whats_new_last_page.html"),
    }
    for number in range(1, 40):
        whats_new_routes[f"{whats_new}?page={number}"] = "whats_new_paged.html"
    index = COAL_NA + "national-coal-index"
    minutes_archive = COAL_PI + "minutes-metings/archive-minutes-metings"
    compensation = COAL_NA + "sanction-order-notice"

    def retarget(name: str) -> bytes:
        return COAL_TESTS.fixture_bytes(name).replace(
            b"nominated-authority/archive-national-coal-index",
            b"nominated-authority/archive-sanction-order-notice",
        )

    return {
        "coal_quarterly_reports": _coal(
            {COAL + "major-statistics/quarterly-booklet": "direct_attachment_table.html"}
        ),
        "coal_acts_rules_policies": _coal(
            {COAL + "acts-rules-policies": "acts_rules_policies.html"}
        ),
        "coal_production_reviews": _coal(
            {COAL_NA + "production-reviews": "review_minutes_table.html"}
        ),
        "coal_operationalisation_reviews": _coal(
            {COAL_NA + "operationalisation-reviews": "operationalisation_reviews.html"}
        ),
        "coal_national_coal_index": _coal(
            {
                index: "paged_table_with_archive.html",
                index + "?page=1": "paged_table_with_archive_last_page.html",
                COAL_NA + "archive-national-coal-index": "auction_allotment_archive.html",
            }
        ),
        "coal_compensation_orders": {
            compensation: page(retarget("paged_table_with_archive.html")),
            compensation + "?page=1": page(retarget("paged_table_with_archive_last_page.html")),
            **_coal(
                {
                    COAL_NA + "archive-sanction-order-notice": "archive_table_paged.html",
                    COAL_NA + "archive-sanction-order-notice?page=1": (
                        "archive_table_paged_last_page.html"
                    ),
                }
            ),
        },
        "coal_minutes_of_meetings": _coal(
            {
                COAL_PI + "minutes-metings": "minutes_of_meetings.html",
                minutes_archive: "minutes_archive.html",
                minutes_archive + "?page=1": "minutes_archive_last_page.html",
            }
        ),
        "coal_termination_orders": _coal(
            {
                COAL_NA + "termination-order": "termination_orders_with_archive.html",
                COAL_NA + "archive-termination-order": "termination_archive.html",
            }
        ),
        "coal_whats_new": _coal(whats_new_routes),
    }


# CERC feed id -> the pages (paths under BASE) its adapter requests.
CERC_FEED_PAGES: dict[str, tuple[str, ...]] = {
    "cerc_home": ("index-en.html", "viewall.html"),
    "cerc_draft_regulations": ("Draft_reg.html",),
    "cerc_current_regulations": ("Current_reg.html",),
    "cerc_discussion_papers": ("Disc_Paper.html",),
    "cerc_working_papers": ("Work_Paper.html",),
    "cerc_study_reports": ("Study_Rep.html",),
    "cerc_recent_orders": ("recent_orders.html",),
    "cerc_recent_rops": ("recent_rops.html",),
    "cerc_order_archives": ("orders.html", "recent_orders2025.html", "Neworder1999.html"),
    "cerc_rop_archives": ("record_proceeding.html", "recent_rops2025.html", "Rop08.html"),
    "cerc_market_monitoring": ("report_MM.html",),
    "cerc_annual_reports": ("annual_report.html",),
    "cerc_other_reports": ("Other_Report.html",),
    "cerc_public_hearings": ("public.html",),
    "cerc_hearing_schedule": ("Sched_hear.html",),
    "cerc_public_notices": ("public-notice.html",),
    "cerc_statutory_public_notices": ("SPN.html",),
    "cerc_statutory_advice": ("advice_gov.html",),
}


def cerc_feeds() -> dict[str, dict[str, ReplayEntry]]:
    """All 24 CERC feeds from the adapter tests' ``default_pages`` (one shared page set).

    Feeds not in ``CERC_FEED_PAGES`` (minutes, price indices, escalation rates) read
    multi-page sections; they get every remaining page, which is harmless because
    replay matches exact URLs.
    """

    pages = {CERC + path: page(body) for path, body in CERC_TESTS.default_pages().items()}
    claimed = {CERC + p for paths in CERC_FEED_PAGES.values() for p in paths}
    rest = {url: entry for url, entry in pages.items() if url not in claimed}
    out: dict[str, dict[str, ReplayEntry]] = {}
    for feed_id in sorted(EXPECTED_CERC_COUNTS):
        paths = CERC_FEED_PAGES.get(feed_id)
        if paths is None:
            out[feed_id] = dict(rest)
        else:
            out[feed_id] = {CERC + p: pages[CERC + p] for p in paths}
    return out


# A cross-site subset (authoritative, rolling, paged, JSON API, evolving matters) for drills
# that need many runs; the other tracked feeds fail like unreachable ones.
CORE_FEEDS = (
    "mop_documents",
    "mop_whats_new",
    "mop_whats_new_archive",
    "coal_quarterly_reports",
    "coal_national_coal_index",
    "coal_minutes_of_meetings",
    "coal_termination_orders",
    "cerc_home",
    "cerc_draft_regulations",
    "cerc_public_notices",
)


def all_replayable_feeds() -> dict[str, dict[str, ReplayEntry]]:
    return {**mop_feeds(), **coal_feeds(), **cerc_feeds()}


# ---------------------------------------------------------------------------
# Generated documents


def generated_pdf(url: str) -> bytes:
    name = Path(urlsplit(url).path).name or "document"
    return make_pdf([f"Example sanitized document {name} for the acceptance harness"])


def document_entry(
    body: bytes,
    *,
    etag: str | None = "auto",
    last_modified: str | None = "Mon, 05 Oct 2026 10:00:00 GMT",
    content_type: str = "application/pdf",
) -> ReplayEntry:
    headers = {"Content-Type": content_type}
    if etag == "auto":
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
    if etag:
        headers["ETag"] = etag
    if last_modified:
        headers["Last-Modified"] = last_modified
    return ReplayEntry(status=200, headers=headers, body=body)


_DOCUMENT_SUFFIXES = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".zip")


def _mop_record(url: str) -> ReplayEntry | None:
    parts = urlsplit(url)
    if not parts.path.endswith("/wp-json/post-page/post"):
        return None
    ids = parse_qs(parts.query).get("id") or ["0"]
    record = {
        "posts": {
            "ID": int(ids[0]) if ids[0].isdigit() else 0,
            "acf_data": {
                "pdf_english": {
                    "url": f"https://www.powermin.gov.in/static/uploads/attachment-{ids[0]}.pdf",
                    "mime_type": "application/pdf",
                }
            },
        }
    }
    return page(json.dumps(record), JSON)


def _generated(url: str) -> ReplayEntry | None:
    record = _mop_record(url)
    if record is not None:
        return record
    path = urlsplit(url).path.lower()
    if path.endswith(".pdf"):
        return document_entry(generated_pdf(url))
    if path.endswith((".html", ".htm")):
        body = f"<html><body><p>Example sanitized page {Path(path).name}</p></body></html>"
        return document_entry(body.encode(), content_type=HTML)
    if path.endswith(_DOCUMENT_SUFFIXES):
        return document_entry(b"PK\x03\x04" + hashlib.sha256(url.encode()).digest())
    return None


# ---------------------------------------------------------------------------
# The simulated web


@dataclass
class World:
    """What the three websites serve. Mutate between runs to stage a scenario.

    ``feeds`` maps each replayed feed to its ``url -> entry`` pages (a list of
    entries is a per-run sequence; the last one repeats). ``documents`` overrides
    generated document responses by URL. ``failing`` feeds get a fetcher that
    fails every request, like every tracked feed that is not replayed at all.
    """

    feeds: dict[str, dict[str, ReplayEntry | list[ReplayEntry]]]
    documents: dict[str, ReplayEntry | list[ReplayEntry]] = field(default_factory=dict)
    failing: dict[str, str] = field(default_factory=dict)  # feed id -> error class

    @classmethod
    def full(cls) -> World:
        return cls(feeds=dict(all_replayable_feeds()))

    @classmethod
    def core(cls) -> World:
        return cls.only(*CORE_FEEDS)

    @classmethod
    def only(cls, *feed_ids: str) -> World:
        every = all_replayable_feeds()
        return cls(feeds={feed_id: every[feed_id] for feed_id in feed_ids})

    def set_page(self, feed_id: str, url: str, entry: ReplayEntry | list[ReplayEntry]) -> None:
        """Serve ``entry`` at ``url`` (for every feed that reads that page)."""

        if url not in self.feeds[feed_id]:
            raise KeyError(f"{url} is not a page of {feed_id}")
        for routes in self.feeds.values():
            if url in routes:
                routes[url] = entry

    def routes(self) -> dict[str, ReplayEntry | list[ReplayEntry]]:
        merged: dict[str, ReplayEntry | list[ReplayEntry]] = {}
        for routes in self.feeds.values():
            merged.update(routes)
        merged.update(self.documents)
        return merged

    def body(self, feed_id: str, url: str) -> str:
        entry = self.feeds[feed_id][url]
        assert isinstance(entry, ReplayEntry) and entry.body is not None
        return entry.body.decode("utf-8")

    def edit_page(self, feed_id: str, url: str, edit: Callable[[str], str]) -> None:
        entry = self.feeds[feed_id][url]
        assert isinstance(entry, ReplayEntry)
        self.set_page(
            feed_id, url, dataclasses.replace(entry, body=edit(self.body(feed_id, url)).encode())
        )


class FailingFetcher:
    def __init__(self, error: str) -> None:
        self.error = error
        self.requested: list[str] = []

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResponse:
        self.requested.append(url)
        return FetchResponse(url=url, status_code=0, headers={}, body=b"", error=self.error)


class WorldFetcher:
    """One run's view of the world: exact-URL replay plus generated documents."""

    def __init__(self, world: World) -> None:
        self.replay = ReplayFetcher()
        routes = world.routes()
        for url, entry in routes.items():
            self.replay.add_sequence(url, entry if isinstance(entry, list) else [entry])
        self._routed = {_strip_fragment(url) for url in routes}
        self.generated: list[str] = []

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResponse:
        key = _strip_fragment(url)
        if key not in self._routed:
            entry = _generated(key)
            if entry is not None:
                self.replay.add_sequence(key, [entry])
                self._routed.add(key)
                self.generated.append(key)
        return self.replay.get(url, headers=headers)

    @property
    def requested_urls(self) -> list[str]:
        return self.replay.requested_urls

    @property
    def unmatched(self) -> list[str]:
        return self.replay.unmatched


class Clock:
    """Injected UTC clock: each call returns the current second, then advances one."""

    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def at(self, moment: datetime) -> None:
        if moment < self.current:
            raise ValueError("the clock never runs backwards")
        self.current = moment

    def __call__(self) -> str:
        value = self.current.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.current += timedelta(seconds=1)
        return value


def week(n: int, *, days: int = 0) -> datetime:
    return START + timedelta(weeks=n, days=days)


# ---------------------------------------------------------------------------
# The harness


def no_ocr_extractor() -> Extractor:
    """The production extractor with OCR binaries reported missing (deterministic)."""

    return Extractor(which=lambda name: None)


@dataclass
class Harness:
    tmp: Path
    world: World
    data_dir: Path
    clock: Clock = field(default_factory=Clock)
    config: Path = CONFIG
    max_documents: int = 0
    enrich_extra: dict[str, Any] = field(default_factory=dict)
    publisher_factory: Callable[[], pipeline.Publisher] | None = None
    fetchers: list[WorldFetcher] = field(default_factory=list)
    failing_fetchers: list[FailingFetcher] = field(default_factory=list)

    # -- collaborators ---------------------------------------------------------
    def contracts(self) -> dict[str, FeedContract]:
        return config_module.load_contracts(config_module.load_config(self.config))

    def fetcher_factory(self) -> runner.FetcherFactory:
        """One shared fetcher per run (all sites), like ``http.fetcher_for`` per site."""

        world_fetcher = WorldFetcher(self.world)
        self.fetchers.append(world_fetcher)

        def factory(contract: FeedContract) -> Fetcher:
            if contract.feed_id in self.world.feeds and contract.feed_id not in self.world.failing:
                return world_fetcher
            failing = FailingFetcher(self.world.failing.get(contract.feed_id, "connection_refused"))
            self.failing_fetchers.append(failing)
            return failing

        return factory

    def services(
        self,
        sender: Any = None,
        *,
        recipients: Sequence[str] = RECIPIENTS,
        publisher: pipeline.Publisher | None = None,
        max_documents: int | None = None,
    ) -> pipeline.Services:
        renderer = cli.default_renderer()
        return pipeline.Services(
            enricher=enrich,
            digest_builder=build_digest,
            renderer=renderer,
            sender=sender,
            recipients=tuple(recipients),
            publisher=publisher,
            message_id_domain=MESSAGE_DOMAIN,
            enrich_options={
                "max_documents": self.max_documents if max_documents is None else max_documents,
                "extractor": no_ocr_extractor(),
                **self.enrich_extra,
            },
        )

    # -- running ---------------------------------------------------------------
    def run(
        self,
        mode: runner.Mode = "run",
        *,
        when: datetime | None = None,
        sender: Any = None,
        no_email: bool = False,
        dry_run: bool = False,
        max_documents: int | None = None,
        services: pipeline.Services | None = None,
    ) -> runner.RunReport:
        if when is not None:
            self.clock.at(when)
        if services is None:
            publisher = self.publisher_factory() if self.publisher_factory else None
            services = self.services(sender, publisher=publisher, max_documents=max_documents)
        return pipeline.run_pipeline(
            self.config,
            self.data_dir,
            registry=default_registry(),
            fetcher_factory=self.fetcher_factory(),
            mode=mode,
            dry_run=dry_run,
            code_revision="acceptance",
            now=self.clock,
            no_email=no_email,
            services=services,
        )

    def baseline(self, *, when: datetime = START, **kwargs: Any) -> runner.RunReport:
        report = self.run("baseline", when=when, **kwargs)
        assert report.promoted, report.messages
        return report

    # -- inspection ------------------------------------------------------------
    @property
    def store(self) -> DataStore:
        return DataStore(self.data_dir)

    @property
    def outbox(self) -> Outbox:
        return Outbox(self.data_dir)

    def state(self) -> CatalogState:
        return self.store.load_state()

    def digest(self, report: runner.RunReport) -> dict[str, Any]:
        (path,) = [p for p in report.artifacts if p.endswith(".json")]
        return json.loads((self.data_dir / path).read_text(encoding="utf-8"))

    def all_events(self) -> list[Event]:
        return list(self.store.iter_events())


def make_harness(tmp_path: Path, world: World | None = None, *, max_documents: int = 0) -> Harness:
    data_dir = tmp_path / "tracker-data"
    DataStore(data_dir).initialize()
    return Harness(
        tmp=tmp_path, world=world or World.full(), data_dir=data_dir, max_documents=max_documents
    )


def events_of(
    events: Iterable[Event], event_type: EventType, feed_id: str | None = None
) -> list[Event]:
    return [
        e
        for e in events
        if e.event_type is event_type and (feed_id is None or e.feed_id == feed_id)
    ]


def feed_appearances(state: CatalogState, feed_id: str) -> dict[str, Any]:
    return {a.entity_key: a for a in state.appearances.values() if a.feed_id == feed_id}


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }
