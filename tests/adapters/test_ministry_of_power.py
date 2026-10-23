"""Fixture tests for the Ministry of Power adapter (Step 3)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from policy_tracker.adapters import ministry_of_power as mop
from policy_tracker.adapters.base import Adapter
from policy_tracker.adapters.replay import ReplayFetcher
from policy_tracker.core.config import load_config, load_contracts
from policy_tracker.core.contracts import FeedContract, RunContext
from policy_tracker.core.engine import apply_observation
from policy_tracker.core.models import (
    BaselineStatus,
    CatalogState,
    Discovery,
    EntityKind,
    EventType,
)
from policy_tracker.core.observations import FeedObservation

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "ministry_of_power"
CONFIG = ROOT / "config" / "sources.yaml"
OBSERVED_AT = "2026-10-23T03:00:00Z"
API = "https://www.powermin.gov.in/cms/wp-json/"
EMPTY_PASS = "documents_child_slug_empty.json"  # total_items 0, total_pages 0


def docs_url(category: str, page: int, *, archive: bool = False, limit: int = 50) -> str:
    url = f"{API}document/documents?document_category={category}&limit={limit}&page={page}"
    return url + ("&post_status=archive" if archive else "")


# Coherent production-shaped (limit=50) enumeration of all six roots and both passes.
DOCUMENT_ROUTES: dict[str, str] = {
    mop.TAXONOMY_URL: "adapter_taxonomy.json",
    docs_url("reports", 1): "adapter_reports_page1.json",
    docs_url("reports", 2): "adapter_reports_page2_end.json",
    docs_url("reports", 1, archive=True): EMPTY_PASS,
    docs_url("acts-and-policy", 1): "adapter_acts_page1.json",
    docs_url("acts-and-policy", 2): "adapter_acts_page2_end.json",
    docs_url("acts-and-policy", 1, archive=True): "documents_archive_status.json",
    docs_url("acts-and-policy", 2, archive=True): "adapter_acts_archive_page2_end.json",
    docs_url("orders-and-notices", 1): "adapter_orders_page1.json",
    docs_url("orders-and-notices", 2): "adapter_orders_page2_end.json",
    docs_url("orders-and-notices", 1, archive=True): EMPTY_PASS,
    **{
        docs_url(category, 1, archive=archive): EMPTY_PASS
        for category in ("gazette-notifications", "guidelines", "publications")
        for archive in (False, True)
    },
}


def fixture_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def replay(routes: Mapping[str, str], **overrides: Mapping[str, Any]) -> ReplayFetcher:
    """Fetcher for ``routes`` (url -> fixture); ``overrides`` maps url -> ``add`` kwargs."""

    fetcher = ReplayFetcher(fixture_root=FIXTURES)
    for url, name in routes.items():
        if url not in overrides:
            fetcher.add(url, fixture=name)
    for url, kwargs in overrides.items():
        fetcher.add(url, **kwargs)
    return fetcher


def json_body(payload: Any) -> dict[str, Any]:
    return {"body": json.dumps(payload), "content_type": "application/json; charset=UTF-8"}


@pytest.fixture(scope="module")
def contracts() -> dict[str, FeedContract]:
    return load_contracts(load_config(CONFIG))


@pytest.fixture
def adapter() -> mop.MinistryOfPowerAdapter:
    return mop.MinistryOfPowerAdapter()


def collect(
    adapter: mop.MinistryOfPowerAdapter,
    contract: FeedContract,
    fetcher: ReplayFetcher,
) -> FeedObservation:
    return adapter.collect(contract, fetcher, observed_at=OBSERVED_AT)


# ---------------------------------------------------------------------------
# Config consistency


def test_feed_ids_and_constants_match_config(contracts: dict[str, FeedContract]) -> None:
    config = load_config(CONFIG)
    site = next(site for site in config["sites"] if site["id"] == mop.SITE_ID)
    tracked = {feed["id"] for feed in site["feeds"] if feed["decision"] == "track"}
    adapter: Adapter = mop.make_adapter()
    assert adapter.site_id == mop.SITE_ID
    assert adapter.parser_version == 1
    assert adapter.feed_ids() == frozenset(tracked)
    feeds = {feed["id"]: feed for feed in site["feeds"]}
    documents = feeds["mop_documents"]
    assert tuple(documents["categories"]) == mop.CATEGORIES
    assert tuple(documents["status_passes"]) == mop.STATUS_PASSES
    assert documents["url_template"] == mop.DOCUMENTS_URL_TEMPLATE
    assert documents["archive_url_template"] == mop.DOCUMENTS_ARCHIVE_URL_TEMPLATE
    assert feeds["mop_whats_new"]["url"] == mop.WHATS_NEW_URL
    assert feeds["mop_whats_new_archive"]["url"] == mop.WHATS_NEW_ARCHIVE_URL
    assert feeds["mop_document_taxonomy"]["url"] == mop.TAXONOMY_URL
    for feed_id in tracked:
        assert contracts[feed_id].parser_version == adapter.parser_version


# ---------------------------------------------------------------------------
# mop_whats_new


def test_whats_new_full_enumeration(adapter, contracts) -> None:
    fetcher = replay({mop.WHATS_NEW_URL: "adapter_whats_new.json"})
    observation = collect(adapter, contracts["mop_whats_new"], fetcher)
    evidence = observation.evidence
    assert (evidence.fetch_ok, evidence.parse_valid, evidence.enumeration_complete) == (
        True,
        True,
        True,
    )
    assert evidence.pages_fetched == evidence.pages_expected == 1
    assert observation.observed_at == OBSERVED_AT
    assert observation.parser_version == 1
    assert [m.native_id for m in observation.matters] == ["9002", "9001"]
    first = observation.matters[0]
    assert first.metadata["post_date"] == "2026-10-07T17:28:11"
    assert "menu_order" not in first.metadata  # ordering never becomes metadata
    assert first.provenance.page_url == mop.WHATS_NEW_URL
    assert first.provenance.locator == "posts[0]"
    doc = observation.documents[0]
    assert doc.native_id == "8002"
    assert doc.matter_native_id == "9002"
    assert doc.url == f"{API}post-page/post?id=8002"
    assert doc.provenance.locator == "posts[0].attachments[0]"
    assert doc.published_raw == "2026-10-07 17:28:11"
    assert doc.published_date == "2026-10-07"
    assert doc.section is None
    assert fetcher.unused() == []


def test_whats_new_excerpt_short_of_total_items_is_incomplete(adapter, contracts) -> None:
    fetcher = replay({mop.WHATS_NEW_URL: "whats_new.json"})  # 2 posts, total_items 26
    evidence = collect(adapter, contracts["mop_whats_new"], fetcher).evidence
    assert evidence.parse_valid and evidence.fetch_ok
    assert not evidence.enumeration_complete
    assert any("total_items=26" in line for line in evidence.diagnostics)


def test_whats_new_block_page_is_blocked(adapter, contracts) -> None:
    fetcher = replay({mop.WHATS_NEW_URL: "browser_block.html"})
    observation = collect(adapter, contracts["mop_whats_new"], fetcher)
    evidence = observation.evidence
    assert evidence.block_page_detected
    assert not evidence.parse_valid and not evidence.enumeration_complete
    assert evidence.unexpected_mime_types == ()  # block reported before MIME handling
    assert observation.matters == () and observation.documents == ()


def test_whats_new_403_block_page_also_fails_fetch(adapter, contracts) -> None:
    fetcher = replay(
        {},
        **{mop.WHATS_NEW_URL: {"status": 403, "fixture": "browser_block.html"}},
    )
    evidence = collect(adapter, contracts["mop_whats_new"], fetcher).evidence
    assert evidence.block_page_detected and not evidence.fetch_ok


def test_whats_new_malformed_json_is_parse_invalid(adapter, contracts) -> None:
    fetcher = replay(
        {},
        **{mop.WHATS_NEW_URL: {"body": '{"posts": [', "content_type": "application/json"}},
    )
    evidence = collect(adapter, contracts["mop_whats_new"], fetcher).evidence
    assert evidence.fetch_ok and not evidence.parse_valid
    assert not evidence.enumeration_complete


def test_whats_new_wrong_shape_is_parse_invalid(adapter, contracts) -> None:
    fetcher = replay({}, **{mop.WHATS_NEW_URL: json_body({"posts": [{"title": "no id"}]})})
    evidence = collect(adapter, contracts["mop_whats_new"], fetcher).evidence
    assert not evidence.parse_valid


def test_whats_new_non_json_mime_is_reported(adapter, contracts) -> None:
    body = (FIXTURES / "adapter_whats_new.json").read_text(encoding="utf-8")
    fetcher = replay({}, **{mop.WHATS_NEW_URL: {"body": body, "content_type": "text/plain"}})
    evidence = collect(adapter, contracts["mop_whats_new"], fetcher).evidence
    assert evidence.unexpected_mime_types == ("text/plain",)


def test_whats_new_fetch_error(adapter, contracts) -> None:
    fetcher = replay({}, **{mop.WHATS_NEW_URL: {"error": "connection_reset"}})
    evidence = collect(adapter, contracts["mop_whats_new"], fetcher).evidence
    assert not evidence.fetch_ok and not evidence.enumeration_complete
    assert evidence.pages_fetched == 0


def test_whats_new_http_error_json(adapter, contracts) -> None:
    fetcher = replay(
        {}, **{mop.WHATS_NEW_URL: {"status": 403, "fixture": "rest_root_disabled.json"}}
    )
    evidence = collect(adapter, contracts["mop_whats_new"], fetcher).evidence
    assert not evidence.fetch_ok and not evidence.block_page_detected


# ---------------------------------------------------------------------------
# mop_whats_new_archive


def test_whats_new_archive_enumeration_and_untrusted_dates(adapter, contracts) -> None:
    fetcher = replay({mop.WHATS_NEW_ARCHIVE_URL: "adapter_whats_new_archive.json"})
    observation = collect(adapter, contracts["mop_whats_new_archive"], fetcher)
    assert observation.evidence.enumeration_complete
    assert [m.native_id for m in observation.matters] == ["10177", "17222"]
    assert observation.matters[0].metadata["post_status"] == "archive"
    doc = observation.documents[0]
    assert (doc.native_id, doc.matter_native_id, doc.section) == ("8800", "10177", "archive")
    assert doc.published_raw == "2025-06-21 10:44:49"  # kept verbatim
    assert doc.published_date is None  # migration artifact, never a publication date


def test_whats_new_archive_excerpt_is_incomplete(adapter, contracts) -> None:
    fetcher = replay({mop.WHATS_NEW_ARCHIVE_URL: "whats_new_archive.json"})  # 2 of 637
    evidence = collect(adapter, contracts["mop_whats_new_archive"], fetcher).evidence
    assert evidence.parse_valid and not evidence.enumeration_complete


def test_whats_new_archive_block_page(adapter, contracts) -> None:
    fetcher = replay({mop.WHATS_NEW_ARCHIVE_URL: "browser_block.html"})
    assert collect(
        adapter, contracts["mop_whats_new_archive"], fetcher
    ).evidence.block_page_detected


# ---------------------------------------------------------------------------
# mop_documents


def test_documents_full_enumeration(adapter, contracts) -> None:
    fetcher = replay(DOCUMENT_ROUTES)
    observation = collect(adapter, contracts["mop_documents"], fetcher)
    evidence = observation.evidence
    assert evidence.fetch_ok and evidence.parse_valid and not evidence.block_page_detected
    assert evidence.enumeration_complete, evidence.diagnostics
    assert evidence.pages_fetched == evidence.pages_expected == len(DOCUMENT_ROUTES)
    assert fetcher.unused() == [] and fetcher.unmatched == []
    assert sorted(m.native_id for m in observation.matters) == sorted(
        ["20509", "20411", "13652", "7001", "7002", "21175", "13458", "7101"]
    )
    # Termination: the request after total_pages is the last one per non-empty pass.
    assert docs_url("reports", 3) not in fetcher.requested_urls
    assert docs_url("gazette-notifications", 2) not in fetcher.requested_urls


def test_documents_attachments_link_to_parents(adapter, contracts) -> None:
    observation = collect(adapter, contracts["mop_documents"], replay(DOCUMENT_ROUTES))
    by_id = {doc.native_id: doc for doc in observation.documents if doc.native_id}
    assert by_id["6001"].matter_native_id == "7001"
    assert by_id["6001"].published_raw == "12/02/2005"
    assert by_id["6001"].published_date == "2005-02-12"  # dd/mm/yyyy; single-file parent
    assert by_id["6001"].section == "publish"
    # Multi-file parent: the parent date is not attributed to individual files.
    assert by_id["6011"].published_date is None and by_id["6011"].published_raw is None
    assert by_id["6012"].provenance == by_id["6012"].provenance.__class__(
        docs_url("acts-and-policy", 1), "posts[1].attachments[1]"
    )
    assert by_id["6011"].title == "Policy and amendment"
    assert "group_index" not in by_id["6011"].metadata
    assert by_id["6011"].metadata["group_title"] == "Policy and amendment"
    external = [doc for doc in observation.documents if doc.native_id is None]
    assert [doc.url for doc in external] == ["https://www.example.org/resolution.pdf"]
    assert external[0].matter_native_id == "7002"
    assert external[0].provenance.locator == "posts[1].attachments[2]"
    # Untitled group falls back to the parent title; HTML entities are unescaped.
    assert by_id["8203"].title == "Example Lok Sabha question compilation"
    matter = next(m for m in observation.matters if m.native_id == "7002")
    assert matter.title == "Example tariff policy & amendments"
    assert matter.metadata["terms"] == "acts-and-policy"
    assert matter.metadata["post_status"] == "publish"
    annual = [doc.native_id for doc in observation.documents if doc.matter_native_id == "13652"]
    assert annual == ["5103", "5102", "5101"]


def test_reordered_file_groups_keep_document_metadata(adapter, contracts) -> None:
    """Review repro: reordering a post's file groups must not change any metadata."""

    url = docs_url("acts-and-policy", 1)
    payload = fixture_json("adapter_acts_page1.json")
    groups = payload["posts"][1]["acf_data"]["file"]
    payload["posts"][1]["acf_data"]["file"] = list(reversed(groups))
    original = collect(adapter, contracts["mop_documents"], replay(DOCUMENT_ROUTES))
    reordered = collect(
        adapter, contracts["mop_documents"], replay(DOCUMENT_ROUTES, **{url: json_body(payload)})
    )

    def metadata(observation: FeedObservation) -> dict[str, dict[str, str]]:
        return {doc.url: dict(doc.metadata) for doc in observation.documents}

    assert metadata(reordered) == metadata(original)
    moved = {doc.url: doc.provenance.locator for doc in reordered.documents}
    assert moved["https://www.example.org/resolution.pdf"] == "posts[1].attachments[0]"


