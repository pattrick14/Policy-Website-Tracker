from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

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
    assert "sites[0].feeds[0].history.boundary_verified: research gate requires true" in errors


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

    assert "sites[0].required_feed_ids: required feed 'example_notices' is not configured" in errors


def test_site_coverage_decisions_are_required() -> None:
    inventory = valid_inventory()
    inventory["sites"][0]["coverage_decisions"] = []

    errors = validate_inventory(inventory, require_complete=True)

    assert "sites[0].coverage_decisions: expected a non-empty list" in errors


# --- Closed schema and formalized contract keys (Step 2) ---------------------

SITE = "sites[0]"
FEED = "sites[0].feeds[0]"


def _site(inventory: dict) -> dict:
    return inventory["sites"][0]


def _feed(inventory: dict) -> dict:
    return inventory["sites"][0]["feeds"][0]


def cerc_like_inventory() -> dict:
    inventory = valid_inventory()
    site = _site(inventory)
    site["allowed_hosts"] = ["example.gov.in", "www.example.gov.in"]
    site.update(
        {
            "request_rate_per_host_per_second": 0.25,
            "request_timeout_seconds": 120,
            "rate_limit_shared_across_hosts": True,
            "canonical_host": "example.gov.in",
            "fallback_hosts": ["www.example.gov.in"],
            "host_fallback_on": ["connect_timeout", "empty_reply"],
            "max_attempts_per_url": 6,
            "retry_backoff_seconds": [20, 40.5, 0],
            "abort_after_consecutive_failed_urls": 3,
            "collapse_duplicate_slashes": True,
        }
    )
    feed = _feed(inventory)
    feed.update(
        {
            "archive_url": "https://www.example.gov.in/archive",
            "archive_href_rewrite": {
                "applies_to": "archive footer href http://example.gov.in//<path>",
                "rewrite_to": "https://example.gov.in/<path>",
                "rule": "force HTTPS and collapse the duplicated slash",
            },
            "status_passes": ["publish", "archive"],
            "coverage_revision": 2,
            "parser_version": 1,
        }
    )
    feed["completeness"].update(
        {
            "removal_rule": "Removed after two complete enumerations without the row.",
            "child_term_reconciliation": "Compare child-term counts weekly.",
            "notes": "Free-text note.",
        }
    )
    return inventory


def test_all_formalized_keys_validate() -> None:
    assert validate_inventory(cerc_like_inventory(), require_complete=True) == []


def test_url_template_feed_with_archive_template_and_categories_validates() -> None:
    inventory = valid_inventory()
    feed = _feed(inventory)
    del feed["url"]
    feed["url_template"] = "https://example.gov.in/api?category={category}&page={page}"
    feed["archive_url_template"] = (
        "https://example.gov.in/api?category={category}&page={page}&status=archive"
    )
    feed["categories"] = ["reports", "orders"]

    assert validate_inventory(inventory) == []


def test_unknown_keys_are_rejected_everywhere() -> None:
    inventory = valid_inventory()
    inventory["extra_root"] = 1
    inventory["defaults"] = {"request_rate_per_host_per_second": 1, "canonical_host": "x.gov.in"}
    _site(inventory)["rate"] = 1
    _site(inventory)["coverage_decisions"][0]["notes"] = "x"
    _feed(inventory)["urll"] = "https://example.gov.in/"
    _feed(inventory)["completeness"]["removal"] = "x"

    errors = validate_inventory(inventory)

    assert errors == [
        "root.extra_root: unknown key",
        "defaults.canonical_host: unknown key",
        f"{SITE}.rate: unknown key",
        f"{SITE}.coverage_decisions[0].notes: unknown key",
        f"{FEED}.urll: unknown key",
        f"{FEED}.completeness.removal: unknown key",
    ]


def test_defaults_are_typed() -> None:
    inventory = valid_inventory()
    inventory["defaults"] = {
        "timezone": "",
        "request_rate_per_host_per_second": 0,
        "request_timeout_seconds": "30",
        "max_redirects": -1,
        "max_document_bytes": 0,
    }

    errors = validate_inventory(inventory)

    assert "defaults.timezone: expected non-empty text" in errors
    assert "defaults.request_rate_per_host_per_second: expected a positive number" in errors
    assert "defaults.request_timeout_seconds: expected a positive number" in errors
    assert "defaults.max_redirects: expected a non-negative integer" in errors
    assert "defaults.max_document_bytes: expected an integer >= 1" in errors


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        ("request_rate_per_host_per_second", 0, "expected a positive number"),
        ("request_rate_per_host_per_second", True, "expected a positive number"),
        ("request_timeout_seconds", -5, "expected a positive number"),
        ("rate_limit_shared_across_hosts", "yes", "expected a boolean"),
        ("collapse_duplicate_slashes", 1, "expected a boolean"),
        ("canonical_host", "other.gov.in", "'other.gov.in' is not in allowed_hosts"),
        ("canonical_host", "", "expected non-empty text"),
        ("fallback_hosts", ["other.gov.in"], "'other.gov.in' is not in allowed_hosts"),
        ("fallback_hosts", [], "expected a non-empty string list"),
        ("host_fallback_on", ["http_503"], "'http_503' is not one of"),
        ("host_fallback_on", "connect_timeout", "expected a non-empty string list"),
        ("max_attempts_per_url", 0, "expected an integer >= 1"),
        ("max_attempts_per_url", 1.5, "expected an integer >= 1"),
        ("retry_backoff_seconds", [20, -1], "expected a list of non-negative numbers"),
        ("retry_backoff_seconds", 20, "expected a list of non-negative numbers"),
        ("abort_after_consecutive_failed_urls", 0, "expected an integer >= 1"),
    ],
)
def test_bad_site_fetch_keys_are_rejected(key: str, value: object, error: str) -> None:
    inventory = cerc_like_inventory()
    _site(inventory)[key] = value

    errors = validate_inventory(inventory)

    assert any(e.startswith(f"{SITE}.{key}: ") and error in e for e in errors), errors


