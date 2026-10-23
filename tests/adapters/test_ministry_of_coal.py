"""Fixture tests for the Ministry of Coal adapter (Step 4); no live requests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from policy_tracker.adapters.base import Adapter
from policy_tracker.adapters.ministry_of_coal import (
    FEED_SPECS,
    MinistryOfCoalAdapter,
    make_adapter,
    parse_date,
    rewrite_archive_href,
)
from policy_tracker.adapters.replay import ReplayFetcher
from policy_tracker.core.config import load_config, load_contracts
from policy_tracker.core.contracts import FeedContract, RunContext
from policy_tracker.core.engine import apply_observation
from policy_tracker.core.models import (
    BaselineStatus,
    CatalogState,
    EventType,
    RecordStatus,
)
from policy_tracker.core.observations import FeedObservation

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "sources.yaml"
FIXTURES = ROOT / "tests" / "fixtures" / "ministry_of_coal"
SITE = "https://www.coal.nic.in/"
NA = SITE + "nominated-authority/"
PI = SITE + "public-information/"
OBSERVED = "2026-10-26T02:30:00Z"


@pytest.fixture(scope="module")
def contracts() -> dict[str, FeedContract]:
    return load_contracts(load_config(CONFIG))


@pytest.fixture()
def adapter() -> MinistryOfCoalAdapter:
    return make_adapter()


def fetcher(routes: dict[str, str]) -> ReplayFetcher:
    replay = ReplayFetcher(fixture_root=FIXTURES)
    for url, fixture in routes.items():
        replay.add(url, fixture=fixture)
    return replay


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def collect(
    adapter: MinistryOfCoalAdapter,
    contracts: dict[str, FeedContract],
    feed_id: str,
    replay: ReplayFetcher,
    observed_at: str = OBSERVED,
) -> FeedObservation:
    return adapter.collect(contracts[feed_id], replay, observed_at=observed_at)


def by_title(observation: FeedObservation) -> dict[str, object]:
    return {doc.title: doc for doc in observation.documents}


def assert_clean(observation: FeedObservation) -> None:
    evidence = observation.evidence
    assert evidence.fetch_ok and evidence.parse_valid and evidence.enumeration_complete
    assert not evidence.block_page_detected
    assert evidence.pages_fetched == evidence.pages_expected


# ---------------------------------------------------------------------------
# Feed set, helpers


def test_feed_ids_equal_tracked_coal_feeds(contracts: dict[str, FeedContract]) -> None:
    adapter: Adapter = make_adapter()
    tracked = {fid for fid, c in contracts.items() if c.site_id == "ministry_of_coal"}
    assert adapter.feed_ids() == tracked
    assert len(tracked) == 27
    assert adapter.site_id == "ministry_of_coal" and adapter.parser_version == 1


def test_archive_urls_and_pagers_match_config() -> None:
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    site = next(s for s in raw["sites"] if s["id"] == "ministry_of_coal")
    feeds = {f["id"]: f for f in site["feeds"] if f["decision"] == "track"}
    for feed_id, spec in FEED_SPECS.items():
        assert spec.archive_url == feeds[feed_id].get("archive_url"), feed_id
        assert bool(spec.archive_url) == ("archive_href_rewrite" in feeds[feed_id]), feed_id
        termination = feeds[feed_id]["enumeration"]["termination"]
        assert spec.paged == termination.startswith(
            ("pager Last", "current pager Last", "pager last")
        ), feed_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("29/06/2026", "2026-06-29"),
        ("24-07-2025", "2025-07-24"),
        ("2025-10-07", "2025-10-07"),
        ("16 Oct 2026", "2026-10-16"),
        ("16 August 2017", "2017-08-16"),
        ("08 Oct 0013", None),
        ("September 2026", None),
        (" ", None),
        (None, None),
    ],
)
def test_parse_date(raw: str | None, expected: str | None) -> None:
    assert parse_date(raw) == expected


def test_archive_href_rewrite() -> None:
    page = NA + "auction-allotment"
    assert (
        rewrite_archive_href("http://www.coal.nic.in//nominated-authority/archive-x?page=1", page)
        == "https://www.coal.nic.in/nominated-authority/archive-x?page=1"
    )
    assert rewrite_archive_href("http://example.org//a", page) == "http://example.org//a"


# ---------------------------------------------------------------------------
# Direct attachment tables


def test_direct_attachment_table(adapter, contracts) -> None:
    replay = fetcher({SITE + "major-statistics/quarterly-booklet": "direct_attachment_table.html"})
    obs = collect(adapter, contracts, "coal_quarterly_reports", replay)
    assert_clean(obs)
    docs = by_title(obs)
    current = docs["Current example publication"]
    assert current.url == "/sites/default/files/2026-10/current-example.pdf"
    assert current.native_id == SITE + "sites/default/files/2026-10/current-example.pdf"
    assert (current.published_date, current.published_raw) == ("2026-10-16", "16 Oct 2026")
    assert current.metadata == {"file_size": "1.2 MB", "file_type": "PDF"}
    assert current.section is None
    assert docs["Backdated example publication"].published_date == "2021-04-01"
    assert obs.feed_id == "coal_quarterly_reports" and obs.observed_at == OBSERVED


def test_acts_rules_policies_five_tables(adapter, contracts) -> None:
    replay = fetcher({SITE + "acts-rules-policies": "acts_rules_policies.html"})
    obs = collect(adapter, contracts, "coal_acts_rules_policies", replay)
    assert_clean(obs)
    tables = [doc.metadata["table"] for doc in obs.documents]
    assert tables == ["Acts", "Rules", "Policies", "Methodology", "Notifications"]
    assert obs.documents[1].title == "Example Amendment Rules, 2026"
    assert obs.documents[1].published_date is None  # upload folders are not dates


def test_acts_with_fewer_tables_is_invalid(adapter, contracts) -> None:
    replay = fetcher({SITE + "acts-rules-policies": "direct_attachment_table.html"})
    obs = collect(adapter, contracts, "coal_acts_rules_policies", replay)
    assert not obs.evidence.parse_valid and not obs.evidence.enumeration_complete


def test_review_tables_keep_coal_gov_in_hrefs_raw(adapter, contracts) -> None:
    replay = fetcher({NA + "production-reviews": "review_minutes_table.html"})
    obs = collect(adapter, contracts, "coal_production_reviews", replay)
    assert_clean(obs)
    assert obs.documents[0].native_id == (
        "http://coal.gov.in/sites/default/files/2023-06/Example-MoM-2025.pdf"
    )
    assert obs.documents[0].published_date == "2025-07-24"


def test_second_table_and_repeated_href(adapter, contracts) -> None:
    url = NA + "operationalisation-reviews"
    obs = collect(
        adapter,
        contracts,
        "coal_operationalisation_reviews",
        fetcher({url: "operationalisation_reviews.html"}),
    )
    assert_clean(obs)
    assert len(obs.documents) == 2  # summary table ignored; repeated href collapsed
    assert obs.documents[0].provenance.locator == "table[1]/row[1]/link[0]"
    assert any("1 repeated href" in d for d in obs.evidence.diagnostics)
    single = collect(
        adapter,
        contracts,
        "coal_operationalisation_reviews",
        fetcher({url: "review_minutes_table.html"}),
    )
    assert not single.evidence.parse_valid  # fewer than two tables


def test_missing_table_is_invalid(adapter, contracts) -> None:
    replay = fetcher({SITE + "major-statistics/rsr-report": "paged_view_out_of_range.html"})
    obs = collect(adapter, contracts, "coal_rsr_reports", replay)
    assert not obs.evidence.parse_valid
    assert obs.documents == ()


def test_missing_heading_is_invalid(adapter, contracts) -> None:
    body = b"<html><body><main><table><tr><th>Title</th></tr></table></main></body></html>"
    replay = ReplayFetcher().add(
        SITE + "public-information/sop", body=body, content_type="text/html"
    )
    obs = collect(adapter, contracts, "coal_sop", replay)
    assert not obs.evidence.parse_valid


# ---------------------------------------------------------------------------
# Pagers


def test_paged_table_with_archive(adapter, contracts) -> None:
    url = NA + "national-coal-index"
    replay = fetcher(
        {
            url: "paged_table_with_archive.html",
            url + "?page=1": "paged_table_with_archive_last_page.html",
            NA + "archive-national-coal-index": "auction_allotment_archive.html",
        }
    )
    obs = collect(adapter, contracts, "coal_national_coal_index", replay)
    assert_clean(obs)
    assert replay.unmatched == [] and replay.unused() == []
    assert obs.evidence.pages_fetched == 3
    sections = [doc.section for doc in obs.documents]
    assert sections == ["current", "current", "current", "archive", "archive"]
    archived = obs.documents[3]
    assert archived.published_date is None and archived.published_raw is None
    assert archived.provenance.page_url == NA + "archive-national-coal-index"
    assert obs.documents[2].provenance.page_url == url + "?page=1"


def test_missing_last_link_is_incomplete(adapter, contracts) -> None:
    url = PI + "monthly-statistics-at-glance"
    obs = collect(
        adapter,
        contracts,
        "coal_monthly_statistics",
        fetcher({url: "direct_attachment_table.html"}),
    )
    assert obs.evidence.parse_valid and not obs.evidence.enumeration_complete
    assert any("Last page link" in d for d in obs.evidence.diagnostics)
    assert len(obs.documents) == 2


def test_out_of_range_page_is_invalid_where_contract_is_silent(adapter, contracts) -> None:
    url = PI + "monthly-statistics-at-glance"
    replay = fetcher(
        {url: "paged_table_with_archive.html", url + "?page=1": "paged_view_out_of_range.html"}
    )
    obs = collect(adapter, contracts, "coal_monthly_statistics", replay)
    assert not obs.evidence.parse_valid


def test_archive_link_mismatch_is_incomplete(adapter, contracts) -> None:
    url = PI + "monthly-report-cabinet"
    replay = fetcher(
        {
            url: "paged_table_with_archive.html",
            url + "?page=1": "paged_table_with_archive_last_page.html",
        }
    )
    obs = collect(adapter, contracts, "coal_monthly_cabinet_reports", replay)
    assert obs.evidence.parse_valid and not obs.evidence.enumeration_complete
    assert any("differs from the contract" in d for d in obs.evidence.diagnostics)
    assert not any("archive" in u for u in replay.requested_urls)


def _compensation_fetcher() -> ReplayFetcher:
    """Shared-shape fixtures with the archive footer pointed at the sanction-order archive."""

    def retarget(name: str) -> bytes:
        return fixture_bytes(name).replace(
            b"nominated-authority/archive-national-coal-index",
            b"nominated-authority/archive-sanction-order-notice",
        )

    url = NA + "sanction-order-notice"
    replay = ReplayFetcher(fixture_root=FIXTURES)
    replay.add(url, body=retarget("paged_table_with_archive.html"), content_type="text/html")
    # Observed Last page is ?page=4; the fixture's pager says ?page=1.
    replay.add(
        url + "?page=1",
        body=retarget("paged_table_with_archive_last_page.html"),
        content_type="text/html",
    )
    replay.add(NA + "archive-sanction-order-notice", fixture="archive_table_paged.html")
    replay.add(
        NA + "archive-sanction-order-notice?page=1", fixture="archive_table_paged_last_page.html"
    )
    return replay


def test_current_and_paged_archive_with_linkless_row(adapter, contracts) -> None:
    replay = _compensation_fetcher()
    obs = collect(adapter, contracts, "coal_compensation_orders", replay)
    assert_clean(obs)
    assert replay.requested_urls[2] == NA + "archive-sanction-order-notice"
    assert [d.section for d in obs.documents].count("archive") == 4
    linkless = obs.documents[-1]
    assert linkless.title == "Example archive row without a link"
    assert linkless.metadata["linkless"] == "true"
    assert linkless.native_id.startswith("linkless:")
    assert linkless.url == NA + "archive-sanction-order-notice"
    again = collect(adapter, contracts, "coal_compensation_orders", _compensation_fetcher())
    assert again.documents[-1].native_id == linkless.native_id  # stable across runs


def test_minutes_dated_archive_pager(adapter, contracts) -> None:
    archive = PI + "minutes-metings/archive-minutes-metings"
    routes = {
        PI + "minutes-metings": "minutes_of_meetings.html",
        archive: "minutes_archive.html",
        archive + "?page=1": "minutes_archive_last_page.html",
    }
    obs = collect(adapter, contracts, "coal_minutes_of_meetings", fetcher(routes))
    assert_clean(obs)
    archived = [d for d in obs.documents if d.section == "archive"]
    assert [d.published_date for d in archived] == [
        "2013-11-25",
        "2019-12-13",
        "2015-04-30",
        "2005-06-02",
        "2017-07-11",
    ]
    routes[archive] = "auction_allotment_archive.html"  # archive without its pager Last link
    del routes[archive + "?page=1"]
    partial = collect(adapter, contracts, "coal_minutes_of_meetings", fetcher(routes))
    assert partial.evidence.parse_valid and not partial.evidence.enumeration_complete


def test_termination_two_tables_and_archive(adapter, contracts) -> None:
    url = NA + "termination-order"
    routes = {
        url: "termination_orders_with_archive.html",
        NA + "archive-termination-order": "termination_archive.html",
    }
    obs = collect(adapter, contracts, "coal_termination_orders", fetcher(routes))
    assert_clean(obs)
    assert [(d.title, d.section) for d in obs.documents] == [
        ("Example termination order", "current"),
        ("Example allotment termination order", "archive"),
    ]
    assert obs.documents[0].published_date == "2026-06-29"
    old = collect(
        adapter, contracts, "coal_termination_orders", fetcher({url: "termination_orders.html"})
    )
    assert old.evidence.parse_valid and not old.evidence.enumeration_complete
    assert any("missing Archive link" in d for d in old.evidence.diagnostics)


def _auction_routes(last: str = "auction_allotment_last_page.html") -> dict[str, str]:
    url = NA + "auction-allotment"
    routes = {url: "auction_allotment.html"}
    for page in range(1, 8):
        page_fixture = (
            "auction_allotment_page_2.html" if page <= 2 else "auction_allotment_page_3.html"
        )
        routes[f"{url}?page={page}"] = page_fixture
    routes[url + "?page=8"] = last
    routes[NA + "archive-auction-allotment"] = "auction_allotment_archive.html"
    return routes


def test_auction_pager_tie_repeats_and_archive(adapter, contracts) -> None:
    replay = fetcher(_auction_routes())
    obs = collect(adapter, contracts, "coal_auction_allotment", replay)
    assert_clean(obs)
    assert obs.evidence.pages_fetched == 10 and replay.unused() == []
    hrefs = [d.native_id for d in obs.documents]
    assert len(hrefs) == len(set(hrefs)) == 9
    tied = by_title(obs)["Example list of technically qualified bidders"]
    assert tied.provenance.page_url.endswith("?page=1")  # first sighting wins
    assert tied.provenance.locator == "table[1]/row[2]/link[0]"  # statistics table skipped
    assert replay.requested_urls[-1] == NA + "archive-auction-allotment"
    assert not contracts["coal_auction_allotment"].removal_authority


def test_auction_out_of_range_page_is_terminus(adapter, contracts) -> None:
    replay = fetcher(_auction_routes(last="paged_view_out_of_range.html"))
    obs = collect(adapter, contracts, "coal_auction_allotment", replay)
    assert_clean(obs)
    assert any("pager terminus" in d for d in obs.evidence.diagnostics)
    assert len(obs.documents) == 7


def test_whats_new_rolling_pager(adapter, contracts) -> None:
    url = SITE + "whats-new"
    routes = {url: "whats_new_paged.html", url + "?page=40": "whats_new_last_page.html"}
    for page in range(1, 40):
        routes[f"{url}?page={page}"] = "whats_new_paged.html"
    replay = fetcher(routes)
    obs = collect(adapter, contracts, "coal_whats_new", replay)
    assert_clean(obs)
    assert obs.evidence.pages_fetched == 41
    assert contracts["coal_whats_new"].removal_authority is False
    docs = by_title(obs)
    malformed = docs["Example notice with a malformed displayed year"]
    assert (malformed.published_raw, malformed.published_date) == ("08 Oct 0013", None)
    test_row = docs["test"]
    assert test_row.metadata["linkless"] == "true" and test_row.url == url
    undated = docs["Example e-auction result notice with no displayed date"]
    assert undated.published_raw is None and undated.published_date is None
    assert docs["Example meeting notice from 2012"].published_date == "2012-05-08"


def test_whats_new_without_pager_is_incomplete(adapter, contracts) -> None:
    obs = collect(
        adapter, contracts, "coal_whats_new", fetcher({SITE + "whats-new": "whats_new.html"})
    )
    assert obs.evidence.parse_valid and not obs.evidence.enumeration_complete
    assert len(obs.documents) == 2


# ---------------------------------------------------------------------------
# Nested reports, link list, narrative


def test_nested_report_index_and_details(adapter, contracts) -> None:
    index = PI + "reports/annual-reports"
    replay = fetcher(
        {
            index: "nested_report_index.html",
            PI + "reports/example/current-report": "nested_report_detail.html",
            PI + "reports/example/historical-report": "nested_report_detail_historical.html",
        }
    )
    obs = collect(adapter, contracts, "coal_annual_reports", replay)
    assert_clean(obs)
    assert [m.title for m in obs.matters] == ["Example Report 2025-26", "Example Report 2009-10"]
    current = obs.matters[0]
    assert current.native_id == PI + "reports/example/current-report"
    assert current.url == "/public-information/reports/example/current-report"
    parents = {d.title: d.matter_native_id for d in obs.documents}
    assert parents == {
        "Policy initiatives": current.native_id,
        "Hindi chapter": current.native_id,
        "Full report": obs.matters[1].native_id,
    }
    assert obs.documents[0].provenance.page_url == PI + "reports/example/current-report"


def test_nested_detail_fetch_error(adapter, contracts) -> None:
    replay = fetcher(
        {
            PI + "reports/budget-reports": "nested_report_index.html",
            PI + "reports/example/current-report": "nested_report_detail.html",
        }
    )  # the historical detail page is unrecorded: HTTP 404
    obs = collect(adapter, contracts, "coal_budget_reports", replay)
    assert not obs.evidence.fetch_ok and not obs.evidence.enumeration_complete
    assert any("HTTP 404" in d for d in obs.evidence.diagnostics)


def test_direct_link_list(adapter, contracts) -> None:
    replay = fetcher({PI + "action-plan": "direct_link_list.html"})
    obs = collect(adapter, contracts, "coal_action_plans", replay)
    assert_clean(obs)
    assert [d.title for d in obs.documents] == [
        "Action Plan for FY 2025-26",
        "Action Plan for FY 2024-25",
    ]
    assert obs.documents[0].provenance.locator == "list[0]/item[0]/link[0]"


def test_narrative_scheme_page(adapter, contracts) -> None:
    url = SITE + "central-sector-schemes"
    obs = collect(
        adapter, contracts, "coal_central_schemes", fetcher({url: "central_sector_schemes.html"})
    )
    assert_clean(obs)
    (matter,) = obs.matters
    assert matter.native_id == url and matter.title == "Central Sector Schemes"
    assert matter.metadata["schemes"] == (
        "Exploration; Research and Development; Conservation and Safety"
    )
    assert obs.documents == ()
    noisy = fixture_bytes("central_sector_schemes.html").replace(
        b"<main>", b'<main><script src="/bnith__token-123"></script><!-- refetch -->'
    )
    again = collect(
        adapter,
        contracts,
        "coal_central_schemes",
        ReplayFetcher().add(url, body=noisy, content_type="text/html"),
    )
    assert again.matters[0].metadata == matter.metadata  # anti-bot script and comments ignored
    no_table = fixture_bytes("central_sector_schemes.html").split(b"<table>")[0] + b"</main>"
    broken = collect(
        adapter,
        contracts,
        "coal_central_schemes",
        ReplayFetcher().add(url, body=no_table, content_type="text/html"),
    )
    assert not broken.evidence.parse_valid


# ---------------------------------------------------------------------------
# Failure evidence


def test_captcha_page_is_blocked(adapter, contracts) -> None:
    obs = collect(
        adapter,
        contracts,
        "coal_auction_allotment",
        fetcher({NA + "auction-allotment": "captcha_block.html"}),
    )
    assert obs.evidence.block_page_detected
    assert not obs.evidence.enumeration_complete
    assert obs.documents == ()


def test_captcha_mid_pager_stops_feed(adapter, contracts) -> None:
    routes = _auction_routes()
    routes[NA + "auction-allotment?page=3"] = "captcha_block.html"
    replay = fetcher(routes)
    obs = collect(adapter, contracts, "coal_auction_allotment", replay)
    assert obs.evidence.block_page_detected and not obs.evidence.enumeration_complete
    assert replay.requested_urls[-1].endswith("?page=3")  # no further requests


def test_fetch_error(adapter, contracts) -> None:
    replay = ReplayFetcher().add(SITE + "CSR/guidelines", error="connection_reset")
    obs = collect(adapter, contracts, "coal_csr_guidelines", replay)
    assert not obs.evidence.fetch_ok and not obs.evidence.enumeration_complete
    assert obs.evidence.pages_fetched == 0


def test_unexpected_mime_type(adapter, contracts) -> None:
    replay = ReplayFetcher().add(
        SITE + "public-information/paper-laid-table",
        body=b"%PDF-1.7",
        content_type="application/pdf",
    )
    obs = collect(adapter, contracts, "coal_papers_laid", replay)
    assert obs.evidence.unexpected_mime_types == ("application/pdf",)
    assert not obs.evidence.enumeration_complete


def test_unknown_feed_raises(adapter, contracts) -> None:
    with pytest.raises(ValueError):
        adapter.collect(contracts["mop_whats_new"], ReplayFetcher(), observed_at=OBSERVED)


# ---------------------------------------------------------------------------
# Engine integration


def run_ctx(n: int) -> RunContext:
    return RunContext(
        run_id=f"run-{n}",
        started_at=f"2026-11-{n:02d}T02:30:00Z",
        config_hash="cfg",
        code_revision="test",
    )


def at(n: int) -> str:
    return f"2026-11-{n:02d}T02:40:00Z"


def test_engine_silent_baseline_then_new_document(adapter, contracts) -> None:
    contract = contracts["coal_quarterly_reports"]
    url = SITE + "major-statistics/quarterly-booklet"
    first = adapter.collect(
        contract, fetcher({url: "direct_attachment_table.html"}), observed_at=at(1)
    )
    outcome = apply_observation(CatalogState(), first, contract, run_ctx(1))
    assert outcome.events == () and outcome.feed_run.accepted
    assert outcome.state.feeds[contract.feed_id].baseline_status is BaselineStatus.COMPLETE
    second = adapter.collect(
        contract, fetcher({url: "direct_attachment_table_new_row.html"}), observed_at=at(2)
    )
    outcome = apply_observation(outcome.state, second, contract, run_ctx(2))
    assert [(e.event_type, e.discovery.value) for e in outcome.events] == [
        (EventType.NEW_DOCUMENT, "live")
    ]
    new_doc = outcome.state.documents[outcome.events[0].entity_key]
    assert new_doc.title == "Newly added example publication"
    assert new_doc.published_date == "2026-10-22"
    assert new_doc.first_seen_at == at(2)


def test_engine_archive_move_is_status_change(adapter, contracts) -> None:
    contract = contracts["coal_termination_orders"]
    url = NA + "termination-order"
    archive = NA + "archive-termination-order"
    base = {url: "termination_orders_with_archive.html", archive: "termination_archive.html"}
    moved = {url: "termination_orders_moved.html", archive: "termination_archive_moved.html"}
    state = CatalogState()
    outcome = apply_observation(
        state, adapter.collect(contract, fetcher(base), observed_at=at(1)), contract, run_ctx(1)
    )
    assert outcome.events == ()
    for n in (2, 3):  # two complete enumerations: enough for a removal if one were inferred
        obs = adapter.collect(contract, fetcher(moved), observed_at=at(n))
        outcome = apply_observation(outcome.state, obs, contract, run_ctx(n))
        kinds = sorted(e.event_type.value for e in outcome.events)
        if n == 2:
            # Archive tables have no Date column; the engine keeps the known date, so the
            # move is only a status change (D-025).
            assert kinds == ["new_document", "status_changed"]
            change = next(e for e in outcome.events if e.event_type is EventType.STATUS_CHANGED)
            moved_doc = outcome.state.documents[change.entity_key]
            assert moved_doc.title == "Example termination order"
            assert moved_doc.published_date is not None
        else:
            assert kinds == []
    assert all(d.status is RecordStatus.ACTIVE for d in outcome.state.documents.values())
    assert len(outcome.state.documents) == 3


def test_engine_quarantines_captcha(adapter, contracts) -> None:
    contract = contracts["coal_quarterly_reports"]
    url = SITE + "major-statistics/quarterly-booklet"
    first = adapter.collect(
        contract, fetcher({url: "direct_attachment_table.html"}), observed_at=at(1)
    )
    outcome = apply_observation(CatalogState(), first, contract, run_ctx(1))
    blocked = adapter.collect(contract, fetcher({url: "captcha_block.html"}), observed_at=at(2))
    after = apply_observation(outcome.state, blocked, contract, run_ctx(2))
    assert not after.feed_run.accepted and "block_page" in after.feed_run.quarantine_reasons
    assert after.state.documents == outcome.state.documents and after.events == ()