def test_documents_archive_pass_sections(adapter, contracts) -> None:
    observation = collect(adapter, contracts["mop_documents"], replay(DOCUMENT_ROUTES))
    archived = {m.native_id: m for m in observation.matters if m.native_id in {"21175", "13458"}}
    assert {m.metadata["post_status"] for m in archived.values()} == {"archive"}
    url = docs_url("acts-and-policy", 1, archive=True)
    assert archived["13458"].provenance.page_url == url
    assert archived["13458"].provenance.locator == "posts[1]"
    sections = {doc.section for doc in observation.documents}
    assert sections == {"publish"}  # archived fixture parents carry no acf files


def test_documents_multi_page_archive_pass(contracts) -> None:
    adapter = mop.MinistryOfPowerAdapter(categories=("acts-and-policy",), page_limit=1)
    routes = {
        mop.TAXONOMY_URL: "adapter_taxonomy.json",
        docs_url("acts-and-policy", 1, limit=1): EMPTY_PASS,
        docs_url("acts-and-policy", 1, archive=True, limit=1): (
            "adapter_acts_archive_limit1_page1.json"
        ),
        docs_url("acts-and-policy", 2, archive=True, limit=1): "documents_archive_page.json",
        docs_url("acts-and-policy", 3, archive=True, limit=1): "documents_archive_after_end.json",
    }
    fetcher = replay(routes)
    observation = collect(adapter, contracts["mop_documents"], fetcher)
    assert observation.evidence.enumeration_complete, observation.evidence.diagnostics
    assert [m.native_id for m in observation.matters] == ["21175", "13458"]
    assert observation.matters[1].provenance.page_url == docs_url(
        "acts-and-policy", 2, archive=True, limit=1
    )
    assert observation.evidence.pages_expected == 5
    # Published root count 2 versus an empty publish pass is reported, not fatal.
    assert any("published total 0" in line for line in observation.evidence.diagnostics)


