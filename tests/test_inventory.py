from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from policy_tracker.inventory import load_inventory, validate_inventory


ROOT = Path(__file__).resolve().parents[1]


def valid_inventory() -> dict:
    return {
        "schema_version": 1,
        "sites": [
            {
                "id": "example_site",
                "name": "Example Site",
                "base_url": "https://example.gov.in/",
                "allowed_hosts": ["example.gov.in"],
                "required_feed_ids": ["example_publications"],
                "coverage_decisions": [
                    {
                        "scope": "Generic organization and contact pages",
                        "decision": "exclude",
                        "reason": "Static administrative content is outside the selected publication feeds.",
                        "evidence_urls": ["https://example.gov.in/sitemap"],
                        "coverage_limit": "No independent crawl; bulletin appearances remain captured.",
                        "refresh_policy": {"weekly": "compare sitemap link set"},
                        "fixtures": ["tests/fixtures/example/sitemap.html"],
                        "last_verified": "2026-10-22",
                    }
                ],
                "research_status": "complete",
                "feeds": [
                    {
                        "id": "example_publications",
                        "purpose": "Complete publication index.",
                        "url": "https://example.gov.in/publications",
                        "discovery_method": "sitemap",
                        "kind": "authoritative_collection",
                        "decision": "track",
                        "enumeration": {"method": "single_page", "termination": "end_of_table"},
                        "identity": {"native_ids": ["href"], "fallback": "canonical_url"},
                        "date_semantics": {"displayed": "publication_date"},
                        "expected_mime_types": ["text/html"],
                        "language_behavior": "English listing with English and Hindi documents.",
                        "refresh_policy": {"weekly": "full", "historical": "quarterly"},
                        "completeness": {
                            "removal_authority": True,
                            "failure_indicators": ["missing table"],
                        },
                        "history": {"boundary": "all rows", "boundary_verified": True},
                        "evidence_urls": ["https://example.gov.in/sitemap"],
                        "fixtures": ["tests/fixtures/example/publications.html"],
                        "verified": True,
                        "last_verified": "2026-10-22",
                    }
                ],
            }
        ],
    }


def test_complete_inventory_passes_gate() -> None:
    assert validate_inventory(valid_inventory(), require_complete=True) == []


def test_repository_inventory_is_structurally_valid() -> None:
    inventory = load_inventory(ROOT / "config" / "sources.yaml")

    assert validate_inventory(inventory) == []


def test_declared_repository_fixtures_exist() -> None:
    inventory = load_inventory(ROOT / "config" / "sources.yaml")
    fixture_paths = [
        fixture
        for site in inventory["sites"]
        for feed in site["feeds"]
        for fixture in feed["fixtures"]
    ]
    fixture_paths.extend(
        fixture
        for site in inventory["sites"]
        for decision in site["coverage_decisions"]
        for fixture in decision["fixtures"]
    )

    assert fixture_paths
    assert all((ROOT / fixture).is_file() for fixture in fixture_paths)


def test_duplicate_feed_ids_are_rejected_across_sites() -> None:
    inventory = valid_inventory()
    duplicate_site = deepcopy(inventory["sites"][0])
    duplicate_site["id"] = "second_site"
    inventory["sites"].append(duplicate_site)

    errors = validate_inventory(inventory)

    assert "sites[1].feeds[0].id: duplicate feed id 'example_publications'" in errors


def test_preliminary_contract_can_validate_but_cannot_pass_gate() -> None:
    inventory = valid_inventory()
    inventory["sites"][0]["research_status"] = "preliminary"
    feed = inventory["sites"][0]["feeds"][0]
    feed["verified"] = False
    feed["fixtures"] = []
    feed["history"]["boundary_verified"] = False

    assert validate_inventory(inventory) == []
    errors = validate_inventory(inventory, require_complete=True)

    assert "sites[0].research_status: research gate requires 'complete'" in errors
    assert "sites[0].feeds[0].verified: research gate requires true" in errors
    assert "sites[0].feeds[0].fixtures: research gate requires at least one fixture" in errors
    assert (
        "sites[0].feeds[0].history.boundary_verified: research gate requires true" in errors
    )


def test_endpoint_requires_exactly_one_url_shape() -> None:
    inventory = valid_inventory()
    inventory["sites"][0]["feeds"][0]["url_template"] = (
        "https://example.gov.in/publications?page={page}"
    )

    errors = validate_inventory(inventory)

    assert "sites[0].feeds[0]: provide exactly one of url or url_template" in errors


def test_completion_gate_rejects_missing_required_feed() -> None:
    inventory = valid_inventory()
    inventory["sites"][0]["required_feed_ids"].append("example_notices")

    errors = validate_inventory(inventory, require_complete=True)

    assert (
        "sites[0].required_feed_ids: required feed 'example_notices' is not configured"
        in errors
    )


def test_site_coverage_decisions_are_required() -> None:
    inventory = valid_inventory()
    inventory["sites"][0]["coverage_decisions"] = []

    errors = validate_inventory(inventory, require_complete=True)

    assert "sites[0].coverage_decisions: expected a non-empty list" in errors
