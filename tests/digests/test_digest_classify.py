"""Unit tests for rule-based classification driven by config/taxonomy.yaml."""

from __future__ import annotations

import pytest

from policy_tracker.core.models import EventType
from policy_tracker.reporting.classify import (
    FALLBACK_REASON,
    TaxonomyError,
    load_taxonomy,
    parse_taxonomy,
)
from policy_tracker.reporting.classify import classify as _classify
from policy_tracker.reporting.types import Category

A, R, P, S, ROUTINE = (
    Category.ACTION_REQUIRED,
    Category.RULES_AND_REGULATION,
    Category.POLICY_AND_DIRECTION,
    Category.RESEARCH_AND_REPORTING,
    Category.ROUTINE,
)


def classify(title: str, feed_id: str = "mop_documents", text: str = "", event=None):  # type: ignore[no-untyped-def]
    return _classify(title, text, feed_id, "site", event)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Public Notice inviting comments on draft Electricity (Amendment) Rules, 2026", A),
        ("Suggestions sought from stakeholders on battery storage", A),
        ("Public hearing on draft tariff regulations", A),
        ("Electricity (Amendment) Act, 2026", R),
        ("Gazette Notification: Electricity (Rights of Consumers) Rules", R),
        ("Corrigendum to Notification No. 23/1/2026", R),
        ("Statement of Reasons for the Connectivity Regulations", R),
        ("National Electricity Policy 2026", P),
        ("Guidelines for Resource Adequacy Planning", P),
        ("Standard Operating Procedure for coal block auction", P),
        ("Statutory Advice to the Government of India", P),
        ("Annual Report 2025-26", S),
        ("Monthly Coal Statistics September 2026", S),
        ("Discussion Paper on Market Coupling", S),
        ("National Coal Index for September 2026", S),
        ("विद्युत (संशोधन) नियम, 2026", R),
        ("राष्ट्रीय विद्युत नीति", P),
        ("वार्षिक रिपोर्ट 2025-26", S),
        ("मसौदे पर टिप्पणी आमंत्रित", A),
    ],
)
def test_title_rules(title: str, expected: Category) -> None:
    category, reasons = classify(title)
    assert category is expected, reasons
    assert reasons and reasons[0].startswith("title:")


def test_word_boundaries_avoid_false_matches() -> None:
    category, reasons = classify("Contract impact assessment for transactions")
    assert category is ROUTINE
    assert reasons == (FALLBACK_REASON,)


def test_act_is_case_sensitive_but_act_with_year_is_not() -> None:
    assert classify("Plan of action for the sector")[0] is ROUTINE
    assert classify("Energy Conservation act 2001 explained")[0] is R


def test_highest_priority_category_wins() -> None:
    category, reasons = classify("Comments on draft Guidelines for open access")
    assert category is A
    assert reasons == ("title:action_required:comment",)


def test_routine_feed_default() -> None:
    category, reasons = classify("Petition No. 12/MP/2026 - NTPC vs. XYZ", "cerc_recent_rops")
    assert category is ROUTINE
    assert reasons == ("feed_default:cerc_recent_rops",)


def test_routine_feed_suppresses_ordinary_terms() -> None:
    category, _ = classify(
        "Order in Petition No. 5/MP/2026 on tariff regulation", "cerc_recent_orders"
    )
    assert category is ROUTINE


@pytest.mark.parametrize(
    ("title", "expected", "promoted_by"),
    [
        ("Order in Suo Motu Petition No. 3/SM/2026", P, "suo motu"),
        ("Suo-moto order on Draft Regulations for market coupling", R, "suo motu"),
        ("ROP on Draft Regulations hearing", R, "draft regulation"),
        ("In the matter of removal of difficulty under the Regulations, 2024", R,
         "regulations with year"),
    ],
)  # fmt: skip
def test_policy_significance_promotes_routine(
    title: str, expected: Category, promoted_by: str
) -> None:
    category, reasons = classify(title, "cerc_recent_orders")
    assert category is expected, reasons
    assert reasons[0] == f"promoted:{promoted_by}"


def test_promoted_without_other_match_uses_promoted_default() -> None:
    category, reasons = classify("Suo Motu Petition No. 4/SM/2026", "cerc_recent_rops")
    assert category is P
    assert reasons == ("promoted:suo motu", "promoted_default:policy_and_direction")


def test_routine_title_terms_gate_any_feed() -> None:
    category, reasons = classify("Record of Proceedings dated 01.10.2026", "mop_documents")
    assert category is ROUTINE
    assert reasons == ("title:routine:record of proceedings",)


def test_feed_default_when_title_has_no_term() -> None:
    category, reasons = classify(
        "CERC (Terms and Conditions of Tariff) 2029", "cerc_draft_regulations"
    )
    assert category is R
    assert reasons == ("feed_default:cerc_draft_regulations",)


def test_text_terms_when_title_and_feed_say_nothing() -> None:
    category, reasons = classify("Document 42", "mop_documents", text="category: Reports")
    assert category is S
    assert reasons == ("text:research_and_reporting:report",)


def test_action_text_promotes() -> None:
    category, reasons = classify(
        "Draft Electricity (Amendment) Rules, 2026",
        text="Comments and suggestions are invited from all stakeholders.",
    )
    assert category is A
    assert reasons == (
        "title:rules_and_regulation:rule",
        "text:action_required:comments invited",
    )


def test_action_text_beyond_scan_window_is_ignored() -> None:
    padding = "x " * 3000
    category, _ = classify(
        "Draft Electricity (Amendment) Rules, 2026",
        text=padding + "Comments are invited.",
    )
    assert category is R


def test_deadline_changed_event_is_action() -> None:
    category, reasons = classify("Annual Report", event=EventType.DEADLINE_CHANGED)
    assert category is A
    assert reasons[-1] == "event:deadline_changed"


def test_taxonomy_loads_and_validates() -> None:
    taxonomy = load_taxonomy()
    assert "cerc_recent_rops" in taxonomy.routine_feeds
    assert taxonomy.feed_defaults["coal_monthly_statistics"] is S
    assert taxonomy.site_for_feed("coal_whats_new") == "ministry_of_coal"
    with pytest.raises(TaxonomyError):
        parse_taxonomy({"schema_version": 1})
    with pytest.raises(TaxonomyError):
        parse_taxonomy(
            {"schema_version": 2, "categories": {"routine": {"priority": 1, "title_terms": []}}}
        )
    with pytest.raises(TaxonomyError):
        parse_taxonomy(
            {
                "schema_version": 2,
                "categories": {},
                "feed_defaults": {"x": "not_a_category"},
            }
        )