def test_documents_pagination_gap_is_incomplete(contracts) -> None:
    adapter = mop.MinistryOfPowerAdapter(categories=("acts-and-policy",), page_limit=1)
    routes = {
        mop.TAXONOMY_URL: "adapter_taxonomy.json",
        docs_url("acts-and-policy", 1, limit=1): EMPTY_PASS,
        docs_url("acts-and-policy", 1, archive=True, limit=1): (
            "adapter_acts_archive_limit1_page1.json"
        ),
        docs_url("acts-and-policy", 3, archive=True, limit=1): "documents_archive_after_end.json",
    }
    gap = {"posts": [], "total_items": 2, "total_pages": 2, "current_page": 2}
    fetcher = replay(
        routes, **{docs_url("acts-and-policy", 2, archive=True, limit=1): json_body(gap)}
    )
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert evidence.fetch_ok and evidence.parse_valid
    assert not evidence.enumeration_complete
    assert any("pagination gap" in line for line in evidence.diagnostics)


def test_documents_missing_page_fetch_fails(adapter, contracts) -> None:
    routes = dict(DOCUMENT_ROUTES)
    del routes[docs_url("reports", 2)]  # replay answers 404
    evidence = collect(adapter, contracts["mop_documents"], replay(routes)).evidence
    assert not evidence.fetch_ok and not evidence.enumeration_complete


