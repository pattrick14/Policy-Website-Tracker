"""Immutable outbox bundles: layout, integrity, recipient hashing, state transitions."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from policy_tracker.core.models import DeliveryStatus, PendingDelivery
from policy_tracker.operations import outbox as outbox_module
from policy_tracker.operations.outbox import (
    Attempt,
    Outbox,
    OutboxError,
    RecipientMismatch,
    make_message_id,
    recipient_set_sha256,
)
from policy_tracker.operations.store import StoreError
from policy_tracker.reporting.types import RenderedEmail, RenderedFile

RUN = "20261026T023000Z-1a2b3c4d"
RECIPIENTS = ("alice@example.org", "Bob <BOB@example.net>")


def _header(**changes) -> PendingDelivery:
    header = PendingDelivery(
        delivery_id=RUN,
        run_id=RUN,
        message_id=make_message_id(RUN, "example.org"),
        base_data_commit="a" * 40,
        code_revision="rev",
        config_hash="c" * 64,
        digest_paths=(f"digests/2026/10/{RUN}.md",),
        recipient_set_sha256=recipient_set_sha256(RECIPIENTS),
        recipient_count=2,
    )
    return replace(header, **changes)


def _email() -> RenderedEmail:
    csv = RenderedFile(f"digests/2026/10/{RUN}.csv", b"title\n=cmd\n", "text/csv")
    return RenderedEmail("Subject", "text", "<p>html</p>", (csv,), 42)


def test_round_trip_and_no_addresses_on_disk(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.create(_header(), _email())
    assert box.pending_ids() == [RUN]
    bundle = box.load(RUN)
    assert bundle.email == _email() and bundle.header == _header()
    blob = b"".join(p.read_bytes() for p in (tmp_path / "outbox").rglob("*") if p.is_file())
    assert b"alice" not in blob and b"example.net" not in blob


def test_bundle_is_immutable(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.create(_header(), _email())
    with pytest.raises(OutboxError, match="already exists"):
        box.create(_header(), _email())
    email_path = tmp_path / "outbox" / RUN / "email.json"
    document = json.loads(email_path.read_text())
    document["subject"] = "Tampered"
    email_path.write_text(json.dumps(document))
    with pytest.raises(OutboxError, match="modified"):
        box.load(RUN)


def test_attachment_tampering_detected(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.create(_header(), _email())
    (tmp_path / "outbox" / RUN / "attachments" / f"{RUN}.csv").write_bytes(b"other")
    with pytest.raises(OutboxError, match="digest"):
        box.load(RUN)


def test_recipient_hash_is_normalized_and_verified(tmp_path: Path) -> None:
    same = ["bob@example.net", " ALICE@example.org ", "alice@example.org"]
    assert recipient_set_sha256(same) == recipient_set_sha256(RECIPIENTS)
    box = Outbox(tmp_path)
    box.create(_header(), _email())
    header = box.load_header(RUN)
    assert box.verify_recipients(header, same) == ("alice@example.org", "bob@example.net")
    with pytest.raises(RecipientMismatch) as info:
        box.verify_recipients(header, ["alice@example.org", "carol@example.org"])
    assert "carol" not in str(info.value)


def test_message_id_is_deterministic() -> None:
    assert make_message_id(RUN, "example.org") == make_message_id(RUN, "EXAMPLE.org")
    assert make_message_id(RUN).endswith("@policy-tracker.invalid>")
    assert make_message_id(RUN, "bad domain!").endswith("@policy-tracker.invalid>")
    with pytest.raises(StoreError):
        make_message_id("not-a-run-id")


def test_attempts_and_delivered_transition(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.create(_header(), _email())
    first = box.record_attempt(RUN, Attempt("t1", 1, 1, False, None), delivered=False)
    assert first.status is DeliveryStatus.PENDING and first.attempts == 1
    second = box.record_attempt(RUN, Attempt("t2", 2, 0, False, None), delivered=True)
    assert second.status is DeliveryStatus.DELIVERED and second.attempts == 2
    assert box.pending_ids() == []
    assert [a["at"] for a in box.load_attempts(RUN)] == ["t1", "t2"]
    assert box.load(RUN).email == _email()  # content untouched by transitions
    with pytest.raises(OutboxError, match="already delivered"):
        box.record_attempt(RUN, Attempt("t3", 2, 0, False, None), delivered=True)


def test_new_bundle_must_be_fresh(tmp_path: Path) -> None:
    with pytest.raises(OutboxError):
        Outbox(tmp_path).create(_header(attempts=1), _email())
    with pytest.raises(OutboxError):
        Outbox(tmp_path).create(_header(delivery_id="20261026T023000Z-ffffffff"), _email())


def test_abandon_moves_entry(tmp_path: Path) -> None:
    box = Outbox(tmp_path)
    box.create(_header(), _email())
    box.abandon(RUN, reason="SMTP account closed", at="t")
    assert box.pending_ids() == [] and box.ids() == []
    assert (tmp_path / "outbox" / "abandoned" / RUN / "abandoned.json").is_file()


def test_malformed_recipient_rejected_without_echo() -> None:
    with pytest.raises(OutboxError) as info:
        outbox_module.normalize_recipients(["secret-name"])
    assert "secret-name" not in str(info.value)
