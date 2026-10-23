from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from policy_tracker.core.config import (
    ConfigError,
    config_hash,
    load_config,
    load_contracts,
)
from policy_tracker.core.identity import canonical_url
from policy_tracker.core.models import FeedKind
from policy_tracker.inventory import ALLOWED_KINDS

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "sources.yaml"


@pytest.fixture(scope="module")
def config() -> dict:
    return load_config(CONFIG_PATH)


@pytest.fixture(scope="module")
def contracts(config) -> dict:
    return load_contracts(config)


def _tracked_required_ids(site: dict) -> set[str]:
    tracked = {feed["id"] for feed in site["feeds"] if feed["decision"] == "track"}
    return set(site["required_feed_ids"]) & tracked


def test_feed_kind_enum_matches_inventory_kinds() -> None:
    assert {kind.value for kind in FeedKind} == ALLOWED_KINDS


def test_every_tracked_feed_becomes_a_contract(config, contracts) -> None:
    for site in config["sites"]:
        site_contracts = {c.feed_id for c in contracts.values() if c.site_id == site["id"]}
        assert site_contracts == _tracked_required_ids(site)
    assert {c.site_id for c in contracts.values()} == {
        "ministry_of_power",
        "ministry_of_coal",
        "cerc",
    }
    # Discovery feeds (sitemaps, taxonomy) are indirect and never collected.
    assert "cerc_sitemap" not in contracts
    assert "mop_document_taxonomy" not in contracts
    assert all(c.decision == "track" for c in contracts.values())


def test_contract_counts_per_site(contracts) -> None:
    counts: dict[str, int] = {}
    for contract in contracts.values():
        counts[contract.site_id] = counts.get(contract.site_id, 0) + 1
    assert counts == {"ministry_of_power": 3, "ministry_of_coal": 27, "cerc": 24}


def test_all_kinds_are_typed_and_rolling_feeds_lack_removal_authority(contracts) -> None:
    assert {c.kind for c in contracts.values()} >= {
        FeedKind.ROLLING,
        FeedKind.ROTATING_ARCHIVE,
        FeedKind.AUTHORITATIVE_COLLECTION,
        FeedKind.EVOLVING_COLLECTION,
        FeedKind.DETAIL_PARENT,
    }
    for contract in contracts.values():
        if contract.kind is FeedKind.ROLLING:
            assert contract.is_rolling
    assert contracts["cerc_home"].removal_authority is False
    assert contracts["cerc_order_archives"].removal_authority is True


def test_versions_default_to_one(contracts) -> None:
    assert {c.coverage_revision for c in contracts.values()} == {1}
    assert {c.parser_version for c in contracts.values()} == {1}


def test_cerc_fetch_policy_follows_d020(contracts) -> None:
    fetch = contracts["cerc_home"].fetch
    assert fetch.request_rate_per_host_per_second == 1.0
    assert fetch.request_timeout_seconds == 120.0
    assert fetch.rate_limit_shared_across_hosts is True
    assert fetch.canonical_host == "cercind.gov.in"
    assert fetch.fallback_hosts == ("www.cercind.gov.in",)
    assert fetch.host_fallback_on == (
        "connect_timeout",
        "tls_handshake_timeout",
        "read_timeout",
        "connection_reset",
        "empty_reply",
    )
    assert fetch.max_attempts_per_url == 6
    assert fetch.retry_backoff_seconds == (20.0, 40.0)
    assert fetch.abort_after_consecutive_failed_urls == 3
    # Global defaults still fill keys the site does not set.
    assert fetch.max_redirects == 5
    assert fetch.max_document_bytes == 104857600


def test_coal_rate_limit_and_slash_rule(contracts) -> None:
    contract = contracts["coal_auction_allotment"]
    assert contract.fetch.request_rate_per_host_per_second == 0.25  # D-017
    assert contract.fetch.request_timeout_seconds == 30.0  # global default
    assert contract.fetch.fallback_hosts == ()
    assert contract.fetch.abort_after_consecutive_failed_urls is None
    assert contract.host_rules.collapse_duplicate_slashes is True
    url = canonical_url(
        "http://www.coal.nic.in//nominated-authority/archive-auction-allotment",
        base_url=contract.base_url,
        rules=contract.host_rules,
    )
    assert url == "https://www.coal.nic.in/nominated-authority/archive-auction-allotment"


def test_only_coal_collapses_duplicate_slashes(contracts) -> None:
    for contract in contracts.values():
        expected = contract.site_id == "ministry_of_coal"
        assert contract.host_rules.collapse_duplicate_slashes is expected