def test_documents_terminus_not_empty_is_incomplete(adapter, contracts) -> None:
    extra = fixture_json("adapter_orders_page1.json")
    extra.update(current_page=2)
    fetcher = replay(DOCUMENT_ROUTES, **{docs_url("orders-and-notices", 2): json_body(extra)})
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert not evidence.enumeration_complete
    assert any("is not empty" in line for line in evidence.diagnostics)


def test_documents_totals_short_of_total_items(adapter, contracts) -> None:
    excerpt = fixture_json("documents_root_child_terms.json")  # 3 posts, total_items 9
    fetcher = replay(
        DOCUMENT_ROUTES,
        **{
            docs_url("reports", 1): json_body(excerpt),
            docs_url("reports", 2): json_body(
                {"posts": [], "total_items": 9, "total_pages": 1, "current_page": 2}
            ),
        },
    )
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert not evidence.enumeration_complete
    assert any("total_items=9" in line for line in evidence.diagnostics)


def test_documents_empty_categories_are_complete(contracts) -> None:
    adapter = mop.MinistryOfPowerAdapter(categories=("gazette-notifications", "guidelines"))
    routes = {
        mop.TAXONOMY_URL: "adapter_taxonomy.json",
        **{
            docs_url(category, 1, archive=archive): EMPTY_PASS
            for category in ("gazette-notifications", "guidelines")
            for archive in (False, True)
        },
    }
    fetcher = replay(routes)
    observation = collect(adapter, contracts["mop_documents"], fetcher)
    assert observation.evidence.enumeration_complete, observation.evidence.diagnostics
    assert observation.matters == () and observation.documents == ()
    assert observation.evidence.pages_fetched == 5
    assert fetcher.unused() == [] and fetcher.unmatched == []