def test_host_fallback_requires_fallback_hosts() -> None:
    inventory = cerc_like_inventory()
    del _site(inventory)["fallback_hosts"]

    assert validate_inventory(inventory) == [f"{SITE}.host_fallback_on: requires fallback_hosts"]


def test_feed_and_base_urls_must_use_allowed_hosts() -> None:
    inventory = valid_inventory()
    _site(inventory)["base_url"] = "https://other.gov.in/"
    _feed(inventory)["url"] = "https://other.gov.in/publications"
    _feed(inventory)["archive_url"] = "https://other.gov.in/archive"

    errors = validate_inventory(inventory)

    assert f"{SITE}.base_url: host 'other.gov.in' is not in the site's allowed_hosts" in errors
    assert f"{FEED}.endpoint: host 'other.gov.in' is not in the site's allowed_hosts" in errors
    assert f"{FEED}.archive_url: host 'other.gov.in' is not in the site's allowed_hosts" in errors


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        ("status_passes", [], "status_passes: expected a non-empty string list"),
        ("status_passes", ["publish", ""], "status_passes: expected a non-empty string list"),
        ("status_passes", ["publish", "publish"], "status_passes: entries must be unique"),
        ("categories", "reports", "categories: expected a non-empty string list"),
        ("archive_url", "http://example.gov.in/archive", "archive_url: expected an HTTPS URL"),
        ("archive_href_rewrite", "rewrite", "archive_href_rewrite: expected a mapping"),
        (
            "archive_href_rewrite",
            {"applies_to": "x", "rewrite_to": "y"},
            "archive_href_rewrite.rule: required field is missing",
        ),
        (
            "archive_href_rewrite",
            {"applies_to": "x", "rewrite_to": "", "rule": "z"},
            "archive_href_rewrite.rewrite_to: expected non-empty text",
        ),
        (
            "archive_href_rewrite",
            {"applies_to": "x", "rewrite_to": "y", "rule": "z", "hosts": []},
            "archive_href_rewrite.hosts: unknown key",
        ),
        ("coverage_revision", 0, "coverage_revision: expected an integer >= 1"),
        ("coverage_revision", "1", "coverage_revision: expected an integer >= 1"),
        ("parser_version", True, "parser_version: expected an integer >= 1"),
        ("parser_version", -2, "parser_version: expected an integer >= 1"),
    ],
)
def test_bad_feed_keys_are_rejected(key: str, value: object, error: str) -> None:
    inventory = valid_inventory()
    _feed(inventory)[key] = value

    assert f"{FEED}.{error}" in validate_inventory(inventory)


@pytest.mark.parametrize(
    ("template", "error"),
    [
        ("https://example.gov.in/api?page=1", "expected at least one {placeholder}"),
        ("https://example.gov.in/api?page={Page}", "placeholders must be lower_snake_case"),
        ("https://example.gov.in/api?page={page", "placeholders must be lower_snake_case"),
    ],
)
def test_bad_url_templates_are_rejected(template: str, error: str) -> None:
    inventory = valid_inventory()
    feed = _feed(inventory)
    del feed["url"]
    feed["url_template"] = template
    feed["archive_url_template"] = template

    errors = validate_inventory(inventory)

    assert any(e.startswith(f"{FEED}.url_template: ") and error in e for e in errors), errors
    assert any(e.startswith(f"{FEED}.archive_url_template: ") and error in e for e in errors), (
        errors
    )


def test_archive_url_template_requires_url_template() -> None:
    inventory = valid_inventory()
    _feed(inventory)["archive_url_template"] = "https://example.gov.in/api?page={page}"

    assert validate_inventory(inventory) == [f"{FEED}.archive_url_template: requires url_template"]


def test_category_placeholder_requires_categories() -> None:
    inventory = valid_inventory()
    feed = _feed(inventory)
    del feed["url"]
    feed["url_template"] = "https://example.gov.in/api?category={category}&page={page}"

    assert validate_inventory(inventory) == [
        f"{FEED}.categories: required when url_template uses {{category}}"
    ]


@pytest.mark.parametrize("field", ["removal_rule", "child_term_reconciliation", "notes"])
def test_completeness_text_fields_must_be_text(field: str) -> None:
    inventory = valid_inventory()
    _feed(inventory)["completeness"][field] = ["not", "text"]

    assert validate_inventory(inventory) == [
        f"{FEED}.completeness.{field}: expected non-empty text"
    ]


def test_completeness_requires_typed_removal_authority_and_indicators() -> None:
    inventory = valid_inventory()
    _feed(inventory)["completeness"] = {"removal_authority": "yes", "notes": "x"}

    errors = validate_inventory(inventory)

    assert f"{FEED}.completeness.failure_indicators: required field is missing" in errors
    assert f"{FEED}.completeness.removal_authority: expected a boolean" in errors


def test_rolling_feed_cannot_claim_removal_authority() -> None:
    inventory = valid_inventory()
    _feed(inventory)["kind"] = "rolling"

    assert validate_inventory(inventory) == [
        f"{FEED}.completeness.removal_authority: rolling feeds never establish removal"
    ]
