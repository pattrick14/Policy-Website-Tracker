"""Round-trip and determinism tests for ``policy_tracker.core.serialize``."""

from __future__ import annotations

import json
import random

import pytest

from policy_tracker.core import models as m
from policy_tracker.core import serialize as s

TEXT_ALPHABET = "abcXYZ019 -_/:?&=.%हिंदीé₹\"\\\n\t"


def _text(rng: random.Random, *, empty: bool = True) -> str:
    size = rng.randint(0 if empty else 1, 12)
    return "".join(rng.choice(TEXT_ALPHABET) for _ in range(size))


def _opt(rng: random.Random, value):
    return value if rng.random() < 0.7 else None


def _mapping(rng: random.Random) -> dict[str, str]:
    return {_text(rng, empty=False): _text(rng) for _ in range(rng.randint(0, 4))}


def _enum(rng: random.Random, enum):
    return rng.choice(list(enum))


def _provenance(rng: random.Random) -> m.Provenance:
    return m.Provenance(page_url=f"https://example.gov.in/{_text(rng)}", locator=_text(rng))


def _matter(rng: random.Random) -> m.Matter:
    return m.Matter(
        key=f"site:matter:id:{_text(rng, empty=False)}",
        site_id="site",
        native_id=_opt(rng, _text(rng)),
        canonical_url=_opt(rng, "https://example.gov.in/a?b=c"),
        title=_text(rng),
        metadata=_mapping(rng),
        first_seen_at="2026-10-26T02:30:00Z",
        last_seen_at="2026-11-02T02:30:00Z",
        discovery=_enum(rng, m.Discovery),
        status=_enum(rng, m.RecordStatus),
        provenance=_opt(rng, _provenance(rng)),
    )


def _document(rng: random.Random) -> m.Document:
    return m.Document(
        key=f"site:document:url:{_text(rng, empty=False)}",
        site_id="site",
        native_id=_opt(rng, _text(rng)),
        canonical_url="https://example.gov.in/x.pdf",
        original_urls=tuple(_text(rng) for _ in range(rng.randint(0, 3))),
        title=_text(rng),
        matter_key=_opt(rng, "site:matter:id:1"),
        published_date=_opt(rng, "2026-10-01"),
        published_raw=_opt(rng, "01.10.2026"),
        metadata=_mapping(rng),
        metadata_fingerprint=_text(rng),
        first_seen_at="2026-10-26T02:30:00Z",
        last_seen_at="2026-10-26T02:30:00Z",
        discovery=_enum(rng, m.Discovery),
        status=_enum(rng, m.RecordStatus),
        provenance=_opt(rng, _provenance(rng)),
    )


def _appearance(rng: random.Random) -> m.Appearance:
    return m.Appearance(
        key=_text(rng, empty=False),
        feed_id="feed",
        entity_kind=_enum(rng, m.EntityKind),
        entity_key=_text(rng),
        first_seen_at="2026-10-26T02:30:00Z",
        last_seen_at="2026-10-26T02:30:00Z",
        present=rng.random() < 0.5,
        missing_complete_runs=rng.randint(0, 3),
        section=_opt(rng, "archive"),
        provenance=_opt(rng, _provenance(rng)),
    )


def _version(rng: random.Random) -> m.DocumentVersion:
    return m.DocumentVersion(
        document_key="doc",
        observed_at="2026-10-26T02:30:00Z",
        etag=_opt(rng, '"abc"'),
        last_modified=_opt(rng, "Mon, 26 Oct 2026 02:30:00 GMT"),
        content_length=_opt(rng, rng.randint(0, 10**9)),
        content_type=_opt(rng, "application/pdf"),
        byte_sha256=_opt(rng, "0" * 64),
        text_fingerprint=_opt(rng, "f" * 32),
        extraction_status=_opt(rng, "hindi_only"),
        verification_method=_opt(rng, "full_download"),
    )


def _event(rng: random.Random) -> m.Event:
    return m.Event(
        event_id=_text(rng, empty=False),
        event_type=_enum(rng, m.EventType),
        run_id="20261026T023000Z-0123abcd",
        feed_id="feed",
        entity_kind=_enum(rng, m.EntityKind),
        entity_key=_text(rng),
        occurred_at="2026-10-26T02:30:00Z",
        discovery=_enum(rng, m.Discovery),
        coverage_revision=rng.randint(1, 4),
        details=_mapping(rng),
    )


def _feed_state(rng: random.Random) -> m.FeedState:
    return m.FeedState(
        feed_id=_text(rng, empty=False),
        baseline_status=_enum(rng, m.BaselineStatus),
        coverage_revision=rng.randint(1, 3),
        baselined_revision=_opt(rng, 1),
        parser_version=rng.randint(1, 3),
        last_complete_run_id=_opt(rng, "20261026T023000Z-0123abcd"),
        last_item_count=_opt(rng, rng.randint(0, 900)),
        consecutive_failures=rng.randint(0, 5),
    )


def _feed_run(rng: random.Random) -> m.FeedRun:
    return m.FeedRun(
        run_id="20261026T023000Z-0123abcd",
        feed_id="feed",
        started_at="2026-10-26T02:30:00Z",
        finished_at="2026-10-26T02:31:00Z",
        fetch_status=_enum(rng, m.FetchStatus),
        parse_status=_enum(rng, m.ParseStatus),
        enumeration_status=_enum(rng, m.EnumerationStatus),
        accepted=rng.random() < 0.5,
        item_count=rng.randint(0, 900),
        previous_item_count=_opt(rng, 10),
        coverage_revision=1,
        parser_version=1,
        config_hash="c" * 64,
        code_revision="deadbeef",
        quarantine_reasons=tuple(_text(rng) for _ in range(rng.randint(0, 2))),
        diagnostics=tuple(_text(rng) for _ in range(rng.randint(0, 2))),
    )