def test_documents_child_term_reconciliation_mismatch(adapter, contracts) -> None:
    taxonomy = fixture_json("adapter_taxonomy.json")
    for term in taxonomy["documents_category"]:
        if term["slug"] == "general-report":
            term["count"] = 2  # one child-only tagged post is hidden from the root query
    fetcher = replay(DOCUMENT_ROUTES, **{mop.TAXONOMY_URL: json_body(taxonomy)})
    observation = collect(adapter, contracts["mop_documents"], fetcher)
    evidence = observation.evidence
    assert evidence.fetch_ok and evidence.parse_valid
    assert not evidence.enumeration_complete
    assert any(
        "child-term reconciliation mismatch: 'general-report'" in line
        for line in evidence.diagnostics
    )
    assert len(observation.matters) == 8  # records are still reported


def test_documents_reconciliation_counts_archive_pass(adapter, contracts) -> None:
    # general_acts count 3 is met only by counting the two archived parents too.
    taxonomy = fixture_json("adapter_taxonomy.json")
    for term in taxonomy["documents_category"]:
        if term["slug"] == "general_acts":
            term["count"] = 3
    fetcher = replay(DOCUMENT_ROUTES, **{mop.TAXONOMY_URL: json_body(taxonomy)})
    assert collect(adapter, contracts["mop_documents"], fetcher).evidence.enumeration_complete