def test_cerc_www_host_canonicalizes_to_apex(contracts) -> None:
    contract = contracts["cerc_public_notices"]
    rules = contract.host_rules
    assert rules.allowed_hosts == frozenset({"cercind.gov.in", "www.cercind.gov.in"})
    assert rules.host_aliases == {
        "cercind.gov.in": "cercind.gov.in",
        "www.cercind.gov.in": "cercind.gov.in",
    }
    assert (
        canonical_url(
            "http://WWW.cercind.gov.in/2026/orders/1.pdf", base_url=contract.base_url, rules=rules
        )
        == "https://cercind.gov.in/2026/orders/1.pdf"
    )
    assert (
        canonical_url("/2026/orders/1.pdf", base_url=contract.base_url, rules=rules)
        == "https://cercind.gov.in/2026/orders/1.pdf"
    )


def test_canonical_host_defaults_to_base_url_host(contracts) -> None:
    rules = contracts["mop_whats_new"].host_rules
    assert rules.host_aliases == {
        "powermin.gov.in": "www.powermin.gov.in",
        "www.powermin.gov.in": "www.powermin.gov.in",
    }
    assert contracts["mop_whats_new"].fetch.canonical_host is None


def test_mop_documents_template_and_status_passes(contracts) -> None:
    contract = contracts["mop_documents"]
    assert contract.status_passes == ("publish", "archive")
    assert contract.url is not None and "{category}" in contract.url
    assert contracts["mop_whats_new"].status_passes == ()
    assert contract.base_url == "https://www.powermin.gov.in/"


def test_config_hash_is_sha256_of_file_bytes() -> None:
    expected = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    assert config_hash(CONFIG_PATH) == expected
    assert config_hash(str(CONFIG_PATH)) == expected
    assert config_hash(CONFIG_PATH.read_bytes()) == expected
    assert config_hash(b"a") != config_hash(b"b")


def test_load_config_rejects_non_mapping_and_bad_yaml(tmp_path) -> None:
    listing = tmp_path / "list.yaml"
    listing.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a mapping"):
        load_config(listing)
    broken = tmp_path / "broken.yaml"
    broken.write_text("sites: [\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(broken)
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_load_config_resolves_merge_keys(config) -> None:
    coal = next(site for site in config["sites"] if site["id"] == "ministry_of_coal")
    feed = next(f for f in coal["feeds"] if f["id"] == "coal_monthly_statistics")
    assert feed["decision"] == "track"  # inherited from <<: *coal_direct_table
    assert feed["expected_mime_types"] == ["text/html", "application/pdf"]


def test_invalid_config_raises_with_every_problem(config) -> None:
    broken = deepcopy(config)
    cerc = next(site for site in broken["sites"] if site["id"] == "cerc")
    cerc["canonical_host"] = "elsewhere.gov.in"
    cerc["feeds"][1]["coverage_revision"] = 0
    with pytest.raises(ConfigError) as excinfo:
        load_contracts(broken)
    problems = excinfo.value.problems
    assert any("canonical_host: 'elsewhere.gov.in' is not in allowed_hosts" in p for p in problems)
    assert any(p.endswith("coverage_revision: expected an integer >= 1") for p in problems)
    assert "elsewhere.gov.in" in str(excinfo.value)


def test_missing_fetch_settings_raise(config) -> None:
    broken = deepcopy(config)
    del broken["defaults"]["max_redirects"]
    with pytest.raises(ConfigError) as excinfo:
        load_contracts(broken)
    assert any("fetch policy needs max_redirects" in p for p in excinfo.value.problems)
    # A site may supply the key itself.
    for site in broken["sites"]:
        site["max_redirects"] = 3
    assert {c.fetch.max_redirects for c in load_contracts(broken).values()} == {3}


def test_explicit_versions_are_used(config) -> None:
    changed = deepcopy(config)
    cerc = next(site for site in changed["sites"] if site["id"] == "cerc")
    feed = next(f for f in cerc["feeds"] if f["id"] == "cerc_home")
    feed["coverage_revision"] = 2
    feed["parser_version"] = 3
    contract = load_contracts(changed)["cerc_home"]
    assert (contract.coverage_revision, contract.parser_version) == (2, 3)


def test_coal_file_host_keeps_its_own_identity(contracts) -> None:
    rules = contracts["coal_whats_new"].host_rules
    base = contracts["coal_whats_new"].base_url
    assert set(rules.host_aliases) == {"www.coal.nic.in", "coal.nic.in"}
    # coal.gov.in hrefs stay raw (scheme and doubled slash) until a tested rule exists.
    raw = "http://coal.gov.in//sites/default/files/a.pdf"
    assert canonical_url(raw, base_url=base, rules=rules) == raw
    rewritten = canonical_url("http://www.coal.nic.in//x/y", base_url=base, rules=rules)
    assert rewritten == "https://www.coal.nic.in/x/y"