def _state(rng: random.Random) -> m.CatalogState:
    matters = [_matter(rng) for _ in range(rng.randint(0, 3))]
    documents = [_document(rng) for _ in range(rng.randint(0, 3))]
    appearances = [_appearance(rng) for _ in range(rng.randint(0, 3))]
    feeds = [_feed_state(rng) for _ in range(rng.randint(0, 3))]
    return m.CatalogState(
        matters={x.key: x for x in matters},
        documents={x.key: x for x in documents},
        appearances={x.key: x for x in appearances},
        versions={
            x.key: tuple(_version(rng) for _ in range(rng.randint(0, 2))) for x in documents
        },
        feeds={x.feed_id: x for x in feeds},
    )


def _candidate(rng: random.Random) -> m.CandidateState:
    return m.CandidateState(
        run_id="20261026T023000Z-0123abcd",
        base_state_hash="b" * 64,
        state=_state(rng),
        events=tuple(_event(rng) for _ in range(rng.randint(0, 4))),
        feed_runs=tuple(_feed_run(rng) for _ in range(rng.randint(0, 3))),
    )


def _delivery(rng: random.Random) -> m.PendingDelivery:
    return m.PendingDelivery(
        delivery_id="d1",
        run_id="20261026T023000Z-0123abcd",
        message_id="<x@tracker>",
        base_data_commit="abc",
        code_revision="def",
        config_hash="c" * 64,
        digest_paths=tuple(_text(rng) for _ in range(rng.randint(0, 3))),
        recipient_set_sha256="e" * 64,
        recipient_count=rng.randint(1, 5),
        status=_enum(rng, m.DeliveryStatus),
        attempts=rng.randint(0, 4),
    )


GENERATORS = [
    _matter,
    _document,
    _appearance,
    _version,
    _event,
    _feed_state,
    _feed_run,
    _state,
    _candidate,
    _delivery,
]


@pytest.mark.parametrize("generate", GENERATORS, ids=lambda g: g.__name__.strip("_"))
@pytest.mark.parametrize("seed", range(25))
def test_round_trip_property(generate, seed: int) -> None:
    record = generate(random.Random(seed))
    text = s.document_text(record)
    restored = s.parse_document(type(record), text)
    assert restored == record
    assert s.document_text(restored) == text


def test_documents_are_deterministic_canonical_json() -> None:
    record = _candidate(random.Random(7))
    text = s.document_text(record)
    assert text.endswith("}\n") and not text.endswith("\n\n")
    document = json.loads(text)
    assert document["schema_version"] == m.SCHEMA_VERSION
    assert document["kind"] == "candidate_state"
    assert text == json.dumps(document, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def test_non_ascii_is_stored_as_utf8() -> None:
    matter = _matter(random.Random(1))
    matter = m.Matter(**{**matter.__dict__, "title": "विद्युत नियम ₹"})
    text = s.document_text(matter)
    assert "विद्युत नियम ₹" in text
    assert "विद्युत".encode() in s.dumps_bytes(s.encode_document(matter))


def test_mapping_insertion_order_does_not_change_output() -> None:
    a = m.CatalogState(feeds={"b": _feed_state(random.Random(1)), "a": _feed_state(random.Random(2))})
    b = m.CatalogState(feeds={"a": a.feeds["a"], "b": a.feeds["b"]})
    assert s.document_text(a) == s.document_text(b)
    assert s.state_hash(a) == s.state_hash(b)


def test_enums_and_tuples_are_plain_json() -> None:
    data = s.to_data(_feed_run(random.Random(3)))
    assert isinstance(data["fetch_status"], str)
    assert isinstance(data["diagnostics"], list)


def test_state_hash_changes_with_content() -> None:
    empty = m.CatalogState()
    other = m.CatalogState(feeds={"f": _feed_state(random.Random(4))})
    assert s.state_hash(empty) != s.state_hash(other)
    assert len(s.state_hash(empty)) == 64


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(unexpected=1), "unknown fields"),
        (lambda d: d.pop("title"), "missing field"),
        (lambda d: d.update(discovery="maybe"), "not a valid"),
        (lambda d: d.update(metadata=[]), "expected an object"),
        (lambda d: d.update(title=3), "expected a string"),
    ],
)
def test_malformed_records_are_rejected(mutate, message: str) -> None:
    document = s.encode_document(_matter(random.Random(5)))
    mutate(document)
    with pytest.raises(s.SerializationError, match=message):
        s.decode_document(m.Matter, document)


def test_wrong_kind_is_rejected() -> None:
    document = s.encode_document(_event(random.Random(6)))
    with pytest.raises(s.SerializationError, match="expected a 'matter' document"):
        s.decode_document(m.Matter, document)


def test_booleans_are_not_integers() -> None:
    document = s.encode_document(_feed_state(random.Random(8)))
    document["consecutive_failures"] = True
    with pytest.raises(s.SerializationError, match="expected an integer"):
        s.decode_document(m.FeedState, document)