def test_documents_missing_root_and_unreviewed_terms(adapter, contracts) -> None:
    taxonomy = fixture_json("adapter_taxonomy.json")
    terms = [t for t in taxonomy["documents_category"] if t["slug"] != "publications"]
    terms.append({"term_id": 900, "name": "New", "slug": "new-root", "parent": 0, "count": 1})
    fetcher = replay(
        DOCUMENT_ROUTES, **{mop.TAXONOMY_URL: json_body({"documents_category": terms})}
    )
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert not evidence.enumeration_complete
    assert any("'publications' is missing" in line for line in evidence.diagnostics)
    assert any("unreviewed root term 'new-root'" in line for line in evidence.diagnostics)


def test_documents_taxonomy_block_stops_requests(adapter, contracts) -> None:
    fetcher = replay(DOCUMENT_ROUTES, **{mop.TAXONOMY_URL: {"fixture": "browser_block.html"}})
    observation = collect(adapter, contracts["mop_documents"], fetcher)
    assert observation.evidence.block_page_detected
    assert fetcher.requested_urls == [mop.TAXONOMY_URL]


def test_documents_block_page_mid_pass(adapter, contracts) -> None:
    fetcher = replay(
        DOCUMENT_ROUTES, **{docs_url("acts-and-policy", 1): {"fixture": "browser_block.html"}}
    )
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert evidence.block_page_detected and not evidence.parse_valid
    assert not evidence.enumeration_complete


def test_documents_malformed_page_is_parse_invalid(adapter, contracts) -> None:
    fetcher = replay(
        DOCUMENT_ROUTES,
        **{docs_url("orders-and-notices", 1): json_body({"posts": [], "total_items": "1"})},
    )
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert not evidence.parse_valid and not evidence.enumeration_complete


def test_documents_wordpress_html_error_is_unexpected_mime(adapter, contracts) -> None:
    page = {"body": "<html><body>Critical error</body></html>", "content_type": "text/html"}
    fetcher = replay(DOCUMENT_ROUTES, **{docs_url("guidelines", 1): page})
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert evidence.unexpected_mime_types == ("text/html",)
    assert not evidence.parse_valid and not evidence.block_page_detected


def test_documents_fetch_error(adapter, contracts) -> None:
    fetcher = replay(
        DOCUMENT_ROUTES, **{docs_url("publications", 1, archive=True): {"error": "timeout"}}
    )
    evidence = collect(adapter, contracts["mop_documents"], fetcher).evidence
    assert not evidence.fetch_ok and not evidence.enumeration_complete


def test_rejects_other_feeds(adapter, contracts) -> None:
    other = next(c for c in contracts.values() if c.site_id != mop.SITE_ID)
    with pytest.raises(ValueError):
        collect(adapter, other, replay({}))


def test_date_helpers() -> None:
    assert mop.acf_date_iso("12/02/2005") == "2005-02-12"
    assert mop.acf_date_iso("20160128") == "2016-01-28"
    assert mop.acf_date_iso("31/02/2020") is None
    assert mop.acf_date_iso("") is None
    assert mop.wp_datetime_iso("0000-00-00 00:00:00") is None


# ---------------------------------------------------------------------------
# Engine integration


