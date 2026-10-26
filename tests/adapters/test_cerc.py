"""Fixture tests for the CERC adapter (Step 5).

Multi-page fixtures (``<section data-source="/page.html">``) are split into one
replayed page per section. Pages the fixtures do not hold are derived from an
existing fixture shape in the test that needs them, never invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from policy_tracker.adapters.cerc import CercAdapter, make_adapter
from policy_tracker.adapters.cerc_parsers import parse_date
from policy_tracker.adapters.replay import ReplayFetcher
from policy_tracker.core.config import load_config, load_contracts
from policy_tracker.core.contracts import FeedContract, RunContext
from policy_tracker.core.engine import apply_observation
from policy_tracker.core.identity import canonical_url
from policy_tracker.core.models import (
    BaselineStatus,
    CatalogState,
    EventType,
    RecordStatus,
)
from policy_tracker.core.observations import FeedObservation

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "cerc"
BASE = "https://cercind.gov.in/"
OBSERVED = "2026-10-23T06:00:00Z"
HTML = "text/html; charset=utf-8"

SINGLE_PAGE = {
    "index-en.html": "home_bulletin.html",
    "Draft_reg.html": "draft_regulations.html",
    "Current_reg.html": "current_regulations.html",
    "Disc_Paper.html": "discussion_papers.html",
    "Work_Paper.html": "working_papers.html",
    "Study_Rep.html": "study_reports.html",
    "recent_orders.html": "recent_orders.html",
    "recent_rops.html": "recent_rops.html",
    "orders.html": "order_archives.html",
    "record_proceeding.html": "rop_archives.html",
    "report_MM.html": "market_monitoring.html",
    "annual_report.html": "annual_reports.html",
    "Other_Report.html": "other_reports.html",
    "public.html": "public_hearings.html",
    "Sched_hear.html": "hearing_schedule.html",
    "public-notice.html": "public_notices.html",
    "SPN.html": "statutory_public_notices.html",
    "advice_gov.html": "statutory_advice.html",
}


@pytest.fixture(scope="module")
def contracts() -> dict[str, FeedContract]:
    return load_contracts(load_config(ROOT / "config" / "sources.yaml"))


@pytest.fixture
def adapter() -> CercAdapter:
    return make_adapter()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def page(inner: str) -> str:
    return f'<!doctype html><html lang="en"><body>{inner}</body></html>'


def sections(name: str) -> dict[str, str]:
    """Split a multi-page fixture into {path: page html}; comments are kept verbatim."""

    soup = BeautifulSoup(fixture(name), "lxml")
    return {
        str(section["data-source"]).lstrip("/"): page(section.decode_contents())
        for section in soup.find_all("section", attrs={"data-source": True})
    }


def archive_pages(name: str, index: str, years: dict[str, str]) -> dict[str, str]:
    """Order/ROP archive fixture: the index page plus one annual page per year link."""

    text = fixture(name)
    soup = BeautifulSoup(text, "lxml")
    section = soup.find("section", attrs={"data-year": True})
    assert section is not None
    year_page = page(str(section))
    observed_year = str(section["data-year"])
    pages = {index: text}
    for path, year in years.items():
        pages[path] = year_page.replace(observed_year, year)
    return pages


def escalation_pages() -> dict[str, str]:
    """The escalation tree trimmed to the 2026 hub and the 2010 direct page."""

    pages = sections("escalation_rates.html")
    index = BeautifulSoup(pages["escalation_rates.html"], "lxml")
    for box in index.select("div.box_f"):
        if box.get_text(strip=True) not in {"2026", "2010"}:
            box.decompose()
    pages["escalation_rates.html"] = str(index)
    # The 2026 transmission leaf has the observed 2023 transmission shape.
    pages["escalation_rates_tran_ser_2026.html"] = pages.pop(
        "escalation_rates_tran_ser_2023.html"
    ).replace("2023", "2026")
    return pages


def default_pages() -> dict[str, str]:
    pages = {path: fixture(name) for path, name in SINGLE_PAGE.items()}
    # viewall.html has the bulletin shape without the View All link.
    pages["viewall.html"] = fixture("home_bulletin.html").replace(
        '<a href="/viewall.html">View All</a>', ""
    )
    pages.update(sections("meeting_minutes.html"))
    pages.update(sections("price_indices.html"))
    pages.update(escalation_pages())
    pages.update(
        archive_pages(
            "order_archives.html",
            "orders.html",
            {"recent_orders2025.html": "2025", "Neworder1999.html": "1999"},
        )
    )
    pages.update(
        archive_pages(
            "rop_archives.html",
            "record_proceeding.html",
            {"recent_rops2025.html": "2025", "Rop08.html": "2008"},
        )
    )
    return pages


def fetcher_for(pages: dict[str, str | bytes]) -> ReplayFetcher:
    fetcher = ReplayFetcher()
    for path, body in pages.items():
        fetcher.add(BASE + path, body=body, content_type=HTML)
    return fetcher


def collect(
    adapter: CercAdapter,
    contracts: dict[str, FeedContract],
    feed_id: str,
    pages: dict[str, str] | None = None,
    *,
    observed_at: str = OBSERVED,
) -> FeedObservation:
    fetcher = fetcher_for(pages if pages is not None else default_pages())
    return adapter.collect(contracts[feed_id], fetcher, observed_at=observed_at)


def urls(observation: FeedObservation) -> list[str]:
    return [document.url for document in observation.documents]


def assert_complete(observation: FeedObservation) -> None:
    evidence = observation.evidence
    assert evidence.fetch_ok, evidence.diagnostics
    assert evidence.parse_valid, evidence.diagnostics
    assert evidence.enumeration_complete, evidence.diagnostics
    assert not evidence.block_page_detected
    assert evidence.pages_fetched == evidence.pages_expected


# ---------------------------------------------------------------------------
# Adapter surface


def test_feed_ids_equal_tracked_cerc_feeds(adapter, contracts) -> None:
    tracked = {fid for fid, contract in contracts.items() if contract.site_id == "cerc"}
    assert adapter.feed_ids() == frozenset(tracked)
    assert len(tracked) == 24
    assert "cerc_sitemap" not in adapter.feed_ids()  # indirect, not collected
    assert adapter.site_id == "cerc"
    assert adapter.parser_version == 1


EXPECTED_COUNTS = {
    "cerc_home": 2,
    "cerc_draft_regulations": 6,
    "cerc_current_regulations": 7,
    "cerc_discussion_papers": 4,
    "cerc_working_papers": 2,
    "cerc_study_reports": 2,
    "cerc_recent_orders": 2,
    "cerc_order_archives": 2,
    "cerc_recent_rops": 2,
    "cerc_rop_archives": 2,
    "cerc_market_monitoring": 2,
    "cerc_annual_reports": 3,
    "cerc_other_reports": 1,
    "cerc_public_hearings": 3,
    "cerc_hearing_schedule": 5,
    "cerc_public_notices": 2,
    "cerc_statutory_public_notices": 3,
    "cerc_statutory_advice": 2,
    "cerc_commission_minutes": 3,
    "cerc_cac_minutes": 2,
    "cerc_other_meeting_minutes": 4,
    "cerc_coordination_forum_minutes": 1,
    "cerc_price_indices": 7,
    "cerc_escalation_rates": 18,
}


@pytest.mark.parametrize("feed_id", sorted(EXPECTED_COUNTS))
def test_every_feed_parses_its_fixture(adapter, contracts, feed_id) -> None:
    observation = collect(adapter, contracts, feed_id)
    assert_complete(observation)
    assert observation.site_id == "cerc"
    assert observation.observed_at == OBSERVED
    assert len(observation.documents) == EXPECTED_COUNTS[feed_id]
    assert len(set(urls(observation))) == len(observation.documents)
    for document in observation.documents:
        assert document.url.startswith(("https://cercind.gov.in/", "http://cercind.gov.in/"))
        assert document.title


def test_assert_expected_counts_cover_every_feed(adapter) -> None:
    assert set(EXPECTED_COUNTS) == adapter.feed_ids()


def test_parse_date_formats() -> None:
    assert parse_date("Notification dated 16-04-2026") == ("2026-04-16", "16-04-2026")
    assert parse_date("07.09.2026") == ("2026-09-07", "07.09.2026")
    assert parse_date("26-04-99") == ("1999-04-26", "26-04-99")
    assert parse_date("held on 30th, March, 2010") == ("2010-03-30", "30th, March, 2010")
    assert parse_date("dated 6 September 2024") == ("2024-09-06", "6 September 2024")
    assert parse_date("Notification dated 07-2014") == (None, None)
    assert parse_date("31.02.2026") == (None, None)
    assert parse_date(None) == (None, None)


# ---------------------------------------------------------------------------
# Bulletin


def test_home_reads_bulletin_and_view_all_once(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_home")
    assert observation.evidence.pages_fetched == 2
    assert urls(observation) == [
        "https://cercind.gov.in/2026/whatsnew/example-consultation.pdf",
        "https://cercind.gov.in/2022/whatsnew/example-persistent-form.docx",
    ]
    assert all(d.provenance.page_url.endswith("index-en.html") for d in observation.documents)
    assert "viewall.html" not in " ".join(urls(observation))
    assert contracts["cerc_home"].removal_authority is False


def test_home_view_all_404_is_a_fetch_failure(adapter, contracts) -> None:
    pages = default_pages()
    del pages["viewall.html"]
    observation = collect(adapter, contracts, "cerc_home", pages)
    assert not observation.evidence.fetch_ok
    assert not observation.evidence.enumeration_complete
    assert any("HTTP 404" in d for d in observation.evidence.diagnostics)


# ---------------------------------------------------------------------------
# Evolving matters


def test_draft_row_is_a_matter_with_every_child_file(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_draft_regulations")
    assert [(m.native_id, m.metadata["year_group"]) for m in observation.matters] == [
        ("draft_reg:119", "2026")
    ]
    assert observation.matters[0].title == "Draft Example (Fifth Amendment) Regulations, 2026."
    assert {d.matter_native_id for d in observation.documents} == {"draft_reg:119"}
    assert "https://cercind.gov.in/2026/draft_reg/example-comments.html" in urls(observation)
    assert "https://cercind.gov.in/2026/draft_reg/example-presentations.html" in urls(observation)


def test_draft_row_gaining_a_file_is_a_new_document_of_the_same_matter(adapter, contracts) -> None:
    pages = default_pages()
    pages["Draft_reg.html"] = with_new_draft_file(pages["Draft_reg.html"])
    observation = collect(adapter, contracts, "cerc_draft_regulations", pages)
    assert_complete(observation)
    added = [d for d in observation.documents if d.url.endswith("example-reasons.pdf")]
    assert len(added) == 1 and added[0].matter_native_id == "draft_reg:119"
    assert [m.native_id for m in observation.matters] == ["draft_reg:119"]


def with_new_draft_file(html: str) -> str:
    marker = '<a href="/2026/draft_reg/example-presentations.html">'
    return html.replace(
        marker,
        '<a href="/2026/draft_reg/example-reasons.pdf">7. Statement of Reasons</a>\n' + marker,
    )


def test_draft_without_year_groups_is_invalid(adapter, contracts) -> None:
    pages = default_pages()
    pages["Draft_reg.html"] = pages["Draft_reg.html"].replace(
        '<tr><td colspan="2">2026</td></tr>', ""
    )
    observation = collect(adapter, contracts, "cerc_draft_regulations", pages)
    assert observation.evidence.fetch_ok
    assert not observation.evidence.parse_valid
    assert not observation.evidence.enumeration_complete


def test_current_regulation_files_and_amendment_history(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_current_regulations")
    matters = {m.native_id: m for m in observation.matters}
    assert set(matters) == {"current_reg:208", "current_reg:1"}
    assert matters["current_reg:208"].title == "Example Third Amendment Regulations, 2026"
    assert matters["current_reg:208"].metadata["gazette_no"] == "542"
    by_url = {d.url: d for d in observation.documents}
    gazette = by_url["https://cercind.gov.in/2026/regulation/example-gazette.pdf"]
    assert (gazette.published_date, gazette.published_raw) == ("2026-09-07", "07.09.2026")
    old = by_url["https://cercind.gov.in/1999/regulation/example-gazette.pdf"]
    assert (old.published_date, old.published_raw) == ("1999-04-26", "26-04-99")
    principal = by_url["https://cercind.gov.in/2024/regulation/example-principal.pdf"]
    assert principal.matter_native_id == "current_reg:208"
    assert principal.metadata == {"role": "amendment_history"}
    assert principal.published_date is None
    # Primary files precede history links so a file's own row wins as its parent.
    roles = [d.metadata.get("role") for d in observation.documents]
    assert roles == sorted(roles, key=lambda role: role is not None)


def test_discussion_papers_terminate_at_serial_one(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_discussion_papers")
    assert {m.native_id for m in observation.matters} == {"disc_paper:32", "disc_paper:1"}
    pages = default_pages()
    html = pages["Disc_Paper.html"]
    start = html.index("<tr><td>1.</td>")
    pages["Disc_Paper.html"] = html[:start] + html[html.index("</tr>", start) + 5 :]
    truncated = collect(adapter, contracts, "cerc_discussion_papers", pages)
    assert truncated.evidence.parse_valid
    assert not truncated.evidence.enumeration_complete


def test_statutory_public_notice_paired_files_are_distinct_documents(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_statutory_public_notices")
    paired = [d for d in observation.documents if d.matter_native_id == "spn:272/TL/2026"]
    assert [(d.url.rsplit("/", 1)[1], d.metadata["language"]) for d in paired] == [
        ("example-english.pdf", "english"),
        ("example-hindi.pdf", "hindi"),
    ]
    assert {d.published_date for d in paired} == {"2026-10-07"}
    assert {m.native_id for m in observation.matters} == {"spn:272/TL/2026", "spn:118/TL/2019"}


def test_public_hearings_group_by_year_and_matter(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_public_hearings")
    assert len(observation.matters) == 2
    by_year = {m.metadata["year_group"]: m.native_id for m in observation.matters}
    hearing_2018 = [d for d in observation.documents if d.matter_native_id == by_year["2018"]]
    assert len(hearing_2018) == 2


# ---------------------------------------------------------------------------
# Orders, ROPs and annual archives


def test_recent_orders_metadata_and_dates(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_recent_orders")
    first = observation.documents[0]
    assert first.metadata == {
        "petition_no": "329/TL/2026",
        "posted_date": "22.10.2026",
        "category": "Licence (Transmission)",
    }
    assert (first.published_date, first.published_raw) == ("2026-10-22", "22.10.2026")
    assert observation.documents[1].published_date == "2025-12-29"


@pytest.mark.parametrize(
    ("feed_id", "index", "year_pages"),
    [
        ("cerc_order_archives", "orders.html", ["recent_orders2025.html", "Neworder1999.html"]),
        ("cerc_rop_archives", "record_proceeding.html", ["recent_rops2025.html", "Rop08.html"]),
    ],
)
def test_archives_enumerate_every_listed_year_page(
    adapter, contracts, feed_id, index, year_pages
) -> None:
    pages = default_pages()
    fetcher = fetcher_for(pages)
    observation = adapter.collect(contracts[feed_id], fetcher, observed_at=OBSERVED)
    assert_complete(observation)
    assert fetcher.requested_urls == [BASE + index] + [BASE + p for p in year_pages]
    years = {d.provenance.page_url.rsplit("/", 1)[1]: d.metadata["archive_year"]
             for d in observation.documents}  # fmt: skip
    assert set(years) == set(year_pages)
    # A missing year page fails the whole feed instead of shrinking the archive.
    del pages[year_pages[-1]]
    failed = collect(adapter, contracts, feed_id, pages)
    assert not failed.evidence.fetch_ok and not failed.evidence.enumeration_complete


# ---------------------------------------------------------------------------
# Hearing schedule, minutes, reports


def test_hearing_schedule_months_and_last_modified(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_hearing_schedule")
    october = [d for d in observation.documents if d.metadata.get("month") == "October"]
    assert [d.title for d in october] == ["Advance Cause List", "Weekly Cause List"]
    assert october[0].published_raw == "22.10.2026 4:10PM"
    assert october[0].published_date == "2026-10-22"


def test_minutes_dates_from_link_text(adapter, contracts) -> None:
    commission = collect(adapter, contracts, "cerc_commission_minutes")
    assert [d.published_date for d in commission.documents] == [
        "2026-04-17",
        "2024-05-31",
        "2009-08-11",
    ]
    other = collect(adapter, contracts, "cerc_other_meeting_minutes")
    presentation = other.documents[1]
    assert presentation.title.endswith("(1. Presentation)")
    assert presentation.published_date is None  # an application period is not a meeting date
    assert presentation.url == "https://cercind.gov.in/2015/whatsnew/example_presentation.pdf"


def test_report_period_labels(adapter, contracts) -> None:
    annual = collect(adapter, contracts, "cerc_annual_reports")
    assert annual.documents[0].title == "Annual Report 2024-2025"
    assert annual.documents[0].published_raw == "2024-2025"
    monitoring = collect(adapter, contracts, "cerc_market_monitoring")
    assert monitoring.documents[-1].metadata["reporting_year"] == "2008-2009"


# ---------------------------------------------------------------------------
# Tariff inputs


def test_price_indices_cover_collapsed_panel_and_cil_page(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_price_indices")
    pages_seen = {d.provenance.page_url for d in observation.documents}
    assert pages_seen == {BASE + "coal_price.html", BASE + "coal_price_index_base.html"}
    panel = [d for d in observation.documents if "updated 130326" in d.url]
    assert len(panel) == 1 and panel[0].published_date == "2026-03-13"
    assert not any(d.url.endswith("coal_price_index_base.html") for d in observation.documents)


def test_price_indices_missing_accordion_is_invalid(adapter, contracts) -> None:
    pages = default_pages()
    pages["coal_price.html"] = (
        pages["coal_price.html"].replace("panel-collapse", "x").replace("panel-group", "y")
    )
    observation = collect(adapter, contracts, "cerc_price_indices", pages)
    assert not observation.evidence.parse_valid


def test_escalation_tree_identity_and_hazards(adapter, contracts) -> None:
    fetcher = fetcher_for(escalation_pages())
    contract = contracts["cerc_escalation_rates"]
    observation = adapter.collect(contract, fetcher, observed_at=OBSERVED)
    assert_complete(observation)
    assert fetcher.requested_urls == [
        BASE + "escalation_rates.html",
        BASE + "escaltion_2026.html",
        BASE + "escalation_rates_gen_pro_2026.html",
        BASE + "escalation_rates_tran_ser_2026.html",
        BASE + "escalation_rates_2010.html",
    ]
    canon = {
        canonical_url(d.url, base_url=contract.base_url, rules=contract.host_rules): d
        for d in observation.documents
    }
    spaced = BASE + "2026/escalation/Example%20Monthly%20Notification%20Sep%202026.pdf"
    assert spaced in canon
    assert canon[spaced].published_date == "2026-10-01"
    assert canon[spaced].metadata["panel"] == "Monthly Escalation Rates 2026"
    assert canon[spaced].metadata["series"] == "generation"
    absolute = BASE + "2010/ORDER/Sept10/example_Order_Escalation_rates_1-2010.pdf"
    assert canon[absolute].url.startswith("http://cercind.gov.in/")
    assert canon[absolute].metadata["series"] == "combined"
    # Every accordion panel is read, including collapsed ones.
    assert {d.metadata.get("panel") for d in observation.documents} - {None} == {
        "Annual Escalation Rates 2026",
        "Six-Monthly Escalation Rates 2026",
        "Monthly Escalation Rates 2026",
    }
    # The repeated 2010 href with different link text is one document.
    amendment = [d for d in observation.documents if "Example_Amendment_dated" in d.url]
    assert len(amendment) == 1
    # Same-date transmission variants stay distinct.
    transmission = [d for d in observation.documents if d.metadata["series"] == "transmission"]
    assert len(transmission) == 4
    assert all(d.metadata["leaf_page"].endswith("tran_ser_2026.html") for d in transmission)


def test_escalation_commented_href_is_ignored(adapter, contracts) -> None:
    observation = collect(adapter, contracts, "cerc_escalation_rates")
    assert_complete(observation)
    assert "<!--" in escalation_pages()["escalation_rates_tran_ser_2026.html"]
    assert not any("example-noti7" in url for url in urls(observation))


def test_escalation_unlisted_year_page_404_fails_fetch(adapter, contracts) -> None:
    pages = escalation_pages()
    pages["escalation_rates.html"] = sections("escalation_rates.html")["escalation_rates.html"]
    observation = collect(adapter, contracts, "cerc_escalation_rates", pages)
    assert not observation.evidence.fetch_ok
    assert not observation.evidence.enumeration_complete
    assert any("escaltion_2025.html" in d for d in observation.evidence.diagnostics)


def test_escalation_hub_missing_transmission_link_is_invalid(adapter, contracts) -> None:
    pages = escalation_pages()
    pages["escaltion_2026.html"] = pages["escaltion_2026.html"].replace(
        "escalation_rates_tran_ser_2026.html", "elsewhere.html"
    )
    observation = collect(adapter, contracts, "cerc_escalation_rates", pages)
    assert not observation.evidence.parse_valid


# ---------------------------------------------------------------------------
# Comments, failures and evidence


def test_comment_hrefs_are_ignored(adapter, contracts) -> None:
    pages = default_pages()
    pages["Draft_reg.html"] = pages["Draft_reg.html"].replace(
        "<td>119.</td>",
        '<td>119.</td><!-- <a href="/2015/whatsnew/example-old-notice.pdf">stale</a> -->',
    )
    observation = collect(adapter, contracts, "cerc_draft_regulations", pages)
    assert_complete(observation)
    assert not any("example-old-notice" in url for url in urls(observation))


def test_missing_table_is_parse_invalid(adapter, contracts) -> None:
    pages = default_pages()
    html = pages["recent_orders.html"]
    pages["recent_orders.html"] = html[: html.index("<table>")] + "</body></html>"
    observation = collect(adapter, contracts, "cerc_recent_orders", pages)
    assert observation.evidence.fetch_ok
    assert not observation.evidence.parse_valid
    assert not observation.evidence.enumeration_complete
    assert any("missing order table" in d for d in observation.evidence.diagnostics)


def test_missing_heading_is_parse_invalid(adapter, contracts) -> None:
    pages = default_pages()
    pages["public-notice.html"] = pages["public-notice.html"].replace("<h4>Public Notice</h4>", "")
    observation = collect(adapter, contracts, "cerc_public_notices", pages)
    assert not observation.evidence.parse_valid


def test_malformed_order_row_is_parse_invalid(adapter, contracts) -> None:
    pages = default_pages()
    pages["recent_orders.html"] = pages["recent_orders.html"].replace(
        "<td>22.10.2026</td><td>22.10.2026</td>", "", 1
    )
    observation = collect(adapter, contracts, "cerc_recent_orders", pages)
    assert not observation.evidence.parse_valid


def test_http_404_is_a_fetch_failure(adapter, contracts) -> None:
    pages = default_pages()
    del pages["SPN.html"]
    observation = collect(adapter, contracts, "cerc_statutory_public_notices", pages)
    evidence = observation.evidence
    assert not evidence.fetch_ok
    assert not evidence.enumeration_complete
    assert (evidence.pages_fetched, evidence.pages_expected) == (0, 1)
    assert observation.documents == ()


def test_connection_error_is_a_fetch_failure(adapter, contracts) -> None:
    fetcher = ReplayFetcher().add(BASE + "advice_gov.html", error="connection_reset")
    observation = adapter.collect(contracts["cerc_statutory_advice"], fetcher, observed_at=OBSERVED)
    assert not observation.evidence.fetch_ok
    assert any("connection_reset" in d for d in observation.evidence.diagnostics)


def test_block_page_is_reported(adapter, contracts) -> None:
    block = page(
        "<title>Request Rejected</title>The requested URL was rejected. "
        "Please consult with your administrator."
    )
    pages = default_pages()
    pages["advice_gov.html"] = block
    observation = collect(adapter, contracts, "cerc_statutory_advice", pages)
    assert observation.evidence.block_page_detected
    assert not observation.evidence.parse_valid
    assert not observation.evidence.enumeration_complete


def test_unexpected_mime_type_is_reported(adapter, contracts) -> None:
    fetcher = ReplayFetcher().add(
        BASE + "advice_gov.html", body=b"%PDF-1.7", content_type="application/pdf"
    )
    observation = adapter.collect(contracts["cerc_statutory_advice"], fetcher, observed_at=OBSERVED)
    assert observation.evidence.unexpected_mime_types == ("application/pdf",)
    assert not observation.evidence.enumeration_complete


# ---------------------------------------------------------------------------
# Engine integration


def run(index: int) -> RunContext:
    return RunContext(
        run_id=f"run-{index}",
        started_at=f"2026-10-{20 + index:02d}T06:00:00Z",
        config_hash="test",
        code_revision="test",
    )


def test_engine_baseline_then_live_new_draft_document(adapter, contracts) -> None:
    contract = contracts["cerc_draft_regulations"]
    pages = default_pages()
    first = collect(adapter, contracts, contract.feed_id, pages, observed_at=run(1).started_at)
    outcome = apply_observation(CatalogState(), first, contract, run(1))
    assert outcome.feed_run.accepted
    assert outcome.events == ()
    assert outcome.state.feeds[contract.feed_id].baseline_status is BaselineStatus.COMPLETE

    pages["Draft_reg.html"] = with_new_draft_file(pages["Draft_reg.html"])
    second = collect(adapter, contracts, contract.feed_id, pages, observed_at=run(2).started_at)
    outcome = apply_observation(outcome.state, second, contract, run(2))
    assert [e.event_type for e in outcome.events] == [EventType.NEW_DOCUMENT]
    event = outcome.events[0]
    assert event.details["canonical_url"] == BASE + "2026/draft_reg/example-reasons.pdf"
    assert event.details["matter_key"] == "cerc:matter:id:draft_reg:119"


def test_engine_rolling_home_item_disappearing_never_withdraws(adapter, contracts) -> None:
    contract = contracts["cerc_home"]
    pages = default_pages()
    state = CatalogState()
    observation = collect(adapter, contracts, "cerc_home", pages, observed_at=run(1).started_at)
    state = apply_observation(state, observation, contract, run(1)).state
    gone = '<li><a href="/2022/whatsnew/example-persistent-form.docx">'
    for key in ("index-en.html", "viewall.html"):
        html = pages[key]
        start = html.index(gone)
        pages[key] = html[:start] + html[html.index("</li>", start) + 5 :]
    for index in (2, 3, 4):
        observation = collect(
            adapter, contracts, "cerc_home", pages, observed_at=run(index).started_at
        )
        assert_complete(observation)
        outcome = apply_observation(state, observation, contract, run(index))
        assert outcome.feed_run.accepted
        assert not [e for e in outcome.events if e.event_type is EventType.WITHDRAWN]
        state = outcome.state
    docs = [d for d in state.documents.values() if d.canonical_url.endswith(".docx")]
    assert len(docs) == 1 and docs[0].status is RecordStatus.ACTIVE