def _run(n: int) -> RunContext:
    return RunContext(
        run_id=f"run-{n}",
        started_at=f"2026-10-{n:02d}T02:30:00Z",
        config_hash="test",
        code_revision="test",
    )


def test_engine_silent_baseline_then_live_new_document(adapter, contracts) -> None:
    contract = contracts["mop_documents"]
    first = adapter.collect(contract, replay(DOCUMENT_ROUTES), observed_at="2026-10-01T03:00:00Z")
    outcome = apply_observation(CatalogState(), first, contract, _run(1))
    assert outcome.feed_run.accepted
    assert outcome.events == ()  # silent baseline
    assert outcome.state.feeds["mop_documents"].baseline_status is BaselineStatus.COMPLETE
    document = outcome.state.documents["ministry_of_power:document:id:6001"]
    assert document.matter_key == "ministry_of_power:matter:id:7001"
    assert document.discovery is Discovery.BASELINE

    variant = replay(
        DOCUMENT_ROUTES,
        **{docs_url("acts-and-policy", 1): {"fixture": "adapter_acts_page1_new_attachment.json"}},
    )
    second = adapter.collect(contract, variant, observed_at="2026-10-08T03:00:00Z")
    outcome = apply_observation(outcome.state, second, contract, _run(8))
    assert outcome.feed_run.accepted
    new_docs = [e for e in outcome.events if e.event_type is EventType.NEW_DOCUMENT]
    assert [e.entity_key for e in new_docs] == ["ministry_of_power:document:id:6013"]
    assert new_docs[0].discovery is Discovery.LIVE
    added = outcome.state.documents["ministry_of_power:document:id:6013"]
    assert added.matter_key == "ministry_of_power:matter:id:7002"
    # The parent's modified timestamp changed with the new attachment.
    changed = [e for e in outcome.events if e.event_type is EventType.METADATA_CHANGED]
    assert [(e.entity_kind, e.details["changed_fields"]) for e in changed] == [
        (EntityKind.MATTER, "metadata.post_modified")
    ]


def test_engine_quarantines_blocked_and_incomplete_runs(adapter, contracts) -> None:
    contract = contracts["mop_whats_new"]
    blocked = adapter.collect(
        contract, replay({mop.WHATS_NEW_URL: "browser_block.html"}), observed_at=OBSERVED_AT
    )
    outcome = apply_observation(CatalogState(), blocked, contract, _run(1))
    assert not outcome.feed_run.accepted
    assert "block_page" in outcome.feed_run.quarantine_reasons

    partial = adapter.collect(
        contract, replay({mop.WHATS_NEW_URL: "whats_new.json"}), observed_at=OBSERVED_AT
    )
    outcome = apply_observation(CatalogState(), partial, contract, _run(1))
    assert outcome.feed_run.accepted
    assert outcome.state.feeds["mop_whats_new"].baseline_status is BaselineStatus.PARTIAL


def test_engine_whats_new_live_post_and_archive_move(adapter, contracts) -> None:
    rolling = contracts["mop_whats_new"]
    archive = contracts["mop_whats_new_archive"]
    state = CatalogState()
    for n, (contract, url, name) in enumerate(
        [
            (rolling, mop.WHATS_NEW_URL, "adapter_whats_new.json"),
            (archive, mop.WHATS_NEW_ARCHIVE_URL, "adapter_whats_new_archive.json"),
        ],
        start=1,
    ):
        obs = adapter.collect(contract, replay({url: name}), observed_at=f"2026-10-0{n}T03:00:00Z")
        state = apply_observation(state, obs, contract, _run(n)).state
    obs = adapter.collect(
        rolling,
        replay({mop.WHATS_NEW_URL: "adapter_whats_new_added.json"}),
        observed_at="2026-10-09T03:00:00Z",
    )
    outcome = apply_observation(state, obs, rolling, _run(9))
    kinds = sorted((e.event_type.value, e.entity_key) for e in outcome.events)
    assert kinds == [
        ("new_document", "ministry_of_power:document:id:8003"),
        ("new_matter", "ministry_of_power:matter:id:9003"),
    ]
    assert outcome.state.documents["ministry_of_power:document:id:8003"].published_date == (
        "2026-10-21"
    )
